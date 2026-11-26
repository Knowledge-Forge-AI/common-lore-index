"""Restore admission validates real artifacts before touching a destination store."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from common_lore_index.storage.sqlite_local.backup import check_backup_output, inspect_sealed_database, write_backup
from common_lore_index.storage.sqlite_local.backup_manifest import BackupManifest
from common_lore_index.storage.sqlite_local.connection import graph_database_uri, publisher_lock, read_transaction
from common_lore_index.storage.sqlite_local.migrations import MIGRATION_V1
from common_lore_index.storage.sqlite_local.restore import install_backup, verify_backup
from common_lore_index.storage.sqlite_local.schema import LocalStoreError, accepted_identity
from common_lore_index_test_support.sqlite_local_fixtures import (
    local_binding,
    published_sqlite_database,
    sqlite_logical_dump,
)

BINDING = local_binding()
STAMP = "2026-10-18T00:00:00Z"


def _artifact(root: Path, *, generations: int = 1) -> tuple[Path, BackupManifest]:
    source = published_sqlite_database(root / "live", generations=generations)
    output = root / "backup"
    with publisher_lock(source):
        check_backup_output(output)
        manifest = write_backup(source, BINDING, output, created_at=STAMP)
    return output, manifest


def _restore(backup: Path, target: Path) -> None:
    # Keep validation before the lock, as the operator entrypoint does.
    manifest = verify_backup(backup, BINDING)
    with publisher_lock(target):
        install_backup(backup, manifest, target, BINDING, durable_ancestors=0)


@pytest.mark.parametrize(
    ("block", "key", "value", "reason"),
    [
        (None, "graph", [], "manifest-invalid"),
        (None, "extra", True, "manifest-invalid"),
        (None, "format", "unrecognized", "format-unsupported"),
        (None, "manifest_version", True, "format-unsupported"),
        (None, "manifest_version", 1.0, "format-unsupported"),
        (None, "created_at", "yesterday", "manifest-invalid"),
        ("publication", "generation", True, "manifest-invalid"),
        ("publication", "publication_bundle_id", "", "manifest-invalid"),
        ("publication", "accepted", False, "manifest-invalid"),
        ("publication", "accepted", 1, "manifest-invalid"),
        ("database", "file", "../unrelated.sqlite3", "manifest-invalid"),
        ("database", "sha256", "A" * 64, "manifest-invalid"),
        ("database", "bytes", 0, "manifest-invalid"),
        ("database", "bytes", 1, "database-length-mismatch"),
        ("graph", "graph_id", "another-graph", "graph-mismatch"),
    ],
    ids=["block-type", "unknown-key", "format", "version-bool", "version-float", "timestamp", "generation-bool",
         "empty-bundle", "false-with-identity", "accepted-integer", "file-escape", "digest-case",
         "zero-size", "wrong-size", "graph-binding"],
)
def test_manifest_refusal_leaves_the_destination_uncreated(
    tmp_path: Path, block: str | None, key: str, value: Any, reason: str,
) -> None:
    backup, _ = _artifact(tmp_path)
    manifest_file = backup / "manifest.json"
    payload = json.loads(manifest_file.read_bytes())
    (payload if block is None else payload[block])[key] = value
    manifest_file.write_text(json.dumps(payload), encoding="utf-8")
    before = {name.name: name.read_bytes() for name in backup.iterdir()}
    target = tmp_path / "untouched-store" / "graph.sqlite3"
    with pytest.raises(LocalStoreError) as caught:
        _restore(backup, target)
    assert str(caught.value) == f"sqlite-backup-artifact-invalid: {reason}"
    assert not target.parent.exists()
    assert {name.name: name.read_bytes() for name in backup.iterdir()} == before


@pytest.mark.parametrize("shape", ["oversized", "directory", "unexpected-entry", "not-directory", "database-directory"])
def test_artifact_filesystem_shape_is_checked_before_install(tmp_path: Path, shape: str) -> None:
    backup, _ = _artifact(tmp_path)
    if shape == "oversized":
        (backup / "manifest.json").write_bytes(b" " * 17000)
    elif shape == "directory":
        (backup / "manifest.json").unlink()
        (backup / "manifest.json").mkdir()
    elif shape == "unexpected-entry":
        (backup / "graph.sqlite3-wal").write_bytes(b"unrelated")
    elif shape == "database-directory":
        (backup / "graph.sqlite3").unlink()
        (backup / "graph.sqlite3").mkdir()
    else:
        backup = backup / "graph.sqlite3"
    reason = {"oversized": "manifest-invalid", "directory": "manifest-invalid",
              "unexpected-entry": "unexpected-entry", "not-directory": "not-a-directory",
              "database-directory": "database-unreadable"}[shape]
    target = tmp_path / "untouched" / "graph.sqlite3"
    with pytest.raises(LocalStoreError) as caught:
        _restore(backup, target)
    assert str(caught.value) == f"sqlite-backup-artifact-invalid: {reason}"
    assert not target.parent.exists()


@pytest.mark.parametrize("damage", ["short-header", "not-wal", "missing-page", "integrity", "schema-version"])
def test_rehashed_artifact_still_requires_a_valid_sealed_database(tmp_path: Path, damage: str) -> None:
    backup, _ = _artifact(tmp_path)
    database = backup / "graph.sqlite3"
    if damage == "short-header":
        database.write_bytes(b"short")
    elif damage == "not-wal":
        data = bytearray(database.read_bytes())
        data[18:20] = b"\x01\x01"
        database.write_bytes(data)
    elif damage == "missing-page":
        sealed_bytes = database.read_bytes()
        page_size = int.from_bytes(sealed_bytes[16:18], "big")
        page_size = 65536 if page_size == 1 else page_size
        assert len(sealed_bytes) >= 2 * page_size
        database.write_bytes(sealed_bytes[:-page_size])
    elif damage == "integrity":
        uri = graph_database_uri(database, read_only=True) + "&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            root_page, = connection.execute(
                "SELECT rootpage FROM sqlite_schema WHERE name = 'canonical_nodes'"
            ).fetchone()
            page_size, = connection.execute("PRAGMA page_size").fetchone()
        assert root_page > 1
        offset = (root_page - 1) * page_size
        with database.open("r+b") as file:
            file.seek(offset)
            assert file.read(1)[0] in (5, 13)  # table b-tree interior or leaf
            file.seek(offset)
            file.write(bytes(1))
    payload = json.loads((backup / "manifest.json").read_bytes())
    if damage == "schema-version":
        payload["sqlite"].update(user_version=1, schema_name=MIGRATION_V1.name,
                                 schema_checksum=MIGRATION_V1.checksum)
    assert not any(database.with_name(database.name + suffix).exists() for suffix in ("-wal", "-shm"))
    payload["database"].update(bytes=database.stat().st_size, sha256=hashlib.sha256(database.read_bytes()).hexdigest())
    (backup / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    before = database.read_bytes()
    target = tmp_path / "untouched" / "graph.sqlite3"
    with pytest.raises(LocalStoreError) as caught:
        _restore(backup, target)
    reason = "schema-mismatch" if damage == "schema-version" else "graph-database-unrecognized"
    assert str(caught.value) == f"sqlite-backup-artifact-invalid: {reason}"
    assert database.read_bytes() == before and not target.parent.exists()


def test_short_manifest_reads_restore_the_same_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backup, manifest = _artifact(tmp_path)
    identity = (backup / "manifest.json").stat()
    read = os.read
    transferred = []

    def short_manifest_reads(descriptor: int, count: int) -> bytes:
        details = os.fstat(descriptor)
        if (details.st_dev, details.st_ino) == (identity.st_dev, identity.st_ino):
            data = read(descriptor, min(count, 47))
            transferred.append(len(data))
            return data
        return read(descriptor, count)

    target = tmp_path / "restored" / "graph.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(os, "read", short_manifest_reads)
        _restore(backup, target)
    assert len(transferred) > 2 and sum(transferred) == identity.st_size
    assert verify_backup(backup, BINDING) == manifest
    assert sqlite_logical_dump(target) == sqlite_logical_dump(backup / "graph.sqlite3")


def test_unpublished_backup_restores_without_inventing_a_publication(tmp_path: Path) -> None:
    backup, manifest = _artifact(tmp_path, generations=0)
    target = tmp_path / "restored" / "graph.sqlite3"
    _restore(backup, target)
    assert manifest.publication is None
    with read_transaction(target, BINDING) as reader:
        assert accepted_identity(reader) is None
        assert reader.execute("SELECT count(*) FROM runs").fetchone() == (0,)
    assert sqlite_logical_dump(target) == sqlite_logical_dump(backup / "graph.sqlite3")


def test_sealed_snapshot_accepts_backup_and_refuses_nonregular_input(tmp_path: Path) -> None:
    artifact, manifest = _artifact(tmp_path)
    database, = artifact.glob("*.sqlite3")
    _, publication = inspect_sealed_database(database, BINDING)
    assert publication == manifest.publication

    pipe = tmp_path / "pipe.sqlite3"
    os.mkfifo(pipe)
    with pytest.raises(LocalStoreError, match="graph-database-unrecognized"):
        inspect_sealed_database(pipe, BINDING)


def test_sealed_database_header_allows_short_descriptor_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact, manifest = _artifact(tmp_path)
    path = artifact / "graph.sqlite3"
    identity = path.stat()
    read = os.read
    header: list[bytes] = []

    def read_short(descriptor: int, size: int) -> bytes:
        details = os.fstat(descriptor)
        if (details.st_dev, details.st_ino) == (identity.st_dev, identity.st_ino):
            chunk = read(descriptor, min(size, 7))
            header.append(chunk)
            return chunk
        return read(descriptor, size)

    with monkeypatch.context() as local:
        local.setattr(os, "read", read_short)
        checked = inspect_sealed_database(path, BINDING)
    assert checked == (manifest.schema_version, manifest.publication)
    assert [len(chunk) for chunk in header] == [7, 7, 6]


def test_restore_refuses_duplicate_manifest_members_before_creating_destination(tmp_path: Path) -> None:
    backup, _ = _artifact(tmp_path)
    manifest = backup / "manifest.json"
    encoded = manifest.read_bytes()
    assert b'"manifest_version"' in encoded
    manifest.write_bytes(b'{"manifest_version":2,' + encoded[1:])
    target = tmp_path / "unused" / "graph.sqlite3"
    with pytest.raises(LocalStoreError, match="manifest-invalid"):
        _restore(backup, target)
    assert not target.parent.exists()
