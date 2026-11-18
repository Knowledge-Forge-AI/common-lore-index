"""Focused ownership tests for BODY artifact retention and evidence joins."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import sys
import types
from typing import Any

import pytest

import retrieval_body_study_host_evidence as subject
from retrieval_body_study_observation import observation_record

_SHA = "sha256:" + "a" * 64
_REPORT_RAW_SHA = "sha256:" + "b" * 64
_FAILURE_RAW_SHA = "sha256:" + "c" * 64
_JOURNAL_SHA = "sha256:" + "d" * 64
_REPORT_ID = "sha256:" + "e" * 64
_COMMON_ID = "sha256:" + "f" * 64
_PLAN = {
    "attempt_id": "body-attempt-1",
    "input_pin_sha256": _SHA,
    "source_closure_sha256": _SHA,
    "treatment_sha256": _SHA,
    "protocol_sha256": _SHA,
    "candidate_snapshot_sha256": _SHA,
    "worker_closure_sha256": _SHA,
    "evaluator_closure_sha256": _SHA,
    "operator_decision_sha256": _SHA,
}


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(subject, "plan_digest", lambda _value: _SHA)
    return dict(_PLAN)


def _optional_map(monkeypatch: pytest.MonkeyPatch,
                  values: dict[str, tuple[bytes | None, str | None]]) -> None:
    def fake_optional(_root: Any, name: str, _maximum: int,
                      _charge: Callable[[int], None]) -> tuple[bytes | None, str | None]:
        return values.get(name, (None, None))
    monkeypatch.setattr(subject, "_optional", fake_optional)

def _report_root(tmp_path: Path) -> Path:
    root = tmp_path / "report"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return root


@pytest.mark.parametrize(("seam", "expected"), [
    ("matched", ()),
    ("report", ("report_journal_join",)),
    ("failure", ("failure_journal_join",)),
    ("complete_with_failure", ("unexpected_failure_artifact",)),
    ("mislabeled_evaluation", ("report_journal_join",)),
    ("malformed_common", ("report_journal_join",)),
    ("matched_precleanup", ()),
    ("extra_precleanup", ("failure_precleanup_join",)),
    ("missing_precleanup", ("failure_precleanup_join",)),
    ("missing_report", ()),
])
def test_report_and_failure_join_separate_terminal_identities(
    monkeypatch: pytest.MonkeyPatch, plan: dict[str, Any], tmp_path: Path,
    seam: str, expected: tuple[str, ...],
) -> None:
    precleanup_id = "sha256:" + "2" * 64
    values: dict[str, tuple[bytes | None, str | None]] = {
        subject.REPORT_MEMBER: ((None, None) if seam == "missing_report"
                                else (b"report-original", _REPORT_RAW_SHA)),
        subject.FAILURE_MEMBER: (b"failure-original", _FAILURE_RAW_SHA),
    }
    if seam in {"matched_precleanup", "extra_precleanup"}:
        values[subject.FAILURE_EVIDENCE_MEMBER] = (b"precleanup-original", precleanup_id)
    _optional_map(monkeypatch, values)
    outcome = "signal_not_demonstrated" if seam == "complete_with_failure" else "incomplete_technical"
    report = {
        "report_identity": _REPORT_ID, "quality_outcome": outcome, "outcome": outcome,
        "common_freeze": [] if seam == "malformed_common" else {"common_identity": _COMMON_ID},
        "evaluation": {"outcome": "inadmissible" if seam == "mislabeled_evaluation" else outcome},
    }
    failure = {
        **plan, "plan_sha256": _SHA, "report_identity": "sha256:" + "1" * 64,
        "precleanup_envelope_sha256": precleanup_id if seam in {"matched_precleanup", "missing_precleanup"} else None,
    }
    precleanup = {**plan, "plan_sha256": _SHA, "report_identity": precleanup_id}
    logical = {b"report-original": report, b"failure-original": failure, b"precleanup-original": precleanup}
    monkeypatch.setattr(subject, "_json", lambda raw, canonical: logical[raw])
    monkeypatch.setattr(subject, "validate_actual_body_report", lambda value: value)
    monkeypatch.setattr(subject, "validate_actual_body_envelope", lambda value, **_kwargs: value)
    monkeypatch.setattr(subject, "validate_body_failure_envelope", lambda value: value)
    monkeypatch.setattr(subject, "_read_journal", lambda *_args: {
        "raw_sha256": _JOURNAL_SHA, "bytes": 32, "journal_sha256": _JOURNAL_SHA,
        "terminal_report_sha256": _SHA if seam == "report" else _REPORT_ID,
        "common_identity": _COMMON_ID, "observations_raw_sha256": None, "observations_bytes": 0,
        "terminal": {"sealed_observation_count": 0, "outcome": outcome,
                     "failure_envelope_sha256": _SHA if seam == "failure" else failure["report_identity"]},
    })

    result = subject.retain_body_evidence(
        _report_root(tmp_path), plan=plan,
        cleanup={"cleanup": "unknown"}, charge_bytes=lambda _amount: None,
    )

    assert result["state"] == ("retained" if seam in {"matched", "matched_precleanup"} else "partial")
    assert result["binding"]["report_sha256"] == (None if seam == "missing_report" else _REPORT_RAW_SHA)
    assert result["binding"]["failure_sha256"] == _FAILURE_RAW_SHA
    assert result["binding"]["journal_sha256"] == _JOURNAL_SHA
    assert result["errors"] == expected


def test_both_body_readers_preserve_owned_report_above_four_mib(
    monkeypatch: pytest.MonkeyPatch, plan: dict[str, Any], tmp_path: Path,
) -> None:
    import retrieval_body_study_host_report as host_report
    from retrieval_body_study_files import publish_member
    from retrieval_body_study_report_helpers import encode_body_report, write_body_report
    from retrieval_body_study_report import REPORT_SCHEMA
    from retrieval_body_study_values import SLOT_COUNT, STUDY_PHASE, digest_value

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA, "phase": STUDY_PHASE, "origin_proven": True,
        "scientific_acceptance": False, "slot_count": SLOT_COUNT, "slots": [{}] * SLOT_COUNT,
        "evaluation": {"outcome": "signal_not_demonstrated"}, "fixture": "x" * (5 * 1024**2),
    }
    report["report_identity"] = digest_value(report)
    root = _report_root(tmp_path)
    raw = encode_body_report(report)
    assert len(raw) > 4 * 1024**2
    assert write_body_report(root, report) == report["report_identity"]
    monkeypatch.setattr(host_report, "validate_actual_body_envelope", lambda value, **_kwargs: value)
    monkeypatch.setattr(subject, "validate_actual_body_envelope", lambda value, **_kwargs: value)
    charged: list[int] = []
    assert host_report.read_actual_body_report(root, plan=plan, charge_bytes=charged.append) == report
    assert charged == [subject.MAX_REPORT_BYTES + 1]
    result = subject.retain_body_evidence(root, plan=plan, cleanup={}, charge_bytes=charged.append)
    assert result["artifacts"]["report"]["bytes"] == len(raw)
    assert result["artifacts"]["report"]["valid"] is True
    assert result["binding"]["report_sha256"] == subject._digest(raw)
    assert charged[1:] == [len(raw) + 1]
    with pytest.raises(ValueError, match="member_too_large"):
        publish_member(root, subject.FAILURE_MEMBER, b"x" * (subject.MAX_FAILURE_BYTES + 1))


def test_shared_result_ceiling_refuses_one_extra_byte(
    plan: dict[str, Any], tmp_path: Path,
) -> None:
    from retrieval_body_study_files import publish_member

    root = _report_root(tmp_path)
    publish_member(root, subject.REPORT_MEMBER, b"x" * subject.MAX_REPORT_BYTES)
    with pytest.raises(ValueError, match="result_members_too_large"):
        publish_member(root, subject.FAILURE_MEMBER, b"x")
    assert not (root / subject.FAILURE_MEMBER).exists()

    # The exported reader also enforces the shared bound independently.
    (root / subject.FAILURE_MEMBER).write_bytes(b"x")
    charged: list[int] = []
    result = subject.retain_body_evidence(root, plan=plan, cleanup={}, charge_bytes=charged.append)
    assert "failure_unavailable" in result["errors"]
    assert charged == [subject.MAX_REPORT_BYTES + 1]


def test_missing_originals_are_unavailable_and_never_success(
    monkeypatch: pytest.MonkeyPatch, plan: dict[str, Any], tmp_path: Path
) -> None:
    _optional_map(monkeypatch, {})
    monkeypatch.setattr(subject, "_read_journal",
                        lambda *_args: (_ for _ in ()).throw(
                            subject.BodyEvidenceRefusal("body_evidence_missing")))
    result = subject.retain_body_evidence(
        _report_root(tmp_path), plan=plan,
        cleanup={"cleanup": "unknown"}, charge_bytes=lambda _amount: None,
    )

    assert result["state"] == "unavailable"
    assert all(value is None for value in (
        result["binding"]["report_sha256"], result["binding"]["journal_sha256"],
        result["binding"]["failure_sha256"],
    ))
    assert result["artifacts"]["report"]["error"] == "missing"


def test_altered_report_keeps_raw_pointer_but_blocks_retention(
    monkeypatch: pytest.MonkeyPatch, plan: dict[str, Any], tmp_path: Path
) -> None:
    _optional_map(monkeypatch, {
        subject.REPORT_MEMBER: (b"altered", _REPORT_RAW_SHA),
        subject.FAILURE_EVIDENCE_MEMBER: (b"foreign-failure", _FAILURE_RAW_SHA),
    })
    monkeypatch.setattr(subject, "_json", lambda *_args: {"altered": True})
    monkeypatch.setattr(subject, "validate_actual_body_report",
                        lambda _value: (_ for _ in ()).throw(ValueError("altered")))
    monkeypatch.setattr(subject, "_read_journal",
                        lambda *_args: (_ for _ in ()).throw(
                            subject.BodyEvidenceRefusal("body_evidence_foreign")))

    result = subject.retain_body_evidence(
        _report_root(tmp_path), plan=plan,
        cleanup={"cleanup": "unknown"}, charge_bytes=lambda _amount: None,
    )

    assert result["state"] == "partial"
    assert result["artifacts"]["report"]["raw_sha256"] == _REPORT_RAW_SHA
    assert result["binding"]["report_sha256"] == _REPORT_RAW_SHA
    assert result["binding"]["failure_sha256"] == _FAILURE_RAW_SHA
    assert "report_invalid" in result["errors"]


def test_foreign_journal_is_refused_before_replay(
    monkeypatch: pytest.MonkeyPatch, plan: dict[str, Any]
) -> None:
    monkeypatch.setattr(subject, "_names",
                        lambda *_args: (_ for _ in ()).throw(
                            subject.BodyEvidenceRefusal("body_evidence_foreign")))
    with pytest.raises(subject.BodyEvidenceRefusal, match="body_evidence_foreign"):
        subject._read_journal(Path("/private/journal"),
                               Path("/private/observations"), plan,
                               lambda _amount: None, None)


def test_capture_fallback_preserves_unavailable_binding(
    monkeypatch: pytest.MonkeyPatch, plan: dict[str, Any], tmp_path: Path
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("unexpected retention boundary")
    monkeypatch.setattr(subject, "retain_body_evidence", fail)

    result = subject.capture_body_evidence(
        _report_root(tmp_path), plan=plan,
        cleanup={"cleanup": "unknown"}, charge_bytes=lambda _amount: None,
    )

    assert result["state"] == "unavailable"
    assert result["binding"]["plan_sha256"] == _SHA
    assert result["binding"]["attempt_id"] == plan["attempt_id"]
    assert result["errors"] == ("body_evidence_retention",)

def test_report_root_custody_rejects_public_mode(plan: dict[str, Any], tmp_path: Path) -> None:
    root = tmp_path / "report"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    with pytest.raises(subject.BodyEvidenceRefusal, match="body_evidence_report_root"):
        subject.retain_body_evidence(root, plan=plan, cleanup={}, charge_bytes=lambda _amount: None)


def test_exporter_custody_accepts_directory_and_member_modes(tmp_path: Path) -> None:
    journal = tmp_path / "attempt-journal"
    journal.mkdir(mode=0o750)
    os.chmod(journal, 0o750)
    (journal / ".lock").write_bytes(b"")
    os.chmod(journal / ".lock", 0o640)
    (journal / "00000.json").write_bytes(b"{}\n")
    os.chmod(journal / "00000.json", 0o640)
    observations = tmp_path / "attempt-journal-observations"
    observations.mkdir(mode=0o750)
    os.chmod(observations, 0o750)
    observation = observation_record({
        "task_id": "task-1", "arm": "native_tools_baseline", "repetition": 1,
        "generation": 1, "execution_state": "failed", "ranked_files": (),
    }, index=0, record_kind="failure")
    raw_observation = json.dumps(observation, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False).encode() + b"\n"
    (observations / "sealed-0000.json").write_bytes(raw_observation)
    os.chmod(observations / "sealed-0000.json", 0o640)

    assert subject._names(journal, subject._EVENT, subject.MAX_EVENT_BYTES,
                          (0o700, 0o750)) == ("00000.json",)
    assert subject._names(observations, subject._SEALED,
                          subject.MAX_OBSERVATION_BYTES, 0o750) == ("sealed-0000.json",)
    _rows, hashes, _raw_sha, manifest, total = subject._read_observations(
        observations, lambda _amount: None)
    assert hashes == (observation["observation_sha256"],)
    assert manifest[0]["raw_sha256"] == subject._digest(raw_observation)
    assert total == len(raw_observation)


def test_failure_manifest_binds_original_raw_and_logical_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subject, "observation_identity", lambda _rows: _SHA)
    manifest = ({"index": 0, "observation_sha256": _SHA,
                 "bytes": 4, "raw_sha256": _SHA},)
    failure = {"sealed_observation_manifest": list(manifest),
               "sealed_observation_count": 1, "sealed_observation_bytes": 4,
               "sealed_observation_identity": _SHA,
               "journal_observation_identity": _SHA}
    subject._validate_failure_observations(failure, ({"slot": 1},), manifest, 4)
    altered = {**failure, "sealed_observation_manifest": [
        {**manifest[0], "raw_sha256": "sha256:" + "9" * 64}]}
    with pytest.raises(subject.BodyEvidenceRefusal, match="body_evidence_failure_observation_join"):
        subject._validate_failure_observations(altered, ({"slot": 1},), manifest, 4)


def test_replay_uses_current_five_argument_journal_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    fake = types.SimpleNamespace(_validate_replay=lambda *args: calls.append(args))
    monkeypatch.setitem(sys.modules, "retrieval_body_study_attempt_journal", fake)
    rows, tasks, observations, failure = (), ("task",), ({"slot": 1},), {"failure": 1}
    subject._replay(rows, tasks, False, observations, failure)
    assert len(calls) == 1 and len(calls[0]) == 5
    assert calls[0][3] == observations and calls[0][4] == failure


def test_terminal_persistence_uses_bounded_hash_bound_report_reference() -> None:
    full_report = {"payload": "x" * (3 * 1024**2)}
    reference = {"path": subject.REPORT_MEMBER, "raw_sha256": _REPORT_RAW_SHA,
                 "bytes": len(full_report["payload"]), "valid": True, "error": None}
    terminal = {"report": full_report, "evidence": {"report": reference}}
    evidence = terminal["evidence"]
    assert isinstance(evidence, dict)
    persisted = {**terminal, "report": evidence["report"],
                 "report_persisted_as": "hash_bound_export_reference"}
    raw = json.dumps(persisted, sort_keys=True, separators=(",", ":")).encode()
    assert len(raw) < 2 * 1024**2
    assert persisted["report"] is reference and terminal["report"] is full_report


def test_failure_digest_is_computed_from_plan_and_all_scientific_joins_are_required(plan):
    failure = {**plan, "plan_sha256": _SHA}
    subject._join_failure(failure, plan)
    for field in ("plan_sha256", "worker_closure_sha256", "evaluator_closure_sha256",
                  "protocol_sha256", "operator_decision_sha256"):
        with pytest.raises(subject.BodyEvidenceRefusal, match="failure_join"):
            subject._join_failure({**failure, field: "sha256:" + "0" * 64}, plan)


def test_foreign_journal_tasks_cannot_reuse_a_declared_plan(plan, tmp_path):
    plan = {**plan, "declaration": {"slot_manifest": [
        {"task_id": f"body-{index:02d}"} for index in range(24)]}}
    expected = {key: plan[key] for key in ("attempt_id", "input_pin_sha256",
        "source_closure_sha256", "treatment_sha256", "protocol_sha256",
        "candidate_snapshot_sha256")}
    expected["plan_sha256"] = _SHA
    row = {"schema": "cloe-body-study-attempt-journal-v1", "index": 0,
        "identity_sha256": subject._domain_digest(expected),
        "previous_sha256": "sha256:" + "0" * 64, "event": "attempt_created",
        "payload": {"identity": expected, "functional_only": False,
                    "task_ids": [f"foreign-{index:02d}" for index in range(24)]}}
    row["event_sha256"] = subject._domain_digest(row)
    root = tmp_path / "journal"
    root.mkdir(mode=0o750)
    os.chmod(root, 0o750)
    event = root / "00000.json"
    event.write_bytes(subject._canonical(row) + b"\n")
    event.chmod(0o640)
    with pytest.raises(subject.BodyEvidenceRefusal, match="journal_tasks"):
        subject._read_journal(root, tmp_path / "observations", plan, lambda _n: None, None)
