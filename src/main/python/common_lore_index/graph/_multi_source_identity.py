"""Private canonical framing primitives for multi-source identities."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from itertools import batched
from typing import Any


TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_TOKEN_BYTES = 124


class MultiSourceIdentityError(ValueError):
    """A multi-source identity or canonical input is invalid."""


def bounded_ascii(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value:
        raise MultiSourceIdentityError(f"{label} is invalid")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        raise MultiSourceIdentityError(f"{label} is invalid") from None
    if len(encoded) > maximum:
        raise MultiSourceIdentityError(f"{label} exceeds its byte bound")
    return value


def token(value: object, label: str, maximum: int = MAX_TOKEN_BYTES) -> str:
    text = bounded_ascii(value, label, maximum)
    if TOKEN_PATTERN.fullmatch(text) is None:
        raise MultiSourceIdentityError(f"{label} is invalid")
    return text


def prefixed_token(value: object, prefix: str, label: str) -> str:
    text = bounded_ascii(value, label, len(prefix) + MAX_TOKEN_BYTES)
    if not text.startswith(prefix):
        raise MultiSourceIdentityError(f"{label} has an unsupported version")
    token(text[len(prefix) :], label)
    return text


def prefixed_digest(value: object, prefix: str, label: str) -> str:
    text = bounded_ascii(value, label, len(prefix) + 64)
    if not text.startswith(prefix):
        raise MultiSourceIdentityError(f"{label} has an unsupported version")
    if HEX_PATTERN.fullmatch(text[len(prefix) :]) is None:
        raise MultiSourceIdentityError(f"{label} is invalid")
    return text


def digest(domain: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"domain": domain, "payload": payload},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


_MANIFEST_JSON_FRAME = b'{"domain":"repomap-selected-content-manifest-v1","payload":{"entries":['


def digest_manifest_entries(entries: Iterable[tuple[str, str, int, bool]]) -> str:
    """Hash the canonical selected-content manifest row by row."""
    hasher = hashlib.sha256(_MANIFEST_JSON_FRAME)
    separator = b""
    for row in entries:
        hasher.update(separator)
        hasher.update(json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
        separator = b","
    hasher.update(b"]}}")
    return hasher.hexdigest()


def snapshot_vector_generation(
    graph_id: str, records: Iterable[tuple[str, int, str]],
) -> str:
    """Hash the existing ordered snapshot vector in bounded record batches."""
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    hasher = hashlib.sha256(b'{"algorithm":"multi-source-snapshot-vector-v1","graph_id":')
    hasher.update(encoder.encode(graph_id).encode("ascii"))
    hasher.update(b',"snapshot_vector":[')
    separator = b""
    for group in batched(records, 256):
        # The batch encoder supplies an array; retain only its comma-joined entries.
        encoded = encoder.encode([
            {"binding_id": binding_id, "binding_revision": binding_revision, "snapshot_id": snapshot_id}
            for binding_id, binding_revision, snapshot_id in group
        ])
        hasher.update(separator)
        hasher.update(encoded[1:-1].encode("ascii"))
        separator = b","
    hasher.update(b"]}")
    return "sg1:" + hasher.hexdigest()
