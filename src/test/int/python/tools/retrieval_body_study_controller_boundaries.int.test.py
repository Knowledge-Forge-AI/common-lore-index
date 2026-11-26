"""Core controller joins for cancellation, failure, cleanup, and reuse.

The reservation and journal are real private files.  This integration owner
does not claim a host process, Docker container, model worker, or native study
origin; those seams remain separately qualified by the authenticated owner.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from common_lore_index_test_support.retrieval_body_study_fixtures import PIN, quality_inputs
from retrieval_body_study_attempt_journal import BodyAttemptJournal, read_attempt_journal
from retrieval_body_study_journal_io import JournalRefusal, digest
from retrieval_body_study_observation import build_body_failure_envelope, write_body_failure_envelope
from retrieval_body_study_reservation import (
    consume_reservation,
    create_reservation,
    read_reservation,
    settle_reservation,
)
from retrieval_body_study_values import ARMS
from retrieval_body_study_contracts import BodyStudyRefusal


_DIGESTS = tuple("sha256:" + letter * 64 for letter in "abcdef")


def _reservation(path: Path, plan_sha256: str, attempt_id: str) -> None:
    create_reservation(
        path,
        plan_sha256=plan_sha256,
        attempt_id=attempt_id,
        source_closure_sha256=_DIGESTS[1],
        treatment_sha256=_DIGESTS[2],
        worker_closure_sha256=_DIGESTS[3],
        protocol_sha256=_DIGESTS[4],
        operator_decision_sha256=_DIGESTS[5],
        nonce_sha256="1" * 64,
    )


def _journal_identity(attempt_id: str) -> dict[str, str]:
    return {
        "attempt_id": attempt_id,
        "input_pin_sha256": PIN,
        "source_closure_sha256": _DIGESTS[1],
        "treatment_sha256": _DIGESTS[2],
        "protocol_sha256": _DIGESTS[3],
        "candidate_snapshot_sha256": _DIGESTS[4],
        "plan_sha256": _DIGESTS[5],
    }


def test_controller_retains_cancelled_failure_and_lost_authority_without_reuse(
    tmp_path: Path,
) -> None:
    """Unknown cleanup and an incomplete journal remain closed and non-reusable."""
    plan_sha256 = _DIGESTS[0]
    reservation = tmp_path / "reservation.json"
    _reservation(reservation, plan_sha256, "controller-boundary-unknown")
    assert reservation.stat().st_mode & 0o777 == 0o600
    assert read_reservation(reservation)["status"] == "reserved"

    consumed = consume_reservation(
        reservation, plan_sha256=plan_sha256, attempt_id="controller-boundary-unknown"
    )
    assert consumed["state"] == "consumed"
    assert consumed["cleanup"] == "in_progress"
    with pytest.raises(BodyStudyRefusal, match="already consumed"):
        consume_reservation(reservation, plan_sha256=plan_sha256)

    blocked = settle_reservation(
        reservation,
        plan_sha256=plan_sha256,
        cleanup="unknown",
        runtime_imports=1,
        native_runtime_loads=1,
        model_workers=1,
        body_study_workers=1,
    )
    assert blocked["status"] == "blocked"
    assert blocked["cleanup"] == "unknown"
    with pytest.raises(BodyStudyRefusal, match="settlement state"):
        settle_reservation(reservation, plan_sha256=plan_sha256, cleanup="proven")

    failed_path = tmp_path / "reservation-failed.json"
    _reservation(failed_path, plan_sha256, "controller-boundary-failed")
    consume_reservation(failed_path, plan_sha256=plan_sha256)
    failed = settle_reservation(failed_path, plan_sha256=plan_sha256, cleanup="failed")
    assert failed["status"] == "blocked" and failed["cleanup"] == "failed"

    proven_path = tmp_path / "reservation-proven.json"
    _reservation(proven_path, plan_sha256, "controller-boundary-proven")
    consume_reservation(proven_path, plan_sha256=plan_sha256)
    settled = settle_reservation(proven_path, plan_sha256=plan_sha256, cleanup="proven")
    assert settled["status"] == "settled" and settled["cleanup"] == "proven"

    meta, _golds, _universes, common, _slots = quality_inputs()
    task_ids = tuple(meta)
    report_sha256 = digest({"case": "controller-boundary", "outcome": "incomplete_technical"})
    journal_root = tmp_path / "failed-journal"
    journal = BodyAttemptJournal.create(
        journal_root,
        identity=_journal_identity("controller-journal-failure"),
        task_ids=task_ids,
        functional_only=True,
    )
    journal.freeze_common(common)
    journal.before_slot("body-01", ARMS[0], 1)
    failure = build_body_failure_envelope(
        attempt_id="controller-journal-failure", failure_code="incomplete_technical",
        failure_stage="controller", cleanup={"cleanup_ok": False,
        "relations_clean": False, "container_absent": False,
        "integrity_ok": False, "ownership_ok": False}, journal=journal,
        functional_only=True,
    )
    failure_sha256 = write_body_failure_envelope(journal_root.parent, failure)
    terminal = journal.terminal(
        outcome="incomplete_technical",
        report_sha256=report_sha256,
        integrity_ok=False,
        ownership_ok=False,
        cleanup_ok=False,
        expected_common_identity=common.identity,
        failure_envelope_sha256=failure_sha256,
        failure_stage="controller",
    )
    assert terminal["execution_authority_granted"] is False
    assert terminal["origin_proven"] is False
    retained = read_attempt_journal(
        journal_root,
        identity=_journal_identity("controller-journal-failure"),
        task_ids=task_ids,
        expected_common_identity=common.identity,
        functional_only=True,
    )
    terminal_payload = retained[-1]["payload"]
    assert terminal_payload["outcome"] == "incomplete_technical"
    assert terminal_payload["pending_slots"] == 1
    assert terminal_payload["possible_unknown_exposure"] is True
    with pytest.raises(JournalRefusal, match="journal_already_terminal"):
        journal.before_slot("body-01", ARMS[0], 2)

    unclosed_root = tmp_path / "unclosed-journal"
    unclosed = BodyAttemptJournal.create(
        unclosed_root,
        identity=_journal_identity("controller-journal-unclosed"),
        task_ids=task_ids,
        functional_only=True,
    )
    unclosed.freeze_common(common)
    with pytest.raises(JournalRefusal, match="journal_incomplete_terminal"):
        read_attempt_journal(
            unclosed_root,
            identity=_journal_identity("controller-journal-unclosed"),
            task_ids=task_ids,
            expected_common_identity=common.identity,
            functional_only=True,
        )



def test_physical_authority_roundtrip_preserves_both_hash_joins(tmp_path: Path) -> None:
    from retrieval_body_study_host_admission import read_parent_admission
    from common_lore_index_test_support.retrieval_body_study_host_cases import write_physical_authority_case

    plan, path, digest = write_physical_authority_case(tmp_path / "authority")
    admitted = read_parent_admission(path, expected_sha256=digest, plan=plan)
    admitted.require_current(lambda _count: None)
    assert len(admitted.pins) == 5
    assert admitted.record["operator_selection"]["sha256"] != plan["operator_decision_sha256"]
    operator_path = Path(admitted.record["operator_selection"]["path"])
    original_size = operator_path.stat().st_size
    operator_path.write_bytes(operator_path.read_bytes() + b"x" * 4096)
    charged: list[int] = []
    with pytest.raises(BodyStudyRefusal, match="receipt bounds"):
        admitted.require_current(charged.append)
    assert charged[-1] == original_size + 1

    bad_plan, bad_path, bad_digest = write_physical_authority_case(
        tmp_path / "wrong-authority", wrong_decision=True)
    with pytest.raises(BodyStudyRefusal, match="body operator decision"):
        read_parent_admission(bad_path, expected_sha256=bad_digest, plan=bad_plan)
