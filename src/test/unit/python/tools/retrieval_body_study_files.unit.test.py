"""BODY file custody tests using only synthetic private temporary data."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

import pytest

from retrieval_body_study_contracts import BodyStudyRefusal
from retrieval_body_study_files import publish_member, read_member


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "body-files"
    root.mkdir(mode=0o700)
    return root


def test_read_member_checks_size_digest_and_identity(tmp_path: Path) -> None:
    root = _root(tmp_path)
    nested = root / "nested"
    nested.mkdir(mode=0o700)
    payload = b"synthetic-body-member"
    (nested / "member.bin").write_bytes(payload)
    assert read_member(root, "nested/member.bin", max_bytes=len(payload),
                       expected_sha256=_digest(payload)) == payload
    with pytest.raises(BodyStudyRefusal, match="io:member_too_large"):
        read_member(root, "nested/member.bin", max_bytes=len(payload) - 1)
    with pytest.raises(BodyStudyRefusal, match="io:member_digest_mismatch"):
        read_member(root, "nested/member.bin", max_bytes=len(payload),
                    expected_sha256=_digest(b"other"))


def test_directory_and_leaf_symlinks_are_never_followed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    real = root / "real"
    real.mkdir(mode=0o700)
    (real / "member.bin").write_bytes(b"payload")
    (root / "linked").symlink_to(real, target_is_directory=True)
    with pytest.raises(BodyStudyRefusal, match="io:unsafe_directory_component"):
        read_member(root, "linked/member.bin", max_bytes=32)
    (root / "leaf-link").symlink_to(real / "member.bin")
    with pytest.raises(BodyStudyRefusal, match="io:member_not_regular"):
        read_member(root, "leaf-link", max_bytes=32)
    alias = tmp_path / "root-alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(BodyStudyRefusal, match="io:unsafe_directory_component"):
        read_member(alias, "real/member.bin", max_bytes=32)


def test_read_member_refuses_nonregular_files(tmp_path: Path) -> None:
    root = _root(tmp_path)
    fifo = root / "pipe"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(BodyStudyRefusal, match="io:member_not_regular"):
        read_member(root, "pipe", max_bytes=32)


def test_publish_member_is_private_exclusive_and_non_overwriting(tmp_path: Path) -> None:
    root = _root(tmp_path)
    payload = b"first-public-safe-record"
    publish_member(root, "nested/record.json", payload)
    target = root / "nested/record.json"
    assert target.read_bytes() == payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert read_member(root, "nested/record.json", max_bytes=len(payload),
                       expected_sha256=_digest(payload)) == payload
    with pytest.raises(BodyStudyRefusal, match="io:destination_exists"):
        publish_member(root, "nested/record.json", b"replacement")
    assert target.read_bytes() == payload


def test_publish_member_refuses_existing_leaf_symlink(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = root / "target"
    target.write_bytes(b"original")
    link = root / "published"
    link.symlink_to(target)
    with pytest.raises(BodyStudyRefusal, match="io:destination_exists"):
        publish_member(root, "published", b"replacement")
    assert target.read_bytes() == b"original"
