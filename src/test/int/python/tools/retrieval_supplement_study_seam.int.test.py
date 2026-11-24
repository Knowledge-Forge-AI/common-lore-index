"""Real non-native filesystem + SQLite Local + PostgreSQL integration seam for the study.

Ordinary CI uses the ``core`` PostgreSQL profile. The separately admitted
offline proof instead uses the exact socket-only runtime with synthetic
inputs; neither path starts a model or consumes a registered study. Exercises maintained study
owners end to end:
1. Exclusive ``0600`` reservation creation, admission consumption, runner
   pre-dispatch gate, inner-plan private-path stripping, and ``unknown``
   settlement reuse refusal on disk.
2. Real ``publish_study_stores`` over SQLite Local + containerized PostgreSQL
   + ``build_graph_lexical_generation`` + ``materialize_native_search_view``,
   followed by ``prepare_supplement``, ``prepare_public_generation_input``,
   ``require_store_joins``, and ``build_live_study_requests`` round-tripping
   both representation requests through ``parse_worker_request``.
3. Real baseline slot execution (``execute_baseline_slot`` on ``git grep``,
   PostgreSQL graph store, and PostgreSQL FTS, including privacy-first refusal)
   plus explicit ``NOT_RUN`` vector slots into ``report_supplement`` and
   ``analyze_slots``.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path

import pytest

from repomap_test_support.postgres_harness import temporary_postgres
from repomap_test_support.retrieval_supplement_fixtures import (
    make_test_bindings,
    make_test_catalog_payload,
    make_test_corpus_payload,
    make_test_identity,
    make_test_policy,
)
from repomap_test_support.retrieval_supplement_report_fixture import (
    make_report_declaration,
)
from repomap_test_support.retrieval_supplement_study_fixtures import (
    make_preparation,
    make_study_plan,
)
from repomap_test_support.retrieval_supplement_study_pg import disposable_socket_postgres, publish_study_stores
from repomap_test_support.retrieval_supplement_study_slots import execute_baseline_slot
from retrieval_evaluation_grading import ExecutionState
from retrieval_public_text_contracts import PrivateMarkerScope
from retrieval_semantic_generator_inputs import prepare_public_generation_input
from retrieval_semantic_observed_operation_capture import (
    make_unadmitted_operation_identity,
)
from retrieval_semantic_provenance import verify_publication_provenance_from_adapter
from retrieval_supplement_applicability import CandidateExposure
from retrieval_supplement_capture import bind_operation_capture
from retrieval_supplement_catalog import parse_catalog
from retrieval_supplement_corpus import materialize_corpus, parse_corpus
from retrieval_supplement_declaration import capture_arm, ordered_slots
from retrieval_supplement_preparation import prepare_supplement
from retrieval_supplement_report import SlotResult, report_supplement
from retrieval_supplement_study_adapter import parse_exchanges
from retrieval_supplement_study_contracts import (
    ARTIFACT_SOURCE_IDENTITY_SHA256,
    StudyRefusal,
)
from retrieval_supplement_study_live_requests import build_live_study_requests
from retrieval_supplement_study_plan import (
    admission_file_for_plan,
    create_reservation,
    issue_admission,
    plan_digest,
    project_inner_study_plan,
    read_reservation,
    require_reservation_admitted_for_runner,
    settle_reservation,
)
from retrieval_supplement_study_preflight import (
    SlotOutcome,
    analyze_slots,
    require_store_joins,
)
from retrieval_supplement_values import ARMS, canonical_bytes, digest_json, strict_json


@contextmanager
def seam_database():
    from runner_pgvector_context import load_inner_plan, offline_plan_requested
    if offline_plan_requested(os.environ):
        plan = load_inner_plan(os.environ)
        assert plan["role"] == "proof", "synthetic seam cannot consume study admission"
        with disposable_socket_postgres(plan["derived"]["runtime_config"]) as (handle, receipt):
            yield handle.database
        assert receipt["absent_after_rm"] is True
    else:
        with temporary_postgres() as database:
            yield database


def test_reservation_runner_gate_and_settlement_on_disk(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan = make_study_plan(tmp_path)
    res_path = tmp_path / "reservation.json"
    plan["reservation_path"] = str(res_path)
    plan_path.write_bytes(canonical_bytes(plan))
    plan_path.chmod(0o600)
    digest = plan_digest(plan)
    create_reservation(res_path, plan_sha256=digest, attempt_id=plan["attempt_id"])
    assert oct(res_path.stat().st_mode & 0o777) == "0o600"
    assert read_reservation(res_path)["consumed"] is False

    with pytest.raises(StudyRefusal):
        require_reservation_admitted_for_runner(plan, plan_path=plan_path)

    adm = issue_admission(
        plan, reservation_path=res_path,
        evaluator_closure_sha256=plan["evaluator_closure_sha256"],
        admission_path=admission_file_for_plan(plan_path),
    )
    assert read_reservation(res_path)["cleanup"] == "in_progress"
    verified = require_reservation_admitted_for_runner(plan, plan_path=plan_path)
    inner = project_inner_study_plan(plan, admission=verified)
    assert "reservation_path" not in inner and "allocation_path" not in inner
    assert inner["study_admission"]["attempt_id"] == adm.attempt_id

    settle_reservation(res_path, plan_sha256=digest, cleanup="unknown", runtime_imports=1)
    with pytest.raises(StudyRefusal):
        issue_admission(plan, reservation_path=res_path,
                        evaluator_closure_sha256=plan["evaluator_closure_sha256"])


def test_filesystem_preparation_roundtrip_refuses_drift(tmp_path: Path) -> None:
    preparation = make_preparation()
    target = tmp_path / "preparation.json"
    target.write_bytes(canonical_bytes(preparation))
    loaded = json.loads(target.read_bytes().decode("utf-8"))
    v1, v2 = parse_exchanges(loaded, expected_counts=((2, 2), (1, 2)),
                             expected_total=7, expected_slots=18)
    assert v1.record_count + v2.record_count == 7
    loaded["exchanges"][0]["queries"].pop()
    target.write_bytes(canonical_bytes(loaded))
    with pytest.raises(StudyRefusal):
        parse_exchanges(json.loads(target.read_bytes().decode("utf-8")),
                        expected_counts=((2, 2), (1, 2)), expected_total=7, expected_slots=18)


def test_authenticated_sdk_capacity_matches_its_selected_profile() -> None:
    from test_sandbox_capacity import validate_scratch_capacity
    validate_scratch_capacity()


def test_real_sqlite_and_postgres_maintained_study_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity, bindings, policy = make_test_identity(), make_test_bindings(), make_test_policy()
    catalog = parse_catalog(
        canonical_bytes(make_test_catalog_payload()),
        identity=identity, binding_ids=tuple(bindings.values()),
    )
    corpus = parse_corpus(
        canonical_bytes(make_test_corpus_payload()), identity=identity, bindings=bindings,
    )
    working_copy = tmp_path / "working-copy"
    working_copy.mkdir(mode=0o700)
    materialize_corpus(corpus, working_copy)

    workspace = tmp_path / "stores"
    assert not workspace.exists()
    with seam_database() as database:
        stores = publish_study_stores(
            workspace, working_copy, identity=identity, bindings=bindings,
            policy=policy, postgres=database, psql_command=database.psql_command,
        )
        assert workspace.is_dir() and workspace.stat().st_mode & 0o777 == 0o700
        public_ids = {key for key, resolved in policy.bindings.items()
                      if resolved.resolved_binding_privacy == "public-dev"}
        private_ids = set(bindings.values()) - public_ids
        assert private_ids
        for published in (stores.sqlite_store, stores.postgres_store):
            assert {binding.binding_id for binding in published.published.manifest.bindings} == public_ids
            assert private_ids <= set(published.pin.denied_bindings)
        assert all(graph.privacy == "public-dev" for graph in stores.ops_config.graphs)
        # Exercise the exact-read transport and descriptor on real PostgreSQL,
        # without pretending this core-compatible synthetic query is a vector study.
        import hashlib
        from types import SimpleNamespace
        from typing import cast
        from common_lore_index.storage.authority import GraphRunId
        from common_lore_index.storage.publication_readback import RunPublicationRecord
        from common_lore_index.storage.semantic_candidates import CandidatePublication
        from repomap_test_support import w5_public_text_postgres as pg
        from retrieval_public_text_study_queries import StudyQuery
        pin = stores.postgres_store.pin
        publication = CandidatePublication(pin.repository_identity, RunPublicationRecord(GraphRunId(pin.run_id), pin.receipt))
        statement = "BEGIN READ ONLY; SELECT json_build_object('status','ok','candidates','[]'::json)::text; COMMIT;"
        monkeypatch.setattr(pg, "exact_sql", lambda _query: statement)
        read = pg.exact_read(database, cast(StudyQuery, SimpleNamespace(
            publication=publication, source_binding_id=sorted(public_ids)[0], limit=20)), pin)
        assert read.state == "completed" and not read.rows
        assert read.query_descriptor_sha256 == "sha256:" + hashlib.sha256(statement.encode()).hexdigest()

        scope = PrivateMarkerScope.create((str(tmp_path),))
        reps = stores.postgres_store.representations()
        pub_text = stores.postgres_store.public_text(scope)
        v1_input = prepare_public_generation_input(
            reps, catalog.tasks,
            read_current=lambda: verify_publication_provenance_from_adapter(
                stores.postgres_store.adapter, policy=policy),
            read_representations=stores.postgres_store.representations,
        )

        span_map: dict[tuple[str, str, str], tuple[int | None, int | None]] = {}
        for row in stores.postgres_store.spans():
            meta = json.loads(row.metadata_json) if isinstance(row.metadata_json, str) else row.metadata_json
            if isinstance(meta, dict) and row.link_kind not in {"inferred", "inferred_from_edge"}:
                if type(row.start_line) is int and type(row.end_line) is int:
                    span_map.setdefault(
                        (row.canonical_key, meta["binding_id"], meta["source_relative_path"]),
                        (row.start_line, row.end_line),
                    )
        exposures: list[CandidateExposure] = []
        for entry in reps.verified_entries:
            for rel_path in entry.source_relative_paths:
                sp = span_map.get((entry.canonical_key, entry.source_binding_id, rel_path), (None, None))
                for arm in (ARMS[1], ARMS[3]):
                    exposures.append(CandidateExposure(
                        arm, entry.source_binding_id, rel_path, entry.canonical_key,
                        entry.kind, sp[0], sp[1],
                    ))
        for doc in pub_text.documents:
            d = doc.descriptor
            for arm in (ARMS[0], ARMS[2], ARMS[4], ARMS[5]):
                exposures.append(CandidateExposure(
                    arm, d["source_binding_id"], d["source_relative_path"],
                    d["canonical_key"], d["kind"], d["start_line"], d["end_line"],
                ))

        prepared = prepare_supplement(catalog, corpus, policy=policy, exposures=exposures)
        v1_counts = (prepared["exchanges"][0]["document_count"], prepared["exchanges"][0]["query_count"])
        v2_counts = (prepared["exchanges"][1]["document_count"], prepared["exchanges"][1]["query_count"])
        live = build_live_study_requests(
            prepared, v1_input=v1_input, public_text=pub_text,
            tasks=catalog.tasks, attempt_id="studytest-seam1",
            artifact_profile_identity=ARTIFACT_SOURCE_IDENTITY_SHA256,
            expected_counts=(v1_counts, v2_counts),
            expected_total=sum(v1_counts) + sum(v2_counts),
            expected_slots=prepared["slot_count"],
        )
        assert len(live.v1_request["records"]) == sum(v1_counts)
        assert len(live.v2_request["records"]) == sum(v2_counts)

        acc_nodes: dict[str, dict[str, object]] = {}
        for doc in pub_text.documents:
            d = doc.descriptor
            acc_nodes[f"{d['source_binding_id']}:{d['source_relative_path']}"] = {
                "publication_identity": pub_text.publication_identity,
                "span_start": int(d["start_line"]), "span_end": int(d["end_line"]),
            }
        golds = {
            c.task.task_id: [f"{g.locator.binding_id}:{g.locator.source_relative_path}"
                             for g in c.task.gold_targets if g.relevance_grade > 0]
            for c in catalog.cases if any(g.relevance_grade > 0 for g in c.task.gold_targets)
        }
        pos_ids = [c.task.task_id for c in catalog.cases if c.kind == "positive"]
        require_store_joins(
            ordered_task_ids=[t.task_id for t in catalog.tasks],
            gold_targets=golds, accepted_nodes=acc_nodes,
            publication_identity=pub_text.publication_identity,
            expected_publication_identity=digest_json(stores.postgres_store.pin.to_dict()),
            positive_task_ids=pos_ids,
            expected_task_count=len(catalog.tasks),
            expected_positive_count=len(pos_ids),
        )

        declaration = make_report_declaration(catalog)
        cases_by_id = {c.task.task_id: c for c in catalog.cases}
        slot_results: list[SlotResult] = []
        slot_outcomes: list[SlotOutcome] = []
        for slot in ordered_slots(catalog):
            case = cases_by_id[slot.task_id]
            if slot.arm in ARMS[:3]:
                res, (baseline_state, _envelope) = execute_baseline_slot(
                    case.task, slot, declaration=declaration,
                    stores=stores, policy=policy, delivery_kind="functional_synthetic",
                )
                if case.kind == "positive" and slot.arm == ARMS[2]:
                    assert baseline_state is ExecutionState.COMPLETED
                if case.kind == "privacy":
                    assert baseline_state is ExecutionState.REFUSED
            else:
                raw = canonical_bytes(make_unadmitted_operation_identity(
                    slot.task_id, slot.generation, capture_arm(slot.arm), slot.repetition,
                    ExecutionState.NOT_RUN, "non_native_seam_vector_not_run",
                ))
                ref = bind_operation_capture(raw, slot=slot, declaration=declaration)
                res = SlotResult(slot, stores.postgres_store.pin, raw, ref.raw)
            slot_results.append(res)
            state = str(strict_json(res.capture_reference_raw)["state"])
            slot_outcomes.append(SlotOutcome(
                slot.task_id, case.task.family, case.kind, slot.arm, slot.repetition, state,
            ))

        report = report_supplement(catalog, declaration, slot_results)
        analysis = analyze_slots(
            slot_outcomes, evidence={},
            ordered_task_ids=[t.task_id for t in catalog.tasks],
            cost_summary=report["phase_costs"],
            expected_task_count=len(catalog.tasks),
            expected_slot_count=len(slot_outcomes),
        )
        assert report["slot_count"] == len(slot_outcomes)
        assert report["arm_summaries"][ARMS[0]]["safety_refusal_correct"] > 0
        assert analysis["incomplete_attempt_retained"] is True
        assert analysis["scientific_acceptance_claimed"] is False
