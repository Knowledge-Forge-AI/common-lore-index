"""Real filesystem custody and static-only source delivery for functional data."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from common_lore_index.graph.discovery import discover_observations
from common_lore_index_test_support.retrieval_supplement_fixtures import (
    make_test_bindings, make_test_corpus_payload, make_test_identity,
)
from retrieval_supplement_corpus import materialize_corpus, parse_corpus
from retrieval_supplement_files import file_inventory, publish_member, read_member
from retrieval_supplement_values import canonical_bytes, digest_bytes


def test_real_nested_publication_is_exclusive_and_digest_bound(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir(mode=0o700)
    raw = "functional café\n".encode("utf-8")
    publish_member(root, "nested/member.txt", raw)
    assert file_inventory(root) == ("nested/member.txt",)
    assert read_member(root, "nested/member.txt", max_bytes=128, expected_sha256=digest_bytes(raw)) == raw
    with pytest.raises((ValueError, OSError)):
        publish_member(root, "nested/member.txt", b"replacement")
    assert (root / "nested/member.txt").read_bytes() == raw
    with pytest.raises(ValueError):
        read_member(root, "nested/member.txt", max_bytes=1)
    with pytest.raises(ValueError):
        read_member(root, "nested/member.txt", max_bytes=128, expected_sha256=digest_bytes(b"wrong"))


def test_real_root_parent_component_and_leaf_symlinks_are_refused(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "member").write_bytes(b"outside")
    (root / "link").symlink_to(other, target_is_directory=True)
    (root / "leaf").symlink_to(other / "member")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    for base, name in ((root, "link/member"), (root, "leaf"), (alias, "leaf")):
        with pytest.raises((ValueError, OSError)):
            read_member(base, name, max_bytes=128)
    with pytest.raises((ValueError, OSError)):
        publish_member(root, "link/created", b"forbidden")
    assert not (other / "created").exists()


def test_real_fifo_is_rejected_before_blocking_read(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises((ValueError, OSError)):
        read_member(tmp_path, "pipe", max_bytes=128)


def test_real_member_change_during_read_cannot_issue_a_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    member = tmp_path / "member"
    member.write_bytes(b"original")
    original_read = os.read
    changed = False
    def changing_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        raw = original_read(descriptor, size)
        if not changed:
            changed = True
            member.write_bytes(b"changed-and-longer")
        return raw
    monkeypatch.setattr(os, "read", changing_read)
    with pytest.raises(ValueError):
        read_member(tmp_path, "member", max_bytes=128)
    assert changed


def test_failed_write_cleans_only_its_temporary_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "retained").write_bytes(b"keep")
    def fail_write(descriptor: int, data: bytes | memoryview) -> int:
        raise OSError("synthetic write failure")
    monkeypatch.setattr(os, "write", fail_write)
    with pytest.raises(ValueError, match="io:publication_failed"):
        publish_member(tmp_path, "new", b"complete")
    assert file_inventory(tmp_path) == ("retained",)
    assert (tmp_path / "retained").read_bytes() == b"keep"


def test_materialization_and_discovery_never_execute_target_source(tmp_path: Path) -> None:
    payload = make_test_corpus_payload()
    for row in payload["files"]:
        if row["role"] == "code":
            row["content_utf8"] = "raise AssertionError('SOURCE_MUST_NOT_EXECUTE')\n\ndef inert_target():\n    return 1\n"
            break
    corpus = parse_corpus(canonical_bytes(payload), identity=make_test_identity(), bindings=make_test_bindings())
    root = tmp_path / "working-copy"
    root.mkdir(mode=0o700)
    members = materialize_corpus(corpus, root)
    assert members
    observations = discover_observations(root)
    assert any(row.name == "inert_target" for row in observations)
    assert file_inventory(root) == tuple(sorted(members))
    with pytest.raises(ValueError):
        materialize_corpus(corpus, root)
