"""Deterministic, public-safe completeness checksums for SCALE1 stage rows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any


__all__ = ("FamilyChecksum", "checksum_family")

_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_ROW_KEY_PREFIX = b"repomap-staging-checksum-v2\x00key\x00"
_ROW_PAYLOAD_PREFIX = b"repomap-staging-checksum-v2\x00payload\x00"
_SUM_MODULUS = 1 << 256
# Canonical JSON sorts the two envelope members in this order.
_PAYLOAD_HEAD = b'{"identity":'
_PAYLOAD_MIDDLE = b',"payload":'
_PAYLOAD_END = b"}"


def _row_digest_value(prefix: bytes, *parts: bytes) -> int:
    digest = hashlib.sha256(prefix)
    for part in parts:
        digest.update(part)
    return int.from_bytes(digest.digest(), "big")


@dataclass(frozen=True)
class FamilyChecksum:
    """Client-computed trusted transfer receipt for one staged family."""

    row_count: int
    normalized_byte_count: int
    stable_key_digest: str
    payload_digest: str

    def validate(self) -> "FamilyChecksum":
        valid = (
            isinstance(self.row_count, int)
            and not isinstance(self.row_count, bool)
            and self.row_count >= 0
            and isinstance(self.normalized_byte_count, int)
            and not isinstance(self.normalized_byte_count, bool)
            and self.normalized_byte_count >= 0
            and isinstance(self.stable_key_digest, str)
            and _DIGEST_PATTERN.fullmatch(self.stable_key_digest) is not None
            and isinstance(self.payload_digest, str)
            and _DIGEST_PATTERN.fullmatch(self.payload_digest) is not None
        )
        if not valid:
            raise ValueError("invalid family checksum")
        return self


class _FamilyChecksumAccumulator:
    """Accumulate one order-independent checksum-v2 family receipt."""

    def __init__(self, identity_fields: Sequence[str]) -> None:
        identity_names = tuple(identity_fields)
        if not identity_names or any(
            not isinstance(name, str) for name in identity_names
        ):
            raise ValueError("checksum identity field is missing")
        self._identity_names = identity_names
        self._key_sum = 0
        self._payload_sum = 0
        self._row_count = 0
        self._normalized_byte_count = 0

    def add(self, row: Mapping[str, object]) -> None:
        """Add one validated row to the commutative checksum state."""

        if not isinstance(row, Mapping):
            raise ValueError("checksum row is invalid")
        if any(name not in row for name in self._identity_names):
            raise ValueError("checksum identity field is missing")
        identity = {name: row[name] for name in self._identity_names}
        encoded_identity = _canonical_json(_typed_value(identity))
        encoded_row = _canonical_json(_typed_value(row if type(row) is dict else dict(row)))
        payload_parts = (
            _PAYLOAD_HEAD, encoded_identity, _PAYLOAD_MIDDLE, encoded_row, _PAYLOAD_END,
        )
        self._key_sum = (
            self._key_sum + _row_digest_value(_ROW_KEY_PREFIX, encoded_identity)
        ) % _SUM_MODULUS
        self._payload_sum = (
            self._payload_sum + _row_digest_value(_ROW_PAYLOAD_PREFIX, *payload_parts)
        ) % _SUM_MODULUS
        self._row_count += 1
        self._normalized_byte_count += sum(map(len, payload_parts)) + 1

    def finish(self) -> FamilyChecksum:
        """Finalize the checksum state without changing its contents."""

        count_bytes = self._row_count.to_bytes(8, "big")
        normalized_bytes = self._normalized_byte_count.to_bytes(16, "big")
        key_digest = hashlib.sha256(
            b"repomap-staging-checksum-v2\x00key-stream\x00"
            + count_bytes
            + normalized_bytes
            + self._key_sum.to_bytes(32, "big")
        ).hexdigest()
        payload_digest = hashlib.sha256(
            b"repomap-staging-checksum-v2\x00payload-stream\x00"
            + count_bytes
            + normalized_bytes
            + self._payload_sum.to_bytes(32, "big")
        ).hexdigest()
        return FamilyChecksum(
            row_count=self._row_count,
            normalized_byte_count=self._normalized_byte_count,
            stable_key_digest=key_digest,
            payload_digest=payload_digest,
        ).validate()


def checksum_family(
    rows: Iterable[Mapping[str, object]], *, identity_fields: Sequence[str]
) -> FamilyChecksum:
    """Return a trusted transfer receipt without claiming server recomputation."""

    accumulator = _FamilyChecksumAccumulator(identity_fields)
    for row in rows:
        accumulator.add(row)
    return accumulator.finish()


def _typed_value(value: Any) -> object:
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("unsupported checksum value")
        return ["float", repr(value)]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("unsupported checksum value")
        return [
            "object",
            [[key, _typed_value(value[key])] for key in sorted(value)],
        ]
    if isinstance(value, (list, tuple)):
        return ["list", [_typed_value(item) for item in value]]
    raise ValueError("unsupported checksum value")


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("unsupported checksum value") from error
