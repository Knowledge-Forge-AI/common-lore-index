"""Authenticated body query owners for the fixed 432-slot grid."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from common_lore_index.ops.lexical_service import query_graph_lexical_generation
from common_lore_index.retrieval.query_contracts import (
    DEFAULT_ORDERING_POLICY_VERSION, QUERY_CONTRACT_VERSION, CapabilityState,
    EvaluationLocator, MatchSurface, QueryEnvelope, QueryHit, QueryMode, QueryScope,
    refused_envelope, unavailable_envelope,
)
from retrieval_body_study_slots import BodySlotBatch, captured_v2_slot_matches, execute_body_slots
from retrieval_body_study_values import (
    ARMS, FTS_ARM, FUSION_ARM, GRAPH_ARM, NATIVE_ARM,
    V1_ARM, V2_ARM, digest_value, require,
)
from retrieval_evaluation_admission_w5 import AdmissionState, execute_after_admission
from retrieval_evaluation_graph import execute_graph_baseline_task
from retrieval_evaluation_grading import ExecutionState
from retrieval_evaluation_native import execute_native_baseline_task
from retrieval_public_text_contracts import PrivateMarkerScope
from retrieval_public_text_fusion import (
    LEXICAL_ARM, fuse_study_arms, project_public_text_candidates,
    public_text_vector_arm, v1_vector_arm,
)
from retrieval_public_text_admission import public_text_applicability, supplemental_body_reason
from retrieval_public_text_request import ARM_PTX, ARM_V1
from retrieval_public_text_study_queries import StudyBatch, StudyEntry, StudyQuery
from retrieval_public_text_study_schema import StudyEmbeddingProfile, StudyNames
from retrieval_semantic_arm_validation import AdmittedArmObservation, FileIdentityTuple
from retrieval_semantic_experiment_scope import selected_public_binding_ids
from retrieval_semantic_generator_kernel_contracts import MODEL_REVISION
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_supplement_baselines import prepare_baseline_task, prepare_fts_request
from common_lore_index_test_support.w5_experiment_support import merge_candidate_rows


@dataclass(frozen=True, slots=True)
class BodySlotExecutionServices:
    """Authenticated per-arm query owners; unsupported masks never call them."""

    arm_queries: Mapping[str, Callable[[Mapping[str, Any], Any], Mapping[str, Any]]]
    preparation_terminal: Callable[[Mapping[str, Any], Any], Mapping[str, Any]]


def make_slot_executor(services: BodySlotExecutionServices) -> Callable[[Mapping[str, Any], Any], Mapping[str, Any]]:
    require(isinstance(services.arm_queries, Mapping), "body.slot_services:arm_mapping")
    require(callable(services.preparation_terminal), "body.slot_services:terminal")

    def execute(row: Mapping[str, Any], case: Any) -> Mapping[str, Any]:
        if row.get("preparation") != "ready":
            return services.preparation_terminal(row, case)
        arm = row.get("arm")
        require(type(arm) is str and arm in ARMS, "body.slot_services:arm")
        assert isinstance(arm, str)
        callback = cast(Callable[[Mapping[str, Any], Any], Mapping[str, Any]],
                        services.arm_queries.get(arm))
        require(callable(callback), "body.slot_services:missing_arm_owner")
        assert callable(callback)
        return callback(row, case)

    return execute


def _profiles(artifact_identity: str) -> dict[str, Any]:
    from common_lore_index.storage.semantic_candidates import EmbeddingProfile

    version = "v1:" + artifact_identity.removeprefix("sha256:")
    return {
        ARM_V1: EmbeddingProfile("BAAI/bge-small-en-v1.5", MODEL_REVISION,
                                 "cloe-bge-onnx-cpu", version, 384).validate(),
        ARM_PTX: StudyEmbeddingProfile("BAAI/bge-small-en-v1.5", MODEL_REVISION,
                                       "cloe-bge-onnx-cpu", version).validate(),
    }


def import_body_vector_batches(
    pg_handle: Any, *, attempt_id: str, artifact_identity: str, v1_artifact: Any,
    v2_seal: Any, public_text: Any, pg_pin: VerifiedPublicationPin, candidate_pub: Any,
) -> tuple[Any, Mapping[str, Any], StudyNames]:
    """Import only response-derived v1/v2 batches into owned study relations."""
    from common_lore_index_test_support.w5_public_text_postgres import (
        cleanup_study, create_study_relations, import_batch, v2_entries,
    )

    names = StudyNames(digest_value({
        "schema": "cloe-body-study-vector-relations-v1", "attempt_id": attempt_id,
        "publication": pg_pin.to_dict(), "artifact": artifact_identity,
    }))
    catalog = create_study_relations(pg_handle, names)
    profiles = _profiles(artifact_identity)
    snapshots = pg_pin.snapshot_by_binding()
    try:
        if getattr(v1_artifact, "complete_generation", False):
            entries = tuple(
                StudyEntry(entry.canonical_key, entry.source_binding_id,
                           snapshots[entry.source_binding_id][1], entry.input_digest,
                           None, False, entry.vector)
                for entry in v1_artifact.require_importable()
            )
            import_batch(pg_handle, catalog, StudyBatch(
                names.batch_id(pg_pin.generation, ARM_V1), pg_pin.generation, ARM_V1,
                profiles[ARM_V1], v1_artifact.input_identity,
                v1_artifact.component_receipt_identity, "complete", entries,
            ), candidate_pub, pg_pin)
        import_batch(pg_handle, catalog, StudyBatch(
            names.batch_id(pg_pin.generation, ARM_PTX), pg_pin.generation, ARM_PTX,
            profiles[ARM_PTX], public_text.publication_identity, digest_value(v2_seal.to_dict()),
            v2_seal.capability, v2_entries(v2_seal, public_text, pg_pin),
        ), candidate_pub, pg_pin)
    except BaseException:
        cleanup_study(pg_handle, names, catalog)
        raise
    return catalog, profiles, names


def _result(state: ExecutionState, envelope: Any, *, hits: Sequence[Any] = (), **extra: Any) -> dict[str, Any]:
    return {"execution_state": state.value, "envelope": envelope,
            "snapshot_hits": tuple(hits), **extra}


def _snapshot_hits(envelope: Any, pin: VerifiedPublicationPin) -> tuple[FileIdentityTuple, ...]:
    snapshots = pin.snapshot_by_binding()
    result: list[FileIdentityTuple] = []
    for hit in getattr(envelope, "hits", ()):
        locator = hit.locator
        binding = getattr(locator, "source_binding_id", None) or getattr(locator, "binding_id", None)
        require(type(binding) is str and bool(binding), "body.slots:baseline_locator_binding")
        assert isinstance(binding, str)
        snapshot = snapshots.get(binding)
        require(snapshot is not None, "body.slots:baseline_binding")
        assert snapshot is not None
        relative = getattr(locator, "source_relative_path", None)
        require(type(relative) is str and bool(relative), "body.slots:baseline_locator_path")
        assert isinstance(relative, str)
        result.append(FileIdentityTuple(binding, snapshot[1], relative))
    return tuple(result)


def _scope(task: Any, pin: VerifiedPublicationPin) -> QueryScope:
    return QueryScope(graph_id=task.graph_id, backend="postgresql",
                      publication_bundle_id=pin.publication_bundle_id,
                      snapshot_manifest_id=pin.snapshot_manifest_id,
                      selected_binding_ids=tuple(task.selected_bindings),
                      source_path_prefix=task.source_path_prefix)


def _vector_terminal(task: Any, pin: VerifiedPublicationPin, state: ExecutionState, reason: str) -> Any:
    scope = _scope(task, pin)
    requested = QueryMode(task.requested_mode)
    if state is ExecutionState.REFUSED:
        return refused_envelope(requested_mode=requested, scope=scope, refusal_code=reason)
    if state is ExecutionState.UNAVAILABLE:
        return unavailable_envelope(requested_mode=requested, scope=scope, reason=reason)
    return QueryEnvelope(
        QUERY_CONTRACT_VERSION, requested, requested, MatchSurface.NONE, scope,
        DEFAULT_ORDERING_POLICY_VERSION, CapabilityState.FAILED,
        provenance={"publication_identity": pin.receipt_identity, "failure_reason": reason},
    ).validate()


def _baseline(row: Mapping[str, Any], task: Any, *, stores: Any, policy: Any) -> Mapping[str, Any]:
    native = row["arm"] == NATIVE_ARM
    pin = stores.sqlite_store.pin if native else stores.postgres_store.pin
    backend = "sqlite" if native else "postgresql"
    scope = QueryScope(graph_id=task.graph_id, backend=backend,
                       publication_bundle_id=pin.publication_bundle_id,
                       snapshot_manifest_id=pin.snapshot_manifest_id,
                       selected_binding_ids=tuple(task.selected_bindings),
                       source_path_prefix=task.source_path_prefix)
    requested = QueryMode(task.requested_mode)

    def run(_decision: Any) -> tuple[ExecutionState, Any]:
        if native:
            views = prepare_baseline_task(task, policy=policy)
            state, envelope, _elapsed, _accounting = execute_native_baseline_task(
                views.alias_task, stores.native_view)
            return state, envelope
        if row["arm"] == GRAPH_ARM:
            if task.comparability_stratum == "body" or task.intended_match_surface == "captured_text":
                return ExecutionState.UNAVAILABLE, unavailable_envelope(
                    requested_mode=requested, scope=scope,
                    reason="body_graph_captured_text_unavailable")
            views = prepare_baseline_task(task, policy=policy)
            state, envelope, _elapsed, _accounting = execute_graph_baseline_task(
                views.alias_task, stores.postgres_store.published.home_root,
                psql_command=stores.postgres_store.psql_command)
            return state, envelope
        request = prepare_fts_request(task, policy=policy)
        envelope = query_graph_lexical_generation(
            stores.ops_config, task.graph_id, request, authorize_body=True,
            publication_bundle_id=pin.publication_bundle_id,
            snapshot_manifest_id=pin.snapshot_manifest_id)
        states = {
            CapabilityState.AVAILABLE: ExecutionState.COMPLETED,
            CapabilityState.PARTIAL: ExecutionState.COMPLETED,
            CapabilityState.REFUSED: ExecutionState.REFUSED,
            CapabilityState.UNAVAILABLE: ExecutionState.UNAVAILABLE,
            CapabilityState.STALE: ExecutionState.UNAVAILABLE,
            CapabilityState.FAILED: ExecutionState.FAILED,
            CapabilityState.NOT_RUN: ExecutionState.NOT_RUN,
        }
        return states[envelope.capability_state], envelope

    decision, outcome = execute_after_admission(
        task, policy=policy, publication_pin=pin, observed_backend=backend, execute=run)
    if outcome is None:
        if decision.state is AdmissionState.REFUSED:
            state = ExecutionState.REFUSED
            envelope = refused_envelope(
                requested_mode=requested, scope=scope,
                refusal_code=decision.refusal_code or "binding_privacy_not_authorized")
        else:
            state = ExecutionState.UNAVAILABLE
            envelope = unavailable_envelope(
                requested_mode=requested, scope=scope,
                reason=decision.reason_category or "body_admission_unavailable")
    else:
        state, envelope = outcome
    return _result(state, envelope, hits=_snapshot_hits(envelope, pin))


def _vector_envelope(task: Any, pin: VerifiedPublicationPin, fused: Any) -> Any:
    scope = _scope(task, pin)
    if fused.execution_state is not ExecutionState.COMPLETED:
        return _vector_terminal(task, pin, fused.execution_state,
                                fused.refusal_code or fused.unavailable_reason or fused.failure_reason or "body_vector_failed")
    hits = tuple(QueryHit(
        EvaluationLocator(hit.file_identity.binding_id, hit.file_identity.source_relative_path),
        MatchSurface.CAPTURED_TEXT, hit.fused_rank, hit.score_float,
        representation_version="cloe-w5-rrf-file-v1", artifact_privacy="public-dev",
    ).validate() for hit in fused.hits)
    return QueryEnvelope(
        QUERY_CONTRACT_VERSION, QueryMode(task.requested_mode), QueryMode(task.requested_mode),
        MatchSurface.CAPTURED_TEXT, scope, DEFAULT_ORDERING_POLICY_VERSION,
        CapabilityState.PARTIAL if fused.partial_coverage else CapabilityState.AVAILABLE,
        hits=hits, scope_complete=not fused.partial_coverage,
        provenance={"publication_identity": pin.receipt_identity,
                    "snapshot_hits": [hit.file_identity.to_dict() for hit in fused.hits]},
    ).validate()


def _vector(row: Mapping[str, Any], task: Any, *, pg_handle: Any, study_catalog: Any,
            profiles: Mapping[str, Any], candidate_pub: Any, pg_pin: VerifiedPublicationPin,
            v1_input: Any, v1_artifact: Any, public_text: Any, v2_seal: Any,
            scope: PrivateMarkerScope, policy: Any) -> tuple[Mapping[str, Any], Any | None]:
    captured = captured_v2_slot_matches(row, task, pg_pin)
    inapplicable = None if captured else public_text_applicability(task)
    if inapplicable is not None:
        env = _vector_terminal(task, pg_pin, ExecutionState.UNAVAILABLE, inapplicable)
        return _result(ExecutionState.UNAVAILABLE, env, unavailable_reason=inapplicable), None
    bindings = selected_public_binding_ids(task)
    if captured:
        for binding in bindings:
            if (denied := supplemental_body_reason(policy, binding)) is not None:
                env = _vector_terminal(task, pg_pin, ExecutionState.REFUSED, denied)
                return _result(ExecutionState.REFUSED, env, refusal_code=denied), None
    is_v1 = row["arm"] == V1_ARM
    rep_key = ARM_V1 if is_v1 else ARM_PTX
    vector = None
    if is_v1:
        if not getattr(v1_artifact, "complete_generation", False):
            env = _vector_terminal(task, pg_pin, ExecutionState.REFUSED, "v1_own_record_truncated")
            return _result(ExecutionState.REFUSED, env, refusal_code="v1_own_record_truncated"), None
        vector = next((item.vector for item in v1_artifact.queries if item.task_id == task.task_id), None)
    else:
        vector = next((item.vector for item in v2_seal.records
                       if item.record_id == "query:" + task.task_id), None)
    if vector is None:
        env = _vector_terminal(task, pg_pin, ExecutionState.UNAVAILABLE, "body_query_vector_missing")
        return _result(ExecutionState.UNAVAILABLE, env, unavailable_reason="body_query_vector_missing"), None
    rows_by_binding: dict[str, tuple[dict[str, Any], ...]] = {}
    from common_lore_index_test_support.w5_public_text_postgres import exact_read
    for binding in bindings:
        read = exact_read(pg_handle, StudyQuery(
            study_catalog, study_catalog.names.batch_id(pg_pin.generation, rep_key), rep_key,
            profiles[rep_key], candidate_pub, binding, vector), pg_pin)
        if read.state != "completed":
            state = ExecutionState.REFUSED if read.state == "refused" else ExecutionState.UNAVAILABLE
            name = "refusal_code" if state is ExecutionState.REFUSED else "unavailable_reason"
            reason = read.reason or "body_exact_read_unavailable"
            return _result(state, _vector_terminal(task, pg_pin, state, reason), **{name: reason}), None
        rows_by_binding[binding] = read.rows
    merged = merge_candidate_rows(rows_by_binding, limit=20)
    observation = (
        v1_vector_arm("exact_vector", merged, task=task, pin=pg_pin, inputs=v1_input)
        if is_v1 else public_text_vector_arm(
            "exact_vector", project_public_text_candidates(
                merged, preparation=public_text, seal=v2_seal, pin=pg_pin, task=task, scope=scope),
            seal=v2_seal, pin=pg_pin, task=task))
    fused = fuse_study_arms((observation,), pin=pg_pin, private_markers=scope.markers)
    return _result(fused.execution_state, _vector_envelope(task, pg_pin, fused), hits=fused.hits), (
        None if is_v1 else observation)


def _admission_terminal(task: Any, *, pin: VerifiedPublicationPin, policy: Any) -> Mapping[str, Any] | None:
    scope = QueryScope(graph_id=task.graph_id, backend="postgresql",
                       publication_bundle_id=pin.publication_bundle_id,
                       snapshot_manifest_id=pin.snapshot_manifest_id,
                       selected_binding_ids=tuple(task.selected_bindings),
                       source_path_prefix=task.source_path_prefix)
    requested = QueryMode(task.requested_mode)
    decision, allowed = execute_after_admission(
        task, policy=policy, publication_pin=pin, observed_backend="postgresql",
        execute=lambda _decision: True)
    if allowed:
        return None
    if decision.state is AdmissionState.REFUSED:
        envelope = refused_envelope(
            requested_mode=requested, scope=scope,
            refusal_code=decision.refusal_code or "binding_privacy_not_authorized")
        return _result(ExecutionState.REFUSED, envelope, refusal_code=envelope.refusal_code)
    envelope = unavailable_envelope(
        requested_mode=requested, scope=scope,
        reason=decision.reason_category or "body_admission_unavailable")
    return _result(ExecutionState.UNAVAILABLE, envelope, unavailable_reason=envelope.unavailable_reason)


def make_actual_body_slot_executor(
    *, preparation: Any, stores: Any, pg_handle: Any, policy: Any, scope: PrivateMarkerScope,
    v1_input: Any, v1_artifact: Any, public_text: Any, v2_seal: Any,
    study_catalog: Any, profiles: Mapping[str, Any], candidate_pub: Any,
) -> Callable[[Mapping[str, Any], Any], Mapping[str, Any]]:
    """Bind real baseline/exact-vector/fusion primitives to one slot executor."""
    lex_cache: dict[tuple[str, int], tuple[ExecutionState, Any]] = {}
    v2_cache: dict[tuple[str, int], Any] = {}

    def terminal(row: Mapping[str, Any], _case: Any) -> Mapping[str, Any]:
        state = ExecutionState.REFUSED if row.get("preparation") == "refused" else ExecutionState.UNAVAILABLE
        scope_obj = QueryScope(
            graph_id=preparation.catalog.identity.graph_id, backend="postgresql",
            publication_bundle_id=stores.postgres_store.pin.publication_bundle_id,
            snapshot_manifest_id=stores.postgres_store.pin.snapshot_manifest_id,
        )
        if state is ExecutionState.REFUSED:
            env = refused_envelope(requested_mode=QueryMode.RANKED_DISCOVERY, scope=scope_obj,
                                   refusal_code=str(row.get("reason") or "body_preparation_refused"))
            return _result(state, env, refusal_code=env.refusal_code,
                           observation_origin="preparation_terminal")
        env = unavailable_envelope(requested_mode=QueryMode.RANKED_DISCOVERY, scope=scope_obj,
                                   reason=str(row.get("reason") or "body_preparation_unavailable"))
        return _result(state, env, unavailable_reason=env.unavailable_reason,
                       observation_origin="preparation_terminal")

    def execute(row: Mapping[str, Any], case: Any) -> Mapping[str, Any]:
        arm, key = row["arm"], (row["task_id"], int(row["repetition"]))
        if row.get("preparation") != "ready":
            return terminal(row, case)
        if arm in (NATIVE_ARM, GRAPH_ARM, FTS_ARM):
            result = _baseline(row, case.task, stores=stores, policy=policy)
            if arm == FTS_ARM:
                lex_cache[key] = (ExecutionState(result["execution_state"]), result["envelope"])
            return result
        denied = _admission_terminal(
            case.task, pin=stores.postgres_store.pin, policy=policy)
        if denied is not None:
            return denied
        if arm == FUSION_ARM:
            lexical, vector = lex_cache.get(key), v2_cache.get(key)
            if lexical is None or vector is None:
                env = _vector_terminal(case.task, stores.postgres_store.pin,
                                       ExecutionState.UNAVAILABLE, "body_fusion_prerequisite_missing")
                return _result(ExecutionState.UNAVAILABLE, env,
                               unavailable_reason="body_fusion_prerequisite_missing")
            fused = fuse_study_arms((AdmittedArmObservation(
                arm_id=LEXICAL_ARM, mode=case.task.requested_mode,
                verified_pin=stores.postgres_store.pin, execution_state=lexical[0],
                envelope=lexical[1], selected_binding_ids=tuple(case.task.selected_bindings)), vector),
                pin=stores.postgres_store.pin, private_markers=scope.markers)
            return _result(fused.execution_state, _vector_envelope(case.task, stores.postgres_store.pin, fused),
                           hits=fused.hits)
        result, observed = _vector(
            row, case.task, pg_handle=pg_handle, study_catalog=study_catalog,
            profiles=profiles, candidate_pub=candidate_pub, pg_pin=stores.postgres_store.pin,
            v1_input=v1_input, v1_artifact=v1_artifact, public_text=public_text,
            v2_seal=v2_seal, scope=scope, policy=policy)
        if arm == V2_ARM:
            v2_cache[key] = observed
        return result

    return execute


def execute_actual_body_slots(
    *, preparation: Any, common_freeze: Any, pins: Mapping[str, Any], operation: Any,
    execute: Callable[[Mapping[str, Any], Any], Mapping[str, Any]],
    private_markers: Sequence[str] = (),
) -> BodySlotBatch:
    """Keep native support imports independent from other study slot owners."""
    return execute_body_slots(
        preparation=preparation, common_freeze=common_freeze, pins=pins,
        operation=operation, execute=execute, private_markers=private_markers)


__all__ = [
    "BodySlotBatch", "BodySlotExecutionServices", "execute_actual_body_slots",
    "import_body_vector_batches", "make_actual_body_slot_executor", "make_slot_executor",
]
