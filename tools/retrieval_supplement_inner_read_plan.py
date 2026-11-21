"""Source-read plan shape and commitments; this leaf grants no execution."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib,json,re
from pathlib import PurePosixPath
from typing import Any
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_INNER_SCHEMAS = {"study": "cloe-supplement-study-inner-plan-v1",
                  "proof": "cloe-pgvector-supplement-proof-inner-v1"}
class InnerReadRefusal(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code); self.code = code
def _refuse(condition: object, code: str) -> None:
    if not condition:
        raise InnerReadRefusal(code)
def _canonical(value: object) -> bytes:
    try:
        text = json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise InnerReadRefusal("noncanonical_plan") from error
    return text.encode("ascii")
def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()
def _digest_value(value: object) -> str:
    _refuse(type(value) is str, "invalid_digest")
    text = str(value)
    if _SHA256.fullmatch(text): return text
    _refuse(_HEX64.fullmatch(text) is not None, "invalid_digest")
    return "sha256:" + text
def _relative(value: object, code: str = "unsafe_snapshot_path") -> str:
    _refuse(type(value) is str and bool(value), code)
    text = str(value)
    _refuse("\x00" not in text and "\\" not in text and ":" not in text
            and not text.startswith("/"), code)
    parts = PurePosixPath(text).parts
    _refuse(bool(parts) and all(part not in {"", ".", ".."} for part in parts)
            and PurePosixPath(*parts).as_posix() == text, code)
    return text
def _link_target(value: object, name: str) -> str:
    _refuse(type(value) is str and bool(value), "unsafe_snapshot_link")
    target = str(value)
    _refuse("\x00" not in target and "\\" not in target and not target.startswith("/"), "unsafe_snapshot_link")
    parts = PurePosixPath(target).parts
    _refuse(bool(parts) and all(part not in {"", "."} for part in parts), "unsafe_snapshot_link")
    depth = len(PurePosixPath(name).parent.parts)
    for component in parts:
        depth += -1 if component == ".." else 1; _refuse(depth >= 0, "snapshot_link_escape")
    return target
@dataclass(frozen=True)
class _PlanInfo:
    plan_sha256: str
    role: str
    inner_schema: str
    candidate_head: str
    snapshot: dict[str, Any]
    source_snapshot_sha256: str
def _snapshot_parts(record: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    head = record.get("candidate_head")
    if "current_head" in record:
        _refuse(head is None or head == record["current_head"], "head_alias_mismatch"); head = record["current_head"]
    snapshot = record.get("candidate_snapshot")
    alias = record.get("source_snapshot")
    if isinstance(alias, Mapping):
        _refuse(snapshot is None or snapshot == alias, "snapshot_alias_mismatch"); snapshot = alias
    elif alias is not None:
        _refuse(snapshot is not None, "snapshot_inventory_missing")
    _refuse(type(head) is str and _HEX40.fullmatch(str(head)) is not None, "invalid_current_head")
    if not isinstance(snapshot, Mapping):
        raise InnerReadRefusal("source_snapshot_required")
    return str(head), snapshot
def _validate_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(snapshot)
    for name, row in result.items():
        _relative(name)
        _refuse(isinstance(row, Mapping), "invalid_snapshot_row")
        if set(row) == {"sha256", "mode"}:
            _refuse(type(row["sha256"]) is str and _HEX64.fullmatch(row["sha256"]) is not None, "invalid_snapshot_hash")
            _refuse(type(row["mode"]) is int and not isinstance(row["mode"], bool) and 0 <= row["mode"] <= 0o777, "invalid_snapshot_mode")
        elif set(row) == {"symlink"}:
            _link_target(row["symlink"], str(name))
        else:
            raise InnerReadRefusal("invalid_snapshot_row_fields")
    _refuse(bool(result), "empty_source_snapshot")
    return result
def _snapshot_commitment(record: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
    expected = _digest(snapshot)
    expected_nl = "sha256:" + hashlib.sha256(_canonical(snapshot) + b"\n").hexdigest()
    values: list[str] = []
    for key in ("candidate_snapshot_sha256", "source_freeze_sha256", "source_snapshot_sha256"):
        if key in record:
            value = _digest_value(record[key])
            _refuse(value in {expected, expected_nl}, "source_snapshot_commitment"); values.append(value)
    if isinstance(record.get("source_snapshot"), str):
        value = _digest_value(record["source_snapshot"])
        _refuse(value in {expected, expected_nl}, "source_snapshot_commitment"); values.append(value)
    _refuse(not values or len(set(values)) == 1, "source_snapshot_commitment")
    return values[0] if values else expected
def _validate_plan(plan: Mapping[str, Any]) -> _PlanInfo:
    _refuse(isinstance(plan, Mapping), "plan_object_required")
    record = dict(plan)
    role = record.get("role")
    if not isinstance(role, str) or role not in _INNER_SCHEMAS:
        raise InnerReadRefusal("inner_role_required")
    schema = record.get("schema")
    _refuse(schema == _INNER_SCHEMAS[role], "inner_schema_required")
    if "inner_type" in record:
        _refuse(record["inner_type"] == role, "inner_type_mismatch")
    head, raw_snapshot = _snapshot_parts(record)
    snapshot = _validate_snapshot(raw_snapshot)
    return _PlanInfo(_digest(record), str(role), str(schema), head, snapshot, _snapshot_commitment(record, snapshot))
