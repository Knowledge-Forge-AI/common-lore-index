"""Byte-only custody and namespace checks for W5 predecessor fixtures.

The predecessor manifest names the older public fixture bytes.  This module
reads only those declared regular files, compares exact byte sizes and hashes,
and lexically extracts ``task_id`` string tokens from the three older catalog
members.  No old question, gold, or other catalog value is decoded.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from retrieval_body_values import (
    BodyIdentity,
    check_keys,
    digest_bytes,
    integer,
    require,
    safe_id,
    safe_relative_path,
    strict_json,
    text,
)
from retrieval_supplement_files import read_member


MAX_MEMBERS = 4096
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_AGGREGATE_BYTES = 64 * 1024 * 1024
MANIFEST_FIELDS = ("commit", "files")
FILE_FIELDS = ("path", "bytes", "sha256")
_RAW_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"\A[0-9a-f]{40}\Z")
_BASE = "src/test/fixtures/retrieval_evaluation/"
_ALLOWED_ROOTS = frozenset({"context-v1", "mixed-v1", "supplement-v1", "body-v1"})
_CATALOG_SUFFIXES = (
    "context-v1/task_catalog.json",
    "mixed-v1/tasks/catalog-v1.json",
    "supplement-v1/catalog-v1.json",
)
_WS = frozenset(b" \t\r\n")
_SCALAR = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null")
_MAX_DEPTH = 32


@dataclass(frozen=True)
class PredecessorFile:
    """One manifest declaration and its verified raw digest."""

    path: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class PredecessorSnapshot:
    """Verified predecessor custody plus ID-only catalog observations."""

    commit: str
    files: tuple[PredecessorFile, ...]
    _catalog_hashes: tuple[tuple[str, str], ...]
    _catalog_counts: tuple[tuple[str, int], ...]
    _catalog_task_ids: tuple[tuple[str, tuple[str, ...]], ...]
    _task_ids: tuple[str, ...]
    task_id_token_count: int

    @property
    def catalog_hashes(self) -> dict[str, str]:
        return dict(self._catalog_hashes)

    @property
    def catalog_counts(self) -> dict[str, int]:
        return dict(self._catalog_counts)

    @property
    def catalog_task_ids(self) -> dict[str, tuple[str, ...]]:
        return {path: tuple(ids) for path, ids in self._catalog_task_ids}

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(self._task_ids)


def _raw_sha(value: object, label: str) -> str:
    require(type(value) is str and _RAW_SHA256.fullmatch(value) is not None, f"{label}:raw_sha256")
    return str(value)


def _canonical_path(value: object) -> str:
    path = safe_relative_path(value)
    if path.startswith(_BASE):
        suffix = path[len(_BASE):]
    else:
        suffix = path
    require(any(suffix == root or suffix.startswith(root + "/") for root in _ALLOWED_ROOTS),
            "body.predecessor:path_root")
    return path if path.startswith(_BASE) else _BASE + path


def _read_relative(root: Path, canonical_path: str) -> str:
    if root.name == "retrieval_evaluation" and canonical_path.startswith(_BASE):
        return canonical_path[len(_BASE):]
    return canonical_path


class _TaskIDScanner:
    """Bounded JSON walker that decodes only values of the exact task_id key."""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.ids: list[str] = []

    def _ws(self, index: int) -> int:
        while index < len(self.raw) and self.raw[index] in _WS:
            index += 1
        return index

    def _expect(self, index: int, token: bytes) -> int:
        require(self.raw[index:index + 1] == token, "body.predecessor:catalog_structure")
        return index + 1

    def _string_end(self, index: int) -> int:
        index = self._expect(index, b'"')
        while index < len(self.raw):
            byte = self.raw[index]
            if byte == 0x22:
                return index + 1
            require(byte >= 0x20, "body.predecessor:catalog_string")
            index += 2 if byte == 0x5C else 1
        require(False, "body.predecessor:catalog_string")
        raise ValueError("unreachable")

    def _key(self, index: int) -> tuple[bytes, int]:
        end = self._string_end(index)
        token = self.raw[index:end]
        require(b"\\" not in token, "body.predecessor:catalog_key")
        return token, self._expect(self._ws(end), b":")

    def _task_id(self, start: int, end: int) -> None:
        token = self.raw[start:end]
        require(token.startswith(b'"'), "body.predecessor:task_id_string")
        try:
            value = json.loads(token.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (UnicodeDecodeError, ValueError, RecursionError):
            require(False, "body.predecessor:task_id_string")
            raise ValueError("unreachable")
        safe_id(value, "body.predecessor.task_id")
        self.ids.append(value)

    def _value(self, index: int, depth: int) -> int:
        require(depth <= _MAX_DEPTH, "body.predecessor:catalog_depth")
        head = self.raw[index:index + 1]
        if head == b'"':
            return self._string_end(index)
        if head in (b"{", b"["):
            return self._members(index, head, depth + 1)
        match = _SCALAR.match(self.raw, index)
        require(match is not None, "body.predecessor:catalog_scalar")
        assert match is not None
        return match.end()

    def _members(self, index: int, opening: bytes, depth: int) -> int:
        keyed = opening == b"{"
        closing = b"}" if keyed else b"]"
        index = self._ws(self._expect(index, opening))
        require(depth <= _MAX_DEPTH, "body.predecessor:catalog_depth")
        if self.raw[index:index + 1] == closing:
            return index + 1
        seen: set[bytes] = set()
        while True:
            key: bytes | None = None
            if keyed:
                key, index = self._key(index)
                require(key not in seen, "body.predecessor:catalog_duplicate_key")
                seen.add(key)
                index = self._ws(index)
            value_start = index
            index = self._value(value_start, depth)
            if key == b'"task_id"':
                self._task_id(value_start, index)
            index = self._ws(index)
            if self.raw[index:index + 1] != b",":
                return self._expect(index, closing)
            index = self._ws(index + 1)

    def scan(self) -> tuple[str, ...]:
        end = self._value(self._ws(0), 0)
        require(self._ws(end) == len(self.raw), "body.predecessor:catalog_trailing")
        return tuple(self.ids)


def task_id_tokens(raw: bytes) -> tuple[str, ...]:
    """Return task IDs without decoding any other catalog value."""
    require(type(raw) is bytes, "body.predecessor:catalog_bytes")
    return _TaskIDScanner(raw).scan()


def _manifest(raw: bytes) -> tuple[str, tuple[PredecessorFile, ...]]:
    root = check_keys(strict_json(raw), MANIFEST_FIELDS, "body.predecessor.manifest")
    commit = text(root["commit"], "body.predecessor.commit", 64)
    require(_COMMIT.fullmatch(commit) is not None, "body.predecessor.commit:sha1")
    values = root["files"]
    require(type(values) is list and 1 <= len(values) <= MAX_MEMBERS, "body.predecessor.files:count")
    records: list[PredecessorFile] = []
    paths: set[str] = set()
    total = 0
    for value in values:
        row = check_keys(value, FILE_FIELDS, "body.predecessor.file")
        path = _canonical_path(row["path"])
        require(path not in paths, "body.predecessor.file:duplicate")
        paths.add(path)
        size = integer(row["bytes"], "body.predecessor.file.bytes", 0, MAX_MEMBER_BYTES)
        total += size
        require(total <= MAX_AGGREGATE_BYTES, "body.predecessor.file:aggregate_limit")
        records.append(PredecessorFile(path, size, _raw_sha(row["sha256"], "body.predecessor.file.sha256")))
    return commit, tuple(sorted(records, key=lambda item: item.path))


def verify_predecessor_manifest(
    raw: bytes, predecessor_root: Path, *, identity: BodyIdentity,
) -> PredecessorSnapshot:
    """Verify every declared older byte and the ID-only predecessor namespace."""
    require(type(identity) is BodyIdentity, "body.predecessor:identity")
    require(isinstance(predecessor_root, Path), "body.predecessor:root")
    commit, records = _manifest(raw)
    declared: dict[str, bytes] = {}
    for record in records:
        data = read_member(
            predecessor_root,
            _read_relative(predecessor_root, record.path),
            max_bytes=record.bytes,
            expected_sha256="sha256:" + record.sha256,
        )
        require(len(data) == record.bytes, "body.predecessor:file_size")
        declared[record.path] = data
    expected_catalogs = {_BASE + suffix for suffix in _CATALOG_SUFFIXES}
    catalog_paths = set(declared) & expected_catalogs
    require(catalog_paths == expected_catalogs, "body.predecessor:catalog_set")
    catalog_hashes: list[tuple[str, str]] = []
    catalog_counts: list[tuple[str, int]] = []
    catalog_task_ids: list[tuple[str, tuple[str, ...]]] = []
    all_ids: list[str] = []
    for path in sorted(expected_catalogs):
        data = declared[path]
        ids = task_id_tokens(data)
        require(ids, "body.predecessor:catalog_empty")
        require(len(set(ids)) == len(ids), "body.predecessor:catalog_duplicate_task")
        require(not any(item.startswith(identity.task_prefix) for item in ids),
                "body.predecessor:namespace_collision")
        catalog_hashes.append((path, digest_bytes(data)[len("sha256:"):]))
        catalog_counts.append((path, len(ids)))
        catalog_task_ids.append((path, tuple(sorted(ids))))
        all_ids.extend(ids)
    require(len(set(all_ids)) == len(all_ids), "body.predecessor:cross_catalog_collision")
    return PredecessorSnapshot(
        commit=commit,
        files=records,
        _catalog_hashes=tuple(catalog_hashes),
        _catalog_counts=tuple(catalog_counts),
        _catalog_task_ids=tuple(catalog_task_ids),
        _task_ids=tuple(sorted(all_ids)),
        task_id_token_count=len(all_ids),
    )


__all__ = [
    "MAX_AGGREGATE_BYTES", "MAX_MEMBER_BYTES", "MAX_MEMBERS", "PredecessorFile",
    "PredecessorSnapshot", "task_id_tokens", "verify_predecessor_manifest",
]
