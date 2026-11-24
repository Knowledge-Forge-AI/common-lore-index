"""Owners for BODY reservation one-use and failed-cleanup semantics."""
from __future__ import annotations

from pathlib import Path
import os

import pytest

from retrieval_body_study_contracts import BodyStudyRefusal
from retrieval_body_study_reservation import (
    consume_reservation, create_reservation, read_reservation, settle_reservation, validate_reservation,
)


def _args() -> dict[str, str]:
    return {name: "sha256:" + "a" * 64 for name in (
        "source_closure_sha256", "treatment_sha256", "worker_closure_sha256",
        "protocol_sha256", "operator_decision_sha256")}


def test_reservation_is_consumed_once_before_runner(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    path = tmp_path / "reservation.json"
    create_reservation(path, plan_sha256="sha256:" + "b" * 64, attempt_id="body-a", **_args())
    consumed = consume_reservation(path, plan_sha256="sha256:" + "b" * 64, attempt_id="body-a")
    assert consumed["consumed"] is True and consumed["cleanup"] == "in_progress"
    with pytest.raises(BodyStudyRefusal):
        consume_reservation(path, plan_sha256="sha256:" + "b" * 64, attempt_id="body-a")


def test_failed_cleanup_is_terminal_and_not_reusable(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    path = tmp_path / "reservation.json"
    create_reservation(path, plan_sha256="sha256:" + "c" * 64, attempt_id="body-b", **_args())
    consume_reservation(path, plan_sha256="sha256:" + "c" * 64)
    settled = settle_reservation(path, plan_sha256="sha256:" + "c" * 64, cleanup="failed")
    assert settled["state"] == "settled" and settled["cleanup"] == "failed"
    assert read_reservation(path)["cleanup"] == "failed"
    evidence = settled["result_evidence"]
    assert evidence["state"] == "unavailable"
    assert evidence["attempt_id"] == "body-b"
    assert evidence["plan_sha256"] == "sha256:" + "c" * 64
    assert all(evidence[key] is None for key in
               ("report_sha256", "journal_sha256", "failure_sha256"))
    with pytest.raises(BodyStudyRefusal, match="body result evidence settlement state"):
        validate_reservation({**settled, "result_evidence": None})


def test_raw_plan_hash_is_refused_without_creating_reservation(tmp_path: Path) -> None:
    os.chmod(tmp_path, 0o700)
    path = tmp_path / "reservation.json"
    with pytest.raises(BodyStudyRefusal, match="body reservation plan"):
        create_reservation(path, plan_sha256="b" * 64, attempt_id="body-raw", **_args())
    assert not path.exists()
