"""Source-read ledger/report joins over a disposable candidate tree.

This owner exercises bookkeeping and custody only.  The candidate bytes are
temporary public-safe members; the receipts remain non-authorizing and are
never promoted to a worker or study admission.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
import tarfile

import pytest

from offline_qualification_values import BODY_CAPS, digest as offline_digest, json_bytes
from runner_body_study_sandbox import BodyOfflineSandbox
from runner_pgvector_budget import Allocation
from retrieval_body_study_closure import BODY_SOURCE_MEMBERS, BODY_TREATMENT_MEMBERS
from retrieval_body_study_host_meter import HostMeter
from retrieval_body_study_inner_guard_reads import validate_exported_closure_receipt
from retrieval_body_study_inner_read_report import (
    MAX_SNAPSHOT_RECEIPT_BYTES, SNAPSHOT_RECEIPT_NAME,
    validate_exported_source_read_receipt,
)
from retrieval_body_study_inner_read_report_finalizer import (
    CLOSURE_RECORD_NAME, SOURCE_RECORD_NAME, refresh_body_source_read_report,
)
from retrieval_body_study_report_custody import privatize_body_report_export
from test_sandbox_report import export_sandbox_report
from retrieval_body_study_inner_reads import (
    InnerReadRefusal,
    counter_path_for_plan,
    read_inner_source_closure_receipt,
    read_receipt,
    reserve_inner_source_closure_guard,
    reserve_inner_source_verification,
)
from retrieval_semantic_generator_worker_contracts import WORKER_SOURCE_FILES
from retrieval_body_study_files import publish_member
from retrieval_body_study_snapshot_counter import (
    BodySnapshotCounter, SnapshotCounterBinding, SnapshotCounterReceipt,
)


_ACTIVE_MEMBERS = (
    "src/test/fixtures/retrieval_evaluation/body-v2/bindings-v1.json",
    "src/test/fixtures/retrieval_evaluation/body-v2/catalog-v1.json",
    "src/test/fixtures/retrieval_evaluation/body-v2/corpus-v1.json",
    "src/test/fixtures/retrieval_evaluation/body-v2/input-pin-v1.json",
    "src/test/fixtures/retrieval_evaluation/body-v2/namespace-v1.json",
    "src/test/fixtures/retrieval_evaluation/body-v2/policy-v1.json",
    "src/test/fixtures/retrieval_evaluation/body-v2/preauthoring-gate-v1.json",
    "src/test/fixtures/retrieval_evaluation/body-v2/predecessor-manifest.json",
    "src/test/fixtures/retrieval_evaluation/body-v2/rationale.md",
    "src/test/fixtures/retrieval_evaluation/body-v2/static-inventory-v1.json",
)


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("ascii")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _plan_and_source(root: Path) -> tuple[dict[str, Any], int]:
    source_members = tuple(BODY_SOURCE_MEMBERS)
    treatment_members = tuple(BODY_TREATMENT_MEMBERS)
    names = {
        "README.md",
        *source_members,
        *treatment_members,
        *("tools/" + name for name in WORKER_SOURCE_FILES),
        *_ACTIVE_MEMBERS,
    }
    snapshot: dict[str, dict[str, Any]] = {}
    total = 0
    for name in sorted(names):
        path = root.joinpath(*name.split("/"))
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw = ("public body source member: " + name + "\n").encode("ascii")
        path.write_bytes(raw)
        path.chmod(0o600)
        snapshot[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "mode": 0o600,
        }
        total += len(raw)
    snapshot_digest = _digest(snapshot)
    return {
        "attempt_id": "body-attempt",
        "role": "body-study",
        "schema": "cloe-body-study-inner-plan-v1",
        "host_plan_sha256": "sha256:" + "a" * 64,
        "input_pin_sha256": "sha256:" + "b" * 64,
        "source_closure_sha256": "sha256:" + "c" * 64,
        "candidate_head": "1" * 40,
        "candidate_snapshot": snapshot,
        "candidate_snapshot_sha256": snapshot_digest,
        "source_snapshot_sha256": snapshot_digest,
        "source_members": list(source_members),
        "treatment_members": list(treatment_members),
    }, total


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def _body_allocation(root: Path) -> Allocation:
    path = root / "body-allocation.json"
    path.write_text(json.dumps({
        "schema": "cloe-pgvector-allocation-v1",
        "caps": BODY_CAPS,
        "used": {key: 0 for key in BODY_CAPS},
        "records": {},
    }, sort_keys=True))
    path.chmod(0o600)
    return Allocation(path, BODY_CAPS)


def test_source_and_closure_receipts_bind_plan_bytes_and_report(tmp_path: Path) -> None:
    """Ledger reservations and exported reports reject stale custody joins."""
    source_root = tmp_path / "candidate"
    ledger_root = tmp_path / "ledger"
    report_root = tmp_path / "report"
    for root in (source_root, ledger_root, report_root):
        root.mkdir(mode=0o700)
    plan, source_bytes = _plan_and_source(source_root)
    plan["inner"] = {"report_dir": str(report_root / "body-study"),
                     "scratch_dir": str(ledger_root)}

    source_receipt = reserve_inner_source_verification(
        plan, source_root, ledger_root=ledger_root,
    )
    assert source_receipt["bookkeeping_only"] is True
    assert source_receipt["execution_authorized"] is False
    assert source_receipt["consumed_reads"] == 1
    assert source_receipt["regular_file_bytes"] == source_bytes
    assert read_receipt(
        plan, ledger_root=ledger_root, receipt=source_receipt,
    )["remaining_reads"] == 15

    closure_receipt = reserve_inner_source_closure_guard(
        plan, source_root, ledger_root=ledger_root,
    )
    checked_closure = read_inner_source_closure_receipt(
        plan, ledger_root=ledger_root, receipt=closure_receipt,
    )
    assert checked_closure["bookkeeping_only"] is True
    assert checked_closure["execution_authorized"] is False
    assert checked_closure["consumed_guards"] == 1
    assert checked_closure["file_visits"] == (
        len(plan["source_members"]) + len(plan["treatment_members"])
        + len(WORKER_SOURCE_FILES) + len(_ACTIVE_MEMBERS)
    )

    counter_root = tmp_path / "counter-journal"
    counter_root.mkdir(mode=0o700)
    counter_root.chmod(0o700)
    binding = SnapshotCounterBinding(
        plan["attempt_id"], plan["host_plan_sha256"],
        plan["source_closure_sha256"], plan["candidate_snapshot_sha256"],
        plan["input_pin_sha256"], counter_root,
    )
    def read_snapshot(path: Path) -> bytes:
        return path.read_bytes()
    with BodySnapshotCounter(read_snapshot.__code__, binding=binding,
                             reserved_count=201) as counter:
        for _ in range(201):
            read_snapshot(source_root / "README.md")
    receipt = counter.receipt
    assert isinstance(receipt, SnapshotCounterReceipt) and receipt.valid
    receipt_raw = (json.dumps(receipt.to_dict(), sort_keys=True,
                              ensure_ascii=True, separators=(",", ":"),
                              allow_nan=False).encode("ascii") + b"\n")
    receipt_hash = "sha256:" + hashlib.sha256(receipt_raw).hexdigest()

    active_bytes = sum((source_root / name).stat().st_size for name in _ACTIVE_MEMBERS)
    budget = {
        "schema": "cloe-body-study-source-read-budget-v1",
        "source_read_receipt": source_receipt,
        "source_guard_receipt": checked_closure,
        "source_guard_count": 1, "source_guard_limit": 512,
        "source_guard_read_bytes": checked_closure["regular_file_bytes"],
        "active_guard_read_bytes": active_bytes,
        "active_guard_read_bytes_kind": "informational_subset",
        "registered_snapshot_count": 201,
        "registered_snapshot_count_reserved": 201,
        "registered_snapshot_count_kind": "measured_final_receipt",
        "registered_snapshot_file_bytes": active_bytes,
        "registered_snapshot_read_bytes": 201 * active_bytes,
        "registered_snapshot_read_bytes_kind": "reserved_upper_bound",
        "source_guard_total_read_bytes": checked_closure["regular_file_bytes"] + 201 * active_bytes,
        "source_guard_total_read_bytes_kind": "source_plus_final_reserved_upper_bound",
        "source_guard_byte_limit": 12 * 1024**3,
        "snapshot_counter_receipt_file": SNAPSHOT_RECEIPT_NAME,
        "snapshot_counter_receipt_sha256": receipt_hash,
        "snapshot_counter_receipt": receipt.to_dict(),
    }
    publish_member(report_root, SNAPSHOT_RECEIPT_NAME, receipt_raw)
    publish_member(report_root, "body-study-source-read-budget.json", _canonical(budget))
    # Model the canonical post-study source reservation and then export its
    # current ledgers. This remains synthetic bookkeeping, not study origin.
    reserve_inner_source_verification(plan, source_root, ledger_root=ledger_root)
    refreshed = refresh_body_source_read_report(plan, source_root=source_root,
        expected_registered_snapshot_count=201, charge_bytes=lambda _count: None)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name in ("body-study-source-read-budget.json", SNAPSHOT_RECEIPT_NAME,
                     SOURCE_RECORD_NAME, CLOSURE_RECORD_NAME):
            archive.add(report_root / name, arcname=name)
    exported_root = tmp_path / "exported-report"
    export_sandbox_report(stream.getvalue(), exported_root,
                          max_members=8, max_content_bytes=128 * 1024)
    privatize_body_report_export(exported_root)
    charges: list[int] = []
    exported = validate_exported_source_read_receipt(
        exported_root,
        inner_plan=plan,
        source_root=source_root,
        ledger_root=exported_root,
        regular_file_bytes=source_bytes,
        active_file_bytes=active_bytes,
        registered_snapshot_file_bytes=active_bytes,
        expected_registered_snapshot_count=201,
        charge_bytes=charges.append,
    )
    assert exported == refreshed
    assert exported["source_read_receipt"]["consumed_reads"] == 2
    assert exported["source_guard_receipt"]["consumed_guards"] == 1
    assert charges == [65537 + MAX_SNAPSHOT_RECEIPT_BYTES + 2 * (16384 + 1)]
    assert validate_exported_closure_receipt(
        plan, checked_closure,
        regular_file_bytes=checked_closure["regular_file_bytes"],
    ) == checked_closure

    stale_plan = dict(plan)
    stale_plan["candidate_head"] = "2" * 40
    with pytest.raises(InnerReadRefusal, match="counter_plan_mismatch"):
        read_receipt(stale_plan, ledger_root=ledger_root)

    alias = tmp_path / "candidate-alias"
    alias.symlink_to(source_root, target_is_directory=True)
    with pytest.raises(InnerReadRefusal, match="repo_root_path"):
        reserve_inner_source_verification(plan, alias, ledger_root=ledger_root)

    changed = source_root / "README.md"
    changed.write_bytes(changed.read_bytes() + b"size drift\n")
    with pytest.raises(InnerReadRefusal, match="source_size_changed"):
        reserve_inner_source_verification(plan, source_root, ledger_root=ledger_root)

    pending = counter_path_for_plan(plan, ledger_root=ledger_root)
    pending = pending.with_name(pending.name + ".next")
    pending.write_bytes(b"{}")
    pending.chmod(0o600)
    try:
        with pytest.raises(InnerReadRefusal, match="pending_journal"):
            read_receipt(plan, ledger_root=ledger_root)
    finally:
        pending.unlink()


def test_body_sdk_and_host_terminal_records_persist_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SDK settlement and host settlement retain distinct durable records."""
    source_root = tmp_path / "candidate"
    source_root.mkdir(mode=0o700)
    source = source_root / "candidate.py"
    source.write_bytes(b"# public candidate\n")
    source.chmod(0o644)
    plan = {
        "candidate_head": "a" * 40,
        "candidate_snapshot": {
            "candidate.py": {
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "mode": 0o644,
            },
        },
    }
    allocation = _body_allocation(tmp_path)
    context = SimpleNamespace(
        plan=plan,
        repo_root=source_root,
        allocation=lambda: allocation,
        fixed=lambda: None,
    )
    adapter = object.__new__(BodyOfflineSandbox)
    from runner_pgvector_context import OfflineContext
    adapter.context = cast(OfflineContext, context)
    checkout_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        "runner_pgvector_context.verify_checkout",
        lambda *args: checkout_calls.append(args),
    )

    adapter.finished(0)
    assert len(checkout_calls) == 1
    assert checkout_calls[0][0] == source_root
    assert allocation.path.stat().st_mode & 0o777 == 0o600
    with Allocation(allocation.path, BODY_CAPS).session() as ledger:
        assert set(ledger.records) == {"body_study_sdk_result"}
        assert ledger.records["body_study_sdk_result"] == {
            "exit": 0,
            "cleanup_proven": True,
            "candidate_head": plan["candidate_head"],
            "candidate_snapshot_sha256": offline_digest(json_bytes(plan["candidate_snapshot"])),
        }

    terminal = {
        "schema": "cloe-body-study-result-v1",
        "status": "synthetic_settlement",
        "settlement": "functional_only_no_sdk_origin",
        "origin_proven": False,
        "execution_authority_granted": False,
        "scientific_acceptance": False,
        "counters": {"sdk_finished": 1, "body_workers": 0, "native_queries": 0},
    }
    HostMeter(
        Allocation(allocation.path, BODY_CAPS), started=100.0, clock=lambda: 100.0,
    ).record_result(terminal)

    with Allocation(allocation.path, BODY_CAPS).session() as ledger:
        assert set(ledger.records) == {"body_study_sdk_result", "body_study_result"}
        assert ledger.records["body_study_result"] == terminal
        assert ledger.records["body_study_result"]["origin_proven"] is False
        with pytest.raises(RuntimeError, match="already bound allocation record"):
            ledger.bind("body_study_sdk_result", {"exit": 99})
