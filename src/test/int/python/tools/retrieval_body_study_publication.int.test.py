"""Real SQLite/PostgreSQL bound body-study preparation (functional, inert).

Uses only unrelated unit-body fixtures through BodyStoreFixture.  No model,
native study or registered-study admission is exercised.
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from copy import deepcopy
from typing import Any

import pytest

from common_lore_index_test_support.postgres_harness import temporary_postgres
from common_lore_index_test_support.retrieval_body_fixtures import (
    make_body_bindings, make_body_catalog_payload, make_body_corpus_payload,
    make_body_identity, make_body_policy,
)
from common_lore_index_test_support.retrieval_body_publication_fixture import (
    BodyStoreFixture, materialize_body_home,
)
from common_lore_index_test_support.retrieval_body_study_fixtures import PIN
from retrieval_body_admission_rules import evaluate_body_case
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_corpus import parse_body_corpus
from retrieval_body_study_join import prepare_bound_body_study
from retrieval_body_study_queries import prepare_anchor_v1_queries
from retrieval_body_study_values import ARMS, FTS_ARM, FUSION_ARM, V2_ARM, BodyStudyError
from retrieval_body_values import canonical_bytes
from common_lore_index.graph.multi_source_records import graph_source_binding_id
from common_lore_index.retrieval.query_contracts import EvaluationLocator
from retrieval_evaluation_schema import GoldTarget
from retrieval_semantic_file_projection import (
    compute_file_level_ranking_metrics, verify_and_project_returned_files,
)
from retrieval_semantic_fusion import FileIdentityTuple
from retrieval_semantic_representation import RepresentationValidationError


def _store(root: Path, name: str, *, postgres: Any = None, unrelated: bool = False):
    identity, bindings, policy = make_body_identity(), make_body_bindings(), make_body_policy()
    catalog_payload, corpus_payload = make_body_catalog_payload(), make_body_corpus_payload()
    if unrelated:
        changed_identity = replace(identity, graph_id=identity.graph_id + "-other",
                                   class_id=identity.class_id + "-other",
                                   corpus_id=identity.corpus_id + "-other")
        changed_bindings = {alias: graph_source_binding_id(changed_identity.graph_id, alias)
                            for alias in bindings}
        replacements = {identity.graph_id: changed_identity.graph_id,
                        identity.class_id: changed_identity.class_id,
                        identity.corpus_id: changed_identity.corpus_id,
                        **{bindings[alias]: value for alias, value in changed_bindings.items()}}

        def rewrite(value):
            if isinstance(value, dict):
                return {rewrite(key): rewrite(item) for key, item in value.items()}
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            return replacements.get(value, value) if isinstance(value, str) else value

        policy = replace(policy, graph_id=changed_identity.graph_id,
                         selected_binding_ids=frozenset(changed_bindings.values()),
                         bindings={changed_bindings[alias]: replace(policy.bindings[binding],
                                                                   binding_id=changed_bindings[alias])
                                   for alias, binding in bindings.items()}).validate()
        catalog_payload, corpus_payload = rewrite(catalog_payload), rewrite(corpus_payload)
        identity, bindings = changed_identity, changed_bindings
    catalog = parse_functional_catalog(canonical_bytes(catalog_payload),
                                       identity=identity, binding_ids=tuple(bindings.values()))
    corpus = parse_body_corpus(canonical_bytes(corpus_payload),
                                identity=identity, bindings=bindings)
    published = materialize_body_home(
        root / name, corpus=corpus, identity=identity, policy=policy, postgres=postgres,
        psql_command="psql" if postgres is None else postgres.psql_command)
    store = BodyStoreFixture(published,
                             psql_command="psql" if postgres is None else postgres.psql_command)
    return catalog, policy, store


def _decisions(catalog, policy, store, backend: str):
    rows = []
    for case in catalog.cases:
        for arm in ARMS:
            row = evaluate_body_case(catalog, case, arm=arm, policy=policy,
                                     frozen_policy=policy, publication_pin=store.pin,
                                     observed_backend=backend)
            row["input_pin_sha256"] = PIN
            rows.append(row)
    return rows


def _assert_snapshot_bound_file_grading(catalog, store):
    """Use real issued snapshots, same-path public files and a denied binding."""
    task = next(case.task for case in catalog.cases if case.kind == "body_positive")
    bindings = make_body_bindings()
    snapshots = store.pin.snapshot_by_binding()
    path = task.gold_targets[0].locator.source_relative_path
    first = FileIdentityTuple(bindings["oak"], snapshots[bindings["oak"]][1], path)
    second = FileIdentityTuple(bindings["elm"], snapshots[bindings["elm"]][1], path)
    returned = (first, first, second)
    golds = (GoldTarget(EvaluationLocator(bindings["oak"], path), 3, "Functional oak gold."),
             GoldTarget(EvaluationLocator(bindings["elm"], path), 2, "Functional elm gold."))
    assert verify_and_project_returned_files(returned, verified_pin=store.pin, task=task) == (
        (bindings["oak"], path), (bindings["elm"], path))
    assert compute_file_level_ranking_metrics(
        returned, golds, verified_pin=store.pin, task=task, cutoffs=(10,)) == {
            "file_recall@10": 1.0, "file_mrr@10": 1.0, "file_ndcg@10": 1.0}
    assert bindings["vault"] in store.pin.denied_bindings
    denied = FileIdentityTuple(bindings["vault"], first.snapshot_id, path)
    with pytest.raises(RepresentationValidationError, match="binding_privacy_not_authorized"):
        compute_file_level_ranking_metrics((denied,), golds, verified_pin=store.pin, task=task)
    stale = FileIdentityTuple(bindings["oak"], "not-the-current-snapshot", path)
    with pytest.raises(RepresentationValidationError, match="snapshot_id_mismatch"):
        compute_file_level_ranking_metrics((stale,), golds, verified_pin=store.pin, task=task)


def test_bound_same_input_sqlite_postgres_correspondence(tmp_path: Path) -> None:
    with temporary_postgres() as database:
        catalog, policy, sqlite_store = _store(tmp_path, "sqlite")
        _, _, pg_store = _store(tmp_path, "postgres", postgres=database)
        sqlite_decisions = _decisions(catalog, policy, sqlite_store, "sqlite")
        pg_decisions = _decisions(catalog, policy, pg_store, "postgresql")
        matrix = prepare_bound_body_study(
            catalog,
            sqlite_decisions=sqlite_decisions,
            postgres_decisions=pg_decisions,
            sqlite_pin=sqlite_store.pin, postgres_pin=pg_store.pin,
            policy=policy, frozen_policy=policy)
        assert matrix["bound_preparation"] is True
        assert matrix["decision_rows_verified"] is True
        assert matrix["selected_row_count"] == 144 and matrix["planned_slot_count"] == 432
        assert matrix["origin_proven"] is False
        assert matrix["execution_authority_granted"] is False
        assert matrix["functional_only"] is True
        assert matrix["sqlite_pin_identity"] != matrix["postgres_pin_identity"]
        anchors = prepare_anchor_v1_queries(catalog, matrix["selected_rows"])
        assert {record.task_id for record in anchors} == {
            case.task.task_id for case in catalog.cases if case.kind == "metadata_anchor"}
        assert len(anchors) == 4
        for row in matrix["selected_rows"]:
            if row["case_kind"] == "body_positive" and row["arm"] in (FTS_ARM, V2_ARM, FUSION_ARM):
                assert row["preparation"] == "ready" and row["source_publication"] == "postgresql"
        # These are preparation capabilities, not actual vector/model readiness.
        _assert_snapshot_bound_file_grading(catalog, sqlite_store)
        _assert_snapshot_bound_file_grading(catalog, pg_store)
        for kind, arm, replacement in (
            ("privacy", V2_ARM, {"preparation": "ready", "reason": None,
                                  "actual_surface": "captured_text", "fallback_policy": "none"}),
            ("body_positive", FTS_ARM, {"preparation": "unavailable", "reason": "forged_gap",
                                         "actual_surface": "none", "fallback_policy": "none"}),
            ("body_positive", FUSION_ARM, {"fallback_policy": "forged_fallback"}),
        ):
            forged = deepcopy(pg_decisions)
            ids = {case.task.task_id for case in catalog.cases if case.kind == kind}
            next(row for row in forged if row["task_id"] in ids and row["arm"] == arm).update(replacement)
            with pytest.raises(BodyStudyError, match="decision_recomputed_mismatch"):
                prepare_bound_body_study(
                    catalog, sqlite_decisions=sqlite_decisions, postgres_decisions=forged,
                    sqlite_pin=sqlite_store.pin, postgres_pin=pg_store.pin,
                    policy=policy, frozen_policy=policy)
        # Wrong-backend pin placement is an unrelated-publication refusal.
        with pytest.raises(BodyStudyError, match="sqlite_pin_backend"):
            prepare_bound_body_study(
                catalog, sqlite_decisions=[], postgres_decisions=[],
                sqlite_pin=pg_store.pin, postgres_pin=pg_store.pin,
                policy=policy, frozen_policy=policy)
        # A genuine same-backend publication for another graph also refuses.
        _, _, unrelated = _store(tmp_path, "unrelated-sqlite", unrelated=True)
        with pytest.raises(BodyStudyError, match="pin_graph_mismatch"):
            prepare_bound_body_study(
                catalog, sqlite_decisions=[], postgres_decisions=[],
                sqlite_pin=unrelated.pin, postgres_pin=pg_store.pin,
                policy=policy, frozen_policy=policy)
        _, _, unrelated_pg = _store(tmp_path, "unrelated-postgres", postgres=database, unrelated=True)
        with pytest.raises(BodyStudyError, match="pin_graph_mismatch"):
            prepare_bound_body_study(
                catalog, sqlite_decisions=[], postgres_decisions=[],
                sqlite_pin=sqlite_store.pin, postgres_pin=unrelated_pg.pin,
                policy=policy, frozen_policy=policy)
        with pytest.raises(BodyStudyError, match="sqlite_pin_not_issued"):
            prepare_bound_body_study(
                catalog, sqlite_decisions=[], postgres_decisions=[],
                sqlite_pin=replace(sqlite_store.pin), postgres_pin=pg_store.pin,
                policy=policy, frozen_policy=policy)
        with pytest.raises(BodyStudyError, match="policy_drift"):
            prepare_bound_body_study(
                catalog, sqlite_decisions=[], postgres_decisions=[],
                sqlite_pin=sqlite_store.pin, postgres_pin=pg_store.pin,
                policy=policy,
                frozen_policy=replace(policy, max_projected_files=policy.max_projected_files - 1))
        # After the live SQLite publication advances, the old pin is stale.
        old_sqlite_pin = sqlite_store.pin
        sqlite_store.refresh()
        with pytest.raises(BodyStudyError, match="^body_study:sqlite_pin_not_current$"):
            prepare_bound_body_study(
                catalog,
                sqlite_decisions=sqlite_decisions,
                postgres_decisions=pg_decisions,
                sqlite_pin=old_sqlite_pin, postgres_pin=pg_store.pin,
                policy=policy, frozen_policy=policy)
        fresh_sqlite = BodyStoreFixture(sqlite_store.published)
        with pytest.raises(BodyStudyError, match="^body_study:pin_generation_mismatch$"):
            prepare_bound_body_study(
                catalog, sqlite_decisions=[], postgres_decisions=[],
                sqlite_pin=fresh_sqlite.pin, postgres_pin=pg_store.pin,
                policy=policy, frozen_policy=policy)
