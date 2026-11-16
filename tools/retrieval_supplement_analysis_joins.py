"""Admitted record types plus counted-token, record-ID and phase-cost joins.

Pure leaf over stdlib and maintained phase-cost contracts. Enforces the exact
``PhaseRecorder`` state/elapsed/error/observation_complete invariants from
``retrieval_supplement_operation._checked_record``, joins ``paired.cost_summary``
to ``supplement_report.phase_costs``, and cross-checks counted worker tokens.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from retrieval_supplement_analysis_values import (
    COST_STATES,
    COST_SUMMARY_SCHEMA,
    ELAPSED_OBSERVATIONS,
    NATIVE_TOKEN_RECORDS,
    ExpectedProvenance,
    TaskDescriptor,
    as_bool,
    as_int,
    as_safe_id,
    as_str,
    exact_keys,
    fail,
    refuse,
    sample_name_category,
    shared_owner_category,
)
from retrieval_supplement_operation import _checked_record
from retrieval_supplement_primitives import ARMS, digest_json
from retrieval_supplement_study_contracts import MODEL_MAX_TOKENS, V1_DOCUMENTS, V1_QUERIES, V2_DOCUMENTS, V2_QUERIES

_COST_SUMMARY_KEYS = frozenset({
    "schema", "record_count", "groups", "cross_layer_sum",
    "shared_cost_allocation", "unknown_defaults", "native_or_scientific_acceptance",
})


@dataclass(frozen=True)
class SlotRecord:
    task_id: str
    family: str
    kind: str
    arm: str
    repetition: int
    state: str
    reason_category: str
    grading_rule: str
    grading_state: str
    capability_category: str
    metrics: dict[str, float]
    returned_hit_count: int
    safety_refusal_correct: bool | None
    absent_answer_correct: bool | None
    surface_expectation_matches: bool
    fallback_expectation_matches: bool
    capability_expectation_matches: bool
    query_record_id: str | None
    target_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class CostObservation:
    phase: str
    scope: str  # "shared" | "per_arm_query" | "per_arm_non_query"
    task_id: str | None
    arm: str | None
    repetition: int | None
    shared_owner_category: str | None
    state: str
    observation_complete: bool
    elapsed_ns: int | None
    elapsed_units: str
    elapsed_observation: str
    error_flags: dict[str, bool]
    samples: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class TokenRecordEvidence:
    record_id: str
    representation: str
    token_count: int
    token_count_before_limit: int
    overflow_chunks: int
    truncated: bool


@dataclass(frozen=True)
class AdmittedBundle:
    origin: str
    native_qualified: bool
    raw_sha256: str
    declaration_source_closure_sha256: str
    observed_namespaces: tuple[str, ...]
    tasks: tuple[TaskDescriptor, ...]
    slots: tuple[SlotRecord, ...]
    state_counts: dict[str, int]
    task_truncated: dict[str, bool]
    task_completed_all_arms: dict[str, bool]
    comparable_positive_tasks: tuple[str, ...]
    family_counts: dict[str, int]
    floor_met: bool
    costs: tuple[CostObservation, ...]
    cost_record_count: int
    cost_completeness: str
    counted_worker_tokens_verified: bool

    @property
    def task_count(self) -> int:
        return len(self.tasks)


def parse_counted_token_evidence(
    worker_outputs: Sequence[Mapping[str, Any]],
    *,
    native: bool,
    expected_total: int,
) -> dict[str, TokenRecordEvidence]:
    """Validate counted-worker token outputs; reject forged truncation flags."""
    refuse(
        isinstance(worker_outputs, (list, tuple)) and not isinstance(worker_outputs, (str, bytes)),
        "analysis:counted_tokens_sequence_required",
    )
    if native:
        refuse(len(worker_outputs) == 2, "analysis:native_worker_token_count")
        expected_v1 = V1_DOCUMENTS + V1_QUERIES
        expected_v2 = V2_DOCUMENTS + V2_QUERIES
        refuse(
            [w.get("representation") for w in worker_outputs if isinstance(w, Mapping)]
            == ["canonical-node-v1", "canonical-node-public-text-v2"],
            "analysis:native_worker_representations",
        )
        refuse(
            [w.get("record_count") for w in worker_outputs if isinstance(w, Mapping)]
            == [expected_v1, expected_v2],
            "analysis:native_worker_record_counts",
        )
        refuse(expected_total == NATIVE_TOKEN_RECORDS, "analysis:native_token_records")

    by_id: dict[str, TokenRecordEvidence] = {}
    for output in worker_outputs:
        if not isinstance(output, Mapping):
            fail("analysis:counted_worker_object_required")
        representation = as_str(output.get("representation"), "worker_representation")
        refuse(
            representation in {"canonical-node-v1", "canonical-node-public-text-v2"},
            "analysis:worker_representation",
        )
        rows = output.get("token_evidence")
        if not isinstance(rows, list):
            fail("analysis:token_evidence_list_required")
        declared_count = as_int(output.get("record_count"), "worker_record_count", 0, 4096)
        refuse(declared_count == len(rows), "analysis:worker_record_count_mismatch")
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                fail("analysis:token_row_object_required")
            record_id = as_str(raw_row.get("record_id"), "token_record_id")
            refuse(bool(record_id) and len(record_id) <= 256 and record_id not in by_id,
                   "analysis:duplicate_or_invalid_token_record")
            token_count = as_int(raw_row.get("token_count"), "token_count", 0, MODEL_MAX_TOKENS)
            before_raw = raw_row.get("token_count_before_limit")
            before = (
                token_count
                if before_raw is None
                else as_int(before_raw, "token_count_before_limit", token_count, 1000000)
            )
            overflow = as_int(raw_row.get("overflow_chunks", 0), "overflow_chunks", 0, 10000)
            truncated = as_bool(raw_row.get("truncated"), "token_truncated")
            if before > token_count or overflow > 0:
                refuse(truncated is True, "analysis:forged_untruncated_token_row")
            by_id[record_id] = TokenRecordEvidence(
                record_id=record_id,
                representation=representation,
                token_count=token_count,
                token_count_before_limit=before,
                overflow_chunks=overflow,
                truncated=truncated,
            )
    refuse(len(by_id) == expected_total, "analysis:token_record_total_mismatch")
    return by_id


def validate_slot_record_ids(
    *,
    task: TaskDescriptor,
    arm: str,
    state: str,
    query_raw: object,
    targets_raw: object,
    native: bool,
) -> tuple[str | None, tuple[str, ...]]:
    """Enforce baseline vs vector/fusion query/target record ID contracts."""
    if not isinstance(targets_raw, list) or len(targets_raw) > 128:
        fail("analysis:target_record_ids_shape")
    if arm in ARMS[:3] or state != "completed":
        refuse(query_raw is None and targets_raw == [], "analysis:unexpected_record_ids")
        return None, ()
    query_id = as_str(query_raw, "query_record_id")
    refuse(0 < len(query_id) <= 256, "analysis:query_record_id_bounds")
    target_ids: list[str] = []
    for item in targets_raw:
        tid = as_str(item, "target_record_id")
        refuse(0 < len(tid) <= 256, "analysis:target_record_id_bounds")
        target_ids.append(tid)
    refuse(len(set(target_ids)) == len(target_ids), "analysis:duplicate_target_record_id")
    if task.kind == "positive":
        refuse(bool(target_ids), "analysis:missing_positive_target_record_ids")
    elif task.kind == "absent":
        refuse(not target_ids, "analysis:unexpected_absent_target_record_ids")
    if native:
        if arm == ARMS[3]:
            refuse(query_id == f"v1.query:{task.task_id}", "analysis:native_v1_query_id")
            refuse(
                all(t.startswith("v1.document:") for t in target_ids),
                "analysis:native_v1_target_id",
            )
        else:
            refuse(query_id == f"ptx.q.{task.task_id}", "analysis:native_v2_query_id")
            refuse(
                all(t.startswith("ptx.d.") for t in target_ids),
                "analysis:native_v2_target_id",
            )
    return query_id, tuple(target_ids)


def validate_bundle_costs(
    *,
    report: Mapping[str, Any],
    paired: Mapping[str, Any],
    provenance: ExpectedProvenance,
    declared_slots: frozenset[tuple[str, str, int, int]],
) -> tuple[CostObservation, ...]:
    """Validate layered phase costs and join ``paired.cost_summary`` to ``phase_costs``."""
    summary = exact_keys(report["phase_costs"], _COST_SUMMARY_KEYS, "phase_costs")
    refuse(summary["schema"] == COST_SUMMARY_SCHEMA, "analysis:cost_schema")
    refuse(summary["cross_layer_sum"] is None, "analysis:cost_cross_layer_sum")
    refuse(
        summary["shared_cost_allocation"] == "retained_once_not_divided",
        "analysis:cost_allocation",
    )
    refuse(summary["unknown_defaults"] == "remain_unknown", "analysis:cost_unknown_defaults")
    refuse(summary["native_or_scientific_acceptance"] is False, "analysis:cost_acceptance")
    refuse(paired["cost_summary"] == summary, "analysis:cost_summary_join")

    groups = summary["groups"]
    if not isinstance(groups, dict):
        fail("analysis:cost_groups")
    observations: list[CostObservation] = []
    seen_scopes: set[str] = set()
    binding: tuple[str, ...] | None = None

    for owner, records in sorted(groups.items()):
        as_str(owner, "cost_group_owner")
        if not isinstance(records, list) or not records:
            fail("analysis:cost_group_records")
        for raw_record in records:
            if not isinstance(raw_record, Mapping):
                fail("analysis:cost_record_object_required")
            try:
                checked, identity = _checked_record(raw_record)
            except ValueError:
                fail("analysis:cost_record_invalid")
            current_binding = (
                identity.supplement_id,
                identity.input_pin_sha256,
                identity.freeze_sha256,
                identity.source_closure_sha256,
                identity.attempt_id,
            )
            if binding is not None:
                refuse(current_binding == binding, "analysis:cost_foreign_binding")
            binding = current_binding
            refuse(
                identity.input_pin_sha256 == provenance.input_pin_sha256
                and identity.attempt_id == provenance.attempt_id,
                "analysis:cost_provenance_binding",
            )
            if "supplement_id" in report:
                refuse(
                    identity.supplement_id == report["supplement_id"],
                    "analysis:cost_supplement_id_join",
                )
            if "freeze_sha256" in report:
                refuse(
                    identity.freeze_sha256 == report["freeze_sha256"],
                    "analysis:cost_freeze_join",
                )
            if "source_closure_sha256" in report:
                refuse(
                    identity.source_closure_sha256 == report["source_closure_sha256"],
                    "analysis:cost_source_closure_join",
                )
            expected_owner = (
                f"shared:{identity.shared_owner}"
                if identity.shared_owner is not None
                else str(identity.arm)
            )
            refuse(owner == expected_owner, "analysis:cost_group_owner")
            if identity.shared_owner is None:
                slot_key = (
                    str(identity.task_id),
                    str(identity.arm),
                    int(identity.repetition or 0),
                    int(identity.generation),
                )
                refuse(slot_key in declared_slots, "analysis:cost_foreign_slot")
            scope_digest = digest_json(identity.to_dict())
            refuse(scope_digest not in seen_scopes, "analysis:cost_duplicate_scope")
            seen_scopes.add(scope_digest)

            state = as_str(checked["state"], "cost_state")
            refuse(state in COST_STATES, "analysis:cost_state_closed")
            elapsed_obs = as_str(checked["elapsed_observation"], "elapsed_observation")
            refuse(elapsed_obs in ELAPSED_OBSERVATIONS, "analysis:cost_elapsed_observation")
            elapsed_raw = checked["elapsed_ns"]
            elapsed_ns = (
                None
                if elapsed_raw is None
                else as_int(elapsed_raw, "elapsed_ns", 0, 2**63 - 1)
            )
            errors_raw = checked["errors"]
            error_flags = {
                key: errors_raw[key] is not None for key in ("body", "clock", "capture")
            }
            safe_samples: list[dict[str, Any]] = []
            for sample in checked["samples"]:
                safe_samples.append({
                    "name": sample_name_category(str(sample["name"])),
                    "units": str(sample["units"]),
                    "value": sample["value"],
                    "state": str(sample["state"]),
                    "layer": str(sample["layer"]),
                    "shared_owner_category": shared_owner_category(sample["shared_owner"]),
                    "additive": False,
                })
            if identity.shared_owner is not None:
                scope = "shared"
            elif identity.phase == "query":
                scope = "per_arm_query"
            else:
                scope = "per_arm_non_query"
            observations.append(CostObservation(
                phase=identity.phase,
                scope=scope,
                task_id=None if identity.task_id is None else as_safe_id(identity.task_id, "cost_task"),
                arm=identity.arm,
                repetition=identity.repetition,
                shared_owner_category=shared_owner_category(identity.shared_owner),
                state=state,
                observation_complete=as_bool(checked["observation_complete"], "observation_complete"),
                elapsed_ns=elapsed_ns,
                elapsed_units="ns",
                elapsed_observation=elapsed_obs,
                error_flags=error_flags,
                samples=tuple(safe_samples),
            ))

    refuse(
        as_int(summary["record_count"], "cost_record_count", 0, 4096) == len(observations),
        "analysis:cost_record_count",
    )
    if len(observations) == 0:
        refuse(report["cost_completeness"] in {"unobserved", "reported_scopes_only"},
               "analysis:cost_completeness_claim")
    else:
        refuse(report["cost_completeness"] == "reported_scopes_only",
               "analysis:cost_completeness_claim")
    return tuple(observations)


__all__ = [
    "AdmittedBundle",
    "CostObservation",
    "SlotRecord",
    "TokenRecordEvidence",
    "parse_counted_token_evidence",
    "validate_bundle_costs",
    "validate_slot_record_ids",
]
