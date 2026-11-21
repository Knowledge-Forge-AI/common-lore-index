"""A failed private link index setup reclaims its acquired resources."""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from common_lore_index.artifacts import _bundle_stream_links as links
from common_lore_index.artifacts._bundle_stream_links import DiskFamilyLinkValidator


@pytest.mark.parametrize("failure", ["connect", "setup"])
def test_partial_link_index_initialization_reclaims_connection_and_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    original_connect = sqlite3.connect
    marker = RuntimeError("synthetic link-index refusal")
    connections: list[sqlite3.Connection] = []

    class Connection:
        def execute(self, *_args, **_kwargs):
            raise marker

        def close(self):
            connections[0].close()

    def connect(*args, **kwargs):
        if failure == "connect":
            raise marker
        connections.append(original_connect(*args, **kwargs))
        return Connection()

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(RuntimeError) as caught:
        DiskFamilyLinkValidator(spool_dir=tmp_path)
    assert caught.value is marker
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
    assert not tuple(tmp_path.iterdir())


def test_link_batch_flushes_at_encoded_byte_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links, "_MAX_BATCH_BYTES", 16)
    with DiskFamilyLinkValidator(spool_dir=tmp_path) as validator:
        # Each short key occupies eight ASCII bytes after JSON escaping.
        for key in ("雪", "雲"):
            validator.record_canonical_node({"canonical_key": key})
        assert validator._conn.execute("SELECT count(*) FROM node_keys").fetchone() == (0,)

        validator.record_canonical_node({"canonical_key": "風"})
        assert validator._conn.execute("SELECT count(*) FROM node_keys").fetchone() == (2,)

        validator.record_canonical_node({"canonical_key": "吹雪の雲"})
        assert validator._conn.execute("SELECT count(*) FROM node_keys").fetchone() == (4,)
        assert validator._node_keys._pending_bytes == 0
        validator.finish_family("canonical_nodes")
        assert validator._conn.execute("SELECT count(*) FROM node_keys").fetchone() == (4,)
    assert not tuple(tmp_path.iterdir())
