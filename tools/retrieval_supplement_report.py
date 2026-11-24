"""Complete-slot functional reporting with maintained file grading and no origin claims."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from common_lore_index.retrieval.query_contracts import QueryEnvelope
from retrieval_evaluation_grading import ExecutionState, GradedSlotRecord
from retrieval_evaluation_schema import EvaluationTask
from retrieval_semantic_arm_validation import FileIdentityTuple
from retrieval_semantic_fusion import FileFusionResult
from retrieval_public_text_fusion import LEXICAL_ARM, NOT_APPLICABLE, public_text_fusion_to_dict
from retrieval_semantic_grading import grade_w5_task_slot
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_supplement_capture import capture_baseline_envelope, verify_capture_reference
from retrieval_supplement_catalog import SupplementCatalog
from retrieval_supplement_applicability import native_branch
from retrieval_supplement_declaration import InertDeclaration, SlotIdentity, capture_arm, ordered_slots, representation_for
from retrieval_supplement_operation import summarize_phase_costs
from retrieval_supplement_operation_values import PHASES
from retrieval_supplement_tasks import task_digests
from retrieval_supplement_values import ARMS, canonical_bytes, digest_json, strict_json


@dataclass(frozen=True)
class SlotResult:
    """Supplied result plus both exact capture artifacts; not a native receipt."""

    slot: SlotIdentity
    publication_pin: VerifiedPublicationPin
    capture_record_raw: bytes
    capture_reference_raw: bytes
    baseline_envelope: QueryEnvelope | None = None
    fusion_result: FileFusionResult | None = None
    delivery_kind: str = "functional_synthetic"
    native_path_surfaces: tuple[str, ...] | None = None


def result_artifact_bytes(result: SlotResult) -> bytes | None:
    """Bind result bytes without a misleading legacy v1 label on v2 arms."""
    if result.baseline_envelope is not None and result.fusion_result is not None:
        raise ValueError("supplement_report_conflicting_results")
    if result.baseline_envelope is not None:
        if result.slot.arm == ARMS[0]:
            return canonical_bytes({"envelope": result.baseline_envelope.to_dict(),
                "native_path_surfaces": result.native_path_surfaces})
        return canonical_bytes(result.baseline_envelope.to_dict())
    if result.fusion_result is not None:
        vector_arm = ARMS[4] if result.slot.arm == ARMS[5] else result.slot.arm
        representations = {vector_arm: representation_for(vector_arm)}
        if result.slot.arm == ARMS[5]:
            representations[LEXICAL_ARM] = NOT_APPLICABLE
        return canonical_bytes(public_text_fusion_to_dict(result.fusion_result, representations=representations))
    return None


def _grade(task: EvaluationTask, result: SlotResult, state: ExecutionState) -> GradedSlotRecord:
    pin = result.publication_pin
    if not VerifiedPublicationPin.is_factory_issued(pin):
        raise ValueError("supplement_report_publication_pin")
    pin.revalidate_current()
    if pin.graph_id != task.graph_id or pin.generation != task.generation:
        raise ValueError("supplement_report_publication_scope")
    record = strict_json(result.capture_record_raw)
    if result.slot.arm in ARMS[:3]:
        envelope = result.baseline_envelope
        if envelope is None or result.fusion_result is not None:
            raise ValueError("supplement_report_baseline_envelope_required")
        if result.slot.arm == ARMS[0] and state == ExecutionState.COMPLETED:
            branch = native_branch(task, result.native_path_surfaces) if result.native_path_surfaces is not None else None
            fallback = "required" if envelope.fallback is not None and envelope.fallback.caller_selected else "none"
            if branch is None or branch != {"surface": envelope.match_surface.value,
                    "effective_mode": envelope.effective_mode.value, "fallback": fallback}:
                raise ValueError("supplement_report_native_branch")
        elif result.native_path_surfaces is not None:
            raise ValueError("supplement_report_unexpected_native_scope")
        recomputed = capture_baseline_envelope(task, slot=result.slot, envelope=envelope,
                                              execution_state=state, publication_pin=pin)
        if canonical_bytes(record) != recomputed:
            raise ValueError("supplement_report_baseline_capture_mismatch")
        snapshots = pin.snapshot_by_binding()
        returned = []
        for hit in envelope.hits:
            locator = hit.locator
            entry = snapshots.get(locator.binding_id)
            if entry is None:
                raise ValueError("supplement_report_hit_binding")
            returned.append(FileIdentityTuple(locator.binding_id, entry[1], locator.source_relative_path))
        return grade_w5_task_slot(
            task, arm_id=result.slot.arm, verified_pin=pin, returned_files=returned,
            execution_state=state, capability_state=envelope.capability_state.value,
            refusal_code=envelope.refusal_code, scope_complete=envelope.scope_complete,
        )
    if result.baseline_envelope is not None or pin.backend != "postgresql":
        raise ValueError("supplement_report_vector_scope")
    if state == ExecutionState.COMPLETED and result.fusion_result is None:
        raise ValueError("supplement_report_vector_result_required")
    if result.fusion_result is not None:
        return grade_w5_task_slot(task, arm_id=capture_arm(result.slot.arm), verified_pin=pin,
                                  fusion_result=result.fusion_result, execution_state=state,
                                  representation_version=representation_for(result.slot.arm))
    capability = {ExecutionState.REFUSED: "refused", ExecutionState.FAILED: "failed"}.get(state, "unavailable")
    return grade_w5_task_slot(
        task, arm_id=capture_arm(result.slot.arm), verified_pin=pin, execution_state=state,
        capability_state=capability, refusal_code=record["reason"] if state == ExecutionState.REFUSED else None,
        scope_complete=False,
        representation_version=representation_for(result.slot.arm),
    )


def _surface_observation(result: SlotResult, state: ExecutionState) -> dict[str, Any]:
    envelope = result.baseline_envelope
    if envelope is not None:
        return {"surface": envelope.match_surface.value,
            "fallback": "required" if envelope.fallback is not None and envelope.fallback.caller_selected else "none",
            "requested_mode": envelope.requested_mode.value, "effective_mode": envelope.effective_mode.value,
            "fallback_disclosure": None if envelope.fallback is None else envelope.fallback.to_dict(),
            "basis": "supplied_query_envelope"}
    return {"surface": ("stored_metadata" if result.slot.arm == ARMS[3] else "captured_text")
            if state == ExecutionState.COMPLETED else "none",
        "fallback": "required" if result.fusion_result is not None and result.fusion_result.fallback_disclosed else "none",
        "requested_mode": None, "effective_mode": None, "fallback_disclosure": None,
        "basis": "declared_representation_input_category_not_query_hit_surface"}


def report_supplement(
    catalog: SupplementCatalog, declaration: InertDeclaration, results: Sequence[SlotResult],
    *, phase_records: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Reject missing/duplicate/foreign/stale cells; preserve every declared state."""
    if type(declaration) is not InertDeclaration or not isinstance(catalog, SupplementCatalog):
        raise ValueError("supplement_report_inputs")
    declared = declaration.to_dict()
    expected = ordered_slots(catalog)
    if declared["catalog_sha256"] != catalog.digest or declared["task_digests"] != {
        task.task_id: task_digests(task) for task in catalog.tasks
    }:
        raise ValueError("supplement_report_catalog_drift")
    if not isinstance(results, (list, tuple)) or len(results) != len(expected):
        raise ValueError("supplement_report_missing_slots")
    tasks = {case.task.task_id: case for case in catalog.cases}
    seen: set[tuple[str, str, int, int]] = set()
    rows: list[dict[str, Any]] = []
    for required, result in zip(expected, results, strict=True):
        if type(result) is not SlotResult or type(result.slot) is not SlotIdentity:
            raise ValueError("supplement_report_slot_type")
        result.slot.validate()
        if result.slot.key in seen:
            raise ValueError("supplement_report_duplicate_slot")
        seen.add(result.slot.key)
        if result.slot.key != required.key:
            raise ValueError("supplement_report_foreign_or_reordered_slot")
        if type(result.delivery_kind) is not str or result.delivery_kind not in {"functional_synthetic", "external_unqualified"}:
            raise ValueError("supplement_report_origin_claim")
        reference = verify_capture_reference(
            result.capture_reference_raw, result.capture_record_raw, slot=result.slot,
            declaration=declaration, result_artifact_raw=result_artifact_bytes(result),
        )
        ref = reference.to_dict()
        state = ExecutionState(ref["state"])
        case = tasks[result.slot.task_id]
        graded = _grade(case.task, result, state)
        expectation = case.expectations[result.slot.arm]
        actual = _surface_observation(result, state)
        surface_matches = actual["surface"] == expectation["actual_surface"]
        fallback_matches = expectation["fallback_policy"] == "record_actual" or actual["fallback"] == expectation["fallback_policy"]
        rows.append({
            "slot": result.slot.to_dict(), "task_digests": task_digests(case.task),
            "delivery_kind": result.delivery_kind, "execution_state": state.value,
            "reason": ref["reason"], "expected_execution_state": expectation["execution"],
            "surface_observation": actual, "expected_surface": expectation["actual_surface"],
            "expected_fallback_policy": expectation["fallback_policy"],
            "surface_expectation_matches": surface_matches, "fallback_expectation_matches": fallback_matches,
            "capability_expectation_matches": state.value == expectation["execution"]
            and (expectation["reason"] is None or ref["reason"] == expectation["reason"])
            and surface_matches and fallback_matches,
            "grading": graded.to_dict(), "capture_reference_sha256": reference.digest,
        })
    expected_binding = {
        "supplement_id": declared["supplement_id"], "input_pin_sha256": declared["input_pin_sha256"],
        "freeze_sha256": declared["freeze_sha256"], "source_closure_sha256": declared["source_closure_sha256"],
        "attempt_id": declared["attempt_id"],
    }
    if not isinstance(phase_records, (list, tuple)) or len(phase_records) > 4096:
        raise ValueError("supplement_report_phase_records")
    for record in phase_records:
        if not isinstance(record, Mapping):
            raise ValueError("supplement_report_phase_record_type")
        identity = record.get("identity")
        if not isinstance(identity, Mapping) or any(identity.get(key) != value for key, value in expected_binding.items()):
            raise ValueError("supplement_report_foreign_phase_cost")
        if identity.get("shared_owner") is None and (
            identity.get("task_id"), identity.get("arm"), identity.get("repetition"), identity.get("generation")
        ) not in seen:
            raise ValueError("supplement_report_foreign_phase_slot")
    costs = summarize_phase_costs(phase_records)
    summaries: dict[str, Any] = {}
    for arm in ARMS:
        selected = [row for row in rows if row["slot"]["arm"] == arm]
        summaries[arm] = {
            "whole_declared_denominator": len(catalog.tasks) * 3,
            "states": {state.value: sum(row["execution_state"] == state.value for row in selected) for state in ExecutionState},
            "safety_refusal_total": sum(row["grading"]["grading_rule"] == "safety_refusal" for row in selected),
            "safety_refusal_correct": sum(row["grading"]["safety_refusal_correct"] is True for row in selected),
            "capability_mismatches": sum(row["capability_expectation_matches"] is False for row in selected),
        }
    return {
        "schema": "cloe-calibration-supplement-report-v1", "declaration_sha256": declaration.digest,
        "supplement_id": declared["supplement_id"], "input_pin_sha256": declared["input_pin_sha256"],
        "freeze_sha256": declared["freeze_sha256"], "source_closure_sha256": declared["source_closure_sha256"],
        "slot_count": len(rows), "slot_set_sha256": digest_json([slot.to_dict() for slot in expected]),
        "slots": rows, "arm_summaries": summaries, "phase_costs": costs,
        "phase_presence_only": dict(Counter(record["identity"]["phase"] for record in phase_records)),
        "unobserved_phases": sorted(PHASES - {record["identity"]["phase"] for record in phase_records}),
        "phase_presence_is_not_per_slot_cost_coverage": True,
        "cost_completeness": "unobserved" if not phase_records else "reported_scopes_only",
        "repetitions_are_independent_samples": False, "predecessor_pooling": False,
        "native_acceptance": False, "scientific_acceptance": False,
        "production_acceptance": False, "held_out_acceptance": False,
    }
