"""Pure synthetic report delivery; no database, query execution or model worker."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib

from common_lore_index.retrieval.query_contracts import (
    CapabilityState, DEFAULT_ORDERING_POLICY_VERSION, FallbackDisclosure, MatchSurface,
    QUERY_CONTRACT_VERSION, QueryEnvelope, QueryHit, QueryMode, QueryScope,
    refused_envelope, unavailable_envelope,
)
from common_lore_index_test_support.retrieval_supplement_fixtures import (
    make_test_bindings, make_test_catalog_payload, make_test_identity, make_test_policy,
)
from common_lore_index_test_support.w5_semantic_support import (
    InMemoryStoreReadbackCell, make_w5_readback_adapter, make_w5_receipt,
)
from retrieval_evaluation_grading import ExecutionState
from retrieval_evaluation_schema import EvaluationTask
from retrieval_semantic_observed_operation_capture import make_unadmitted_operation_identity
from retrieval_semantic_provenance import VerifiedPublicationPin, verify_publication_provenance_from_adapter
from retrieval_supplement_capture import bind_operation_capture, capture_baseline_envelope
from retrieval_supplement_catalog import SupplementCatalog, parse_catalog
from retrieval_supplement_declaration import (
    InertDeclaration, JournalBinding, SlotIdentity, capture_arm, create_declaration,
    ordered_slots, representation_for,
)
from retrieval_supplement_report import SlotResult, result_artifact_bytes
from retrieval_supplement_values import ARMS, canonical_bytes

PIN, FREEZE, SOURCE, PROFILE = ("sha256:" + str(number) * 64 for number in (1, 2, 3, 4))
EMBEDDING_PROFILES = {"canonical-node-v1": "sha256:" + "5" * 64,
                      "canonical-node-public-text-v2": "sha256:" + "6" * 64}


@dataclass(frozen=True)
class FunctionalReportFixture:
    catalog: SupplementCatalog
    declaration: InertDeclaration
    results: tuple[SlotResult, ...]
    cells: dict[str, InMemoryStoreReadbackCell]


def make_report_pin(backend: str) -> tuple[VerifiedPublicationPin, InMemoryStoreReadbackCell]:
    identity = make_test_identity()
    bindings = make_test_bindings()
    vector = [(binding, 1, "snap1:" + hashlib.sha256(alias.encode()).hexdigest()) for alias, binding in bindings.items()]
    receipt = make_w5_receipt(snapshot_vector=vector)
    cell = make_w5_readback_adapter(receipt, graph_id=identity.graph_id, backend=backend)
    return verify_publication_provenance_from_adapter(cell, policy=make_test_policy()), cell


def make_report_declaration(catalog: SupplementCatalog) -> InertDeclaration:
    journals = {}
    for index, arm in enumerate(ARMS):
        name = "baseline" if index < 3 else "v1" if index == 3 else "public-text"
        journals[arm] = JournalBinding("unit-journal-" + name, "unit-attempt-" + name,
                                       representation_for(arm), EMBEDDING_PROFILES.get(representation_for(arm)))
    return create_declaration(catalog, input_pin_sha256=PIN, freeze_sha256=FREEZE,
                              source_closure_sha256=SOURCE, artifact_profile_sha256=PROFILE,
                              embedding_profiles=EMBEDDING_PROFILES,
                              attempt_id="unit-report-attempt", journals=journals)


def make_report_envelope(task: EvaluationTask, slot: SlotIdentity, *, kind: str) -> tuple[ExecutionState, QueryEnvelope]:
    backend = "native_filesystem" if slot.arm == ARMS[0] else "postgresql"
    scope = QueryScope(graph_id=task.graph_id, backend=backend, selected_binding_ids=task.selected_bindings,
                       source_path_prefix=task.source_path_prefix)
    requested = QueryMode(task.requested_mode)
    if kind == "privacy":
        return ExecutionState.REFUSED, refused_envelope(requested_mode=requested, scope=scope,
                                                       refusal_code="binding_privacy_not_authorized")
    if slot.arm == ARMS[1] and task.requested_mode == "literal_captured_text":
        return ExecutionState.UNAVAILABLE, unavailable_envelope(
            requested_mode=requested, scope=scope, reason="captured_text_surface_unsupported_by_metadata_graph")
    captured = slot.arm != ARMS[1]
    effective = QueryMode.LITERAL_CAPTURED_TEXT if captured and (
        slot.arm == ARMS[0] or requested == QueryMode.CURRENT_LITERAL_ENUMERATION
    ) else requested
    fallback = None
    if effective != requested:
        reason = "native_git_grep_fixed_string_scan" if slot.arm == ARMS[0] else "lexical_captured_text_literal_scan"
        fallback = FallbackDisclosure(True, requested, effective, reason)
    surface = MatchSurface.CAPTURED_TEXT if captured else MatchSurface.STORED_METADATA
    hits = tuple(QueryHit(gold.locator, surface, index) for index, gold in enumerate(task.gold_targets, 1))
    envelope = QueryEnvelope(
        contract_version=QUERY_CONTRACT_VERSION, requested_mode=requested, effective_mode=effective,
        match_surface=surface, scope=scope, ordering_policy_version=DEFAULT_ORDERING_POLICY_VERSION,
        capability_state=CapabilityState.AVAILABLE, hits=hits, scope_complete=True, fallback=fallback,
    ).validate()
    return ExecutionState.COMPLETED, envelope


def make_baseline_slot_result(
    task: EvaluationTask, slot: SlotIdentity, declaration: InertDeclaration,
    pin: VerifiedPublicationPin, state: ExecutionState, envelope: QueryEnvelope,
    *, native_path_surfaces: tuple[str, ...] | None = None,
) -> SlotResult:
    if slot.arm == ARMS[0] and state == ExecutionState.COMPLETED and native_path_surfaces is None:
        native_path_surfaces = ("captured_text",)  # Explicitly synthetic scope.
    raw = capture_baseline_envelope(task, slot=slot, envelope=envelope, execution_state=state, publication_pin=pin)
    result = SlotResult(slot, pin, raw, b"", baseline_envelope=envelope, native_path_surfaces=native_path_surfaces)
    reference = bind_operation_capture(raw, slot=slot, declaration=declaration,
                                       result_artifact_raw=result_artifact_bytes(result))
    return replace(result, capture_reference_raw=reference.raw)


def make_functional_report_fixture() -> FunctionalReportFixture:
    catalog = parse_catalog(canonical_bytes(make_test_catalog_payload()), identity=make_test_identity(),
                            binding_ids=tuple(make_test_bindings().values()))
    declaration = make_report_declaration(catalog)
    sqlite_pin, sqlite_cell = make_report_pin("sqlite")
    postgres_pin, postgres_cell = make_report_pin("postgresql")
    cases = {case.task.task_id: case for case in catalog.cases}
    results = []
    for slot in ordered_slots(catalog):
        case = cases[slot.task_id]
        if slot.arm in ARMS[:3]:
            state, envelope = make_report_envelope(case.task, slot, kind=case.kind)
            pin = sqlite_pin if slot.arm == ARMS[0] else postgres_pin
            results.append(make_baseline_slot_result(case.task, slot, declaration, pin, state, envelope))
        else:
            # Explicitly absent vector execution remains NOT_RUN in every slot.
            raw = canonical_bytes(make_unadmitted_operation_identity(
                slot.task_id, slot.generation, capture_arm(slot.arm), slot.repetition,
                ExecutionState.NOT_RUN, "functional_vector_not_run",
            ))
            reference = bind_operation_capture(raw, slot=slot, declaration=declaration)
            results.append(SlotResult(slot, postgres_pin, raw, reference.raw))
    return FunctionalReportFixture(catalog, declaration, tuple(results), {"sqlite": sqlite_cell, "postgresql": postgres_cell})
