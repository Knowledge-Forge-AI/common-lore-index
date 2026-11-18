"""Functional publication slots stop on a lost lease without padding work.

The first baseline rows use real SQLite-native, PostgreSQL graph-policy, and
PostgreSQL lexical operations.  The catalog, admissions, and common set stay
functional/inert; no worker receipt, source admission, model origin, absence,
or scientific acceptance is inferred from these operations.
"""
from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping
import json
from types import SimpleNamespace
from typing import Any

import pytest

from common_lore_index.ops.config_local import load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_service import build_graph_lexical_generation
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
    publish_body_fixture, write_body_home,
)
from repomap_test_support.retrieval_body_study_fixtures import PIN
from repomap_test_support.retrieval_body_study_slots import _baseline
from retrieval_body_admission_rules import evaluate_body_case
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_corpus import materialize_body_corpus, parse_body_corpus
from retrieval_body_study_attempt_journal import BodyAttemptJournal, read_attempt_journal
from retrieval_body_study_common import select_common_positives
from retrieval_body_study_journal_io import JournalRefusal, digest
from retrieval_body_study_observation import build_body_failure_envelope, write_body_failure_envelope
from retrieval_body_study_preflight import CommonFreezeReceipt
from retrieval_body_study_slots import execute_body_slots
from retrieval_body_study_values import (
    ARM_REPRESENTATIONS,
    ARMS,
    COMPARISON_ARMS,
    digest_value,
)
from retrieval_body_study_join import prepare_bound_body_study
from retrieval_body_values import canonical_bytes
from retrieval_evaluation_views import materialize_native_search_view


_DIGESTS = tuple("sha256:" + letter * 64 for letter in "abcdef")


def _publish(root: Path, *, postgres: Any = None) -> tuple[Any, Any, BodyStoreFixture]:
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
    root.mkdir(mode=0o700)
    working = root / "source"
    working.mkdir(mode=0o700)
    materialize_body_corpus(corpus, working)
    home = root / "home"
    write_body_home(home, working, identity=identity, bindings=bindings,
                    policy=policy, postgres=postgres)
    public_aliases = {row.alias for row in policy.bindings.values()
                      if row.resolved_binding_privacy == "public-dev"}
    assert public_aliases
    config_file = home / "body.cloe.toml"
    header, *blocks = config_file.read_text().split("[[graphs.source_bindings]]")
    selected = [block for block in blocks
                if any("alias = " + json.dumps(alias) + "\n" in block for alias in public_aliases)]
    assert len(selected) == len(public_aliases)
    config_file.write_text(header + "".join("[[graphs.source_bindings]]" + block for block in selected))
    published = publish_body_fixture(home, identity=identity, policy=policy,
        psql_command="psql" if postgres is None else postgres.psql_command)
    return catalog, policy, BodyStoreFixture(
        published,
        psql_command="psql" if postgres is None else postgres.psql_command,
    )


def _decisions(catalog: Any, policy: Any, store: BodyStoreFixture, backend: str) -> list[dict[str, Any]]:
    return [
        {
            **evaluate_body_case(
                catalog,
                case,
                arm=arm,
                policy=policy,
                frozen_policy=policy,
                publication_pin=store.pin,
                observed_backend=backend,
            ),
            "input_pin_sha256": PIN,
        }
        for case in catalog.cases
        for arm in ARMS
    ]


def _functional_common(catalog: Any) -> Any:
    meta = [
        {
            "task_id": case.task.task_id,
            "family": case.task.family,
            "intent_id": case.to_dict()["intent_id"],
            "wording_stratum": case.to_dict()["wording_stratum"],
            "case_kind": case.kind,
        }
        for case in catalog.cases
        if case.kind == "body_positive"
    ]
    eligibility = [
        {
            "task_id": row["task_id"],
            "arm": arm,
            "admitted": True,
            "captured_text_reachable": True,
            "token_count": 10,
            "truncated": False,
            "representation_identity": ARM_REPRESENTATIONS[arm],
        }
        for row in meta
        for arm in COMPARISON_ARMS
    ]
    common = select_common_positives(meta, eligibility)
    assert common.floor_passed is True
    return common


def _freeze_receipt(common: Any, eligibility: list[dict[str, Any]]) -> CommonFreezeReceipt:
    eligibility_identity = digest_value(eligibility)
    worker_receipts = (_DIGESTS[0], _DIGESTS[1])
    freeze_identity = digest_value({
        "common_identity": common.identity,
        "eligibility_identity": eligibility_identity,
        "worker_receipt_identities": list(worker_receipts),
        "frozen_before_ranking": True,
    })
    receipt = CommonFreezeReceipt(
        common,
        eligibility_identity,
        worker_receipts,
        True,
        freeze_identity,
    )
    receipt.require_current()
    return receipt


def _journal_identity() -> dict[str, str]:
    return {
        "attempt_id": "slot-boundary-authority-loss",
        "input_pin_sha256": PIN,
        "source_closure_sha256": _DIGESTS[2],
        "treatment_sha256": _DIGESTS[3],
        "protocol_sha256": _DIGESTS[4],
        "candidate_snapshot_sha256": _DIGESTS[5],
        "plan_sha256": _DIGESTS[0],
    }


def test_functional_bound_slots_retain_failed_and_unstarted_work_after_lease_loss(
    tmp_path: Path,
) -> None:
    with temporary_postgres() as database:
        catalog, policy, sqlite_store = _publish(tmp_path / "sqlite")
        _same_catalog, _same_policy, postgres_store = _publish(
            tmp_path / "postgres", postgres=database
        )
        sqlite_decisions = _decisions(catalog, policy, sqlite_store, "sqlite")
        postgres_decisions = _decisions(catalog, policy, postgres_store, "postgresql")
        matrix = prepare_bound_body_study(
            catalog,
            sqlite_decisions=sqlite_decisions,
            postgres_decisions=postgres_decisions,
            sqlite_pin=sqlite_store.pin,
            postgres_pin=postgres_store.pin,
            policy=policy,
            frozen_policy=policy,
        )
        assert catalog.registered is False
        assert matrix["functional_only"] is True
        assert matrix["origin_proven"] is False
        assert matrix["execution_authority_granted"] is False
        assert matrix["sqlite_pin_identity"] != matrix["postgres_pin_identity"]

        native_view = materialize_native_search_view(
            tmp_path / "native-view",
            manifest=sqlite_store.published.manifest,
            bundle=sqlite_store.published.bundle,
            store=sqlite_store.published.store,
            policy=policy,
            selected_aliases=("oak", "elm"),
        )
        postgres_config = load_graph_registry_config_home(postgres_store.published.home_root)
        assert isinstance(postgres_config, OpsConfig)
        lexical = build_graph_lexical_generation(
            postgres_config, catalog.identity.graph_id, authorize_body=True,
            selected_binding_ids=tuple(catalog.cases[0].task.selected_bindings),
        )
        assert lexical.result == "published", (lexical.result, lexical.refusal_code)
        stores = SimpleNamespace(
            sqlite_store=sqlite_store,
            postgres_store=postgres_store,
            native_view=native_view,
            ops_config=postgres_config,
        )

        common = _functional_common(catalog)
        common_payload = common.to_dict()
        assert common_payload["tokenizer_origin_proven"] is False
        assert common_payload["trusted_controller_admission"] is False
        assert common_payload["similarity_computed"] is False
        eligibility = [
            {
                "task_id": row["task_id"],
                "arm": arm,
                "admitted": True,
                "captured_text_reachable": True,
                "token_count": 10,
                "truncated": False,
                "representation_identity": ARM_REPRESENTATIONS[arm],
            }
            for row in common.planned
            for arm in COMPARISON_ARMS
        ]
        freeze = _freeze_receipt(common, eligibility)
        preparation = SimpleNamespace(catalog=catalog, matrix=matrix)
        journal_root = tmp_path / "slot-journal"
        journal = BodyAttemptJournal.create(
            journal_root,
            identity=_journal_identity(),
            task_ids=tuple(case.task.task_id for case in catalog.cases),
            functional_only=True,
        )
        journal.freeze_common(common)

        observed_queries = 0
        guard_calls = 0

        class LostLease(RuntimeError):
            pass

        def source_guard() -> None:
            nonlocal guard_calls
            sqlite_store.pin.revalidate_current()
            postgres_store.pin.revalidate_current()
            guard_calls += 1
            if guard_calls >= 12:
                raise LostLease("functional publication lease lost")

        def execute(row: Mapping[str, Any], case: Any) -> dict[str, Any]:
            nonlocal observed_queries
            if observed_queries >= 9:
                source_guard()
            if row["arm"] not in ARMS[:3]:
                raise AssertionError("authority loss must precede vector or fusion work")
            result = dict(_baseline(row, case.task, stores=stores, policy=policy))
            envelope = result["envelope"]
            if result["execution_state"] == "unavailable":
                result["unavailable_reason"] = envelope.unavailable_reason or "baseline_unavailable"
            if result["execution_state"] == "refused":
                result["refusal_code"] = envelope.refusal_code or "baseline_refused"
            envelope.validate()
            observed_queries += 1
            return result

        batch = execute_body_slots(
            preparation=preparation,
            common_freeze=freeze,
            pins={"sqlite": sqlite_store.pin, "postgresql": postgres_store.pin},
            operation=journal,
            execute=execute,
            source_guard=source_guard,
        )

    failed_slots = [
        (row.get("task_id"), row.get("arm"), row.get("repetition"), row.get("error_code"))
        for row in batch.actual_slots if row.get("execution_state") == "failed"
    ]
    policy_failed = [
        (row.get("task_id"), row.get("arm"), row.get("repetition"))
        for row in batch.policy_slots if row.get("execution_state") == "failed"
    ]
    assert guard_calls == 12, (
        f"guard_calls={guard_calls} observed_queries={observed_queries} "
        f"failed_slots={failed_slots} policy_failed={policy_failed}"
    )
    assert observed_queries == 9
    assert batch.actual_slots[0]["execution_state"] == "completed"
    assert batch.actual_slots[0]["envelope"]["capability_state"] in {"available", "partial"}
    assert batch.actual_slots[0]["returned_hit_count"] > 0
    assert batch.actual_slots[3]["execution_state"] == "unavailable"
    assert batch.actual_slots[3]["envelope"]["capability_state"] == "unavailable"
    assert not batch.actual_slots[3]["snapshot_hits"]
    assert batch.actual_slots[6]["execution_state"] == "completed"
    assert batch.actual_slots[6]["envelope"]["capability_state"] in {"available", "partial"}
    # A completed lexical query can truthfully have no hits for this wording.
    # Positive lexical ranking is covered by the product-boundary owner.
    assert batch.actual_slots[6]["returned_hit_count"] == len(batch.actual_slots[6]["snapshot_hits"])
    assert batch.unstarted_slots == 422
    assert batch.policy_slots[9]["execution_state"] == "failed"
    assert batch.actual_slots[9]["error_code"] == "LostLease"
    assert all(row["execution_state"] == "unstarted" for row in batch.policy_slots[10:])
    assert all(row.get("absence_answer_correct") is not True for row in batch.policy_slots)
    assert all(row.get("execution_authority_granted") is not True for row in batch.actual_slots)
    assert batch.actual_slots[3]["origin_proven"] is True
    assert batch.actual_slots[9]["origin_proven"] is False

    failure = build_body_failure_envelope(
        attempt_id="functional-authority-loss", failure_code="incomplete_technical",
        failure_stage="slot_execution", cleanup={"cleanup_ok": False,
        "relations_clean": False, "container_absent": False,
        "integrity_ok": False, "ownership_ok": False}, journal=journal,
        slots=batch, functional_only=True,
    )
    failure_sha256 = write_body_failure_envelope(journal_root.parent, failure)
    terminal = journal.terminal(
        outcome="incomplete_technical",
        report_sha256=digest({"batch": "functional-authority-loss", "slots": 432}),
        integrity_ok=False,
        ownership_ok=False,
        cleanup_ok=False,
        expected_common_identity=common.identity,
        failure_envelope_sha256=failure_sha256,
        failure_stage="slot_execution",
    )
    assert terminal["execution_authority_granted"] is False
    assert terminal["origin_proven"] is False
    retained = read_attempt_journal(
        journal_root,
        identity=_journal_identity(),
        task_ids=tuple(case.task.task_id for case in catalog.cases),
        expected_common_identity=common.identity,
        functional_only=True,
    )
    payload = retained[-1]["payload"]
    assert payload["outcome"] == "incomplete_technical"
    assert payload["pending_slots"] == 1
    assert payload["observed_slots"] == 9
    assert payload["possible_unknown_exposure"] is True
    with pytest.raises(JournalRefusal, match="journal_already_terminal"):
        unstarted = batch.actual_slots[10]
        journal.before_slot(
            str(unstarted["task_id"]), str(unstarted["arm"]), int(unstarted["repetition"]),
        )
