from __future__ import annotations
from collections.abc import Callable, Mapping
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, TypeVar, cast
from retrieval_body_study_snapshot_counter import (
    BodySnapshotCounter,
    CANONICAL_RESERVED_SNAPSHOTS,
    MAX_RECEIPT_BYTES,
    SnapshotCounterBinding,
    SnapshotCounterReceipt,
)
from retrieval_body_study_values import REGISTERED_INPUT_PIN_SHA256, require
_ACTIVE_COUNTER: BodySnapshotCounter | None = None
_MAX_BUDGET_BYTES = 128 * 1024
_BodyDriver = TypeVar("_BodyDriver", bound=Callable[[Any], Any])
def current_snapshot_counter() -> BodySnapshotCounter:
    counter = _ACTIVE_COUNTER
    require(counter is not None, "body.snapshot_counter:inactive")
    assert counter is not None
    return counter
def make_pg_byte_charger(limit: int) -> tuple[Callable[[int], None], dict[str, int]]:
    require(type(limit) is int and 0 < limit <= 12 * 1024**3,
            "body.snapshot_counter:pg_limit")
    state = {"limit": limit, "reserved": 0}
    def charge(amount: int) -> None:
        require(type(amount) is int and amount > 0, "body.snapshot_counter:pg_charge")
        state["reserved"] += amount
        require(state["reserved"] <= state["limit"], "body.snapshot_counter:pg_budget")
    return charge, state
def _counter_root(workspace: Path) -> Path:
    root = workspace / "snapshot-counter"
    require(not root.is_symlink(), "body.snapshot_counter:root_symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700,
            "body.snapshot_counter:root_custody")
    if hasattr(os, "getuid"):
        require(info.st_uid == os.getuid(), "body.snapshot_counter:root_owner")
    return root
def _receipt_bytes(receipt: SnapshotCounterReceipt) -> bytes:
    raw = json.dumps(receipt.to_dict(), sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")
    require(len(raw) + 1 <= MAX_RECEIPT_BYTES, "body.snapshot_counter:receipt_bound")
    return raw + b"\n"
def export_snapshot_counter_receipt(
    report_root: Path,
    receipt: SnapshotCounterReceipt,
    publish_member: Callable[[Path, str, bytes], object],
) -> None:
    """Publish an invalid or valid receipt without claiming a source budget."""
    require(type(receipt) is SnapshotCounterReceipt,
            "body.snapshot_counter:receipt_type")
    require(receipt.binding.receipt_name == "body-study-snapshot-counter.json",
            "body.snapshot_counter:receipt_name")
    require(isinstance(report_root, Path) and report_root.is_absolute(),
            "body.snapshot_counter:report_root")
    require(callable(publish_member), "body.snapshot_counter:publisher")
    publish_member(report_root, receipt.binding.receipt_name, _receipt_bytes(receipt))
def export_final_snapshot_budget(
    report_root: Path,
    pending: Mapping[str, Any],
    receipt: SnapshotCounterReceipt,
    publish_member: Callable[[Path, str, bytes], object],
) -> dict[str, Any]:
    require(type(receipt) is SnapshotCounterReceipt and receipt.valid,
            "body.snapshot_counter:final_receipt")
    require(receipt.binding.receipt_name == "body-study-snapshot-counter.json",
            "body.snapshot_counter:receipt_name")
    require(receipt.observed_count == receipt.reserved_count == CANONICAL_RESERVED_SNAPSHOTS,
            "body.snapshot_counter:final_count")
    require(isinstance(report_root, Path) and report_root.is_absolute(),
            "body.snapshot_counter:report_root")
    require(isinstance(pending, Mapping) and callable(publish_member),
            "body.snapshot_counter:pending")
    receipt_raw = _receipt_bytes(receipt)
    receipt_name = "body-study-snapshot-counter.json"
    result = dict(pending)
    file_bytes = int(result["registered_snapshot_file_bytes"])
    source_bytes = int(result["source_guard_read_bytes"])
    reserved = receipt.reserved_count
    snapshot_bytes = reserved * file_bytes
    total = source_bytes + snapshot_bytes
    require(total <= int(result["source_guard_byte_limit"]),
            "body.snapshot_counter:source_budget")
    result.update({
        "registered_snapshot_count": receipt.observed_count,
        "registered_snapshot_count_reserved": reserved,
        "registered_snapshot_count_kind": "measured_final_receipt",
        "registered_snapshot_read_bytes": snapshot_bytes,
        "registered_snapshot_read_bytes_kind": "reserved_upper_bound",
        "source_guard_total_read_bytes": total,
        "source_guard_total_read_bytes_kind": "source_plus_final_reserved_upper_bound",
        "snapshot_counter_receipt_file": receipt_name,
        "snapshot_counter_receipt_sha256": "sha256:" + hashlib.sha256(receipt_raw).hexdigest(),
        "snapshot_counter_receipt": receipt.to_dict(),
    })
    raw = json.dumps({
        "schema": "cloe-body-study-source-read-budget-v1",
        **result,
    }, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
       allow_nan=False).encode("ascii") + b"\n"
    require(len(raw) <= _MAX_BUDGET_BYTES, "body.snapshot_counter:budget_bound")
    publish_member(report_root, receipt_name, receipt_raw)
    publish_member(report_root, "body-study-source-read-budget.json", raw)
    return result
def wrap_authenticated_body_study(
    inner: _BodyDriver,
) -> _BodyDriver:
    require(callable(inner), "body.snapshot_counter:inner_callable")
    require(getattr(inner, "__name__", None) == "run_authenticated_body_study",
            "body.snapshot_counter:inner_origin")
    @wraps(inner)
    def wrapped(admission: Any) -> Any:
        from runner_pgvector_prebuilt import INNER_PLAN
        from retrieval_body_pin import RegisteredBodyInputs
        from retrieval_body_study_plan import (
            BodyStudyAdmission, admission_from_inner_plan,
            read_inner_body_plan, require_admission_current,
        )
        require(type(admission) is BodyStudyAdmission, "body.inner:admission_type")
        plan_path = Path(INNER_PLAN)
        plan = read_inner_body_plan(plan_path)
        expected = admission_from_inner_plan(plan)
        require(admission.to_dict() == expected.to_dict(),
                "body.snapshot_counter:admission_join")
        require_admission_current(admission, plan)
        workspace = Path(plan["inner"]["scratch_dir"])
        report_root = Path(plan["inner"]["report_dir"]).parent
        require(report_root.is_absolute() and report_root.resolve() == report_root,
                "body.snapshot_counter:report_root")
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        report_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root = _counter_root(workspace)
        binding = SnapshotCounterBinding(
            attempt_id=admission.attempt_id,
            plan_sha256=plan["host_plan_sha256"],
            source_closure_sha256=plan["source_closure_sha256"],
            candidate_snapshot_sha256=plan["candidate_snapshot_sha256"],
            input_pin_sha256=REGISTERED_INPUT_PIN_SHA256,
            counter_root=root,
        )
        code = RegisteredBodyInputs._snapshot.__code__
        global _ACTIVE_COUNTER
        counter: BodySnapshotCounter | None = None
        outcome: Any = None
        try:
            with BodySnapshotCounter(code, binding=binding,
                                     reserved_count=CANONICAL_RESERVED_SNAPSHOTS) as active_counter:
                counter = active_counter
                _ACTIVE_COUNTER = active_counter
                try:
                    outcome = inner(admission)
                finally:
                    _ACTIVE_COUNTER = None
        except BaseException:
            receipt = counter.receipt if counter is not None else None
            if (type(receipt) is SnapshotCounterReceipt
                    and receipt.valid is False):
                try:
                    from retrieval_body_study_files import publish_member
                    export_snapshot_counter_receipt(report_root, receipt, publish_member)
                except BaseException:
                    pass
            raise
        assert counter is not None
        receipt = counter.receipt
        require(type(receipt) is SnapshotCounterReceipt and receipt.valid,
                "body.snapshot_counter:receipt_missing")
        assert isinstance(receipt, SnapshotCounterReceipt) and outcome is not None
        cleanup = outcome.report.get("cleanup") if isinstance(outcome.report, Mapping) else None
        pending = cleanup.get("source_read_budget_pending") if isinstance(cleanup, Mapping) else None
        require(isinstance(pending, Mapping), "body.snapshot_counter:budget_pending")
        assert isinstance(pending, Mapping)
        from retrieval_body_study_files import publish_member
        export_final_snapshot_budget(report_root, pending, receipt, publish_member)
        return outcome
    return cast(_BodyDriver, wrapped)
__all__ = ["current_snapshot_counter", "export_snapshot_counter_receipt",
           "export_final_snapshot_budget",
           "make_pg_byte_charger", "wrap_authenticated_body_study"]
