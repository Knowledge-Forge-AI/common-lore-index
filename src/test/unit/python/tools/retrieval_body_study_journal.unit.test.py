"""Durable chronology and exposure boundaries for a body-study attempt."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from repomap_test_support.retrieval_body_study_fixtures import quality_inputs
from retrieval_body_study_attempt_journal import BodyAttemptJournal, read_attempt_journal
from retrieval_body_study_journal_io import JournalRefusal, canonical, digest
from retrieval_body_study_observation import build_body_failure_envelope, write_body_failure_envelope
from retrieval_body_study_recovery import classify_successor, select_primary_attempt
from retrieval_body_study_values import ARMS


def _identity():
    return {"attempt_id": "unit-body-attempt-1", **{name: "sha256:" + "a" * 64 for name in (
        "input_pin_sha256", "source_closure_sha256", "treatment_sha256", "protocol_sha256",
        "candidate_snapshot_sha256", "plan_sha256")}}


def _journal(tmp_path, *, low_floor=False):
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    meta, _, _, common, slots = quality_inputs(common_count=3 if low_floor else None)
    journal = BodyAttemptJournal.create(tmp_path / "journal", identity=_identity(),
        task_ids=tuple(meta), functional_only=True)
    return journal, meta, common, slots


def _terminal(journal, outcome, **overrides):
    flags = {"integrity_ok": True, "ownership_ok": True, "cleanup_ok": True} | overrides
    if outcome in {"incomplete_technical", "inadmissible"}:
        envelope = build_body_failure_envelope(
            attempt_id=_identity()["attempt_id"], failure_code=outcome, failure_stage="unit",
            cleanup=flags, journal=journal, functional_only=True,
        )
        flags["failure_envelope_sha256"] = write_body_failure_envelope(journal.root.parent, envelope)
        flags["failure_stage"] = "unit"
    return journal.terminal(outcome=outcome, report_sha256="sha256:" + "b" * 64, **flags)


def test_complete_negative_is_retained_as_primary_without_retry(tmp_path: Path):
    journal, meta, common, slots = _journal(tmp_path)
    journal.freeze_common(common)
    for slot in slots:
        journal.before_slot(slot["task_id"], slot["arm"], slot["repetition"])
        journal.observe(slot, case_kind=meta[slot["task_id"]]["case_kind"])
    terminal = _terminal(journal, "signal_not_demonstrated")
    rows = read_attempt_journal(journal.root, identity=_identity(), task_ids=tuple(meta),
        expected_common_identity=common.identity, functional_only=True)
    assert len(rows) == 867
    assert rows[1]["event"] == "common_set_frozen"
    assert terminal["quality_observed"] is True
    assert terminal["possible_unknown_exposure"] is False
    assert terminal["observed_slots"] == 432
    assert terminal["execution_authority_granted"] is False
    assert select_primary_attempt([{"outcome": terminal["outcome"]},
                                   {"outcome": "calibration_signal_present"}]) == 0
    with pytest.raises(JournalRefusal, match="already_terminal"):
        _terminal(journal, "signal_not_demonstrated")


def test_valid_low_floor_has_no_query_or_fabricated_observation(tmp_path: Path):
    journal, meta, common, _ = _journal(tmp_path, low_floor=True)
    journal.freeze_common(common)
    with pytest.raises(JournalRefusal, match="before_common_floor"):
        journal.before_slot(next(iter(meta)), ARMS[0], 1)
    terminal = _terminal(journal, "inconclusive_floor")
    assert terminal["observed_slots"] == 0
    assert terminal["quality_observed"] is False
    assert terminal["possible_unknown_exposure"] is False
    rows = read_attempt_journal(journal.root, identity=_identity(), task_ids=tuple(meta),
        expected_common_identity=common.identity, functional_only=True)
    assert [row["event"] for row in rows] == ["attempt_created", "common_set_frozen", "attempt_terminal"]


def test_functional_seal_adds_generation_without_mutating_original_policy_rows(tmp_path: Path):
    journal, meta, common, slots = _journal(tmp_path)
    journal.freeze_common(common)
    slot = dict(slots[0])
    assert "generation" not in slot
    journal.before_slot(slot["task_id"], slot["arm"], slot["repetition"])
    journal.observe(slot, case_kind=meta[slot["task_id"]]["case_kind"])
    sealed = journal.retained_observations()[0]
    assert sealed["slot"]["generation"] == 1
    assert "generation" not in sealed["actual"]
    assert "generation" not in sealed["policy"]


def test_pending_work_is_unknown_exposure_and_cannot_be_a_valid_result(tmp_path: Path):
    journal, _, common, slots = _journal(tmp_path)
    journal.freeze_common(common)
    slot = slots[0]
    journal.before_slot(slot["task_id"], slot["arm"], slot["repetition"])
    with pytest.raises(JournalRefusal, match="valid_result_unproved"):
        _terminal(journal, "signal_not_demonstrated")
    terminal = _terminal(journal, "incomplete_technical", integrity_ok=None, cleanup_ok=None)
    assert terminal["quality_observed"] is False
    assert terminal["possible_unknown_exposure"] is True
    assert terminal["pending_slots"] == 1
    assert classify_successor(outcome="incomplete_technical", exposed_to_scores=True,
        defect_class="transport", scientific_fingerprints_identical=True,
        integrity_restored=None, rank_blind_review_done=True, operator_disposition=False,
        ownership_restored=True, cleanup_proven=None) == "refused_until_integrity_ownership_cleanup_proven"


def test_terminal_cannot_join_another_frozen_common_set(tmp_path: Path):
    journal, _, common, _ = _journal(tmp_path, low_floor=True)
    journal.freeze_common(common)
    with pytest.raises(JournalRefusal, match="terminal_common_mismatch"):
        _terminal(journal, "inconclusive_floor", expected_common_identity="sha256:" + "f" * 64)
    assert len(tuple(journal.root.glob("*.json"))) == 2
    result = _terminal(journal, "inconclusive_floor", expected_common_identity=common.identity)
    assert result["observed_slots"] == 0


def test_real_refusal_observation_marks_quality_exposure(tmp_path: Path):
    journal, meta, common, slots = _journal(tmp_path)
    journal.freeze_common(common)
    slot = next(slot for slot in slots if meta[slot["task_id"]]["case_kind"] == "privacy")
    journal.before_slot(slot["task_id"], slot["arm"], slot["repetition"])
    journal.observe(slot, case_kind="privacy")
    terminal = _terminal(journal, "incomplete_technical")
    assert terminal["quality_observed"] is True
    assert terminal["possible_unknown_exposure"] is False


def test_incomplete_closed_population_cannot_claim_a_valid_negative(tmp_path: Path):
    journal, meta, common, slots = _journal(tmp_path)
    journal.freeze_common(common)
    slot = slots[0]
    journal.before_slot(slot["task_id"], slot["arm"], 1)
    journal.observe(slot, case_kind=meta[slot["task_id"]]["case_kind"])
    with pytest.raises(JournalRefusal, match="population_incomplete"):
        _terminal(journal, "signal_not_demonstrated")


@pytest.mark.parametrize("task,arm,rep", [("foreign", ARMS[0], 1),
    ("body-01", "foreign-arm", 1), ("body-01", ARMS[0], True), ("body-01", ARMS[0], 0)])
def test_foreign_or_malformed_slot_never_starts(tmp_path: Path, task, arm, rep):
    journal, _, common, _ = _journal(tmp_path)
    journal.freeze_common(common)
    with pytest.raises(JournalRefusal):
        journal.before_slot(task, arm, rep)
    assert len(tuple(journal.root.glob("*.json"))) == 2


def test_no_query_before_freeze_no_duplicate_or_reordered_observations(tmp_path: Path):
    journal, meta, common, slots = _journal(tmp_path)
    slot = slots[0]
    with pytest.raises(JournalRefusal, match="before_common_floor"):
        journal.before_slot(slot["task_id"], slot["arm"], 1)
    journal.freeze_common(common)
    with pytest.raises(JournalRefusal, match="observation_order"):
        journal.observe(slot, case_kind=meta[slot["task_id"]]["case_kind"])
    journal.before_slot(slot["task_id"], slot["arm"], 1)
    with pytest.raises(JournalRefusal, match="duplicate_slot_start"):
        journal.before_slot(slot["task_id"], slot["arm"], 1)
    with pytest.raises(JournalRefusal, match="not_prospective"):
        journal.freeze_common(common)


def test_changed_prior_bytes_and_noncontiguous_partial_append_stop_new_work(tmp_path: Path):
    journal, _, common, _ = _journal(tmp_path)
    path = journal.root / "00000.json"
    path.write_bytes(path.read_bytes().replace(b"unit-body-attempt-1", b"unit-body-attempt-2"))
    with pytest.raises(JournalRefusal, match="prior_event_changed"):
        journal.freeze_common(common)
    second, _, common, _ = _journal(tmp_path / "second")
    (second.root / "00002.json").write_bytes(b"partial")
    with pytest.raises(JournalRefusal, match="noncontiguous"):
        second.freeze_common(common)


def test_root_replacement_foreign_member_and_lock_alias_are_not_recovered(tmp_path: Path):
    journal, _, common, _ = _journal(tmp_path)
    original = journal.root.with_name("retained-original")
    journal.root.rename(original)
    journal.root.mkdir(mode=0o700)
    with pytest.raises(JournalRefusal, match="root_replaced"):
        journal.freeze_common(common)
    foreign, _, common, _ = _journal(tmp_path / "foreign")
    (foreign.root / "other.json").write_bytes(b"{}")
    with pytest.raises(JournalRefusal, match="unknown_member"):
        foreign.freeze_common(common)
    alias, _, common, _ = _journal(tmp_path / "alias")
    lock = alias.root / ".lock"
    lock.rename(alias.root / "retained-lock")
    lock.symlink_to(alias.root / "retained-lock")
    with pytest.raises(JournalRefusal, match="custody"):
        alias.freeze_common(common)


def test_owning_identity_and_task_population_are_bound_when_reading(tmp_path: Path):
    journal, meta, _, _ = _journal(tmp_path)
    altered = _identity() | {"attempt_id": "unit-body-attempt-2"}
    with pytest.raises(JournalRefusal, match="chain_join"):
        read_attempt_journal(journal.root, identity=altered, task_ids=tuple(meta), functional_only=True)
    tasks = list(meta)
    tasks[0] = "different-task"
    with pytest.raises(JournalRefusal, match="creation_join"):
        read_attempt_journal(journal.root, identity=_identity(), task_ids=tuple(tasks), functional_only=True)
    with pytest.raises(JournalRefusal, match="root_used"):
        BodyAttemptJournal.create(journal.root, identity=_identity(), task_ids=tuple(meta), functional_only=True)


def test_forged_well_hashed_unknown_event_is_not_valid_history(tmp_path: Path):
    journal, meta, common, _ = _journal(tmp_path, low_floor=True)
    journal.freeze_common(common)
    _terminal(journal, "inconclusive_floor")
    path = journal.root / "00001.json"
    event = json.loads(path.read_bytes())
    event["event"] = "unreviewed_reset"
    value = copy.deepcopy(event)
    del value["event_sha256"]
    event["event_sha256"] = digest(value)
    path.write_bytes(canonical(event) + b"\n")
    terminal_path = journal.root / "00002.json"
    terminal = json.loads(terminal_path.read_bytes())
    terminal["previous_sha256"] = event["event_sha256"]
    del terminal["event_sha256"]
    terminal["event_sha256"] = digest(terminal)
    terminal_path.write_bytes(canonical(terminal) + b"\n")
    with pytest.raises(JournalRefusal, match="unknown_event"):
        read_attempt_journal(journal.root, identity=_identity(), task_ids=tuple(meta),
            expected_common_identity=common.identity, functional_only=True)


def test_unclosed_history_and_wrong_common_identity_never_pass_readback(tmp_path: Path):
    journal, meta, common, _ = _journal(tmp_path, low_floor=True)
    with pytest.raises(JournalRefusal, match="incomplete_terminal"):
        read_attempt_journal(journal.root, identity=_identity(), task_ids=tuple(meta), functional_only=True)
    journal.freeze_common(common)
    _terminal(journal, "inconclusive_floor")
    with pytest.raises(JournalRefusal, match="expected_common_join"):
        read_attempt_journal(journal.root, identity=_identity(), task_ids=tuple(meta),
            expected_common_identity="sha256:" + "c" * 64, functional_only=True)


def test_journal_does_not_accept_an_unrelated_common_population(tmp_path: Path):
    meta, _, _, common, _ = quality_inputs()
    ids = tuple("foreign-" + task for task in meta)
    journal = BodyAttemptJournal.create(tmp_path / "journal", identity=_identity(),
        task_ids=ids, functional_only=True)
    with pytest.raises(JournalRefusal, match="common_population_join"):
        journal.freeze_common(common)


def test_functional_mode_cannot_claim_registered_input_or_accept_bare_slots(tmp_path: Path):
    from retrieval_body_study_values import REGISTERED_INPUT_PIN_SHA256
    meta = quality_inputs()[0]
    with pytest.raises(JournalRefusal, match="input_class_mode"):
        BodyAttemptJournal.create(tmp_path / "wrong", identity=_identity(), task_ids=tuple(meta))
    with pytest.raises(JournalRefusal, match="input_class_mode"):
        BodyAttemptJournal.create(tmp_path / "registered",
            identity=_identity() | {"input_pin_sha256": REGISTERED_INPUT_PIN_SHA256},
            task_ids=tuple(meta), functional_only=True)
    journal, _, common, slots = _journal(tmp_path)
    journal.freeze_common(common)
    slot = slots[0]
    journal.before_slot(slot["task_id"], slot["arm"], slot["repetition"])
    with pytest.raises(JournalRefusal, match="observation_fields"):
        journal.observe({key: slot[key] for key in ("task_id", "arm", "repetition")},
                        case_kind="body_positive")
