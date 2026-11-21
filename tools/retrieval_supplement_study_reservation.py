"""Private, one-use reservation storage for the closed supplement study.

Reservation state is an attempt-owned capability.  Reads and terminal updates
are protected by an adjacent private lock; consumption validates the plan and
attempt while holding that lock and publishes exactly one durable replacement.
An interrupted replacement leaves its ``.next`` journal in place and refuses
future use instead of guessing which state won.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import stat
from typing import Any

from retrieval_supplement_study_contracts import (
    MAX_MODEL_WORKERS,
    MAX_RUNTIME_EXPORTS,
    MAX_RUNTIME_IMPORTS,
    STUDY_RESERVATION_SCHEMA,
    refuse,
)
from retrieval_supplement_primitives import canonical_bytes, digest_bytes, strict_json

MAX_RESERVATION_BYTES = 2 * 1024**2
_SHA = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_RESERVATION_FIELDS = frozenset({
    "schema", "plan_sha256", "attempt_id", "consumed", "cleanup", "status",
    "runtime_imports", "runtime_exports", "model_workers",
})
_CLEANUP_STATES = frozenset({"unstarted", "in_progress", "proven", "unknown", "failed"})
_PENDING_SUFFIX = ".next"
_LOCK_SUFFIX = ".lock"


def _digest(value: object, label: str) -> str:
    refuse(type(value) is str and _SHA.fullmatch(str(value)) is not None,
           f"study_reservation:{label}")
    return str(value)


def _parent_custody(path: Path) -> None:
    """Require an absolute path below an existing, private non-link directory."""
    refuse(isinstance(path, Path) and path.is_absolute(), "study_reservation:relative_path")
    refuse(not path.is_symlink(), "study_reservation:path_link")
    parent = path.parent
    try:
        parent_info = os.lstat(parent)
    except OSError:
        refuse(False, "study_reservation:parent_unavailable")
    refuse(stat.S_ISDIR(parent_info.st_mode), "study_reservation:parent_directory")
    refuse(parent_info.st_uid == os.getuid() and stat.S_IMODE(parent_info.st_mode) == 0o700,
           "study_reservation:parent_custody")
    for ancestor in path.parents:
        try:
            info = os.lstat(ancestor)
        except OSError:
            refuse(False, "study_reservation:ancestor_unavailable")
        refuse(not stat.S_ISLNK(info.st_mode), "study_reservation:ancestor_link")


def _private_file(path: Path, *, allow_missing: bool = False) -> None:
    _parent_custody(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        refuse(allow_missing, "study_reservation:file_missing")
        return
    except OSError:
        refuse(False, "study_reservation:file_unavailable")
    refuse(not stat.S_ISLNK(info.st_mode), "study_reservation:file_link")
    refuse(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
           and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
           "study_reservation:file_custody")


def private_file(path: Path) -> None:
    """Validate an existing private regular file without exposing its path."""
    _private_file(path)


def require_private_destination(path: Path) -> None:
    """Prove a private write-once destination is fresh before admission."""
    _private_file(path, allow_missing=True)
    refuse(not path.exists(), "study_reservation:destination_spent")
    pending = path.with_name(path.name + _PENDING_SUFFIX)
    _private_file(pending, allow_missing=True)
    refuse(not pending.exists(), "study_reservation:ambiguous_pending")


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new(path: Path, raw: bytes) -> None:
    """Create one complete private file; a failed write remains evidence."""
    _private_file(path, allow_missing=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _private_file(path)
    _fsync_parent(path)


def write_private_once(path: Path, record: Mapping[str, Any]) -> str:
    """Write a private record exactly once and return its byte digest."""
    require_private_destination(path)
    raw = canonical_bytes(dict(record)) + b"\n"
    refuse(len(raw) <= MAX_RESERVATION_BYTES, "study_reservation:record_cap")
    _write_new(path, raw)
    return digest_bytes(raw)


def _replace_locked(path: Path, record: Mapping[str, Any]) -> str:
    """Publish a replacement under the caller-held reservation lock."""
    _private_file(path)
    pending = path.with_name(path.name + _PENDING_SUFFIX)
    _private_file(pending, allow_missing=True)
    refuse(not pending.exists(), "study_reservation:ambiguous_pending")
    raw = canonical_bytes(dict(record)) + b"\n"
    refuse(len(raw) <= MAX_RESERVATION_BYTES, "study_reservation:record_cap")
    _write_new(pending, raw)
    try:
        os.replace(pending, path)
        _private_file(path)
        _fsync_parent(path)
    except BaseException:
        # A surviving ``.next`` is intentionally ambiguous.  If replace
        # succeeded but directory sync failed, the current file is still
        # private and the caller will retain the failure as terminal evidence.
        raise
    return digest_bytes(raw)


@contextmanager
def reservation_lock(path: Path) -> Iterator[None]:
    """Hold the exclusive lock used for every reservation state transition."""
    _parent_custody(path)
    lock_path = path.with_name(path.name + _LOCK_SUFFIX)
    _private_file(lock_path, allow_missing=True)
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+b") as lock:
            descriptor = -1
            fcntl.flock(lock, fcntl.LOCK_EX)
            _private_file(lock_path)
            yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _pending_state(path: Path) -> None:
    pending = path.with_name(path.name + _PENDING_SUFFIX)
    try:
        os.lstat(pending)
    except FileNotFoundError:
        return
    except OSError:
        refuse(False, "study_reservation:pending_unavailable")
    refuse(False, "study_reservation:ambiguous_pending")


def _read_unlocked(path: Path) -> dict[str, Any]:
    _private_file(path)
    _pending_state(path)
    refuse(path.stat().st_size <= MAX_RESERVATION_BYTES, "study_reservation:cap")
    value = strict_json(path.read_bytes())
    refuse(isinstance(value, dict) and set(value) == _RESERVATION_FIELDS,
           "study_reservation:fields")
    record = dict(value)
    refuse(record["schema"] == STUDY_RESERVATION_SCHEMA, "study_reservation:schema")
    _digest(record["plan_sha256"], "plan")
    refuse(type(record["attempt_id"]) is str and bool(record["attempt_id"].strip()),
           "study_reservation:attempt")
    refuse(type(record["consumed"]) is bool, "study_reservation:consumed")
    refuse(type(record["cleanup"]) is str and record["cleanup"] in _CLEANUP_STATES,
           "study_reservation:cleanup")
    for key, maximum in (
        ("runtime_imports", MAX_RUNTIME_IMPORTS),
        ("runtime_exports", MAX_RUNTIME_EXPORTS),
        ("model_workers", MAX_MODEL_WORKERS),
    ):
        refuse(type(record[key]) is int and 0 <= record[key] <= maximum,
               f"study_reservation:{key}")
    refuse(type(record["status"]) is str, "study_reservation:status")
    state = (record["consumed"], record["cleanup"], record["status"])
    refuse(state in {
        (False, "unstarted", "reserved"),
        (True, "in_progress", "admitted"),
        (True, "proven", "settled"),
        (True, "unknown", "blocked"),
        (True, "failed", "blocked"),
    }, "study_reservation:state")
    return record


def read_reservation(path: Path) -> dict[str, Any]:
    """Read one closed reservation while excluding a concurrent replacement."""
    with reservation_lock(path):
        return _read_unlocked(path)


def create_reservation(path: Path, *, plan_sha256: str, attempt_id: str) -> str:
    """Exclusively create a fresh unconsumed reservation."""
    _digest(plan_sha256, "plan")
    refuse(type(attempt_id) is str and bool(attempt_id.strip()), "study_reservation:attempt")
    record = {
        "schema": STUDY_RESERVATION_SCHEMA,
        "plan_sha256": plan_sha256,
        "attempt_id": attempt_id,
        "consumed": False,
        "cleanup": "unstarted",
        "status": "reserved",
        "runtime_imports": 0,
        "runtime_exports": 0,
        "model_workers": 0,
    }
    _parent_custody(path)
    with reservation_lock(path):
        _pending_state(path)
        _private_file(path, allow_missing=True)
        refuse(not path.exists(), "study_reservation:already_exists")
        return write_private_once(path, record)


def consume_reservation(
    path: Path, *, plan_sha256: str, attempt_id: str,
) -> dict[str, Any]:
    """Atomically consume a matching attempt before runner/SDK dispatch."""
    _digest(plan_sha256, "plan")
    refuse(type(attempt_id) is str and bool(attempt_id.strip()), "study_reservation:attempt")
    with reservation_lock(path):
        record = _read_unlocked(path)
        # Validate both bindings while the lock is held; no stale read may
        # consume a newer or different attempt.
        refuse(record["plan_sha256"] == plan_sha256, "study_reservation:stale_plan")
        refuse(record["attempt_id"] == attempt_id, "study_reservation:attempt_mismatch")
        refuse(record["consumed"] is False, "study_reservation:reused")
        refuse(record["cleanup"] == "unstarted", "study_reservation:partial")
        refuse(all(record[key] == 0 for key in ("runtime_imports", "runtime_exports", "model_workers")),
               "study_reservation:partial_runtime_spend")
        record.update(consumed=True, cleanup="in_progress", status="admitted")
        _replace_locked(path, record)
        return record


def settle_reservation(
    path: Path, *, plan_sha256: str, cleanup: str,
    runtime_imports: int = 0, runtime_exports: int = 0, model_workers: int = 0,
) -> dict[str, Any]:
    """Record terminal cleanup while preserving unknown/failed custody."""
    refuse(type(cleanup) is str and cleanup in {"proven", "unknown", "failed"},
           "study_reservation:cleanup_state")
    refuse(type(runtime_imports) is int and 0 <= runtime_imports <= MAX_RUNTIME_IMPORTS,
           "study_reservation:runtime_imports")
    refuse(type(runtime_exports) is int and runtime_exports == MAX_RUNTIME_EXPORTS,
           "study_reservation:runtime_exports")
    refuse(type(model_workers) is int and 0 <= model_workers <= MAX_MODEL_WORKERS,
           "study_reservation:model_workers")
    with reservation_lock(path):
        record = _read_unlocked(path)
        refuse(record["plan_sha256"] == plan_sha256, "study_reservation:stale_plan")
        refuse(record["consumed"] is True and record["cleanup"] == "in_progress",
               "study_reservation:not_in_progress")
        record.update(
            cleanup=cleanup,
            status="settled" if cleanup == "proven" else "blocked",
            runtime_imports=runtime_imports,
            runtime_exports=runtime_exports,
            model_workers=model_workers,
        )
        _replace_locked(path, record)
        return record


__all__ = [
    "MAX_RESERVATION_BYTES", "consume_reservation", "create_reservation",
    "private_file", "read_reservation", "reservation_lock", "require_private_destination",
    "settle_reservation", "write_private_once",
]
