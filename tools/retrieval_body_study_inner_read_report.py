"""Validate the final combined BODY inner source-read report.

The exported JSON is conservative reservation bookkeeping, not a physical
I/O meter.  It cannot authorize a runner, extend a reservation, or replace
the durable current receipts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re
from pathlib import Path
from typing import Any, cast

from retrieval_body_study_inner_guard_reads import (
    GUARD_LIMIT,
    closure_guard_read_bound,
    read_inner_source_closure_receipt,
    validate_exported_closure_receipt,
)
from retrieval_body_study_inner_reads import (
    InnerReadRefusal,
    read_receipt,
    validate_exported_receipt,
)
from retrieval_body_study_files import read_member
from retrieval_supplement_primitives import canonical_bytes, digest_bytes, strict_json


REPORT_NAME = "body-study-source-read-budget.json"
REPORT_SCHEMA = "cloe-body-study-source-read-budget-v1"
INNER_SOURCE_READ_BYTES = 12 * 1024**3
MAX_SNAPSHOT_RECEIPT_BYTES = 16 * 1024
SNAPSHOT_BOOKKEEPING_BYTES = 2 * MAX_SNAPSHOT_RECEIPT_BYTES + 4096
SNAPSHOT_COUNTER_SCHEMA = "cloe-w5-body-snapshot-counter-v1"
SNAPSHOT_RECEIPT_NAME = "body-study-snapshot-counter.json"
SNAPSHOT_BINDING_FIELDS = frozenset({
    "attempt_id", "plan_sha256", "source_closure_sha256",
    "candidate_snapshot_sha256", "input_pin_sha256", "receipt_name",
})
SNAPSHOT_RECEIPT_FIELDS = frozenset({
    "schema", "binding", "reserved_count", "observed_count",
    "observed_count_kind", "call_map", "violation_code", "audit_rejections",
    "profile_attested", "profile_restored", "audit_hook_attested",
    "audit_hook_inert", "counter_scope", "bookkeeping_bytes", "valid",
})
REPORT_FIELDS = frozenset({
    "schema",
    "source_read_receipt",
    "source_guard_receipt",
    "source_guard_count",
    "source_guard_limit",
    "source_guard_read_bytes",
    "active_guard_read_bytes",
    "active_guard_read_bytes_kind",
    "registered_snapshot_count",
    "registered_snapshot_count_reserved",
    "registered_snapshot_count_kind",
    "registered_snapshot_file_bytes",
    "registered_snapshot_read_bytes",
    "registered_snapshot_read_bytes_kind",
    "source_guard_total_read_bytes",
    "source_guard_total_read_bytes_kind",
    "source_guard_byte_limit",
    "snapshot_counter_receipt_file",
    "snapshot_counter_receipt_sha256",
    "snapshot_counter_receipt",
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


def _digest(value: object, code: str) -> str:
    _refuse(type(value) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None, code)
    return cast(str, value)


def validate_snapshot_counter_receipt(
    value: object,
    raw: bytes,
    inner_plan: Mapping[str, Any],
    expected_count: int,
) -> dict[str, Any]:
    expected_count = _integer(expected_count, "body_report_snapshot_expected", minimum=1)
    receipt = _mapping(value, "body_report_snapshot_receipt")
    _refuse(set(receipt) == SNAPSHOT_RECEIPT_FIELDS
            and receipt.get("schema") == SNAPSHOT_COUNTER_SCHEMA,
            "body_report_snapshot_receipt_fields")
    binding = _mapping(receipt["binding"], "body_report_snapshot_binding")
    _refuse(set(binding) == SNAPSHOT_BINDING_FIELDS,
            "body_report_snapshot_binding_fields")
    plan_sha = inner_plan.get("host_plan_sha256", inner_plan.get("plan_sha256"))
    for actual, expected, code in (
        (binding["attempt_id"], inner_plan.get("attempt_id"), "attempt"),
        (binding["plan_sha256"], plan_sha, "plan"),
        (binding["source_closure_sha256"], inner_plan.get("source_closure_sha256"), "source"),
        (binding["candidate_snapshot_sha256"], inner_plan.get("candidate_snapshot_sha256"), "candidate"),
        (binding["input_pin_sha256"], inner_plan.get("input_pin_sha256"), "pin"),
    ):
        if code != "attempt":
            _digest(actual, f"body_report_snapshot_binding_{code}")
        _refuse(type(expected) is str and actual == expected,
                f"body_report_snapshot_binding_{code}")
    _refuse(binding["receipt_name"] == SNAPSHOT_RECEIPT_NAME,
            "body_report_snapshot_receipt_name")
    _refuse(_integer(receipt["reserved_count"], "body_report_snapshot_reserved") == expected_count
            and _integer(receipt["observed_count"], "body_report_snapshot_observed") == expected_count,
            "body_report_snapshot_count")
    _refuse(receipt["observed_count_kind"] == "measured_exact_code_object_calls",
            "body_report_snapshot_count_kind")
    _refuse(receipt["violation_code"] is None
            and type(receipt["profile_attested"]) is bool
            and receipt["profile_attested"] is True
            and type(receipt["profile_restored"]) is bool
            and receipt["profile_restored"] is True
            and receipt["audit_hook_attested"] is True
            and receipt["audit_hook_inert"] is True
            and receipt["counter_scope"] == "inner-sdk"
            and receipt["valid"] is True,
            "body_report_snapshot_attestation")
    _refuse(_integer(receipt["audit_rejections"], "body_report_snapshot_rejections") == 0,
            "body_report_snapshot_rejections")
    _refuse(_integer(receipt["bookkeeping_bytes"], "body_report_snapshot_bookkeeping")
            == SNAPSHOT_BOOKKEEPING_BYTES, "body_report_snapshot_bookkeeping")
    call_map = receipt["call_map"]
    _refuse(isinstance(call_map, list) and 0 < len(call_map) <= 64,
            "body_report_snapshot_call_map")
    callers: set[str] = set()
    call_total = 0
    for row in call_map:
        mapped = _mapping(row, "body_report_snapshot_call_map_row")
        _refuse(set(mapped) == {"caller", "count"}
                and type(mapped["caller"]) is str
                and 0 < len(mapped["caller"]) <= 256
                and mapped["caller"] not in callers,
                "body_report_snapshot_call_map_row")
        callers.add(cast(str, mapped["caller"]))
        call_total += _integer(mapped["count"], "body_report_snapshot_call_count", minimum=1)
    _refuse(call_total == expected_count, "body_report_snapshot_call_total")
    _refuse(canonical_bytes(receipt, ascii_only=True) + b"\n" == raw,
            "body_report_snapshot_receipt_canonical")
    return dict(receipt)


def _current_receipts(inner_plan: Mapping[str, Any], ledger_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        source = read_receipt(inner_plan, ledger_root=ledger_root)
        closure = read_inner_source_closure_receipt(inner_plan, ledger_root=ledger_root)
    except InnerReadRefusal:
        raise
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise InnerReadRefusal("body_report_current_receipt_unavailable") from error
    return source, closure


def _validate_budget(value: Mapping[str, Any], *, source_receipt: Mapping[str, Any],
                     guard_receipt: Mapping[str, Any], active_file_bytes: int,
                     registered_snapshot_file_bytes: int,
                     expected_registered_snapshot_count: int,
                     snapshot_receipt: Mapping[str, Any]) -> dict[str, Any]:
    _refuse(set(value) == REPORT_FIELDS and value.get("schema") == REPORT_SCHEMA,
            "body_report_fields")
    source_count = _integer(value["source_guard_count"], "body_report_guard_count", minimum=1)
    _refuse(source_count <= GUARD_LIMIT, "body_report_guard_count")
    _refuse(_integer(value["source_guard_limit"], "body_report_guard_limit") == GUARD_LIMIT,
            "body_report_guard_limit")
    _refuse(source_count == _integer(guard_receipt["consumed_guards"], "body_report_receipt_count"),
            "body_report_receipt_count")
    _refuse(guard_receipt["execution_authorized"] is False
            and source_receipt["execution_authorized"] is False,
            "body_report_execution_authority")
    source_bytes = _integer(guard_receipt["regular_file_bytes"], "body_report_guard_bytes", minimum=1)
    source_guard_read = _integer(value["source_guard_read_bytes"], "body_report_source_bytes")
    active_guard_read = _integer(value["active_guard_read_bytes"], "body_report_active_bytes")
    _refuse(value["active_guard_read_bytes_kind"] == "informational_subset",
            "body_report_active_read_kind")
    snapshot_count = _integer(value["registered_snapshot_count"], "body_report_snapshot_count", minimum=1)
    _refuse(snapshot_count == expected_registered_snapshot_count,
            "body_report_snapshot_count")
    _refuse(_integer(value["registered_snapshot_count_reserved"], "body_report_snapshot_reserved")
            == expected_registered_snapshot_count
            and value["registered_snapshot_count_kind"] == "measured_final_receipt"
            and snapshot_count == snapshot_receipt["observed_count"],
            "body_report_snapshot_receipt_join")
    snapshot_bytes = _integer(value["registered_snapshot_file_bytes"],
                              "body_report_snapshot_bytes", minimum=active_file_bytes)
    _refuse(snapshot_bytes == registered_snapshot_file_bytes,
            "body_report_snapshot_bytes")
    snapshot_read = _integer(value["registered_snapshot_read_bytes"],
                             "body_report_snapshot_read")
    _refuse(value["registered_snapshot_read_bytes_kind"] == "reserved_upper_bound",
            "body_report_snapshot_read_kind")
    total = _integer(value["source_guard_total_read_bytes"], "body_report_total_bytes")
    limit = _integer(value["source_guard_byte_limit"], "body_report_byte_limit")
    # The closure bound already includes active paths.  Keep the active field
    # as an explicit subset/stat bound, but do not add it to the total again.
    # None of these fields is a measured physical-I/O counter.
    _refuse(source_guard_read == source_count * source_bytes, "body_report_source_algebra")
    _refuse(active_guard_read == source_count * active_file_bytes, "body_report_active_algebra")
    _refuse(snapshot_read == snapshot_count * snapshot_bytes, "body_report_snapshot_algebra")
    _refuse(total == source_guard_read + snapshot_read,
            "body_report_total_algebra")
    _refuse(value["source_guard_total_read_bytes_kind"] == "source_plus_final_reserved_upper_bound",
            "body_report_total_kind")
    _refuse(limit == INNER_SOURCE_READ_BYTES and total <= limit, "body_report_byte_limit")
    _refuse(source_receipt["execution_authorized"] is False, "body_report_execution_authority")
    return dict(value)


def validate_exported_source_read_receipt(
    report_root: Path,
    *,
    inner_plan: Mapping[str, Any],
    source_root: Path,
    ledger_root: Path,
    regular_file_bytes: int,
    active_file_bytes: int,
    registered_snapshot_file_bytes: int,
    expected_registered_snapshot_count: int,
    charge_bytes: Callable[[int], None],
) -> dict[str, Any]:
    """Validate reserved report counts against fresh bounds and current ledgers."""
    _refuse(isinstance(report_root, Path), "body_report_root_type")
    _refuse(isinstance(source_root, Path) and source_root.is_absolute(), "body_report_source_root")
    _refuse(isinstance(ledger_root, Path) and ledger_root.is_absolute(), "body_report_ledger_root")
    whole_bytes = _integer(regular_file_bytes, "body_report_regular_bytes", minimum=1)
    active_bytes = _integer(active_file_bytes, "body_report_active_file_bytes")
    snapshot_bytes = _integer(
        registered_snapshot_file_bytes,
        "body_report_snapshot_file_bytes",
        minimum=active_bytes,
    )
    expected_snapshot_count = _integer(
        expected_registered_snapshot_count,
        "body_report_snapshot_count",
        minimum=1,
    )
    charge_bytes(65537 + MAX_SNAPSHOT_RECEIPT_BYTES + 2 * (16384 + 1))
    try:
        raw = read_member(report_root, REPORT_NAME, max_bytes=65536)
        value = strict_json(raw)
    except InnerReadRefusal:
        raise
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise InnerReadRefusal("body_report_read_failed") from error
    budget = _mapping(value, "body_report_fields")
    try:
        snapshot_raw = read_member(report_root, SNAPSHOT_RECEIPT_NAME,
                                   max_bytes=MAX_SNAPSHOT_RECEIPT_BYTES)
        snapshot_value = strict_json(snapshot_raw, maximum_bytes=MAX_SNAPSHOT_RECEIPT_BYTES)
    except InnerReadRefusal:
        raise
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise InnerReadRefusal("body_report_snapshot_receipt_read_failed") from error
    snapshot_receipt = validate_snapshot_counter_receipt(
        snapshot_value, snapshot_raw, inner_plan, expected_snapshot_count,
    )
    _refuse(budget["snapshot_counter_receipt_file"] == SNAPSHOT_RECEIPT_NAME
            and budget["snapshot_counter_receipt"] == snapshot_receipt
            and budget["snapshot_counter_receipt_sha256"] == digest_bytes(snapshot_raw),
            "body_report_snapshot_receipt_join")
    source_receipt = _mapping(budget["source_read_receipt"], "body_report_source_receipt")
    guard_receipt = _mapping(budget["source_guard_receipt"], "body_report_guard_receipt")
    checked_source = validate_exported_receipt(
        inner_plan,
        source_receipt,
        regular_file_bytes=whole_bytes,
    )
    try:
        bound = closure_guard_read_bound(inner_plan, source_root)
    except InnerReadRefusal:
        raise
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise InnerReadRefusal("body_report_closure_bound") from error
    checked_guard = validate_exported_closure_receipt(
        inner_plan,
        guard_receipt,
        regular_file_bytes=bound["regular_file_bytes"],
    )
    for field in ("regular_file_bytes", "file_visits", "reserved_bytes_per_guard"):
        _refuse(checked_guard[field] == bound[field], "body_report_closure_bound")
    current_source, current_guard = _current_receipts(inner_plan, ledger_root)
    _refuse(checked_source == current_source, "body_report_stale_source_receipt")
    _refuse(checked_guard == current_guard, "body_report_stale_guard_receipt")
    return _validate_budget(
        budget,
        source_receipt=checked_source,
        guard_receipt=checked_guard,
        active_file_bytes=active_bytes,
        registered_snapshot_file_bytes=snapshot_bytes,
        expected_registered_snapshot_count=expected_snapshot_count,
        snapshot_receipt=snapshot_receipt,
    )


__all__ = [
    "INNER_SOURCE_READ_BYTES",
    "MAX_SNAPSHOT_RECEIPT_BYTES",
    "SNAPSHOT_BOOKKEEPING_BYTES",
    "REPORT_NAME",
    "REPORT_SCHEMA",
    "SNAPSHOT_COUNTER_SCHEMA",
    "SNAPSHOT_RECEIPT_NAME",
    "validate_snapshot_counter_receipt",
    "validate_exported_source_read_receipt",
]
