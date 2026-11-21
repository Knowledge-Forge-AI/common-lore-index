"""Durable bookkeeping for bounded inner source-verification traversals; no source reads, checkout verification, or execution authority."""
from __future__ import annotations
from collections.abc import Mapping
import fcntl
import json
import os
from pathlib import Path
import stat
import time
from typing import Any
from retrieval_body_study_inner_read_plan import (
    InnerReadRefusal as InnerReadRefusal, _PlanInfo, _canonical, _refuse, _relative, _validate_plan,
)
from retrieval_body_study_contracts import INNER_SOURCE_READ_LIMIT
READ_LIMIT = INNER_SOURCE_READ_LIMIT
MAX_REGULAR_FILE_BYTES = 128 * 1024**2
MAX_RECORD_BYTES = 128 * 1024
DEFAULT_LEDGER_ROOT = Path("/sandbox-scratch/pgvector")
LEDGER_PREFIX = "body-study-inner-source-read-"
LEDGER_SUFFIX = ".json"
LOCK_SUFFIX = ".lock"
PENDING_SUFFIX = ".next"
LEDGER_SCHEMA = "cloe-body-study-inner-source-read-ledger-v1"
RECEIPT_SCHEMA = "cloe-body-study-inner-source-read-receipt-v1"
_LEDGER_FIELDS = frozenset({
    "schema", "plan_sha256", "role", "inner_schema", "candidate_head",
    "source_snapshot_sha256", "regular_file_bytes", "consumed_reads",
    "read_limit", "bookkeeping_only"})
_RECEIPT_FIELDS = _LEDGER_FIELDS | {"remaining_reads", "execution_authorized"}
def inner_plan_digest(plan: Mapping[str, Any]) -> str:
    """Return the exact plan commitment recorded by the private counter."""
    return _validate_plan(plan).plan_sha256
def _checked_root(root: Path) -> Path:
    _refuse(isinstance(root, Path), "ledger_root_type")
    _refuse(root.is_absolute() and not root.is_symlink(), "ledger_root_path")
    try:
        details = os.lstat(root)
    except OSError as error:
        raise InnerReadRefusal("ledger_root_unavailable") from error
    _refuse(stat.S_ISDIR(details.st_mode), "ledger_root_directory")
    _refuse(details.st_uid == os.getuid() and stat.S_IMODE(details.st_mode) == 0o700,
            "ledger_root_custody")
    for ancestor in root.parents:
        try:
            _refuse(not stat.S_ISLNK(os.lstat(ancestor).st_mode), "ledger_parent_link")
        except OSError as error:
            raise InnerReadRefusal("ledger_parent_unavailable") from error
    return root
def _ledger_path(info: _PlanInfo, root: Path) -> Path:
    return root / (LEDGER_PREFIX + "current" + LEDGER_SUFFIX)
def counter_path_for_plan(plan: Mapping[str, Any], ledger_root: Path = DEFAULT_LEDGER_ROOT) -> Path:
    """Return the single SDK counter path; a changed plan never resets it."""
    return _ledger_path(_validate_plan(plan), _checked_root(ledger_root))
def _private_file(path: Path, *, allow_missing: bool = False) -> os.stat_result | None:
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        _refuse(allow_missing, "private_file_missing")
        return None
    except OSError as error:
        raise InnerReadRefusal("private_file_unavailable") from error
    _refuse(stat.S_ISREG(details.st_mode) and not stat.S_ISLNK(details.st_mode), "private_file_type")
    _refuse(details.st_uid == os.getuid() and stat.S_IMODE(details.st_mode) == 0o600
            and details.st_nlink == 1, "private_file_custody")
    return details
def _require_absent(path: Path, code: str) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise InnerReadRefusal(code) from error
    raise InnerReadRefusal(code)
def _open_lock(path: Path, timeout: float) -> int:
    _refuse(type(timeout) in (int, float) and not isinstance(timeout, bool)
            and 0 <= timeout <= 60, "lock_timeout")
    _private_file(path, allow_missing=True)
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as error:
        raise InnerReadRefusal("lock_unavailable") from error
    try:
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + float(timeout)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise InnerReadRefusal("lock_busy")
                time.sleep(min(0.01, max(0.001, deadline - time.monotonic())))
        _private_file(path)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise
def _close_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
def _source_bytes(root: Path, snapshot: Mapping[str, Any]) -> int:
    _refuse(isinstance(root, Path) and root.is_absolute() and not root.is_symlink(), "repo_root_path")
    total = 0
    for name, row in sorted(snapshot.items()):
        relative = _relative(name)
        path = root.joinpath(*relative.split("/"))
        parent = path.parent
        while parent != root:
            try:
                _refuse(not stat.S_ISLNK(os.lstat(parent).st_mode), "source_path_link")
            except OSError as error:
                raise InnerReadRefusal("source_path_unavailable") from error
            parent = parent.parent
        try:
            details = os.lstat(path)
        except OSError as error:
            raise InnerReadRefusal("source_path_unavailable") from error
        if set(row) == {"symlink"}:
            _refuse(stat.S_ISLNK(details.st_mode), "source_symlink_required")
            try:
                _refuse(os.readlink(path) == row["symlink"], "source_symlink_drift")
            except OSError as error:
                raise InnerReadRefusal("source_symlink_unavailable") from error
            continue
        _refuse(stat.S_ISREG(details.st_mode), "source_regular_required")
        total += details.st_size
        _refuse(total <= MAX_REGULAR_FILE_BYTES, "source_bytes_cap")
    return total
def _read_private_json(path: Path) -> dict[str, Any]:
    before = _private_file(path)
    assert before is not None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise InnerReadRefusal("counter_unavailable") from error
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(8192, MAX_RECORD_BYTES - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            _refuse(total <= MAX_RECORD_BYTES, "counter_size_cap")
        after = os.fstat(descriptor)
    except OSError as error:
        raise InnerReadRefusal("counter_read_failed") from error
    finally:
        os.close(descriptor)
    _refuse((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns), "counter_changed")
    raw = b"".join(chunks)
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            _refuse(key not in result, "counter_duplicate_key")
            result[key] = value
        return result
    def reject_constant(_value: str) -> None:
        raise ValueError("nonfinite")
    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=unique,
                           parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise InnerReadRefusal("counter_not_canonical") from None
    _refuse(type(value) is dict and _canonical(value) + b"\n" == raw, "counter_not_canonical")
    record = dict(value)
    _refuse(set(record) == _LEDGER_FIELDS, "counter_fields")
    return record
def _publish(path: Path, record: Mapping[str, Any]) -> None:
    pending = path.with_name(path.name + PENDING_SUFFIX)
    _require_absent(pending, "pending_journal")
    raw = _canonical(dict(record)) + b"\n"
    _refuse(len(raw) <= MAX_RECORD_BYTES, "counter_size_cap")
    descriptor = -1
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as target:
            descriptor = -1
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        _private_file(pending)
        os.replace(pending, path)
        _private_file(path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except InnerReadRefusal:
        raise
    except BaseException as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise InnerReadRefusal("counter_publish_failed") from error
def _state(info: _PlanInfo, source_bytes: int, count: int) -> dict[str, Any]:
    return {"schema": LEDGER_SCHEMA, "plan_sha256": info.plan_sha256,
            "role": info.role, "inner_schema": info.inner_schema,
            "candidate_head": info.candidate_head,
            "source_snapshot_sha256": info.source_snapshot_sha256,
            "regular_file_bytes": source_bytes, "consumed_reads": count,
            "read_limit": READ_LIMIT, "bookkeeping_only": True}
def _validate_state(record: Mapping[str, Any], info: _PlanInfo,
                    source_bytes: int | None) -> dict[str, Any]:
    _refuse(set(record) == _LEDGER_FIELDS, "counter_fields")
    _refuse(record["schema"] == LEDGER_SCHEMA and record["plan_sha256"] == info.plan_sha256,
            "counter_plan_mismatch")
    _refuse(record["role"] == info.role and record["inner_schema"] == info.inner_schema
            and record["candidate_head"] == info.candidate_head
            and record["source_snapshot_sha256"] == info.source_snapshot_sha256,
            "counter_plan_mismatch")
    if source_bytes is not None:
        _refuse(record["regular_file_bytes"] == source_bytes, "source_size_changed")
    _refuse(type(record["regular_file_bytes"]) is int and record["regular_file_bytes"] >= 0
            and type(record["consumed_reads"]) is int
            and not isinstance(record["consumed_reads"], bool)
            and 0 <= record["consumed_reads"] <= READ_LIMIT
            and type(record["read_limit"]) is int
            and record["read_limit"] == READ_LIMIT and record["bookkeeping_only"] is True,
            "counter_state")
    return dict(record)
def _receipt(state: Mapping[str, Any]) -> dict[str, Any]:
    count = int(state["consumed_reads"])
    return {**dict(state), "schema": RECEIPT_SCHEMA,
            "remaining_reads": READ_LIMIT - count, "execution_authorized": False}
def _validate_receipt(value: Mapping[str, Any], info: _PlanInfo,
                      state: Mapping[str, Any]) -> None:
    _refuse(isinstance(value, Mapping) and set(value) == _RECEIPT_FIELDS, "receipt_fields")
    _refuse(value["schema"] == RECEIPT_SCHEMA and value["plan_sha256"] == info.plan_sha256,
            "receipt_plan_mismatch")
    for key in ("role", "inner_schema", "candidate_head", "source_snapshot_sha256",
                "regular_file_bytes"):
        _refuse(value[key] == state[key], "receipt_plan_mismatch")
    _refuse(type(value["consumed_reads"]) is int
            and 1 <= value["consumed_reads"] <= state["consumed_reads"], "receipt_count")
    _refuse(type(value["read_limit"]) is int and value["read_limit"] == READ_LIMIT
            and type(value["regular_file_bytes"]) is int
            and type(value["remaining_reads"]) is int and value["bookkeeping_only"] is True
            and value["execution_authorized"] is False
            and value["remaining_reads"] == READ_LIMIT - value["consumed_reads"], "receipt_state")
def validate_exported_receipt(plan: Mapping[str, Any], value: Mapping[str, Any], *,
                              regular_file_bytes: int) -> dict[str, Any]:
    """Join exported bookkeeping to the host's exact projected plan and size."""
    info = _validate_plan(plan)
    _refuse(isinstance(value, Mapping) and set(value) == _RECEIPT_FIELDS, "receipt_fields")
    state = {key: value[key] for key in _LEDGER_FIELDS}
    state["schema"] = LEDGER_SCHEMA
    state = _validate_state(state, info, regular_file_bytes)
    _validate_receipt(value, info, state)
    return dict(value)
def reserve_inner_source_verification(
    plan: Mapping[str, Any], repo_root: Path,
    ledger_root: Path = DEFAULT_LEDGER_ROOT, *, lock_timeout: float = 5.0,
) -> dict[str, Any]:
    """Reserve one complete verification before the caller verifies source."""
    info = _validate_plan(plan)
    _refuse(isinstance(repo_root, Path) and repo_root.is_absolute() and not repo_root.is_symlink(),
            "repo_root_path")
    try:
        _refuse(repo_root.resolve() == repo_root, "repo_root_path")
    except OSError as error:
        raise InnerReadRefusal("repo_root_unavailable") from error
    source_bytes = _source_bytes(repo_root, info.snapshot)
    root = _checked_root(ledger_root)
    path = _ledger_path(info, root)
    descriptor = _open_lock(path.with_name(path.name + LOCK_SUFFIX), lock_timeout)
    try:
        _require_absent(path.with_name(path.name + PENDING_SUFFIX), "pending_journal")
        try:
            current = _read_private_json(path)
        except InnerReadRefusal as error:
            if error.code != "private_file_missing":
                raise
            current = None
        count = 1 if current is None else int(_validate_state(current, info, source_bytes)["consumed_reads"]) + 1
        _refuse(count <= READ_LIMIT, "read_budget_exhausted")
        next_state = _state(info, source_bytes, count)
        _publish(path, next_state)
        return _receipt(next_state)
    finally:
        _close_lock(descriptor)
def read_receipt(
    plan: Mapping[str, Any], *, ledger_root: Path = DEFAULT_LEDGER_ROOT,
    receipt: Mapping[str, Any] | None = None, lock_timeout: float = 5.0,
) -> dict[str, Any]:
    """Verify plan-bound durable state and return current bookkeeping counts."""
    info = _validate_plan(plan)
    root = _checked_root(ledger_root)
    path = _ledger_path(info, root)
    descriptor = _open_lock(path.with_name(path.name + LOCK_SUFFIX), lock_timeout)
    try:
        _require_absent(path.with_name(path.name + PENDING_SUFFIX), "pending_journal")
        state = _validate_state(_read_private_json(path), info, None)
        if receipt is not None:
            _validate_receipt(receipt, info, state)
        return _receipt(state)
    finally:
        _close_lock(descriptor)
def reserve_inner_source_closure_guard(plan: Mapping[str, Any], repo_root: Path,
        ledger_root: Path = DEFAULT_LEDGER_ROOT, *, lock_timeout: float = 5.0) -> dict[str, Any]:
    from retrieval_body_study_inner_guard_reads import reserve_inner_source_closure_guard as reserve
    return reserve(plan, repo_root, ledger_root, lock_timeout=lock_timeout)

def read_inner_source_closure_receipt(plan: Mapping[str, Any], *,
        ledger_root: Path = DEFAULT_LEDGER_ROOT, receipt: Mapping[str, Any] | None = None,
        lock_timeout: float = 5.0) -> dict[str, Any]:
    from retrieval_body_study_inner_guard_reads import read_inner_source_closure_receipt as read
    return read(plan, ledger_root=ledger_root, receipt=receipt, lock_timeout=lock_timeout)

__all__ = [
    "DEFAULT_LEDGER_ROOT", "InnerReadRefusal", "LEDGER_SCHEMA",
    "MAX_REGULAR_FILE_BYTES", "READ_LIMIT", "RECEIPT_SCHEMA",
    "counter_path_for_plan", "inner_plan_digest", "read_receipt",
    "reserve_inner_source_verification", "validate_exported_receipt",
    "reserve_inner_source_closure_guard", "read_inner_source_closure_receipt",
]
