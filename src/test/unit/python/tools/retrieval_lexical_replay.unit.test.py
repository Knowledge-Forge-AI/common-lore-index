"""Proof of the lexical replay's closed launcher and immutable scheduling."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from offline_qualification_values import BASE_CONFIG, SDK_IMAGE
from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from repomap_test_support import retrieval_body_study_pg
from repomap_test_support.retrieval_body_fixtures import make_body_policy
from repomap_test_support.retrieval_body_study_runtime_fixtures import functional_body_catalog
from retrieval_body_catalog import BodyCase
import retrieval_lexical_replay as replay
from retrieval_lexical_replay import CANDIDATE, LEGACY, ReplayIntegrityError, schedule
from retrieval_lexical_replay_execution import ReplayJournal
from retrieval_semantic_arm_validation import FileIdentityTuple
from retrieval_supplement_values import SupplementError
import retrieval_body_study_inputs
import run_lexical_replay as driver
from run_lexical_replay import _terminal_disposition
from runner_lexical_replay import INPUT_PIN, LIMITS, PHASE, PLAN_SCHEMA, ROLE, load_inner, validate_plan
import test_sandbox_capacity
import test_sandbox_contract


@pytest.fixture
def parent_plan() -> dict[str, object]:
    return {
        "schema": PLAN_SCHEMA, "phase": PHASE, "role": ROLE, "attempt": "lexical-unit",
        "sdk_image": SDK_IMAGE, "core_image": BASE_CONFIG, "candidate_head": "a" * 40,
        "candidate_snapshot": {"tools/run_lexical_replay.py": {"mode": 420, "sha256": "b" * 64}},
        "protocol_sha256": "c" * 64, "input_pin_sha256": INPUT_PIN, "schedule": LIMITS.copy(),
        "approval_sha256": "d" * 64, "review_sha256": "e" * 64,
        "archive": {"bytes": 512, "sha256": "f" * 64},
    }


@pytest.mark.parametrize(("field", "value", "message"), [
    ("role", "study", "closed plan shape"),
    ("sdk_image", "sha256:" + "9" * 64, "runtime or input identity"),
    ("input_pin_sha256", "sha256:" + "9" * 64, "runtime or input identity"),
    ("candidate_head", "main", "source identity"),
    ("schedule", LIMITS | {"ranked_calls": 49}, "finite schedule"),
    ("schedule", LIMITS | {"sdk_starts": True}, "finite schedule"),
    ("archive", {"bytes": -1, "sha256": "0" * 64}, "core archive descriptor"),
    ("worker", "model", "closed plan shape"),
])
def test_no_foreign_profile_or_unsealed_treatment(
    parent_plan: dict[str, object], field: str, value: object, message: str
) -> None:
    validate_plan(parent_plan)
    with pytest.raises(RuntimeError, match=message):
        validate_plan(parent_plan | {field: value})


def test_inner_refuses_host_workspace(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="authenticated inner sandbox"):
        load_inner("a" * 64, root=tmp_path)


def test_schedule_alternates_and_duplicates_fail() -> None:
    cases: tuple[BodyCase, ...] = functional_body_catalog().cases
    first, second = cases[:2]
    result = schedule((second, first))
    assert [slot.case.task.task_id for slot in result] == [
        first.task.task_id, first.task.task_id, second.task.task_id, second.task.task_id,
    ]
    assert [slot.arm for slot in result] == [LEGACY, CANDIDATE, CANDIDATE, LEGACY]
    with pytest.raises(ReplayIntegrityError, match="case_identity_or_count"):
        schedule((first, first))


def test_started_records_are_exclusive(tmp_path: Path) -> None:
    journal = ReplayJournal(tmp_path)
    journal.retain("slot-01-started.json", {"arm": LEGACY})
    path = tmp_path / "slot-01-started.json"
    original = path.read_bytes()
    with pytest.raises(SupplementError, match="destination_exists"):
        journal.retain("slot-01-started.json", {"arm": CANDIDATE})
    assert path.read_bytes() == original


@dataclass(frozen=True)
class _ClaimedRequest(LexicalQueryRequest):
    publication_bundle_id: str | None = None
    snapshot_manifest_id: str | None = None


@pytest.mark.parametrize("arm", [LEGACY, CANDIDATE])
@pytest.mark.parametrize("field", ["publication_bundle_id", "snapshot_manifest_id"])
def test_caller_identity_refused_before_freeze(
    monkeypatch: pytest.MonkeyPatch, arm: str, field: str,
) -> None:
    case = functional_body_catalog().cases[0]
    request = _ClaimedRequest("Which synthetic canopy observation?",
                             selected_binding_ids=case.task.selected_bindings,
                             limit=10,
                             publication_bundle_id="caller-claim" if field == "publication_bundle_id" else None,
                             snapshot_manifest_id="caller-claim" if field == "snapshot_manifest_id" else None)
    monkeypatch.setattr(replay, "prepare_fts_request", lambda *_, **__: request)
    with pytest.raises(ReplayIntegrityError, match="caller_supplied_identity"):
        replay._prepare_request(replay.Slot(case, arm), make_body_policy())


def test_terminal_custody_precedence() -> None:
    root = {"outcome": "inadmissible", "reason": "stale_lexical_fence", "quality": None}
    assert _terminal_disposition(root, "incomplete_technical", "inner_cleanup_unproven") == root
    assert _terminal_disposition(root, "inadmissible", "source_drift") == root
    complete = {"outcome": "complete_descriptive", "reason": None, "quality": {"test": 1}}
    assert _terminal_disposition(complete, "incomplete_technical", "inner_cleanup_unproven") == {
        "outcome": "incomplete_technical", "reason": "inner_cleanup_unproven", "quality": None,
    }


def test_first_hit_order_is_keyed_by_binding_and_file() -> None:
    first = FileIdentityTuple("binding-a", "snapshot-1", "public.py")
    duplicate = FileIdentityTuple("binding-a", "snapshot-2", "public.py")
    distinct = FileIdentityTuple("binding-b", "snapshot-3", "public.py")
    assert replay._first_hit_per_file((first, duplicate, distinct, first)) == (first, distinct)


def test_terminal_record_survives_exhausted_nonterminal_allowance(tmp_path: Path) -> None:
    journal = ReplayJournal(tmp_path)
    for index in range(5):
        journal.retain(f"slot-{index + 1:02d}-observed.json", {"body": "x" * (512 * 1024 - 20)})
    with pytest.raises(RuntimeError, match="receipt exceeds bounded size"):
        journal.retain("excess.json", {"body": "x" * 2048})
    journal.retain("terminal.json", {"outcome": "incomplete_technical"})
    assert (tmp_path / "terminal.json").is_file()


def test_inner_preserves_integrity_error_through_raising_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def disposable(*_args: Any, **_kwargs: Any) -> Iterator[tuple[object, dict[str, Any]]]:
        yield object(), {}
        raise OSError("synthetic cleanup failure")

    snapshot = SimpleNamespace(catalog=functional_body_catalog(), pin_sha256=INPUT_PIN)
    frame = SimpleNamespace(require_current=lambda: snapshot)
    report = tmp_path / "report"
    monkeypatch.setattr(test_sandbox_contract, "INNER_REPORT_ROOT", report)
    monkeypatch.setattr(test_sandbox_capacity, "read_binding", Mock())
    monkeypatch.setattr(test_sandbox_capacity, "validate_scratch_capacity", Mock())
    monkeypatch.setattr(retrieval_body_study_inputs, "load_body_study_inputs", lambda *_a, **_kw: frame)
    monkeypatch.setattr(retrieval_body_study_pg, "disposable_socket_postgres", disposable)
    monkeypatch.setattr(driver, "load_inner", lambda *_a, **_kw: {
        "attempt": "lexical-unit", "candidate_head": "a" * 40,
        "candidate_snapshot": {}, "protocol_sha256": "b" * 64, "schedule": LIMITS,
    })
    monkeypatch.setattr(driver, "check_source", Mock())
    monkeypatch.setattr(driver, "_run_publication_replay",
                        Mock(side_effect=ReplayIntegrityError("registered_public_binding_scope")))
    monkeypatch.delenv("CLOE_PGVECTOR_OFFLINE_PLAN", raising=False)
    monkeypatch.delenv("REPOMAP_TEST_PG_IMAGE", raising=False)
    assert driver._inner() == 2
    terminal = json.loads((report / "terminal.json").read_bytes())
    assert terminal["outcome"] == "inadmissible"
    assert terminal["reason"] == "registered_public_binding_scope"
    assert terminal["inner_cleanup_proven"] is False
    assert terminal["technical_faults"] == [
        {"stage": "publication_and_replay", "error_type": "ReplayIntegrityError"},
        {"stage": "postgres_cleanup", "error_type": "OSError"},
        {"stage": "postgres_cleanup", "error_type": "missing_cleanup_evidence"},
    ]
