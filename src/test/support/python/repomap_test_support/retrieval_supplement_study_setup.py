"""Inner study declaration, worker exchange and store/slot join helpers.

Test support only. Extracted from ``retrieval_supplement_study_driver`` so each
maintained support module stays focused and comfortably below the 400-line
profile ceiling.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from repomap_test_support.retrieval_supplement_prepare import static_exposures
from repomap_test_support.retrieval_supplement_source import supplement_source_snapshot
from repomap_test_support.w5_public_text_functional import SyntheticDelivery
from repomap_test_support.w5_public_text_learned import profiles
from retrieval_supplement_study_inner_evidence import (
    InnerEvidence, bind_worker_admission, begin_worker_evidence, finish_worker_evidence,
)
from retrieval_semantic_experiment_bridge import ComponentExchange
from retrieval_semantic_generator_output import parse_verified_worker_response
from retrieval_supplement_catalog import SupplementCatalog
from retrieval_supplement_corpus import parse_corpus
from retrieval_supplement_declaration import (
    InertDeclaration,
    JournalBinding,
    create_declaration,
    representation_for,
)
from retrieval_supplement_files import read_member
from retrieval_supplement_freeze import (
    build_final_freeze,
    policy_from_bytes,
    verify_input_pin,
)
from retrieval_supplement_preparation import prepare_supplement
from retrieval_supplement_report import SlotResult
from retrieval_supplement_study_contracts import (
    ARTIFACT_SOURCE_IDENTITY_SHA256,
    INPUT_PIN_SHA256,
    REQUEST_ADMISSION_SECONDS,
    REQUEST_RESPONSE_SECONDS,
)
from retrieval_supplement_study_live_requests import LiveStudyRequests
from retrieval_supplement_study_preflight import SlotOutcome
from retrieval_supplement_values import (
    ARMS,
    MAX_JSON_BYTES,
    SupplementIdentity,
    canonical_bytes,
    digest_bytes,
    strict_json,
)


def build_supplement_declaration(
    repo_root: Path, attempt_id: str,
) -> tuple[SupplementIdentity, dict[str, str], SupplementCatalog, Any, Any, dict[str, Any], InertDeclaration]:
    """Verify frozen supplement-v1 pin, freeze and inert declaration for ``attempt_id``."""
    identity = SupplementIdentity(
        "sup1-calibration-v1", "sup1-corpus-v1", "CLOE-W5-SUPPLEMENT1",
        "sup1-calibration", "sup1-",
    )
    in_root = repo_root / "src/test/fixtures/retrieval_evaluation/supplement-v1"
    meta = strict_json(read_member(in_root, "bindings-v1.json", max_bytes=MAX_JSON_BYTES))
    bindings = {str(k): str(v) for k, v in dict(meta["bindings"]).items()}
    pin_raw = read_member(in_root, "input-pin-v1.json", max_bytes=MAX_JSON_BYTES,
                          expected_sha256=INPUT_PIN_SHA256)
    catalog = verify_input_pin(in_root, pin_raw, expected_pin_sha256=INPUT_PIN_SHA256,
                               identity=identity, bindings=bindings)
    corpus = parse_corpus(read_member(in_root, "corpus-v1.json", max_bytes=MAX_JSON_BYTES),
                          identity=identity, bindings=bindings)
    policy = policy_from_bytes(read_member(in_root, "policy-v1.json", max_bytes=MAX_JSON_BYTES))
    exposures = static_exposures(read_member(in_root, "static-inventory-v1.json", max_bytes=MAX_JSON_BYTES))
    preparation = prepare_supplement(catalog, corpus, policy=policy, exposures=exposures)
    snapshot = supplement_source_snapshot(repo_root)
    predecessor = read_member(in_root, "predecessor-manifest.json", max_bytes=MAX_JSON_BYTES)
    freeze = build_final_freeze(
        repo_root, pin_raw, expected_input_pin_sha256=INPUT_PIN_SHA256,
        source_files=snapshot.files, builder_members=snapshot.builder_members,
        explicit_members=snapshot.explicit_members, predecessor_manifest_raw=predecessor,
    )
    emb_profiles = {
        arm: prof.identity for arm, prof in profiles(ARTIFACT_SOURCE_IDENTITY_SHA256).items()
    }
    journals = {
        arm: JournalBinding(
            f"{attempt_id}-{'baseline' if idx < 3 else 'v1' if idx == 3 else 'public-text'}",
            f"{attempt_id}-attempt-{'baseline' if idx < 3 else 'v1' if idx == 3 else 'public-text'}",
            representation_for(arm), emb_profiles.get(representation_for(arm)),
        )
        for idx, arm in enumerate(ARMS)
    }
    declaration = create_declaration(
        catalog, input_pin_sha256=INPUT_PIN_SHA256,
        freeze_sha256=digest_bytes(canonical_bytes(freeze)),
        source_closure_sha256=snapshot.identity,
        artifact_profile_sha256=ARTIFACT_SOURCE_IDENTITY_SHA256,
        embedding_profiles=emb_profiles, attempt_id=attempt_id, journals=journals,
    )
    return identity, bindings, catalog, corpus, policy, preparation, declaration


def run_exchange_epoch(
    request_map: dict[str, Any], request_raw: bytes, *,
    worker_digests: dict[str, str], delivery: SyntheticDelivery | None,
    representation: str, evidence_root: Path, evidence: InnerEvidence,
    require_current: Callable[[], None] | None = None,
) -> Any:
    """Publish one ``ComponentExchange`` request, await host admission and verify response."""
    if require_current is not None:
        require_current()
    exchange = ComponentExchange.publish_request(
        attempt_id=str(request_map["attempt_id"]),
        request=request_map, expected_source_closure=worker_digests,
    )
    worker_evidence = begin_worker_evidence(
        evidence_root=evidence_root, representation=representation, exchange=exchange, ledger=evidence,
    )
    exchange.emit_request_readiness()
    if delivery is not None:
        delivery.admit(exchange)
    exchange = exchange.await_controller_admission(timeout_seconds=float(REQUEST_ADMISSION_SECONDS))
    bind_worker_admission(evidence_root=evidence_root, evidence=worker_evidence, exchange=exchange)
    print(exchange.admission_ack_line, flush=True)
    if delivery is not None:
        delivery.respond(exchange)
    members = exchange.await_response(timeout_seconds=float(REQUEST_RESPONSE_SECONDS))
    finish_worker_evidence(evidence_root=evidence_root, evidence=worker_evidence, members=members)
    if require_current is not None:
        require_current()
    assert exchange.admission_sha256 is not None
    worker_evidence.response = parse_verified_worker_response(
        request_raw=request_raw, report_raw=members["report.json"],
        terminal_raw=members["terminal.json"], declaration_raw=members["declaration.json"],
        source_manifest_raw=members["source-manifest.json"],
        sources_archive_raw=members["sources.zip"],
        expected_controller_admission_sha256=exchange.admission_sha256,
        expected_source_closure=worker_digests,
    )
    return worker_evidence


def store_join_maps(
    catalog: SupplementCatalog, v1_input: Any, public_text: Any,
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]], list[str]]:
    """Build gold-target and accepted-node span lookup maps from live store readbacks."""
    pub_id = public_text.publication_identity
    span_lookup: dict[tuple[str, str], tuple[int, int]] = {}
    for doc in public_text.documents:
        d = doc.descriptor
        span_lookup[(str(d["source_binding_id"]), str(d["source_relative_path"]))] = (
            int(d["start_line"]), int(d["end_line"]),
        )
    accepted_nodes: dict[str, dict[str, Any]] = {}
    for entry in v1_input.documents:
        rep = entry.representation
        for rel_path in rep.source_relative_paths:
            key = f"{rep.source_binding_id}:{rel_path}"
            span = span_lookup.get((rep.source_binding_id, rel_path))
            if span is None:
                continue
            accepted_nodes[key] = {
                "publication_identity": pub_id,
                "span_start": span[0], "span_end": span[1],
            }
    gold_targets: dict[str, list[str]] = {}
    positives: list[str] = []
    for case in catalog.cases:
        if case.kind == "positive":
            positives.append(case.task.task_id)
        targets = [
            f"{g.locator.binding_id}:{g.locator.source_relative_path}"
            for g in case.task.gold_targets if g.relevance_grade > 0
        ]
        if targets:
            gold_targets[case.task.task_id] = targets
    return gold_targets, accepted_nodes, positives


def slot_outcome_row(
    case: Any, result: SlotResult, live: LiveStudyRequests,
    v1_input: Any, public_text: Any,
) -> SlotOutcome:
    """Join one graded ``SlotResult`` to its counted-worker query/target record IDs."""
    arm = result.slot.arm
    ref_map = strict_json(result.capture_reference_raw)
    state = str(ref_map["state"])
    reason = ref_map["reason"]
    q_id: str | None = None
    t_ids: tuple[str, ...] = ()
    gold_files = {
        (g.locator.binding_id, g.locator.source_relative_path)
        for g in case.task.gold_targets if g.relevance_grade > 0
    }
    if arm == ARMS[3] and state == "completed":
        q_id = f"v1.query:{case.task.task_id}"
        t_ids = tuple(
            f"v1.{doc.record_id}" for doc in v1_input.documents
            if any((doc.representation.source_binding_id, p) in gold_files
                   for p in doc.representation.source_relative_paths)
        )
    elif arm in (ARMS[4], ARMS[5]) and state == "completed":
        q_id = f"ptx.q.{case.task.task_id}"
        t_ids = tuple(
            rec.record_id
            for rec, doc in zip(live.v2_records[:len(public_text.documents)],
                                public_text.documents, strict=True)
            if (str(doc.descriptor["source_binding_id"]),
                str(doc.descriptor["source_relative_path"])) in gold_files
        )
    return SlotOutcome(
        case.task.task_id, case.task.family, case.kind, arm,
        result.slot.repetition, state, q_id, t_ids, reason,
    )


__all__ = [
    "build_supplement_declaration", "run_exchange_epoch", "slot_outcome_row", "store_join_maps",
]
