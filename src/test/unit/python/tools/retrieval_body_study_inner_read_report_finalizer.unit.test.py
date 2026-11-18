"""Focused custody tests for the BODY final whole-read refresh draft."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import stat
from typing import Any

import pytest

import retrieval_body_study_inner_read_report_finalizer as subject
from retrieval_body_study_inner_reads import InnerReadRefusal


def _receipt(count: int) -> dict[str, Any]:
    return {
        "schema": subject.SOURCE_RECEIPT_SCHEMA,
        "plan_sha256": "sha256:" + "a" * 64,
        "role": "body-study",
        "inner_schema": "cloe-body-study-inner-plan-v1",
        "candidate_head": "a" * 40,
        "source_snapshot_sha256": "sha256:" + "b" * 64,
        "regular_file_bytes": 31,
        "consumed_reads": count,
        "read_limit": subject.READ_LIMIT,
        "bookkeeping_only": True,
        "remaining_reads": subject.READ_LIMIT - count,
        "execution_authorized": False,
    }


def _guard(count: int) -> dict[str, Any]:
    return {
        "schema": subject.CLOSURE_RECEIPT_SCHEMA,
        "plan_sha256": "sha256:" + "a" * 64,
        "role": "body-study",
        "inner_schema": "cloe-body-study-inner-plan-v1",
        "candidate_head": "a" * 40,
        "source_snapshot_sha256": "sha256:" + "b" * 64,
        "regular_file_bytes": 31,
        "file_visits": 7,
        "reserved_bytes_per_guard": 70000,
        "consumed_guards": count,
        "guard_limit": 512,
        "bookkeeping_only": True,
        "remaining_guards": 512 - count,
        "execution_authorized": False,
    }


def _source_record(count: int) -> dict[str, Any]:
    record = _receipt(count)
    record["schema"] = subject.SOURCE_LEDGER_SCHEMA
    record.pop("remaining_reads")
    record.pop("execution_authorized")
    return record


def _closure_record(count: int) -> dict[str, Any]:
    record = _guard(count)
    record["schema"] = subject.CLOSURE_LEDGER_SCHEMA
    record.pop("remaining_guards")
    record.pop("execution_authorized")
    return record


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode())
    path.chmod(0o600)


def _budget(source: dict[str, Any], guard: dict[str, Any]) -> dict[str, Any]:
    source_count = int(guard["consumed_guards"])
    snapshot_count = 201
    source_read = source_count * int(guard["regular_file_bytes"])
    active_read = source_count * 17
    snapshot_bytes = 41
    snapshot_read = snapshot_count * snapshot_bytes
    plan = {
        "attempt_id": "body-attempt",
        "host_plan_sha256": "sha256:" + "a" * 64,
        "input_pin_sha256": "sha256:" + "d" * 64,
        "source_closure_sha256": "sha256:" + "e" * 64,
        "candidate_snapshot_sha256": "sha256:" + "f" * 64,
    }
    receipt = {
        "schema": "cloe-w5-body-snapshot-counter-v1",
        "binding": {
            "attempt_id": plan["attempt_id"],
            "plan_sha256": plan["host_plan_sha256"],
            "source_closure_sha256": plan["source_closure_sha256"],
            "candidate_snapshot_sha256": plan["candidate_snapshot_sha256"],
            "input_pin_sha256": plan["input_pin_sha256"],
            "receipt_name": "body-study-snapshot-counter.json",
        },
        "reserved_count": snapshot_count,
        "observed_count": snapshot_count,
        "observed_count_kind": "measured_exact_code_object_calls",
        "call_map": [{"caller": "driver:inner:1", "count": snapshot_count}],
        "violation_code": None,
        "audit_rejections": 0,
        "profile_attested": True,
        "profile_restored": True,
        "audit_hook_attested": True,
        "audit_hook_inert": True,
        "counter_scope": "inner-sdk",
        "bookkeeping_bytes": 36864,
        "valid": True,
    }
    receipt_raw = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return {
        "schema": subject.REPORT_SCHEMA,
        "source_read_receipt": source,
        "source_guard_receipt": guard,
        "source_guard_count": source_count,
        "source_guard_limit": 512,
        "source_guard_read_bytes": source_read,
        "active_guard_read_bytes": active_read,
        "active_guard_read_bytes_kind": "informational_subset",
        "registered_snapshot_count": snapshot_count,
        "registered_snapshot_count_reserved": snapshot_count,
        "registered_snapshot_count_kind": "measured_final_receipt",
        "registered_snapshot_file_bytes": snapshot_bytes,
        "registered_snapshot_read_bytes": snapshot_read,
        "registered_snapshot_read_bytes_kind": "reserved_upper_bound",
        "source_guard_total_read_bytes": source_read + snapshot_read,
        "source_guard_total_read_bytes_kind": "source_plus_final_reserved_upper_bound",
        "source_guard_byte_limit": subject.INNER_SOURCE_READ_BYTES,
        "snapshot_counter_receipt_file": "body-study-snapshot-counter.json",
        "snapshot_counter_receipt_sha256": "sha256:" + hashlib.sha256(receipt_raw).hexdigest(),
        "snapshot_counter_receipt": receipt,
        "_snapshot_receipt_raw": receipt_raw,
        "_snapshot_plan": plan,
    }


def _case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    report_root = tmp_path / "report"
    ledger_root = tmp_path / "ledger"
    source_root = tmp_path / "source"
    for path in (report_root, ledger_root, source_root):
        path.mkdir(mode=0o700)
        path.chmod(0o700)
    inner_report_path = report_root / "body-study"
    inner_report_path.mkdir(mode=0o700)
    inner_report_path.chmod(0o700)
    old_source = _receipt(1)
    current_source = _receipt(2)
    report_guard = _guard(5)
    current_guard = _guard(5)
    budget = _budget(old_source, report_guard)
    plan = budget.pop("_snapshot_plan")
    receipt_raw = budget.pop("_snapshot_receipt_raw")
    report_file = report_root / subject.REPORT_NAME
    report_file.write_bytes(json.dumps(budget, sort_keys=True, separators=(",", ":")).encode())
    report_file.chmod(0o600)
    receipt_file = report_root / "body-study-snapshot-counter.json"
    receipt_file.write_bytes(receipt_raw)
    receipt_file.chmod(0o600)
    source_record_file = ledger_root / subject.SOURCE_RECORD_NAME
    closure_record_file = ledger_root / subject.CLOSURE_RECORD_NAME
    _write_json(source_record_file, _source_record(2))
    _write_json(closure_record_file, _closure_record(5))
    monkeypatch.setattr(subject, "validate_exported_receipt", lambda *_args, **_kwargs: dict(_args[1]))
    monkeypatch.setattr(subject, "read_receipt", lambda *_args, **_kwargs: dict(current_source))
    monkeypatch.setattr(subject, "validate_exported_closure_receipt", lambda *_args, **_kwargs: dict(_args[1]))
    monkeypatch.setattr(subject, "read_inner_source_closure_receipt", lambda *_args, **_kwargs: dict(current_guard))
    monkeypatch.setattr(
        subject,
        "closure_guard_read_bound",
        lambda *_args, **_kwargs: {
            "regular_file_bytes": 31,
            "file_visits": 7,
            "reserved_bytes_per_guard": 70000,
        },
    )
    return {
        "plan": {"inner": {"report_dir": str(inner_report_path), "scratch_dir": str(ledger_root)}, **plan},
        "source_root": source_root,
        "report_file": report_file,
        "budget": budget,
        "current_source": current_source,
        "current_guard": current_guard,
        "source_record_file": source_record_file,
        "closure_record_file": closure_record_file,
    }


def _run(case: dict[str, Any]) -> dict[str, Any]:
    charges = case.setdefault("charges", [])
    assert isinstance(charges, list)
    return subject.refresh_body_source_read_report(
        case["plan"],
        source_root=case["source_root"],
        charge_bytes=charges.append,
    )


def test_refresh_replaces_only_the_whole_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(tmp_path, monkeypatch)
    result = _run(case)
    stored = json.loads(Path(case["report_file"]).read_text())
    assert result["source_read_receipt"]["consumed_reads"] == 2
    assert stored["source_read_receipt"]["consumed_reads"] == 2
    assert stored["source_guard_receipt"] == case["budget"]["source_guard_receipt"]
    assert stored["registered_snapshot_count"] == 201
    counter_file = Path(case["report_file"]).parent / subject.SNAPSHOT_RECEIPT_NAME
    counter = json.loads(counter_file.read_text())
    assert counter["valid"] is True and counter["observed_count"] == 201
    assert stored["snapshot_counter_receipt"] == counter
    assert case["charges"] == [subject.FINALIZER_PRECHARGE_BYTES]
    assert Path(case["report_file"]).parent.joinpath(subject.SOURCE_RECORD_NAME).read_bytes() == (
        Path(case["source_record_file"]).read_bytes()
    )
    exported_closure = Path(case["report_file"]).parent.joinpath(subject.CLOSURE_RECORD_NAME)
    assert exported_closure.exists()
    assert stat.S_IMODE(exported_closure.stat().st_mode) == 0o600


def test_counter_reversal_keeps_original_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(tmp_path, monkeypatch)
    case["current_source"]["consumed_reads"] = 1
    case["current_source"]["remaining_reads"] = subject.READ_LIMIT - 1
    _write_json(case["source_record_file"], _source_record(1))
    before = Path(case["report_file"]).read_bytes()
    with pytest.raises(InnerReadRefusal):
        _run(case)
    assert Path(case["report_file"]).read_bytes() == before


def test_stale_closure_keeps_original_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(tmp_path, monkeypatch)
    case["current_guard"]["consumed_guards"] = 6
    case["current_guard"]["remaining_guards"] = 506
    _write_json(case["closure_record_file"], _closure_record(6))
    before = Path(case["report_file"]).read_bytes()
    with pytest.raises(InnerReadRefusal):
        _run(case)
    assert Path(case["report_file"]).read_bytes() == before


def test_undercounted_snapshot_protocol_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(tmp_path, monkeypatch)
    budget = dict(case["budget"])
    budget["registered_snapshot_count"] = 200
    Path(case["report_file"]).write_bytes(
        json.dumps(budget, sort_keys=True, separators=(",", ":")).encode()
    )
    with pytest.raises(InnerReadRefusal):
        _run(case)


def test_nonprivate_report_mode_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(tmp_path, monkeypatch)
    Path(case["report_file"]).chmod(0o644)
    with pytest.raises(InnerReadRefusal):
        _run(case)


def test_noncanonical_exported_ledger_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(tmp_path, monkeypatch)
    source_record = _source_record(2)
    Path(case["source_record_file"]).write_text(json.dumps(source_record, indent=2) + "\n")
    Path(case["source_record_file"]).chmod(0o600)
    with pytest.raises(InnerReadRefusal):
        _run(case)
    assert not Path(case["report_file"]).parent.joinpath(subject.SOURCE_RECORD_NAME).exists()
