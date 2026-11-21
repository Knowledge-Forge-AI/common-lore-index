"""Small, authenticated ownership frames for the selected study cohort."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


MAX_WIRE_BYTES = 480
CONTROL_SCHEMA = "cloe-study-host-control-v2"
RECEIPT_SCHEMA = "cloe-study-host-receipt-v2"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTROL_KEYS = frozenset({"schema", "challenge", "pid", "birth", "scope_sha256"})
_RECEIPT_KEYS = frozenset({"schema", "challenge", "scope_sha256", "cid", "sdk_image_id", "marker_sha256"})


def _refuse(condition: object, code: str) -> None:
    if not condition:
        raise ValueError("ownership wire " + code)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        _refuse(key not in value, "duplicate field")
        value[key] = item
    return value


def _canonical(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: str) -> None:
    _refuse(type(value) is str and _DIGEST.fullmatch(value) is not None, "digest")


def scope_sha256(argv: Iterable[str], source_root: str, source_identity: str,
                sdk_image_id: str) -> str:
    values = list(argv)
    _refuse(all(type(value) is str and value and "\x00" not in value for value in values), "argv")
    _refuse(0 < len(values) <= 128, "argv bounds")
    _refuse(type(source_root) is str and Path(source_root).is_absolute()
            and "\x00" not in source_root, "source root")
    _digest(source_identity)
    _digest(sdk_image_id)
    return "sha256:" + hashlib.sha256(_canonical({
        "argv": values, "source_root": source_root,
        "source_identity": source_identity, "sdk_image_id": sdk_image_id,
    })).hexdigest()


def _encode(value: Mapping[str, object]) -> bytes:
    try:
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError("ownership wire encoding") from error
    _refuse(0 < len(raw) <= MAX_WIRE_BYTES, "frame bounds")
    return raw


def _nonfinite(_value: str) -> None:
    raise ValueError("ownership wire nonfinite JSON")


def _decode(raw: bytes, *, keys: frozenset[str], schema: str) -> dict[str, Any]:
    _refuse(type(raw) is bytes and 0 < len(raw) <= MAX_WIRE_BYTES, "frame bounds")
    try:
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_nonfinite)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("ownership wire encoding") from error
    _refuse(type(value) is dict and set(value) == keys and value.get("schema") == schema,
            "frame schema")
    _refuse(type(value["challenge"]) is str and _HEX.fullmatch(value["challenge"]) is not None,
            "challenge")
    _digest(value["scope_sha256"])
    return value


def encode_control(challenge: str, pid: int, birth: float, scope: str) -> bytes:
    _refuse(type(challenge) is str and _HEX.fullmatch(challenge) is not None, "challenge")
    _refuse(type(pid) is int and not isinstance(pid, bool) and pid > 0, "pid")
    _refuse(type(birth) in (int, float) and not isinstance(birth, bool)
            and math.isfinite(birth) and birth > 0, "birth")
    _digest(scope)
    return _encode({"schema": CONTROL_SCHEMA, "challenge": challenge, "pid": pid,
                    "birth": birth, "scope_sha256": scope})


def decode_control(raw: bytes) -> dict[str, Any]:
    value = _decode(raw, keys=_CONTROL_KEYS, schema=CONTROL_SCHEMA)
    _refuse(type(value["pid"]) is int and not isinstance(value["pid"], bool) and value["pid"] > 0, "pid")
    _refuse(type(value["birth"]) in (int, float) and not isinstance(value["birth"], bool)
            and math.isfinite(value["birth"]) and value["birth"] > 0, "birth")
    return value


def encode_receipt(challenge: str, scope: str, cid: str, sdk_image_id: str,
                   marker_sha256: str) -> bytes:
    _refuse(type(challenge) is str and _HEX.fullmatch(challenge) is not None, "challenge")
    _digest(scope)
    _refuse(type(cid) is str and _HEX.fullmatch(cid) is not None, "cid")
    _digest(sdk_image_id)
    _digest(marker_sha256)
    return _encode({"schema": RECEIPT_SCHEMA, "challenge": challenge,
                    "scope_sha256": scope, "cid": cid,
                    "sdk_image_id": sdk_image_id, "marker_sha256": marker_sha256})


def decode_receipt(raw: bytes) -> dict[str, Any]:
    value = _decode(raw, keys=_RECEIPT_KEYS, schema=RECEIPT_SCHEMA)
    _refuse(type(value["cid"]) is str and _HEX.fullmatch(value["cid"]) is not None, "cid")
    _digest(value["sdk_image_id"])
    _digest(value["marker_sha256"])
    return value


__all__ = ["CONTROL_SCHEMA", "MAX_WIRE_BYTES", "RECEIPT_SCHEMA", "decode_control",
           "decode_receipt", "encode_control", "encode_receipt", "scope_sha256"]
