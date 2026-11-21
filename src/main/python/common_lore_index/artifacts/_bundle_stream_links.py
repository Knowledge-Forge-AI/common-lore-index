"""Disk-backed cross-family semantic link validation for publication bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from common_lore_index._temporary import OwnedTemporaryDirectory


if TYPE_CHECKING:
    from sqlite3 import Connection

_MAX_BATCH_BYTES = 1024 * 1024


class _LinkBatch:
    def __init__(self, connection: Connection, insert_sql: str) -> None:
        self._connection = connection
        self._insert_sql = insert_sql
        self._rows: list[tuple[str, ...]] = []
        self._pending_bytes = 0

    def add(self, values: tuple[str, ...], row_limit: int) -> None:
        # JSON keys are serialized as ASCII, so text length is the bound size.
        size = sum(map(len, values))
        if self._pending_bytes + size > _MAX_BATCH_BYTES:
            self.flush()
        if size > _MAX_BATCH_BYTES:
            self._connection.execute(self._insert_sql, values)
            self._connection.commit()
            return
        self._rows.append(values)
        self._pending_bytes += size
        if len(self._rows) >= row_limit:
            self.flush()

    def flush(self) -> None:
        if self._rows:
            self._connection.executemany(self._insert_sql, self._rows)
            self._connection.commit()
            self._rows.clear()
            self._pending_bytes = 0


class DiskFamilyLinkValidator:
    """Validate referential integrity across bundle families with bounded memory."""

    def __init__(self, *, spool_dir: Path | str | None = None) -> None:
        import sqlite3

        self._spool_dir = Path(spool_dir) if spool_dir is not None else None
        self._temp_dir: OwnedTemporaryDirectory | None = OwnedTemporaryDirectory(
            prefix="repomap_links_",
            dir=str(self._spool_dir) if self._spool_dir is not None else None,
        )
        self._db_path = Path(self._temp_dir.name) / "links.db"
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(str(self._db_path))
            self._conn = connection
            self._conn.execute("PRAGMA synchronous = OFF")
            self._conn.execute("PRAGMA journal_mode = OFF")
            self._conn.execute("PRAGMA temp_store = MEMORY")
            self._conn.execute("CREATE TABLE raw_ordinals (ordinal TEXT PRIMARY KEY)")
            self._conn.execute("CREATE TABLE node_keys (key TEXT PRIMARY KEY)")
            self._conn.execute(
                "CREATE TABLE edge_keys (src TEXT, kind TEXT, tgt TEXT, hash TEXT, "
                "PRIMARY KEY (src, kind, tgt, hash))"
            )
            self._conn.execute("CREATE TABLE evidence_keys (key TEXT PRIMARY KEY)")
            self._raw_ordinals = _LinkBatch(self._conn, "INSERT OR IGNORE INTO raw_ordinals VALUES (?)")
            self._node_keys = _LinkBatch(self._conn, "INSERT OR IGNORE INTO node_keys VALUES (?)")
            self._edge_keys = _LinkBatch(self._conn, "INSERT OR IGNORE INTO edge_keys VALUES (?, ?, ?, ?)")
            self._evidence_keys = _LinkBatch(self._conn, "INSERT OR IGNORE INTO evidence_keys VALUES (?)")
        except BaseException:
            if connection is not None:
                try:
                    connection.close()
                except BaseException:
                    pass
            if self._temp_dir is not None:
                try:
                    self._temp_dir.cleanup()
                except BaseException:
                    pass
            raise

        self._batch_size = 5000

    def record_raw_observation(self, record: Mapping[str, object]) -> None:
        key = json.dumps(record.get("source_ordinal"), separators=(",", ":"))
        self._raw_ordinals.add((key,), self._batch_size)

    def record_canonical_node(self, record: Mapping[str, object]) -> None:
        key = json.dumps(record.get("canonical_key"), separators=(",", ":"))
        self._node_keys.add((key,), self._batch_size)

    def record_canonical_edge(self, record: Mapping[str, object]) -> None:
        src = json.dumps(record.get("source_canonical_key"), separators=(",", ":"))
        kind = json.dumps(record.get("edge_kind"), separators=(",", ":"))
        tgt = json.dumps(record.get("target_canonical_key"), separators=(",", ":"))
        hash_val = json.dumps(record.get("identity_metadata_hash"), separators=(",", ":"))
        self._edge_keys.add((src, kind, tgt, hash_val), self._batch_size)

    def record_and_validate_evidence(self, record: Mapping[str, object]) -> None:
        self._raw_ordinals.flush()
        raw_ord = json.dumps(record.get("raw_observation_ordinal"), separators=(",", ":"))
        cursor = self._conn.execute(
            "SELECT 1 FROM raw_ordinals WHERE ordinal = ? LIMIT 1", (raw_ord,)
        )
        if cursor.fetchone() is None:
            raise ValueError("bundle semantic evidence reference is invalid")
        ev_key = json.dumps(record.get("evidence_key"), separators=(",", ":"))
        self._evidence_keys.add((ev_key,), self._batch_size)

    def validate_node_evidence(self, record: Mapping[str, object]) -> None:
        self._node_keys.flush()
        self._evidence_keys.flush()
        node_key = json.dumps(record.get("canonical_key"), separators=(",", ":"))
        ev_key = json.dumps(record.get("evidence_key"), separators=(",", ":"))
        cursor = self._conn.execute(
            "SELECT 1 FROM node_keys WHERE key = ? LIMIT 1", (node_key,)
        )
        if cursor.fetchone() is None:
            raise ValueError("bundle semantic node evidence reference is invalid")
        cursor = self._conn.execute(
            "SELECT 1 FROM evidence_keys WHERE key = ? LIMIT 1", (ev_key,)
        )
        if cursor.fetchone() is None:
            raise ValueError("bundle semantic node evidence reference is invalid")

    def validate_edge_evidence(self, record: Mapping[str, object]) -> None:
        self._edge_keys.flush()
        self._evidence_keys.flush()
        src = json.dumps(record.get("source_canonical_key"), separators=(",", ":"))
        kind = json.dumps(record.get("edge_kind"), separators=(",", ":"))
        tgt = json.dumps(record.get("target_canonical_key"), separators=(",", ":"))
        hash_val = json.dumps(record.get("identity_metadata_hash"), separators=(",", ":"))
        ev_key = json.dumps(record.get("evidence_key"), separators=(",", ":"))
        cursor = self._conn.execute(
            "SELECT 1 FROM edge_keys WHERE src = ? AND kind = ? AND tgt = ? AND hash = ? LIMIT 1",
            (src, kind, tgt, hash_val),
        )
        if cursor.fetchone() is None:
            raise ValueError("bundle semantic edge evidence reference is invalid")
        cursor = self._conn.execute(
            "SELECT 1 FROM evidence_keys WHERE key = ? LIMIT 1", (ev_key,)
        )
        if cursor.fetchone() is None:
            raise ValueError("bundle semantic edge evidence reference is invalid")

    def finish_family(self, family: str) -> None:
        """Flush buffers and drop tables that are no longer needed."""
        if family == "raw_observations":
            self._raw_ordinals.flush()
        elif family == "canonical_nodes":
            self._node_keys.flush()
        elif family == "canonical_edges":
            self._edge_keys.flush()
        elif family == "canonical_evidence":
            self._evidence_keys.flush()
            self._conn.execute("DROP TABLE IF EXISTS raw_ordinals")
        elif family == "canonical_node_evidence":
            self._conn.execute("DROP TABLE IF EXISTS node_keys")
        elif family == "canonical_edge_evidence":
            self._conn.execute("DROP TABLE IF EXISTS edge_keys")
            self._conn.execute("DROP TABLE IF EXISTS evidence_keys")

    def close(self) -> None:
        """Close database connection and clean up temp files."""
        try:
            self._conn.close()
        except Exception:
            pass
        if self._temp_dir is not None:
            try:
                self._temp_dir.cleanup()
            finally:
                self._temp_dir = None

    def __enter__(self) -> DiskFamilyLinkValidator:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
