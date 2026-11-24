"""Focused pure tests for the combined BODY inner source-read report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from retrieval_body_study_inner_guard_reads import reserve_inner_source_closure_guard
from retrieval_body_study_inner_reads import InnerReadRefusal, reserve_inner_source_verification
from retrieval_body_study_inner_read_plan import _digest as snapshot_commitment
from retrieval_body_study_inner_read_report import (
    INNER_SOURCE_READ_BYTES,
    SNAPSHOT_RECEIPT_NAME,
    validate_exported_source_read_receipt,
)
from retrieval_semantic_generator_worker_contracts import WORKER_SOURCE_FILES


def _write(root: Path, name: str, raw: bytes) -> None:
    path = root / name
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(0o644)


def _case(tmp_path: Path) -> dict[str, Any]:
    source = tmp_path / "source"
    ledger = tmp_path / "ledger"
    report = tmp_path / "report"
    source.mkdir(mode=0o700)
    ledger.mkdir(mode=0o700)
    report.mkdir(mode=0o700)
    names = [
        "tools/body-source.py",
        "tools/body-treatment.py",
        *("tools/" + name for name in WORKER_SOURCE_FILES),
        "src/test/fixtures/retrieval_evaluation/body-v2/input.json",
    ]
    snapshot: dict[str, dict[str, object]] = {}
    for name in names:
        raw = b"{}\n" if name.endswith(".json") else b"# body source\n"
        _write(source, name, raw)
        snapshot[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "mode": 0o644,
        }
    plan = {
        "schema": "cloe-body-study-inner-plan-v1",
        "role": "body-study",
        "attempt_id": "body-attempt",
        "host_plan_sha256": "sha256:" + "c" * 64,
        "input_pin_sha256": "sha256:" + "d" * 64,
        "source_closure_sha256": "sha256:" + "e" * 64,
        "candidate_snapshot_sha256": snapshot_commitment(snapshot),
        "candidate_head": "a" * 40,
        "candidate_snapshot": snapshot,
        "source_members": ["tools/body-source.py"],
        "treatment_members": ["tools/body-treatment.py"],
    }
    whole = reserve_inner_source_verification(plan, source, ledger)
    closure = reserve_inner_source_closure_guard(plan, source, ledger)
    active_bytes = 17
    snapshot_bytes = 41
    snapshot_count = 201
    source_count = int(closure["consumed_guards"])
    source_read = source_count * int(closure["regular_file_bytes"])
    active_read = source_count * active_bytes
    snapshot_read = snapshot_count * snapshot_bytes
    snapshot_receipt = {
        "schema": "cloe-w5-body-snapshot-counter-v1",
        "binding": {
            "attempt_id": plan["attempt_id"],
            "plan_sha256": plan["host_plan_sha256"],
            "source_closure_sha256": plan["source_closure_sha256"],
            "candidate_snapshot_sha256": plan["candidate_snapshot_sha256"],
            "input_pin_sha256": plan["input_pin_sha256"],
            "receipt_name": SNAPSHOT_RECEIPT_NAME,
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
    snapshot_raw = (json.dumps(snapshot_receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    budget = {
        "schema": "cloe-body-study-source-read-budget-v1",
        "source_read_receipt": whole,
        "source_guard_receipt": closure,
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
        "source_guard_byte_limit": INNER_SOURCE_READ_BYTES,
        "snapshot_counter_receipt_file": SNAPSHOT_RECEIPT_NAME,
        "snapshot_counter_receipt_sha256": "sha256:" + hashlib.sha256(snapshot_raw).hexdigest(),
        "snapshot_counter_receipt": snapshot_receipt,
    }
    report_file = report / "body-study-source-read-budget.json"
    report_file.write_bytes(json.dumps(budget, sort_keys=True, separators=(",", ":")).encode())
    report_file.chmod(0o600)
    receipt_file = report / SNAPSHOT_RECEIPT_NAME
    receipt_file.write_bytes(snapshot_raw)
    receipt_file.chmod(0o600)
    return {
        "plan": plan,
        "source": source,
        "ledger": ledger,
        "report": report,
        "budget": budget,
        "regular_file_bytes": sum(
            len((source / name).read_bytes()) for name in snapshot
        ),
        "active_bytes": active_bytes,
        "snapshot_bytes": snapshot_bytes,
        "snapshot_count": snapshot_count,
    }


def _run(case: dict[str, Any], **changes: object) -> dict[str, Any]:
    budget = dict(case["budget"])
    budget.update(changes)
    report_file = Path(case["report"]) / "body-study-source-read-budget.json"
    report_file.write_bytes(json.dumps(budget, sort_keys=True, separators=(",", ":")).encode())
    report_file.chmod(0o600)
    return validate_exported_source_read_receipt(
        Path(case["report"]),
        inner_plan=case["plan"],
        source_root=Path(case["source"]),
        ledger_root=Path(case["ledger"]),
        regular_file_bytes=int(case["regular_file_bytes"]),
        active_file_bytes=int(case["active_bytes"]),
        registered_snapshot_file_bytes=int(case["snapshot_bytes"]),
        expected_registered_snapshot_count=int(case["snapshot_count"]),
        charge_bytes=lambda _amount: None,
    )


def _rewrite_counter(case: dict[str, Any], **changes: object) -> None:
    receipt = dict(case["budget"]["snapshot_counter_receipt"])
    receipt.update(changes)
    raw = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    budget = dict(case["budget"])
    budget.update({
        "snapshot_counter_receipt": receipt,
        "snapshot_counter_receipt_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    })
    report_file = Path(case["report"]) / "body-study-source-read-budget.json"
    report_file.write_bytes(json.dumps(budget, sort_keys=True, separators=(",", ":")).encode())
    report_file.chmod(0o600)
    (Path(case["report"]) / SNAPSHOT_RECEIPT_NAME).write_bytes(raw)
    case["budget"] = budget


def test_combined_report_accepts_current_plan_bound_receipts(tmp_path: Path) -> None:
    case = _case(tmp_path)

    result = _run(case)
    assert result["schema"] == "cloe-body-study-source-read-budget-v1"
    assert result["snapshot_counter_receipt"]["valid"] is True
    assert result["snapshot_counter_receipt_sha256"] == (
        "sha256:" + hashlib.sha256(
            (Path(case["report"]) / SNAPSHOT_RECEIPT_NAME).read_bytes()
        ).hexdigest()
    )


def test_changed_plan_cannot_reuse_exported_receipts(tmp_path: Path) -> None:
    case = _case(tmp_path)
    changed = dict(case["plan"])
    changed["candidate_head"] = "b" * 40
    case["plan"] = changed

    with pytest.raises(InnerReadRefusal):
        _run(case)


def test_count_and_size_changes_are_refused(tmp_path: Path) -> None:
    case = _case(tmp_path)
    with pytest.raises(InnerReadRefusal):
        _run(case, source_guard_count=2)
    with pytest.raises(InnerReadRefusal):
        _run(case, registered_snapshot_count=1)
    with pytest.raises(InnerReadRefusal):
        validate_exported_source_read_receipt(
            Path(case["report"]),
            inner_plan=case["plan"],
            source_root=Path(case["source"]),
            ledger_root=Path(case["ledger"]),
            regular_file_bytes=int(case["regular_file_bytes"]),
            active_file_bytes=int(case["active_bytes"]),
            registered_snapshot_file_bytes=int(case["snapshot_bytes"]),
            expected_registered_snapshot_count=int(case["snapshot_count"]) + 1,
            charge_bytes=lambda _amount: None,
        )
    with pytest.raises(InnerReadRefusal):
        validate_exported_source_read_receipt(
            Path(case["report"]),
            inner_plan=case["plan"],
            source_root=Path(case["source"]),
            ledger_root=Path(case["ledger"]),
            regular_file_bytes=int(case["regular_file_bytes"]) + 1,
            active_file_bytes=int(case["active_bytes"]),
            registered_snapshot_file_bytes=int(case["snapshot_bytes"]),
            expected_registered_snapshot_count=int(case["snapshot_count"]),
            charge_bytes=lambda _amount: None,
        )


def test_float_and_bool_fields_are_not_numeric_substitutes(tmp_path: Path) -> None:
    case = _case(tmp_path)
    with pytest.raises(InnerReadRefusal):
        _run(case, registered_snapshot_count=2.0)
    with pytest.raises(InnerReadRefusal):
        _run(case, source_guard_limit=True)


def test_initial_receipts_cannot_survive_a_new_durable_count(tmp_path: Path) -> None:
    case = _case(tmp_path)
    reserve_inner_source_verification(case["plan"], case["source"], case["ledger"])
    reserve_inner_source_closure_guard(case["plan"], case["source"], case["ledger"])

    with pytest.raises(InnerReadRefusal):
        _run(case)


def test_active_subset_algebra_is_not_silently_double_counted(tmp_path: Path) -> None:
    case = _case(tmp_path)
    with pytest.raises(InnerReadRefusal):
        _run(case, active_guard_read_bytes=int(case["budget"]["active_guard_read_bytes"]) + 1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("audit_rejections", 1),
        ("bookkeeping_bytes", 4096),
        ("call_map", [{"caller": "driver:inner:1", "count": 200}]),
        ("call_map", [{"caller": "same", "count": 1}, {"caller": "same", "count": 200}]),
    ],
)
def test_snapshot_receipt_shape_is_strict(
    tmp_path: Path, field: str, value: object,
) -> None:
    case = _case(tmp_path)
    _rewrite_counter(case, **{field: value})
    with pytest.raises(InnerReadRefusal):
        _run(case)
