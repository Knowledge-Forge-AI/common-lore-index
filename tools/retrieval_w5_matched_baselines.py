"""Execute the already-admitted matched plans through the maintained baselines."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from common_lore_index.retrieval.query_contracts import CapabilityState, MatchSurface, QueryEnvelope, QueryMode
from retrieval_evaluation_grading import ExecutionState
from retrieval_evaluation_native import execute_native_baseline_task_with_breakdown
from retrieval_evaluation_views import MaterializedNativeView
from retrieval_public_text_fusion import ARM_PTX, LEXICAL_ARM, fuse_study_arms, study_arm_id
from retrieval_semantic_arm_validation import (
    _extract_arm_raw_hits, _verify_envelope_against_pin, _verify_observed_accepted_publication,
)
from retrieval_semantic_fusion import AdmittedArmObservation, FileIdentityTuple
from retrieval_semantic_primitives import _validate_representation_string
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_w5_matched_attempt import RankCoverage, RankedObservation, SlotPlan
from retrieval_w5_matched_design import Arm, Slot
from retrieval_w5_matched_inputs import require_match
from retrieval_w5_matched_postgres import MatchedPostgres
from retrieval_w5_matched_publications import MatchedPublications, ObservedPublication, read_native_capture, refusal_for


@dataclass(frozen=True, slots=True)
class _NativeRequest:
    graph_id: str
    task_id: str
    question: str = field(repr=False)
    selected_bindings: tuple[str, ...]
    requested_mode: str
    permitted_query_plan: Mapping[str, Any] = field(repr=False)
    source_path_prefix: str | None = None
    intended_match_surface: str = "captured_text"


def _check_plan(plan: SlotPlan, publications: MatchedPublications, arm: Arm) -> None:
    task = publications.inputs.tasks[plan.slot.ordinal - 1]
    require_match(
        plan.slot.arm is arm and plan.task is task and plan.refusal_code is None and refusal_for(task, arm) is None
        and tuple(publications.binding_ids[alias] for alias in task.bindings) == plan.selected_binding_ids,
        "baseline_plan",
    )


def _ranked(
    plan: SlotPlan, pin: VerifiedPublicationPin, envelope: QueryEnvelope, observed: ObservedPublication,
) -> RankedObservation | None:
    envelope.validate()
    backend = "native_filesystem" if pin.backend == "sqlite" else "postgresql"
    assert plan.task is not None
    require_match(envelope.scope.graph_id == pin.graph_id and envelope.scope.backend == backend
                  and envelope.scope.source_path_prefix is None
                  and frozenset(envelope.scope.selected_binding_ids) == frozenset(plan.selected_binding_ids)
                  and envelope.requested_mode == QueryMode(plan.task.mode), "baseline_envelope_scope")
    require_match(envelope.capability_state not in (CapabilityState.REFUSED, CapabilityState.STALE),
                  "baseline_refused_or_stale")
    if envelope.capability_state not in (CapabilityState.AVAILABLE, CapabilityState.PARTIAL):
        return None
    require_match(all(row.reason_code == "result_page_bounded" for row in envelope.omissions),
                  "baseline_unplanned_omission")
    expected_mode = QueryMode.LITERAL_CAPTURED_TEXT if backend == "native_filesystem" else envelope.requested_mode
    require_match(envelope.effective_mode is expected_mode and envelope.match_surface is MatchSurface.CAPTURED_TEXT,
                  "baseline_envelope_treatment")
    snapshots = pin.snapshot_by_binding()
    require_match(all(hit.locator.binding_id in snapshots for hit in envelope.hits), "baseline_hit_scope")
    hits = tuple(FileIdentityTuple(
        hit.locator.binding_id, snapshots[hit.locator.binding_id][1], hit.locator.source_relative_path,
    ) for hit in envelope.hits)
    coverage = RankCoverage.COMPLETE if envelope.scope_complete else RankCoverage.BOUNDED_PAGE
    return RankedObservation(observed, hits, coverage, envelope.effective_mode, envelope.fallback is not None)


def execute_matched_native(
    plan: SlotPlan, publications: MatchedPublications, view: MaterializedNativeView,
) -> RankedObservation | None:
    """Use Git's maintained fixed-string baseline over the bound materialization."""
    _check_plan(plan, publications, Arm.NATIVE)
    task = plan.task
    assert task is not None
    pin = publications.sqlite
    require_match(read_native_capture(view, pin, publications.native_policy) == publications.sqlite_capture, "native_view_drift")
    request = _NativeRequest(
        pin.graph_id, f"matched-{task.case.ordinal:02}", task.clue, task.bindings, task.mode,
        MappingProxyType({"query": task.clue, "patterns": task.native_patterns, "fixed_strings": True,
                          "ignore_case": task.mode == QueryMode.RANKED_DISCOVERY.value, "limit": 10}),
    )
    try:
        state, envelope, _, _, _ = execute_native_baseline_task_with_breakdown(request, view)
    finally:
        require_match(read_native_capture(view, pin, publications.native_policy) == publications.sqlite_capture,
                      "native_view_drift")
    require_match(state is not ExecutionState.REFUSED, "native_refused")
    if state is not ExecutionState.COMPLETED:
        return None
    # Native files carry their snapshot witnesses in the bound materialization,
    # so there is no PostgreSQL envelope or caller fallback on this arm.
    observed = ObservedPublication(pin.publication_bundle_id, pin.snapshot_manifest_id, pin.run_id)
    return _ranked(plan, pin, envelope, observed)


def execute_matched_lexical(
    plan: SlotPlan, publications: MatchedPublications, postgres: MatchedPostgres,
) -> RankedObservation | None:
    """Use the maintained lexical service with caller identity defaults unset."""
    _check_plan(plan, publications, Arm.LEXICAL)
    assert plan.task is not None and plan.task.lexical is not None
    request = plan.lexical_request
    require_match(request is not None, "lexical_request")
    assert request is not None
    require_match(request == replace(plan.task.lexical, selected_binding_ids=plan.selected_binding_ids), "lexical_treatment")
    publications.require_postgres_reader(postgres)
    envelope = postgres.query(request)
    return _lexical_rank(plan, publications, envelope)


def _observed_lexical(envelope: QueryEnvelope) -> ObservedPublication:
    return ObservedPublication(
        envelope.scope.publication_bundle_id, envelope.scope.snapshot_manifest_id,
        envelope.provenance.get("accepted_run_id"), envelope.provenance.get("lexical_generation_id"),
    )


def _lexical_rank(
    plan: SlotPlan, publications: MatchedPublications, envelope: QueryEnvelope,
) -> RankedObservation | None:
    observed = _observed_lexical(envelope)
    lexical_slot = Slot(plan.slot.ordinal, Arm.LEXICAL)
    publications.check_observed(lexical_slot, observed, completed=False)
    result = _ranked(plan, publications.postgres, envelope, observed)
    if result is not None:
        require_match(all(hit.file_key() in publications.inventory and hit.binding_id in plan.selected_binding_ids
                          for hit in result.hits), "lexical_hit_inventory")
    return result


VectorReader = Callable[[SlotPlan], tuple[AdmittedArmObservation, ObservedPublication] | None]


def execute_matched_fusion(
    plan: SlotPlan, publications: MatchedPublications, postgres: MatchedPostgres, *,
    read_exact_v2: VectorReader, private_markers: tuple[str, ...] = (),
) -> RankedObservation | None:
    """Use the incumbent RRF contract with its own direct-text lexical leg."""
    _check_plan(plan, publications, Arm.FUSION)
    task = plan.task
    assert task is not None
    require_match(plan.lexical_request is None and task.mode == QueryMode.RANKED_DISCOVERY.value, "fusion_treatment")
    pin = publications.postgres
    request = LexicalQueryRequest(
        query=task.clue, query_style="ranked_terms", selected_binding_ids=tuple(sorted(plan.selected_binding_ids)),
        limit=10, offset=0,
    )
    publications.require_postgres_reader(postgres)
    publications.revalidate(Arm.FUSION)
    envelope = postgres.query(request)
    lexical_arm = AdmittedArmObservation(LEXICAL_ARM, task.mode, verified_pin=pin, envelope=envelope,
                                         selected_binding_ids=plan.selected_binding_ids)
    error, _, _, _, truncated = _verify_envelope_against_pin(envelope, pin, allow_missing_identity=True)
    require_match(error is None and not truncated, "fusion_lexical_provenance")
    _, _, capped = _extract_arm_raw_hits(lexical_arm, pin, private_markers)
    require_match(not capped, "fusion_lexical_cap")
    for hit in envelope.hits:
        for value in (hit.locator.canonical_key, hit.locator.evidence_key, hit.locator.link_kind, hit.chunk_id):
            if value is not None:
                _validate_representation_string(value, "fusion_lexical_locator", private_markers=private_markers)
    lexical = _lexical_rank(plan, publications, envelope)
    if lexical is None:
        return None
    observed = lexical.publication
    publications.check_observed(Slot(plan.slot.ordinal, Arm.LEXICAL), observed)
    vector = read_exact_v2(plan)
    if vector is None:
        return None
    arm, vector_identity = vector
    require_match(
        type(arm) is AdmittedArmObservation and arm.arm_id == study_arm_id("exact_vector", ARM_PTX)
        and arm.verified_pin is pin and arm.mode == "vector" and arm.provenance_inputs is None
        and arm.selected_binding_ids is not None and frozenset(arm.selected_binding_ids) == frozenset(plan.selected_binding_ids),
        "fusion_vector_treatment",
    )
    require_match(type(vector_identity) is ObservedPublication
                  and vector_identity.lexical_generation_id in (None, observed.lexical_generation_id), "vector_identity")
    vector_proof = replace(vector_identity, lexical_generation_id=observed.lexical_generation_id)
    publications.check_observed(plan.slot, vector_proof, completed=False)
    _verify_vector(arm, plan, publications, private_markers)
    if arm.execution_state is not ExecutionState.COMPLETED:
        return None
    publications.check_observed(plan.slot, vector_proof)
    result = fuse_study_arms((lexical_arm, arm), pin=pin, private_markers=private_markers, max_results=10)
    require_match(result.execution_state is not ExecutionState.REFUSED and not result.fallback_disclosed
                  and not result.truncated_arms, "fusion_refused_or_truncated")
    if result.execution_state is not ExecutionState.COMPLETED or arm.incomplete:
        return None
    complete = envelope.scope_complete and not result.partial_coverage
    return RankedObservation(
        replace(observed, vector_generation_id=vector_identity.vector_generation_id),
        tuple(row.file_identity for row in result.hits),
        RankCoverage.COMPLETE if complete else RankCoverage.BOUNDED_PAGE, QueryMode.RANKED_DISCOVERY,
    )


def _verify_vector(
    arm: AdmittedArmObservation, plan: SlotPlan, publications: MatchedPublications,
    markers: tuple[str, ...],
) -> None:
    """Check supplied vector evidence before deciding whether a rank may run."""
    pin = publications.postgres
    require_match(_verify_observed_accepted_publication(arm.observed_accepted_publication, pin) is None,
                  "vector_accepted_drift")
    envelope_error, envelope_state, _, _, envelope_truncated = _verify_envelope_against_pin(arm.envelope, pin)
    require_match(envelope_error is None and envelope_state in (None, arm.execution_state),
                  "vector_envelope_drift")
    require_match(arm.execution_state is not ExecutionState.REFUSED and not arm.truncated and not envelope_truncated,
                  "vector_refused_or_truncated")
    candidates, _, capped = _extract_arm_raw_hits(arm, pin, markers)
    require_match(not capped and all(hit.file_key() in publications.inventory and hit.binding_id in plan.selected_binding_ids
                                     for hit, _, _ in candidates), "vector_hit_inventory_or_cap")
