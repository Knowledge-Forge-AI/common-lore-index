"""Real publication grading joined to complete, low-floor, and open journals."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from common_lore_index.retrieval.query_contracts import EvaluationLocator
from repomap_test_support.postgres_harness import temporary_postgres
from repomap_test_support.retrieval_body_fixtures import (
    make_body_bindings,
    make_body_catalog_payload,
    make_body_corpus_payload,
    make_body_identity,
    make_body_policy,
)
from repomap_test_support.retrieval_body_publication_fixture import (
    BodyStoreFixture,
    materialize_body_home,
)
from repomap_test_support.retrieval_body_study_fixtures import PIN, quality_inputs
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_corpus import parse_body_corpus
from retrieval_body_study_attempt_journal import BodyAttemptJournal, read_attempt_journal
from retrieval_body_study_grading import grade_snapshot_hits
from retrieval_body_study_journal_io import JournalRefusal, digest
from retrieval_body_values import canonical_bytes
from retrieval_evaluation_schema import GoldTarget
from retrieval_semantic_fusion import FileIdentityTuple


def _store(root: Path, *, postgres: Any = None) -> tuple[Any, BodyStoreFixture]:
    identity = make_body_identity()
    bindings = make_body_bindings()
    policy = make_body_policy()
    catalog = parse_functional_catalog(
        canonical_bytes(make_body_catalog_payload()),
        identity=identity,
        binding_ids=tuple(bindings.values()),
    )
    corpus = parse_body_corpus(
        canonical_bytes(make_body_corpus_payload()),
        identity=identity,
        bindings=bindings,
    )
    published = materialize_body_home(
        root,
        corpus=corpus,
        identity=identity,
        policy=policy,
        postgres=postgres,
        psql_command="psql" if postgres is None else postgres.psql_command,
    )
    return catalog, BodyStoreFixture(
        published,
        psql_command="psql" if postgres is None else postgres.psql_command,
    )


def _grade(catalog: Any, store: BodyStoreFixture) -> Any:
    task = next(case.task for case in catalog.cases if case.kind == "body_positive")
    bindings = make_body_bindings()
    snapshots = store.pin.snapshot_by_binding()
    path = task.gold_targets[0].locator.source_relative_path
    oak = FileIdentityTuple(bindings["oak"], snapshots[bindings["oak"]][1], path)
    elm = FileIdentityTuple(bindings["elm"], snapshots[bindings["elm"]][1], path)
    golds = (
        GoldTarget(EvaluationLocator(bindings["oak"], path), 3, "Functional oak gold."),
        GoldTarget(EvaluationLocator(bindings["elm"], path), 2, "Functional elm gold."),
    )
    grade = grade_snapshot_hits(
        returned_items=(oak, oak, elm),
        gold_targets=golds,
        verified_pin=store.pin,
        task=task,
    )
    assert grade.metrics == grade.inert_metrics
    assert grade.to_dict()["metric_keys_exact"] is True
    assert grade.to_dict()["six_decimal_equal"] is True
    assert grade.projected_files == ((bindings["oak"], path), (bindings["elm"], path))
    return grade


def _journal_identity(attempt_id: str) -> dict[str, str]:
    return {
        "attempt_id": attempt_id,
        "input_pin_sha256": PIN,
        "source_closure_sha256": "sha256:" + "1" * 64,
        "treatment_sha256": "sha256:" + "2" * 64,
        "protocol_sha256": "sha256:" + "3" * 64,
        "candidate_snapshot_sha256": "sha256:" + "4" * 64,
        "plan_sha256": "sha256:" + "5" * 64,
    }


def _complete_negative_journal(root: Path, report_sha256: str) -> tuple[dict[str, Any], ...]:
    meta, _golds, _universes, common, slots = quality_inputs()
    task_ids = tuple(meta)
    journal = BodyAttemptJournal.create(
        root,
        identity=_journal_identity("grading-complete-negative"),
        task_ids=task_ids,
        functional_only=True,
    )
    journal.freeze_common(common)
    for row in slots:
        journal.before_slot(row["task_id"], row["arm"], row["repetition"])
        journal.observe(row, case_kind=meta[row["task_id"]]["case_kind"])
    journal.terminal(
        outcome="signal_not_demonstrated",
        report_sha256=report_sha256,
        integrity_ok=True,
        ownership_ok=True,
        cleanup_ok=True,
        expected_common_identity=common.identity,
    )
    rows = read_attempt_journal(
        root,
        identity=_journal_identity("grading-complete-negative"),
        task_ids=task_ids,
        expected_common_identity=common.identity,
        functional_only=True,
    )
    assert rows[-1]["payload"]["pending_slots"] == 0
    assert rows[-1]["payload"]["observed_slots"] == 432
    return rows


def test_real_sqlite_postgres_grading_and_journal_closure_boundaries(
    tmp_path: Path,
) -> None:
    """Real pins grade the same public files; journal closure stays explicit."""
    sqlite_catalog, sqlite_store = _store(tmp_path / "sqlite")
    sqlite_grade = _grade(sqlite_catalog, sqlite_store)
    with temporary_postgres() as database:
        postgres_catalog, postgres_store = _store(tmp_path / "postgres", postgres=database)
        postgres_grade = _grade(postgres_catalog, postgres_store)

    assert sqlite_grade.metrics == postgres_grade.metrics
    assert sqlite_grade.pin_identity != postgres_grade.pin_identity

    report_sha256 = digest(sqlite_grade.to_dict())
    complete_rows = _complete_negative_journal(tmp_path / "complete-journal", report_sha256)
    assert complete_rows[-1]["payload"]["outcome"] == "signal_not_demonstrated"

    low_meta, _golds, _universes, low_common, _slots = quality_inputs(common_count=0)
    low_root = tmp_path / "low-floor-journal"
    low = BodyAttemptJournal.create(
        low_root,
        identity=_journal_identity("grading-low-floor"),
        task_ids=tuple(low_meta),
        functional_only=True,
    )
    low.freeze_common(low_common)
    low.terminal(
        outcome="inconclusive_floor",
        report_sha256=report_sha256,
        integrity_ok=True,
        ownership_ok=True,
        cleanup_ok=True,
        expected_common_identity=low_common.identity,
    )
    low_rows = read_attempt_journal(
        low_root,
        identity=_journal_identity("grading-low-floor"),
        task_ids=tuple(low_meta),
        expected_common_identity=low_common.identity,
        functional_only=True,
    )
    assert [row["event"] for row in low_rows].count("slot_started") == 0
    assert low_rows[-1]["payload"]["outcome"] == "inconclusive_floor"

    unclosed_root = tmp_path / "unclosed-journal"
    unclosed = BodyAttemptJournal.create(
        unclosed_root,
        identity=_journal_identity("grading-unclosed"),
        task_ids=tuple(low_meta),
        functional_only=True,
    )
    unclosed.freeze_common(low_common)
    with pytest.raises(JournalRefusal, match="journal_incomplete_terminal"):
        read_attempt_journal(
            unclosed_root,
            identity=_journal_identity("grading-unclosed"),
            task_ids=tuple(low_meta),
            expected_common_identity=low_common.identity,
            functional_only=True,
        )
