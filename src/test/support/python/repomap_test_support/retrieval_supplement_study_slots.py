"""Per-slot baseline, exact-vector and hybrid-RRF execution for the supplement study.

Test support only. Preserves privacy-first W5 admission before every primitive,
never substitutes HNSW or metadata fallback for an unavailable exact-vector
arm, and binds every slot's observed operation record and result artifact into
``SlotResult`` for ``report_supplement``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import time
from typing import Any

from common_lore_index.ops.lexical_service import query_graph_lexical_generation
from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from common_lore_index.retrieval.query_contracts import (
    CapabilityState,
    QueryEnvelope,
    QueryMode,
    QueryScope,
    refused_envelope,
    unavailable_envelope,
)
from common_lore_index.storage.semantic_candidates import (
    CandidatePublication,
    EmbeddingProfile,
)
from repomap_test_support.retrieval_supplement_study_pg import PublishedStudyStores
from repomap_test_support.w5_experiment_support import merge_candidate_rows
from repomap_test_support.w5_public_text_learned import profiles
from repomap_test_support.w5_public_text_postgres import (
    create_study_relations,
    exact_read,
    import_batch,
    v2_entries,
)
from retrieval_evaluation_admission_w5 import AdmissionState, execute_after_admission
from retrieval_evaluation_grading import ExecutionState
from retrieval_evaluation_graph import execute_graph_baseline_task
from retrieval_evaluation_native import execute_native_baseline_task
from retrieval_evaluation_schema import EvaluationTask
from retrieval_public_text_admission import public_text_applicability
from retrieval_public_text_contracts import PrivateMarkerScope
from retrieval_public_text_descriptors import PublicTextPreparation
from retrieval_public_text_fusion import (
    LEXICAL_ARM,
    fuse_study_arms,
    project_public_text_candidates,
    public_text_vector_arm,
    v1_vector_arm,
)
from retrieval_public_text_request import ARM_PTX, ARM_V1
from retrieval_public_text_response import ArmSeal
from retrieval_public_text_study_catalog import StudyCatalog
from retrieval_public_text_study_queries import StudyBatch, StudyEntry, StudyQuery
from retrieval_public_text_study_schema import StudyEmbeddingProfile, StudyNames
from retrieval_semantic_arm_validation import AdmittedArmObservation
from retrieval_semantic_experiment_scope import selected_public_binding_ids
from retrieval_semantic_fusion import FileFusionResult
from retrieval_semantic_generator_inputs import PublicGenerationInput
from retrieval_semantic_generator_output import GeneratedEnrichmentArtifact
from retrieval_semantic_observed_operation import finalize_observed_operation
from retrieval_semantic_observed_operation_capture import make_unadmitted_operation
from retrieval_semantic_observed_operation_fusion import fusion_work
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_supplement_baselines import prepare_baseline_task, prepare_fts_request
from retrieval_supplement_capture import bind_operation_capture, capture_baseline_envelope
from retrieval_supplement_declaration import (
    InertDeclaration,
    SlotIdentity,
    capture_arm,
)
from retrieval_supplement_report import SlotResult, result_artifact_bytes
from retrieval_supplement_study_contracts import refuse
from retrieval_supplement_values import (
    ARMS,
    GRAPH_BODY_REASON,
    canonical_bytes,
    digest_json,
)


def _envelope_state(envelope: QueryEnvelope) -> ExecutionState:
    mapping = {
        CapabilityState.AVAILABLE: ExecutionState.COMPLETED,
        CapabilityState.PARTIAL: ExecutionState.COMPLETED,
        CapabilityState.REFUSED: ExecutionState.REFUSED,
        CapabilityState.UNAVAILABLE: ExecutionState.UNAVAILABLE,
        CapabilityState.STALE: ExecutionState.UNAVAILABLE,
        CapabilityState.FAILED: ExecutionState.FAILED,
        CapabilityState.NOT_RUN: ExecutionState.NOT_RUN,
    }
    return mapping[envelope.capability_state]


def execute_baseline_slot(
    task: EvaluationTask,
    slot: SlotIdentity,
    *,
    declaration: InertDeclaration,
    stores: PublishedStudyStores,
    policy: BodyProjectionPolicy,
    delivery_kind: str,
) -> tuple[SlotResult, tuple[ExecutionState, QueryEnvelope]]:
    """Run one baseline slot (arms 0..2) behind privacy-first ``execute_after_admission``."""
    is_native = slot.arm == ARMS[0]
    pin = stores.sqlite_store.pin if is_native else stores.postgres_store.pin
    backend = "native_filesystem" if is_native else "postgresql"
    scope = QueryScope(
        graph_id=task.graph_id, backend=backend,
        selected_binding_ids=task.selected_bindings, source_path_prefix=task.source_path_prefix,
    )
    requested = QueryMode(task.requested_mode)

    def _run(_dec: Any) -> tuple[ExecutionState, QueryEnvelope, tuple[str, ...] | None]:
        if is_native:
            views = prepare_baseline_task(task, policy=policy)
            state, env, _el, _ac = execute_native_baseline_task(views.alias_task, stores.native_view)
            surfaces = tuple(
                stores.native_view.path_to_surface[p].value
                for p in stores.native_view.selected_relative_paths
                if stores.native_view.path_to_binding[p][0] in set(task.selected_bindings)
            ) or ("captured_text",)
            return state, env, surfaces
        if slot.arm == ARMS[1]:
            if (
                task.comparability_stratum == "body"
                or task.intended_match_surface == "captured_text"
                or task.requested_mode == "literal_captured_text"
            ):
                return (
                    ExecutionState.UNAVAILABLE,
                    unavailable_envelope(requested_mode=requested, scope=scope, reason=GRAPH_BODY_REASON),
                    None,
                )
            views = prepare_baseline_task(task, policy=policy)
            state, env, _el, _ac = execute_graph_baseline_task(
                views.alias_task, stores.postgres_store.published.home_root,
                psql_command=stores.postgres_store.psql_command,
            )
            return state, env, None
        req = prepare_fts_request(task, policy=policy)
        env = query_graph_lexical_generation(
            stores.ops_config, task.graph_id, req, authorize_body=True,
            publication_bundle_id=pin.publication_bundle_id,
            snapshot_manifest_id=pin.snapshot_manifest_id,
        )
        return _envelope_state(env), env, None

    decision, outcome = execute_after_admission(
        task, policy=policy, publication_pin=pin, observed_backend=backend, execute=_run,
    )
    if outcome is None:
        if decision.state is AdmissionState.REFUSED:
            state = ExecutionState.REFUSED
            env = refused_envelope(
                requested_mode=requested, scope=scope,
                refusal_code=decision.refusal_code or "binding_privacy_not_authorized",
            )
        else:
            state = ExecutionState.UNAVAILABLE
            env = unavailable_envelope(
                requested_mode=requested, scope=scope,
                reason=decision.reason_category or "w5_admission_unavailable",
            )
        surfaces = None
    else:
        state, env, surfaces = outcome
    raw = capture_baseline_envelope(
        task, slot=slot, envelope=env, execution_state=state, publication_pin=pin,
    )
    draft = SlotResult(
        slot, pin, raw, b"", baseline_envelope=env,
        delivery_kind=delivery_kind, native_path_surfaces=surfaces,
    )
    ref = bind_operation_capture(
        raw, slot=slot, declaration=declaration, result_artifact_raw=result_artifact_bytes(draft),
    )
    return replace(draft, capture_reference_raw=ref.raw), (state, env)


def import_study_vector_batches(
    pg_handle: Any,
    names: StudyNames,
    *,
    v1_artifact: GeneratedEnrichmentArtifact,
    v2_seal: ArmSeal,
    public_text: PublicTextPreparation,
    pg_pin: VerifiedPublicationPin,
    candidate_pub: CandidatePublication,
    artifact_profile_identity: str,
) -> tuple[StudyCatalog, dict[str, EmbeddingProfile | StudyEmbeddingProfile]]:
    """Create disposable study vector tables (no HNSW index) and import v1 & v2 batches."""
    study_catalog = create_study_relations(pg_handle, names)
    arm_profiles = profiles(artifact_profile_identity)
    snapshots = pg_pin.snapshot_by_binding()
    if v1_artifact.complete_generation:
        v1_study_entries = tuple(
            StudyEntry(
                entry.canonical_key, entry.source_binding_id,
                snapshots[entry.source_binding_id][1], entry.input_digest,
                None, False, entry.vector,
            )
            for entry in v1_artifact.require_importable()
        )
        v1_batch = StudyBatch(
            names.batch_id(pg_pin.generation, ARM_V1), pg_pin.generation, ARM_V1,
            arm_profiles[ARM_V1], v1_artifact.input_identity,
            v1_artifact.component_receipt_identity, "complete", v1_study_entries,
        )
        import_batch(pg_handle, study_catalog, v1_batch, candidate_pub, pg_pin)
    v2_study_entries = v2_entries(v2_seal, public_text, pg_pin)
    v2_batch = StudyBatch(
        names.batch_id(pg_pin.generation, ARM_PTX), pg_pin.generation, ARM_PTX,
        arm_profiles[ARM_PTX], public_text.publication_identity,
        digest_json(v2_seal.to_dict()), v2_seal.capability, v2_study_entries,
    )
    import_batch(pg_handle, study_catalog, v2_batch, candidate_pub, pg_pin)
    return study_catalog, arm_profiles


def execute_vector_or_fusion_slot(
    task: EvaluationTask,
    slot: SlotIdentity,
    *,
    declaration: InertDeclaration,
    pg_handle: Any,
    study_catalog: StudyCatalog,
    arm_profiles: Mapping[str, EmbeddingProfile | StudyEmbeddingProfile],
    candidate_pub: CandidatePublication,
    pg_pin: VerifiedPublicationPin,
    policy: BodyProjectionPolicy,
    v1_input: PublicGenerationInput,
    v1_artifact: GeneratedEnrichmentArtifact,
    public_text: PublicTextPreparation,
    v2_seal: ArmSeal,
    scope: PrivateMarkerScope,
    lexical_outcome: tuple[ExecutionState, QueryEnvelope] | None,
    cached_v2_obs: AdmittedArmObservation | None,
    delivery_kind: str,
) -> tuple[SlotResult, AdmittedArmObservation | None]:
    """Execute one exact-vector (v1/v2) or hybrid-RRF slot; never substitute HNSW or metadata."""
    bare = capture_arm(slot.arm)

    def _unadmitted(state: ExecutionState, reason: str) -> SlotResult:
        raw = canonical_bytes(make_unadmitted_operation(task, bare, slot.repetition, state, reason))
        ref = bind_operation_capture(raw, slot=slot, declaration=declaration)
        return SlotResult(slot, pg_pin, raw, ref.raw, delivery_kind=delivery_kind)

    decision, allowed = execute_after_admission(
        task, policy=policy, publication_pin=pg_pin, observed_backend="postgresql",
        execute=lambda _d: True,
    )
    if not allowed:
        code = (
            decision.refusal_code or "binding_privacy_not_authorized"
            if decision.state is AdmissionState.REFUSED
            else decision.reason_category or "w5_admission_unavailable"
        )
        st = ExecutionState.REFUSED if decision.state is AdmissionState.REFUSED else ExecutionState.UNAVAILABLE
        return _unadmitted(st, code), None

    inapplicable = public_text_applicability(task)
    if inapplicable is not None:
        return _unadmitted(ExecutionState.UNAVAILABLE, inapplicable), None

    selected = selected_public_binding_ids(task)
    is_v1 = slot.arm == ARMS[3]
    rep_key = ARM_V1 if is_v1 else ARM_PTX
    profile_sha = arm_profiles[rep_key].identity
    batch_id = study_catalog.names.batch_id(pg_pin.generation, rep_key)

    if slot.arm in (ARMS[3], ARMS[4]):
        if is_v1 and not v1_artifact.complete_generation:
            return _unadmitted(ExecutionState.REFUSED, "v1_own_record_truncated"), None
        q_vec = (
            next((q.vector for q in v1_artifact.queries if q.task_id == task.task_id), None)
            if is_v1 else
            next((r.vector for r in v2_seal.records if r.record_id == f"ptx.q.{task.task_id}"), None)
        )
        if q_vec is None:
            return _unadmitted(
                ExecutionState.UNAVAILABLE, "study_pg:missing_admitted_query_vector"
            ), None
        rows_by_binding: dict[str, tuple[dict[str, Any], ...]] = {}
        per_binding: list[dict[str, object]] = []
        for binding in selected:
            t0 = time.perf_counter_ns()
            query = StudyQuery(
                study_catalog, batch_id, rep_key, arm_profiles[rep_key],
                candidate_pub, binding, q_vec,
            )
            read = exact_read(pg_handle, query, pg_pin)
            elapsed_ns = max(0, time.perf_counter_ns() - t0)
            if read.state != "completed":
                return _unadmitted(ExecutionState(read.state), read.reason or "exact_sql_failed"), None
            rows_by_binding[binding] = read.rows
            per_binding.append({
                "source_binding_id": binding, "state": "completed", "reason": None,
                "elapsed_ns": elapsed_ns, "candidate_count": len(read.rows),
                "query_descriptor_sha256": read.query_descriptor_sha256,
            })
        merged = merge_candidate_rows(rows_by_binding, limit=20)
        obs = (
            v1_vector_arm("exact_vector", merged, task=task, pin=pg_pin, inputs=v1_input)
            if is_v1 else
            public_text_vector_arm(
                "exact_vector",
                project_public_text_candidates(
                    merged, preparation=public_text, seal=v2_seal,
                    pin=pg_pin, task=task, scope=scope,
                ),
                seal=v2_seal, pin=pg_pin, task=task,
            )
        )
        fused = fuse_study_arms((obs,), pin=pg_pin, private_markers=scope.markers)
        op_rec = _vector_operation_record(
            task, slot, kind="exact_vector", operation="exact_vector_sql",
            pg_pin=pg_pin, profile_sha=profile_sha, rebuild_id=batch_id,
            selected=selected, per_binding=per_binding, fused=fused,
        )
        raw = canonical_bytes(op_rec)
        draft = SlotResult(slot, pg_pin, raw, b"", fusion_result=fused, delivery_kind=delivery_kind)
        ref = bind_operation_capture(
            raw, slot=slot, declaration=declaration, result_artifact_raw=result_artifact_bytes(draft),
        )
        return replace(draft, capture_reference_raw=ref.raw), (None if is_v1 else obs)

    refuse(lexical_outcome is not None, "study_pg:hybrid_lexical_prerequisite_missing")
    if cached_v2_obs is None:
        return _unadmitted(
            ExecutionState.UNAVAILABLE, "study_pg:hybrid_prerequisites_missing"
        ), None
    assert lexical_outcome is not None
    lex_obs = AdmittedArmObservation(
        arm_id=LEXICAL_ARM, mode=task.requested_mode, verified_pin=pg_pin,
        execution_state=lexical_outcome[0], envelope=lexical_outcome[1],
        selected_binding_ids=selected,
    )
    fused = fuse_study_arms((lex_obs, cached_v2_obs), pin=pg_pin, private_markers=scope.markers)
    op_rec = _vector_operation_record(
        task, slot, kind="hybrid_rrf", operation="rrf_file_fusion",
        pg_pin=pg_pin, profile_sha=profile_sha, rebuild_id=batch_id,
        selected=selected, per_binding=[], fused=fused,
    )
    raw = canonical_bytes(op_rec)
    draft = SlotResult(slot, pg_pin, raw, b"", fusion_result=fused, delivery_kind=delivery_kind)
    ref = bind_operation_capture(
        raw, slot=slot, declaration=declaration, result_artifact_raw=result_artifact_bytes(draft),
    )
    return replace(draft, capture_reference_raw=ref.raw), None


def _vector_operation_record(
    task: EvaluationTask,
    slot: SlotIdentity,
    *,
    kind: str,
    operation: str,
    pg_pin: VerifiedPublicationPin,
    profile_sha: str,
    rebuild_id: str,
    selected: Sequence[str],
    per_binding: list[dict[str, object]],
    fused: FileFusionResult,
) -> dict[str, object]:
    reason = fused.refusal_code or fused.unavailable_reason or fused.failure_reason
    return finalize_observed_operation({
        "schema": "cloe-w5-observed-operation-v1",
        "identity": {
            "generation": slot.generation, "task_id": slot.task_id,
            "arm_id": kind, "repetition": slot.repetition,
        },
        "kind": kind, "state": fused.execution_state.value, "reason": reason,
        "declared_mode": None,
        "actual": {
            "operation": operation, "backend": pg_pin.backend,
            "publication_identity": pg_pin.receipt_identity,
            "profile_identity": profile_sha, "rebuild_id": rebuild_id,
            "input_descriptor_sha256": (digest_json({"queries": [
                [row["source_binding_id"], row["query_descriptor_sha256"]] for row in per_binding
            ]}) if per_binding else None), "selected_binding_ids": list(selected),
            "snapshot_manifest_id": pg_pin.snapshot_manifest_id,
            "effective_mode": None, "match_surface": None, "fallback": None,
        },
        "work": {
            "per_binding": per_binding, "hnsw": [],
            "fusion": fusion_work(fused), "reference": None,
        },
    })


__all__ = [
    "execute_baseline_slot", "execute_vector_or_fusion_slot", "import_study_vector_batches",
]
