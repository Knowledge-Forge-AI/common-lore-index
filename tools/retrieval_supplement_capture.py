"""Bind unchanged operation records to qualified supplement/journal identities."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from common_lore_index.retrieval.query_contracts import CapabilityState, QueryEnvelope
from retrieval_evaluation_grading import ExecutionState
from retrieval_evaluation_schema import EvaluationTask
from retrieval_semantic_experiment_contracts import digest_json as observed_digest_json
from retrieval_semantic_observed_operation import finalize_observed_operation, validate_observed_operation
from retrieval_semantic_observed_operation_digest import MAX_OBSERVED_OPERATION_BYTES
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_supplement_declaration import InertDeclaration, JournalBinding, SlotIdentity, capture_arm
from retrieval_supplement_values import ARMS, canonical_bytes, digest_bytes, digest_json, strict_json

_TOKEN = object()


class CaptureReference:
    """A validated join reference; it never certifies native origin or execution."""

    __slots__ = ("_raw",)
    _raw: bytes

    def __init__(self, value: Mapping[str, Any], *, _token: object = None) -> None:
        if _token is not _TOKEN:
            raise ValueError("supplement_capture_factory_required")
        object.__setattr__(self, "_raw", canonical_bytes(value))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("CaptureReference is immutable")

    @property
    def raw(self) -> bytes:
        return self._raw

    @property
    def digest(self) -> str:
        return digest_bytes(self._raw)

    def to_dict(self) -> dict[str, Any]:
        return dict(strict_json(self._raw))


def bind_operation_capture(
    raw_record: bytes, *, slot: SlotIdentity, declaration: InertDeclaration,
    result_artifact_raw: bytes | None = None,
) -> CaptureReference:
    """Keep two representation captures distinct even if their old hashes coincide."""
    if type(declaration) is not InertDeclaration or type(slot) is not SlotIdentity:
        raise ValueError("supplement_capture_identity")
    if result_artifact_raw is not None and (type(result_artifact_raw) is not bytes or not 0 < len(result_artifact_raw) <= 512 * 1024):
        raise ValueError("supplement_capture_result_artifact_bytes")
    slot.validate()
    declared = declaration.to_dict()
    if slot.task_id not in declared["ordered_task_ids"] or slot.arm not in ARMS:
        raise ValueError("supplement_capture_foreign_slot")
    # The old payload-size owner remains authoritative. 256 bounds only the
    # outer canonical digest-field framing; it cannot enlarge the old payload.
    if type(raw_record) is not bytes or len(raw_record) > MAX_OBSERVED_OPERATION_BYTES + 256:
        raise ValueError("supplement_capture_record_bytes")
    value = strict_json(raw_record)
    bare_arm = capture_arm(slot.arm)
    identity = {"generation": slot.generation, "task_id": slot.task_id,
                "arm_id": bare_arm, "repetition": slot.repetition}
    checked = validate_observed_operation(value, identity=identity)
    kind = bare_arm if bare_arm in {"exact_vector", "hybrid_rrf"} else "baseline_query"
    if checked["kind"] != kind:
        raise ValueError("supplement_capture_kind")
    journal = JournalBinding.from_mapping(declared["journals"][slot.arm])
    actual = checked["actual"]
    if not isinstance(actual, Mapping):
        raise ValueError("supplement_capture_actual")
    observed_profile = actual["profile_identity"]
    if observed_profile is not None and observed_profile != journal.profile_sha256:
        raise ValueError("supplement_capture_profile")
    if kind != "baseline_query" and checked["state"] == "completed":
        if observed_profile is None or actual["backend"] != "postgresql":
            raise ValueError("supplement_capture_completed_vector_scope")
    return CaptureReference({
        "schema": "cloe-supplement-operation-reference-v1",
        "supplement_id": declared["supplement_id"], "input_pin_sha256": declared["input_pin_sha256"],
        "freeze_sha256": declared["freeze_sha256"], "source_closure_sha256": declared["source_closure_sha256"],
        "declaration_sha256": declaration.digest, "outer_attempt_id": declared["attempt_id"],
        "artifact_profile_sha256": declared["artifact_profile_sha256"],
        "slot": slot.to_dict(), "capture_arm": bare_arm,
        "journal": journal.to_dict(), "capture_record_sha256": checked["record_sha256"],
        "capture_artifact_sha256": digest_bytes(raw_record),
        "result_artifact_sha256": None if result_artifact_raw is None else digest_bytes(result_artifact_raw),
        "state": checked["state"], "reason": checked["reason"],
        "observed_profile_identity": observed_profile,
        "observed_rebuild_id": actual["rebuild_id"],
        "profile_binding_is_not_observation": observed_profile is None,
        "native_origin_or_execution_qualification": False,
    }, _token=_TOKEN)


def verify_capture_reference(
    reference_raw: bytes, raw_record: bytes, *, slot: SlotIdentity, declaration: InertDeclaration,
    result_artifact_raw: bytes | None = None,
) -> CaptureReference:
    """Reread/rejoin every field; a rehashed stale or foreign reference fails."""
    provided = strict_json(reference_raw)
    expected = bind_operation_capture(raw_record, slot=slot, declaration=declaration,
                                     result_artifact_raw=result_artifact_raw)
    if canonical_bytes(provided) != expected.raw:
        raise ValueError("supplement_capture_reference_changed")
    return expected


def check_envelope_execution(state: ExecutionState, envelope: QueryEnvelope) -> None:
    """Execution and capability are separate but cannot contradict each other."""
    envelope.validate()
    if type(state) is not ExecutionState:
        raise ValueError("supplement_capture_execution_state")
    states = {
        ExecutionState.COMPLETED: {CapabilityState.AVAILABLE, CapabilityState.PARTIAL},
        ExecutionState.REFUSED: {CapabilityState.REFUSED},
        ExecutionState.UNAVAILABLE: {CapabilityState.UNAVAILABLE, CapabilityState.STALE},
        ExecutionState.FAILED: {CapabilityState.FAILED},
        ExecutionState.NOT_RUN: {CapabilityState.UNAVAILABLE, CapabilityState.NOT_RUN},
    }
    if state not in states or envelope.capability_state not in states[state]:
        raise ValueError("supplement_capture_envelope_execution")
    if state != ExecutionState.COMPLETED and envelope.hits:
        raise ValueError("supplement_capture_nonexecution_hits")


def capture_baseline_envelope(
    task: EvaluationTask, *, slot: SlotIdentity, envelope: QueryEnvelope,
    execution_state: ExecutionState, publication_pin: VerifiedPublicationPin,
) -> bytes:
    """Capture a caller's actual envelope without importing generation cost zeros."""
    slot.validate()
    if slot.arm not in ARMS[:3] or slot.task_id != task.task_id or slot.generation != task.generation:
        raise ValueError("supplement_capture_baseline_slot")
    if not VerifiedPublicationPin.is_factory_issued(publication_pin):
        raise ValueError("supplement_capture_publication_pin")
    publication_pin.revalidate_current()
    if publication_pin.graph_id != task.graph_id or publication_pin.generation != task.generation:
        raise ValueError("supplement_capture_publication_scope")
    check_envelope_execution(execution_state, envelope)
    backend = "native_filesystem" if slot.arm == ARMS[0] else "postgresql"
    pin_backend = "sqlite" if slot.arm == ARMS[0] else "postgresql"
    if (
        publication_pin.backend != pin_backend
        or envelope.scope.graph_id != task.graph_id or envelope.scope.backend != backend
        or set(envelope.scope.selected_binding_ids) != set(task.selected_bindings)
        or envelope.scope.source_path_prefix != task.source_path_prefix
        or envelope.requested_mode.value != task.requested_mode
    ):
        raise ValueError("supplement_capture_envelope_scope")
    for value, expected in ((envelope.scope.publication_bundle_id, publication_pin.publication_bundle_id),
                            (envelope.scope.snapshot_manifest_id, publication_pin.snapshot_manifest_id)):
        if value is not None and value != expected:
            raise ValueError("supplement_capture_stale_envelope")
    for key, expected_identity in (("generation", task.generation), ("graph_id", task.graph_id),
                          ("run_id", publication_pin.run_id), ("accepted_run_id", publication_pin.run_id)):
        if key in envelope.provenance:
            value = envelope.provenance[key]
            if type(value) is not type(expected_identity) or value != expected_identity:
                raise ValueError("supplement_capture_stale_envelope")
    record = finalize_observed_operation({
        "schema": "cloe-w5-observed-operation-v1",
        "identity": {"generation": slot.generation, "task_id": slot.task_id,
                     "arm_id": slot.arm, "repetition": slot.repetition},
        "kind": "baseline_query", "state": execution_state.value,
        "reason": envelope.refusal_code or envelope.unavailable_reason,
        "declared_mode": task.requested_mode,
        "actual": {
            "operation": "query_envelope", "backend": envelope.scope.backend,
            "publication_identity": observed_digest_json(publication_pin.to_dict()),
            "profile_identity": None, "rebuild_id": None,
            "input_descriptor_sha256": digest_json({"task": task.to_dict(), "arm": slot.arm}),
            "selected_binding_ids": list(envelope.scope.selected_binding_ids),
            "snapshot_manifest_id": publication_pin.snapshot_manifest_id,
            "effective_mode": envelope.effective_mode.value, "match_surface": envelope.match_surface.value,
            "fallback": None if envelope.fallback is None else envelope.fallback.to_dict(),
        },
        "work": {"per_binding": None, "hnsw": None, "fusion": None, "reference": None},
    })
    return canonical_bytes(record)
