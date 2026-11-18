"""Refresh the BODY combined report after the canonical final source read.

The inner driver exports a report before the host's final plan load.  That
load reserves one more whole-source read.  This helper replaces only the
whole-read receipt, retaining the closure receipt and all reservation fields,
and copies the two final durable ledger records for host readback.  It is
bookkeeping, never execution authority.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import os
from pathlib import Path
import secrets
import stat
from typing import Any, cast

from retrieval_body_study_inner_guard_reads import (
    closure_guard_read_bound,
    read_inner_source_closure_receipt,
    validate_exported_closure_receipt,
)
from retrieval_body_study_inner_read_report import (
    INNER_SOURCE_READ_BYTES,
    MAX_SNAPSHOT_RECEIPT_BYTES,
    REPORT_FIELDS,
    REPORT_NAME,
    REPORT_SCHEMA,
    SNAPSHOT_RECEIPT_NAME,
    validate_snapshot_counter_receipt,
)
from retrieval_body_study_inner_reads import (
    InnerReadRefusal,
    read_receipt,
    READ_LIMIT,
    validate_exported_receipt,
)
from retrieval_body_study_files import read_member
from retrieval_body_study_files import publish_member
from retrieval_supplement_primitives import canonical_bytes, digest_bytes, strict_json


MAX_REPORT_BYTES = 65536
MAX_LEDGER_RECORD_BYTES = 16 * 1024
FINALIZER_PRECHARGE_BYTES = MAX_REPORT_BYTES + MAX_SNAPSHOT_RECEIPT_BYTES + 4 * MAX_LEDGER_RECORD_BYTES + 6
EXPECTED_REGISTERED_SNAPSHOT_COUNT = 201
SOURCE_RECORD_NAME = "body-study-inner-source-read-current.json"
CLOSURE_RECORD_NAME = "body-study-inner-closure-guard-current.json"
SOURCE_LEDGER_SCHEMA = "cloe-body-study-inner-source-read-ledger-v1"
SOURCE_RECEIPT_SCHEMA = "cloe-body-study-inner-source-read-receipt-v1"
CLOSURE_LEDGER_SCHEMA = "cloe-body-study-inner-closure-guard-ledger-v1"
CLOSURE_RECEIPT_SCHEMA = "cloe-body-study-inner-closure-guard-receipt-v1"
SOURCE_RECORD_FIELDS = frozenset({
    "schema", "plan_sha256", "role", "inner_schema", "candidate_head",
    "source_snapshot_sha256", "regular_file_bytes", "consumed_reads",
    "read_limit", "bookkeeping_only",
})
CLOSURE_RECORD_FIELDS = frozenset({
    "schema", "plan_sha256", "role", "inner_schema", "candidate_head",
    "source_snapshot_sha256", "regular_file_bytes", "file_visits",
    "reserved_bytes_per_guard", "consumed_guards", "guard_limit",
    "bookkeeping_only",
})
def _refuse(condition: object, code: str) -> None:
    if not condition:
        raise InnerReadRefusal(code)


def _integer(value: object, code: str, *, minimum: int = 0) -> int:
    _refuse(type(value) is int and value >= minimum, code)
    return cast(int, value)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    _refuse(isinstance(value, Mapping) and all(type(key) is str for key in value), code)
    return cast(Mapping[str, Any], value)
def _paths(inner_plan: Mapping[str, Any]) -> tuple[Path, Path]:
    inner = _mapping(inner_plan.get("inner"), "body_finalizer_inner")
    _refuse(set(inner) == {"report_dir", "scratch_dir"}, "body_finalizer_inner_fields")
    report_path = Path(inner["report_dir"]) if type(inner["report_dir"]) is str else Path()
    ledger_root = Path(inner["scratch_dir"]) if type(inner["scratch_dir"]) is str else Path()
    _refuse(report_path.is_absolute() and ledger_root.is_absolute(), "body_finalizer_paths")
    _refuse(report_path.resolve() == report_path and ledger_root.resolve() == ledger_root,
            "body_finalizer_path_links")
    report_root = report_path.parent
    return report_root, ledger_root


def _read_budget(report_root: Path) -> dict[str, Any]:
    try:
        raw = read_member(report_root, REPORT_NAME, max_bytes=MAX_REPORT_BYTES)
        value = strict_json(raw, maximum_bytes=MAX_REPORT_BYTES)
    except InnerReadRefusal:
        raise
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise InnerReadRefusal("body_finalizer_report_read") from error
    budget = _mapping(value, "body_finalizer_report_fields")
    _refuse(set(budget) == REPORT_FIELDS and budget.get("schema") == REPORT_SCHEMA,
            "body_finalizer_report_fields")
    return dict(budget)


def _read_snapshot_receipt(
    report_root: Path, inner_plan: Mapping[str, Any], expected_count: int,
) -> tuple[dict[str, Any], bytes]:
    try:
        raw = read_member(report_root, SNAPSHOT_RECEIPT_NAME,
                          max_bytes=MAX_SNAPSHOT_RECEIPT_BYTES)
        value = strict_json(raw, maximum_bytes=MAX_SNAPSHOT_RECEIPT_BYTES)
    except InnerReadRefusal:
        raise
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise InnerReadRefusal("body_finalizer_snapshot_receipt_read") from error
    receipt = validate_snapshot_counter_receipt(value, raw, inner_plan, expected_count)
    _refuse(digest_bytes(raw).startswith("sha256:"), "body_finalizer_snapshot_receipt_hash")
    return receipt, raw


def _read_record(
    ledger_root: Path,
    name: str,
    fields: frozenset[str],
    schema: str,
) -> tuple[dict[str, Any], bytes]:
    try:
        raw = read_member(ledger_root, name, max_bytes=MAX_LEDGER_RECORD_BYTES)
        value = strict_json(raw, maximum_bytes=MAX_LEDGER_RECORD_BYTES)
    except InnerReadRefusal:
        raise
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise InnerReadRefusal("body_finalizer_ledger_read") from error
    record = _mapping(value, "body_finalizer_ledger_fields")
    _refuse(set(record) == fields and record.get("schema") == schema,
            "body_finalizer_ledger_fields")
    _refuse(canonical_bytes(record, ascii_only=True) + b"\n" == raw,
            "body_finalizer_ledger_canonical")
    return dict(record), raw


def _source_receipt_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    consumed = _integer(record["consumed_reads"], "body_finalizer_source_record_count", minimum=1)
    limit = _integer(record["read_limit"], "body_finalizer_source_record_limit")
    _refuse(record["bookkeeping_only"] is True and limit == READ_LIMIT and consumed <= limit,
            "body_finalizer_source_record_state")
    return {
        **dict(record),
        "schema": SOURCE_RECEIPT_SCHEMA,
        "remaining_reads": limit - consumed,
        "execution_authorized": False,
    }


def _closure_receipt_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    consumed = _integer(record["consumed_guards"], "body_finalizer_guard_record_count", minimum=1)
    limit = _integer(record["guard_limit"], "body_finalizer_guard_record_limit")
    _refuse(record["bookkeeping_only"] is True and limit == 512 and consumed <= limit,
            "body_finalizer_guard_record_state")
    return {
        **dict(record),
        "schema": CLOSURE_RECEIPT_SCHEMA,
        "remaining_guards": limit - consumed,
        "execution_authorized": False,
    }


def _validate_preserved_budget(
    budget: Mapping[str, Any],
    guard: Mapping[str, Any],
    *,
    expected_snapshot_count: int,
    snapshot_receipt: Mapping[str, Any],
) -> None:
    source_count = _integer(budget["source_guard_count"], "body_finalizer_guard_count", minimum=1)
    _refuse(source_count == _integer(guard["consumed_guards"], "body_finalizer_guard_state"),
            "body_finalizer_guard_count")
    _refuse(_integer(budget["source_guard_limit"], "body_finalizer_guard_limit") == 512,
            "body_finalizer_guard_limit")
    _refuse(_integer(budget["registered_snapshot_count"], "body_finalizer_snapshot_count")
            == expected_snapshot_count, "body_finalizer_snapshot_count")
    _refuse(_integer(budget["registered_snapshot_count_reserved"], "body_finalizer_snapshot_reserved")
            == expected_snapshot_count
            and budget["registered_snapshot_count_kind"] == "measured_final_receipt"
            and budget["snapshot_counter_receipt_file"] == SNAPSHOT_RECEIPT_NAME
            and budget["snapshot_counter_receipt"] == snapshot_receipt
            and budget["registered_snapshot_count"] == snapshot_receipt["observed_count"],
            "body_finalizer_snapshot_receipt_join")
    closure_bytes = _integer(guard["regular_file_bytes"], "body_finalizer_closure_bytes", minimum=1)
    source_read = _integer(budget["source_guard_read_bytes"], "body_finalizer_source_read")
    _integer(budget["active_guard_read_bytes"], "body_finalizer_active_read")
    _refuse(budget["active_guard_read_bytes_kind"] == "informational_subset",
            "body_finalizer_active_read_kind")
    snapshot_bytes = _integer(
        budget["registered_snapshot_file_bytes"], "body_finalizer_snapshot_bytes", minimum=1,
    )
    snapshot_read = _integer(budget["registered_snapshot_read_bytes"], "body_finalizer_snapshot_read")
    _refuse(budget["registered_snapshot_read_bytes_kind"] == "reserved_upper_bound",
            "body_finalizer_snapshot_read_kind")
    total = _integer(budget["source_guard_total_read_bytes"], "body_finalizer_total_read")
    limit = _integer(budget["source_guard_byte_limit"], "body_finalizer_byte_limit")
    _refuse(source_read == source_count * closure_bytes, "body_finalizer_source_algebra")
    _refuse(snapshot_read == expected_snapshot_count * snapshot_bytes,
            "body_finalizer_snapshot_algebra")
    # Active bytes are an explicit subset/stat bound: closure bytes already
    # include those paths, so they are not charged again in the total.
    _refuse(total == source_read + snapshot_read and total <= limit,
            "body_finalizer_total_algebra")
    _refuse(budget["source_guard_total_read_bytes_kind"] == "source_plus_final_reserved_upper_bound",
            "body_finalizer_total_kind")
    _refuse(limit == INNER_SOURCE_READ_BYTES, "body_finalizer_byte_limit")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _private_report_dir(root: Path) -> int:
    _refuse(root.is_absolute() and root.resolve() == root, "body_finalizer_report_root")
    try:
        info = os.lstat(root)
        _refuse(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
                "body_finalizer_report_root")
        descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                             | getattr(os, "O_NOFOLLOW", 0))
    except InnerReadRefusal:
        raise
    except (OSError, TypeError) as error:
        raise InnerReadRefusal("body_finalizer_report_root") from error
    return descriptor


def _assert_report_target(root: Path) -> None:
    parent = _private_report_dir(root)
    try:
        try:
            info = os.stat(REPORT_NAME, dir_fd=parent, follow_symlinks=False)
        except OSError as error:
            raise InnerReadRefusal("body_finalizer_report_missing") from error
        _refuse(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                and info.st_size <= MAX_REPORT_BYTES, "body_finalizer_report_custody")
    finally:
        os.close(parent)


def _replace_report(report_root: Path, raw: bytes) -> None:
    _refuse(type(raw) is bytes and 0 < len(raw) <= MAX_REPORT_BYTES,
            "body_finalizer_report_size")
    parent = _private_report_dir(report_root)
    temporary = f".{REPORT_NAME}.{secrets.token_hex(8)}.tmp"
    descriptor = -1
    owned: tuple[int, int] | None = None
    try:
        try:
            before = os.stat(REPORT_NAME, dir_fd=parent, follow_symlinks=False)
        except OSError as error:
            raise InnerReadRefusal("body_finalizer_report_missing") from error
        _refuse(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and before.st_size <= MAX_REPORT_BYTES, "body_finalizer_report_custody")
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent,
            )
            created = os.fstat(descriptor)
            owned = (created.st_dev, created.st_ino)
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                _refuse(written > 0, "body_finalizer_report_write")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            _refuse(_identity(os.stat(REPORT_NAME, dir_fd=parent, follow_symlinks=False))
                    == _identity(before), "body_finalizer_report_changed")
            os.replace(temporary, REPORT_NAME, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
            after = os.stat(REPORT_NAME, dir_fd=parent, follow_symlinks=False)
            _refuse(stat.S_ISREG(after.st_mode) and after.st_uid == os.getuid()
                    and stat.S_IMODE(after.st_mode) == 0o600 and after.st_nlink == 1
                    and after.st_size == len(raw), "body_finalizer_report_publish")
        except InnerReadRefusal:
            raise
        except OSError as error:
            raise InnerReadRefusal("body_finalizer_report_publish") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if owned is not None:
            info: os.stat_result | None
            try:
                info = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            except OSError:
                info = None
            if info is not None and (info.st_dev, info.st_ino) == owned:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except OSError:
                    pass
        os.close(parent)


def refresh_body_source_read_report(
    inner_plan: Mapping[str, Any],
    *,
    source_root: Path,
    expected_registered_snapshot_count: int = EXPECTED_REGISTERED_SNAPSHOT_COUNT,
    charge_bytes: Callable[[int], None],
) -> dict[str, Any]:
    """Replace only the exported whole-read receipt with the current receipt."""
    _refuse(isinstance(source_root, Path) and source_root.is_absolute(),
            "body_finalizer_source_root")
    expected_count = _integer(
        expected_registered_snapshot_count, "body_finalizer_snapshot_count", minimum=1,
    )
    _refuse(expected_count == EXPECTED_REGISTERED_SNAPSHOT_COUNT,
            "body_finalizer_snapshot_count")
    report_root, ledger_root = _paths(inner_plan)
    charge_bytes(FINALIZER_PRECHARGE_BYTES)
    budget = _read_budget(report_root)
    snapshot_receipt, _snapshot_raw = _read_snapshot_receipt(
        report_root, inner_plan, expected_count,
    )
    _refuse(budget["snapshot_counter_receipt_sha256"] == digest_bytes(_snapshot_raw),
            "body_finalizer_snapshot_receipt_hash")
    old_source = _mapping(budget["source_read_receipt"], "body_finalizer_source_receipt")
    old_bytes = _integer(old_source["regular_file_bytes"], "body_finalizer_source_bytes")
    try:
        checked_old = validate_exported_receipt(inner_plan, old_source, regular_file_bytes=old_bytes)
        current_source = read_receipt(inner_plan, ledger_root=ledger_root)
        checked_current = validate_exported_receipt(
            inner_plan, current_source, regular_file_bytes=old_bytes,
        )
        source_record, source_raw = _read_record(
            ledger_root, SOURCE_RECORD_NAME, SOURCE_RECORD_FIELDS, SOURCE_LEDGER_SCHEMA,
        )
        _refuse(_source_receipt_from_record(source_record) == checked_current,
                "body_finalizer_source_record_mismatch")
        current_guard = read_inner_source_closure_receipt(
            inner_plan, ledger_root=ledger_root,
        )
        closure_record, closure_raw = _read_record(
            ledger_root, CLOSURE_RECORD_NAME, CLOSURE_RECORD_FIELDS, CLOSURE_LEDGER_SCHEMA,
        )
        _refuse(_closure_receipt_from_record(closure_record) == current_guard,
                "body_finalizer_guard_record_mismatch")
        bound = closure_guard_read_bound(inner_plan, source_root)
        checked_guard = validate_exported_closure_receipt(
            inner_plan, budget["source_guard_receipt"],
            regular_file_bytes=bound["regular_file_bytes"],
        )
    except InnerReadRefusal:
        raise
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise InnerReadRefusal("body_finalizer_receipt_unavailable") from error
    _refuse(checked_current["consumed_reads"] > checked_old["consumed_reads"],
            "body_finalizer_counter_not_advanced")
    _refuse(checked_current["regular_file_bytes"] == checked_old["regular_file_bytes"],
            "body_finalizer_source_size_changed")
    _refuse(checked_guard == current_guard, "body_finalizer_stale_closure")
    for field in ("regular_file_bytes", "file_visits", "reserved_bytes_per_guard"):
        _refuse(checked_guard[field] == bound[field], "body_finalizer_closure_bound")
    _validate_preserved_budget(
        budget, checked_guard, expected_snapshot_count=expected_count,
        snapshot_receipt=snapshot_receipt,
    )
    joined = dict(budget)
    joined["source_read_receipt"] = checked_current
    try:
        raw = canonical_bytes(joined, ascii_only=True) + b"\n"
    except (TypeError, ValueError, UnicodeError) as error:
        raise InnerReadRefusal("body_finalizer_report_encode") from error
    _assert_report_target(report_root)
    try:
        publish_member(report_root, SOURCE_RECORD_NAME, source_raw)
        publish_member(report_root, CLOSURE_RECORD_NAME, closure_raw)
    except InnerReadRefusal:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise InnerReadRefusal("body_finalizer_record_publish") from error
    _replace_report(report_root, raw)
    return joined


__all__ = [
    "EXPECTED_REGISTERED_SNAPSHOT_COUNT",
    "FINALIZER_PRECHARGE_BYTES",
    "MAX_LEDGER_RECORD_BYTES",
    "MAX_REPORT_BYTES",
    "refresh_body_source_read_report",
]
