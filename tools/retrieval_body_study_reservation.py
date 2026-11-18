"""Private, one-use BODY reservation custody.

Reservation consumption is an authority transition, not a successful-run
signal.  The transition is journaled with an exclusive lock and an fsynced
``.next`` record; a second consumer or an ambiguous settlement remains
unreusable.
"""
from __future__ import annotations

from collections.abc import Mapping
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

from retrieval_body_study_contracts import (
    BODY_PHASE, BODY_RESERVATION_SCHEMA, BODY_ROLE, BODY_CLEANUP_STATES, refuse, strict_json,
)
from retrieval_body_study_files import read_member

_HEX = set("0123456789abcdef")
_FIELDS = frozenset({
    "schema", "phase", "role", "nonce_sha256", "plan_sha256", "attempt_id",
    "source_closure_sha256", "treatment_sha256", "worker_closure_sha256",
    "protocol_sha256", "operator_decision_sha256", "consumed", "cleanup",
    "state", "status", "runtime_imports", "runtime_exports", "model_workers",
    "native_runtime_loads", "body_study_workers", "result_evidence",
})


def _digest(value: object, code: str, *, scheme: bool = False) -> str:
    text = value if type(value) is str else ""
    if scheme:
        good = len(text) == 71 and text.startswith("sha256:") and set(text[7:]) <= _HEX
    else:
        good = len(text) == 64 and set(text) <= _HEX
    refuse(good, code)
    return text


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=True, sort_keys=True,
                         separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def reservation_digest(record: Mapping[str, Any]) -> str:
    """Commit the complete record, including one-use and cleanup state."""
    return "sha256:" + hashlib.sha256(b"cloe-body-study-reservation-v1\0" +
                                       _canonical(record)).hexdigest()


def _private_path(path: Path) -> None:
    refuse(isinstance(path, Path) and path.is_absolute(), "body reservation path")
    for parent in (path, *path.parents):
        try:
            details = os.lstat(parent)
        except FileNotFoundError:
            refuse(parent == path, "body reservation parent unavailable")
            continue
        except OSError:
            raise ValueError("body reservation custody unavailable") from None
        refuse(not stat.S_ISLNK(details.st_mode), "body reservation path link")


def private_file(path: Path, *, allow_missing: bool = False) -> None:
    _private_path(path)
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        refuse(allow_missing, "body reservation missing")
        return
    except OSError:
        raise ValueError("body reservation unavailable") from None
    refuse(stat.S_ISREG(details.st_mode) and details.st_uid == os.getuid()
           and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1,
           "body reservation custody")


def require_private_destination(path: Path) -> None:
    _private_path(path)
    refuse(path.parent.is_dir() and stat.S_IMODE(path.parent.stat().st_mode) == 0o700,
           "body reservation parent custody")
    private_file(path, allow_missing=True)


def _publish(path: Path, record: Mapping[str, Any], *, replace: bool) -> None:
    require_private_destination(path)
    pending = path.with_name(path.name + ".next")
    private_file(pending, allow_missing=True)
    refuse(not pending.exists(), "body reservation pending journal")
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                        getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        raw = _canonical(record)
        with os.fdopen(descriptor, "wb") as target:
            descriptor = -1
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        private_file(pending)
        if replace:
            os.replace(pending, path)
        else:
            refuse(not path.exists(), "body reservation already exists")
            os.link(pending, path)
            os.unlink(pending)
        private_file(path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def write_private_once(path: Path, value: Mapping[str, Any]) -> str:
    _publish(path, value, replace=False)
    return reservation_digest(value)


def _read(path: Path) -> dict[str, Any]:
    private_file(path)
    raw = read_member(path.parent, path.name, max_bytes=128 * 1024)
    refuse(0 < len(raw) <= 128 * 1024, "body reservation size")
    try:
        value = strict_json(raw, maximum_bytes=128 * 1024)
    except (UnicodeDecodeError, ValueError):
        raise ValueError("body reservation JSON") from None
    refuse(isinstance(value, dict), "body reservation object")
    return validate_reservation(value)


def validate_result_evidence(value: object, *, plan_sha256: str, attempt_id: str) -> dict[str, Any] | None:
    """Bind retained bytes without claiming a missing report or journal exists."""
    if value is None:
        return None
    keys = {"schema", "plan_sha256", "attempt_id", "state",
            "report_sha256", "journal_sha256", "failure_sha256"}
    refuse(isinstance(value, Mapping) and set(value) == keys, "body result evidence keys")
    assert isinstance(value, Mapping)
    refuse(value["schema"] == "cloe-body-study-attempt-evidence-binding-v1"
           and value["plan_sha256"] == plan_sha256 and value["attempt_id"] == attempt_id
           and value["state"] in {"retained", "partial", "unavailable"},
           "body result evidence identity")
    for key in ("report_sha256", "journal_sha256", "failure_sha256"):
        if value[key] is not None:
            _digest(value[key], "body result evidence digest", scheme=True)
    present = any(value[key] is not None for key in ("report_sha256", "journal_sha256", "failure_sha256"))
    refuse(present == (value["state"] != "unavailable"), "body result evidence availability")
    refuse(value["state"] != "retained" or value["report_sha256"] is not None
           and value["journal_sha256"] is not None, "body result evidence completeness")
    return dict(value)


def validate_reservation(record: object) -> dict[str, Any]:
    refuse(isinstance(record, Mapping) and set(record) == _FIELDS,
           "body reservation closed keys")
    assert isinstance(record, Mapping)
    refuse(record["schema"] == BODY_RESERVATION_SCHEMA
           and record["phase"] == BODY_PHASE and record["role"] == BODY_ROLE,
           "body reservation identity")
    _digest(record["nonce_sha256"], "body reservation nonce_sha256")
    _digest(record["plan_sha256"], "body reservation plan_sha256", scheme=True)
    for key in ("source_closure_sha256", "treatment_sha256", "worker_closure_sha256",
                "protocol_sha256", "operator_decision_sha256"):
        _digest(record[key], f"body reservation {key}", scheme=True)
    attempt = record["attempt_id"]
    refuse(type(attempt) is str and 1 <= len(attempt) <= 77
           and attempt[0].isalnum() and all(ch.isalnum() or ch in "._-" for ch in attempt),
           "body reservation attempt")
    refuse(record["consumed"] in {False, True}
           and record["cleanup"] in {"unstarted", "in_progress", *BODY_CLEANUP_STATES}
           and record["state"] in {"reserved", "consumed", "settled"}
           and record["status"] in {"reserved", "admitted", "settled", "blocked"},
           "body reservation state")
    refuse(record["state"] == "reserved" and record["consumed"] is False
           and record["cleanup"] == "unstarted" and record["status"] == "reserved"
           or record["state"] == "consumed" and record["consumed"] is True
           and record["cleanup"] == "in_progress" and record["status"] == "admitted"
           or record["state"] == "settled" and record["consumed"] is True
           and record["cleanup"] in BODY_CLEANUP_STATES
           and record["status"] in {"settled", "blocked"},
           "body reservation transition")
    for key in ("runtime_imports", "runtime_exports", "model_workers",
                "native_runtime_loads", "body_study_workers"):
        value = record[key]
        refuse(type(value) is int and not isinstance(value, bool) and value >= 0,
               f"body reservation counter {key}")
    refuse(record["runtime_imports"] <= 1 and record["runtime_exports"] == 0
           and record["native_runtime_loads"] <= 1
           and record["body_study_workers"] <= 2 and record["model_workers"] <= 2,
           "body reservation cap")
    validate_result_evidence(record["result_evidence"], plan_sha256=record["plan_sha256"],
                             attempt_id=record["attempt_id"])
    refuse((record["state"] == "settled") == (record["result_evidence"] is not None),
           "body result evidence settlement state")
    return dict(record)


def read_reservation(path: Path) -> dict[str, Any]:
    return _read(path)


def create_reservation(
    path: Path, *, plan_sha256: str, attempt_id: str,
    source_closure_sha256: str | None = None, treatment_sha256: str | None = None,
    worker_closure_sha256: str | None = None, protocol_sha256: str | None = None,
    operator_decision_sha256: str | None = None,
    nonce_sha256: str | None = None,
) -> str:
    """Create a fresh private reservation; never overwrite an existing one."""
    _digest(plan_sha256, "body reservation plan", scheme=True)
    nonce = nonce_sha256 or hashlib.sha256(os.urandom(32)).hexdigest()
    record: dict[str, Any] = {
        "schema": BODY_RESERVATION_SCHEMA, "phase": BODY_PHASE, "role": BODY_ROLE,
        "nonce_sha256": nonce, "plan_sha256": plan_sha256, "attempt_id": attempt_id,
        "source_closure_sha256": source_closure_sha256 or "sha256:" + "1" * 64,
        "treatment_sha256": treatment_sha256 or "sha256:" + "2" * 64,
        "worker_closure_sha256": worker_closure_sha256 or "sha256:" + "3" * 64,
        "protocol_sha256": protocol_sha256 or "sha256:" + "4" * 64,
        "operator_decision_sha256": operator_decision_sha256 or "sha256:" + "5" * 64,
        "consumed": False, "cleanup": "unstarted", "state": "reserved", "status": "reserved",
        "runtime_imports": 0, "runtime_exports": 0, "model_workers": 0,
        "native_runtime_loads": 0, "body_study_workers": 0, "result_evidence": None,
    }
    validate_reservation(record)
    write_private_once(path, record)
    return reservation_digest(record)


def _lock(path: Path) -> int:
    lock_path = path.with_name(path.name + ".lock")
    require_private_destination(lock_path)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def consume_reservation(path: Path, *, plan_sha256: str, attempt_id: str | None = None) -> dict[str, Any]:
    """Atomically consume exactly once before canonical runner creation."""
    lock = _lock(path)
    try:
        record = _read(path)
        refuse(record["plan_sha256"] == plan_sha256 and
               (attempt_id is None or record["attempt_id"] == attempt_id),
               "body reservation plan mismatch")
        refuse(record["state"] == "reserved" and record["consumed"] is False,
               "body reservation already consumed")
        updated = {**record, "consumed": True, "cleanup": "in_progress",
                   "state": "consumed", "status": "admitted"}
        _publish(path, validate_reservation(updated), replace=True)
        return updated
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


def settle_reservation(
    path: Path, *, plan_sha256: str, cleanup: str,
    runtime_imports: int = 0, runtime_exports: int = 0, model_workers: int = 0,
    native_runtime_loads: int | None = None, body_study_workers: int | None = None,
    result_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Settle a consumed reservation; ``unknown`` and ``failed`` cannot be reused."""
    refuse(cleanup in BODY_CLEANUP_STATES, "body reservation cleanup")
    lock = _lock(path)
    try:
        record = _read(path)
        refuse(record["plan_sha256"] == plan_sha256 and record["state"] == "consumed"
               and record["consumed"] is True and record["cleanup"] == "in_progress",
               "body reservation settlement state")
        evidence = result_evidence if result_evidence is not None else {
            "schema": "cloe-body-study-attempt-evidence-binding-v1",
            "plan_sha256": plan_sha256, "attempt_id": record["attempt_id"],
            "state": "unavailable", "report_sha256": None,
            "journal_sha256": None, "failure_sha256": None,
        }
        updated = {**record, "cleanup": cleanup, "state": "settled",
                   "result_evidence": validate_result_evidence(evidence,
                       plan_sha256=plan_sha256, attempt_id=record["attempt_id"]),
                   "status": "settled" if cleanup == "proven" else "blocked",
                   "runtime_imports": runtime_imports,
                   "runtime_exports": runtime_exports,
                   "model_workers": model_workers,
                   "native_runtime_loads": (runtime_imports if native_runtime_loads is None
                                              else native_runtime_loads),
                   "body_study_workers": (model_workers if body_study_workers is None
                                          else body_study_workers)}
        updated = validate_reservation(updated)
        _publish(path, updated, replace=True)
        return updated
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


__all__ = [
    "consume_reservation", "create_reservation", "private_file",
    "read_reservation", "reservation_digest", "require_private_destination",
    "settle_reservation", "validate_reservation", "validate_result_evidence", "write_private_once",
]
