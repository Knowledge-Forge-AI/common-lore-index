"""Streaming aggregate for one retained source run."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeVar

from common_lore_index.storage.errors import StorageSchemaError
from common_lore_index.storage.source_rows import SourceRunRecord, source_run_record_from_storage_payload

Ordered = TypeVar("Ordered", str, int)
_INTEGER = re.compile(r"([+-]?)(?:0*([1-9][0-9]*)|0+)")
_BIGINT, _INT4 = 2**63, 2**31
_MAX_INTEGER_DIGITS = len(str(_BIGINT))


@dataclass(frozen=True)
class SourceObservation:
    """One raw row with ``metadata.source_id_configured``: the ``source_observations`` CTE."""

    key: tuple[int, int]
    kind: str
    source_id: str
    source_type: str | None
    display_name: str | None
    policy_status: str | None
    source_run_id: str | None
    artifact_id: str | None
    artifact_path: str | None
    artifact_sha256: str | None
    artifact_bytes: str | None
    acquired_at: str | None
    http_status: str | None
    content_type: str | None
    url_summary: str | None



def _integer(value: str | None, bound: int) -> int | None:
    """``text::bigint``/``::int`` for decimal spellings; anything else refuses."""
    if value is None:
        return None
    text = value.strip(" \t\n\r\v\f")
    match = _INTEGER.fullmatch(text)
    if match is not None and match.end(2) - match.start(2) <= _MAX_INTEGER_DIGITS:
        # PostgreSQL accepts redundant leading zeros. Limit conversion to
        # significant digits so Python's decimal conversion cap cannot refuse them.
        digits = match[2]
        magnitude = int(digits) if digits is not None else 0
        result = -magnitude if match[1] == "-" else magnitude
        if -bound <= result < bound:
            return result
    raise StorageSchemaError("stored source metadata is not a valid integer")


def _max_present(left: Ordered | None, right: Ordered | None) -> Ordered | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


@dataclass
class SourceRunSummary:
    acquired_at: str | None = None
    artifact_id: str | None = None
    artifact_path: str | None = None
    artifact_byte_length: int | None = None
    artifact_sha256: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    observation_count: int = 0
    has_errors: bool = False

    def add(self, item: SourceObservation) -> None:
        self.acquired_at = _max_present(self.acquired_at, item.acquired_at)
        self.artifact_id = _max_present(self.artifact_id, item.artifact_id)
        self.artifact_path = _max_present(self.artifact_path, item.artifact_path)
        self.artifact_sha256 = _max_present(self.artifact_sha256, item.artifact_sha256)
        self.artifact_byte_length = _max_present(self.artifact_byte_length, _integer(item.artifact_bytes, _BIGINT))
        self.http_status = _max_present(self.http_status, _integer(item.http_status, _INT4))
        self.content_type = _max_present(self.content_type, item.content_type)
        self.observation_count += 1
        self.has_errors = self.has_errors or item.kind == "feed.parse_error"

    def record(self, run_id: str) -> SourceRunRecord:
        return source_run_record_from_storage_payload({
            "source_run_id": run_id, "acquired_at": self.acquired_at,
            "artifact_id": self.artifact_id, "artifact_path": self.artifact_path,
            "artifact_byte_length": self.artifact_byte_length, "artifact_sha256": self.artifact_sha256,
            "http_status": self.http_status, "content_type": self.content_type,
            "observation_count": self.observation_count,
            "status_summary": "parse_errors" if self.has_errors else "ok",
        })
