"""Strict in-memory wheel verification and the expected sealed tree (spec 2.2, Q10 host side)."""

from __future__ import annotations

import csv
import hashlib
import zipfile
from dataclasses import replace
from io import BytesIO
from typing import Any

import pytest

from common_lore_index.extractors.rust_pilot.archive_verifier import (
    ArchiveRefused,
    _verify_record,
    expected_install_tree,
    expected_tree_digest,
    record_hash,
    require_member_name,
    tree_digest,
    verify_archive,
    verify_wheel,
)
from common_lore_index.extractors.rust_pilot.component_lock import MemberLock, canonical_json
from repomap_test_support.rust_pilot_fake_tree_sitter.wheels import build_wheel, synthetic_wheel

FILES = {"demo/__init__.py": b"VALUE = 1\n", "demo/_binding.so": b"\x7fELF-not-real",
         "demo-1.0.dist-info/METADATA": b"Name: demo\n"}
DIST = "demo-1.0.dist-info"


def _verify(data: bytes, contents: dict[str, bytes], **overrides: object) -> tuple[tuple[str, bytes], ...]:
    members = [MemberLock(n, len(b), hashlib.sha256(b).hexdigest(), 0o644) for n, b in contents.items()]
    kwargs: dict[str, Any] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "dist_info": DIST,
                              "top_level": ("demo", DIST), "members": members, **overrides}
    return verify_archive(data, **kwargs)


def _code(data: bytes, contents: dict[str, bytes], **overrides: object) -> str:
    with pytest.raises(ArchiveRefused) as raised:
        _verify(data, contents, **overrides)
    return raised.value.code


def _archive_with_directory(*, name: str = "demo/", mode: int = 0o40755,
                            payload: bytes = b"", compression: int = zipfile.ZIP_STORED,
                            duplicate: bool = False) -> tuple[bytes, dict[str, bytes]]:
    record_name = f"{DIST}/RECORD"
    contents = {**FILES}
    rows = [f"{path},{record_hash(blob)},{len(blob)}" for path, blob in contents.items()]
    contents[record_name] = ("\n".join([*rows, f"{record_name},,"]) + "\n").encode()
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, blob in contents.items():
            info = zipfile.ZipInfo(path, date_time=(2026, 11, 5, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, blob)
        for _ in range(2 if duplicate else 1):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 5, 0, 0, 0))
            info.external_attr = mode << 16
            info.compress_type = compression
            archive.writestr(info, payload)
    return buffer.getvalue(), contents


def test_explicit_empty_parent_directory_is_admitted_without_record_row() -> None:
    data, contents = _archive_with_directory()
    verified = _verify(data, contents)
    assert "demo/" not in dict(verified)
    assert dict(verified)["demo/__init__.py"] == FILES["demo/__init__.py"]


def test_regular_members_respect_the_total_inflated_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from common_lore_index.extractors.rust_pilot import archive_verifier

    data, contents = _archive_with_directory()
    total = sum(len(blob) for blob in contents.values())
    monkeypatch.setattr(archive_verifier, "MAX_ARCHIVE_UNCOMPRESSED", total)
    assert dict(_verify(data, contents)) == contents
    monkeypatch.setattr(archive_verifier, "MAX_ARCHIVE_UNCOMPRESSED", total - 1)
    assert _code(data, contents) == "archive_too_large"


@pytest.mark.parametrize(("options", "code"), [
    ({"duplicate": True}, "duplicate_member"),
    ({"name": "demo/unused/"}, "unexpected_directory"),
    ({"name": "demo/__init__.py/"}, "member_path_collision"),
    ({"mode": 0o120777}, "symlink_member"),
    ({"mode": 0o40700}, "directory_mode_mismatch"),
    ({"name": "../escape/"}, "unsafe_member_name"),
    ({"payload": b"payload"}, "directory_payload"),
    ({"compression": zipfile.ZIP_BZIP2}, "compression_refused"),
])
def test_explicit_directory_metadata_is_closed_and_bounded(
        options: dict[str, Any], code: str) -> None:
    if options.get("duplicate"):
        with pytest.warns(UserWarning, match="Duplicate name"):
            data, contents = _archive_with_directory(**options)
    else:
        data, contents = _archive_with_directory(**options)
    assert _code(data, contents) == code


def test_locked_synthetic_wheel_is_verified_member_for_member() -> None:
    wheel = synthetic_wheel("demo", FILES)
    verified = verify_wheel(wheel.data, wheel.lock)
    assert dict(verified.members)["demo/_binding.so"] == FILES["demo/_binding.so"]
    assert [name for name, _ in verified.members][-1] == f"{DIST}/RECORD"


def test_parent_reuses_digests_proved_during_member_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from common_lore_index.extractors.rust_pilot import archive_verifier

    wheel = synthetic_wheel("demo", FILES)
    real_hash = hashlib.sha256
    hashed: list[int] = []

    def count(data: bytes = b""):
        hashed.append(len(data))
        return real_hash(data)

    with monkeypatch.context() as local:
        local.setattr(archive_verifier.hashlib, "sha256", count)
        verified = verify_wheel(wheel.data, wheel.lock)
    assert len(hashed) == 1 + len(verified.members)
    assert dict(verified.members)["demo/_binding.so"] == FILES["demo/_binding.so"]


def test_artifact_size_and_digest_are_checked_before_parsing() -> None:
    wheel = synthetic_wheel("demo", FILES)
    with pytest.raises(ArchiveRefused, match="artifact_size_mismatch"):
        verify_wheel(wheel.data + b"x", wheel.lock)
    with pytest.raises(ArchiveRefused, match="artifact_digest_mismatch"):
        verify_wheel(wheel.data, replace(wheel.lock, sha256="0" * 64))


def test_locked_member_permission_is_checked_independently_of_artifact_digest() -> None:
    wheel = synthetic_wheel("demo", FILES)
    changed = (replace(wheel.lock.members[0], wheel_mode=0o755), *wheel.lock.members[1:])
    with pytest.raises(ArchiveRefused, match="member_mode_mismatch"):
        verify_wheel(wheel.data, replace(wheel.lock, members=changed))


@pytest.mark.parametrize(("extra", "code"), [
    ({"demo/hook.pth": b"import os"}, "pth_member"),
    ({f"{DIST}/entry_points.txt": b"[x]"}, "entry_points_member"),
    ({"demo-1.0.data/scripts/x": b"#!"}, "data_directory_member"),
    ({"other-2.0.dist-info/METADATA": b"Name: other"}, "conflicting_distribution"),
    ({"sneaky/__init__.py": b""}, "unexpected_top_level"),
    ({"demo/extra.py": b""}, "member_set_mismatch"),
    ({"../escape.py": b""}, "unsafe_member_name"),
    ({"demo\\win.py": b""}, "unsafe_member_name"),
])
def test_tampered_members_are_refused_with_closed_codes(extra: dict[str, bytes], code: str) -> None:
    data, contents = build_wheel(FILES, DIST, extra=extra)
    assert _code(data, contents) == code


def test_symlink_special_encrypted_and_compression_attributes_are_refused() -> None:
    data, contents = build_wheel(FILES, DIST, mode=0o120777)
    assert _code(data, contents) == "symlink_member"
    data, contents = build_wheel(FILES, DIST, mode=0o010644)
    assert _code(data, contents) == "special_member"
    data, contents = build_wheel(FILES, DIST, compression=zipfile.ZIP_BZIP2)
    assert _code(data, contents) == "compression_refused"
    data, contents = build_wheel(FILES, DIST)
    flagged = bytearray(data)
    flagged[6] |= 0x01
    central = flagged.rfind(b"PK\x01\x02", 0)
    while central >= 0:
        flagged[central + 8] |= 0x01
        central = flagged.rfind(b"PK\x01\x02", 0, central)
    assert _code(bytes(flagged), contents) == "encrypted_member"


def test_duplicate_members_and_record_defects_are_refused() -> None:
    with pytest.warns(UserWarning, match="Duplicate name"):
        data, contents = build_wheel(FILES, DIST, extra={"demo/__init__.py": b"VALUE = 2\n"})
    assert _code(data, contents) == "duplicate_member"
    rows = [f"{n},{record_hash(b)},{len(b)}" for n, b in FILES.items()]
    cases = {
        "record_mismatch": [rows[0].replace(",10", ",11"), *rows[1:], f"{DIST}/RECORD,,"],
        "record_self_entry": [*rows, f"{DIST}/RECORD,sha256=x,1"],
        "record_incomplete": [*rows[1:], f"{DIST}/RECORD,,"],
        "record_extra_entry": [*rows, "demo/ghost.py,sha256=x,1", f"{DIST}/RECORD,,"],
        "record_invalid": [*rows, rows[0], f"{DIST}/RECORD,,"],
    }
    for code, record in cases.items():
        data, contents = build_wheel(FILES, DIST, record_rows=record)
        assert _code(data, contents) == code, code


def test_member_size_and_digest_must_match_the_lock() -> None:
    data, contents = build_wheel(FILES, DIST)
    wrong = {**contents, "demo/__init__.py": b"VALUE = 9\n"}
    assert _code(data, wrong) == "member_digest_mismatch"
    assert _code(data, {**contents, "demo/__init__.py": b"VALUE = 10\n"}) == "member_size_mismatch"
    assert _code(b"not a zip", contents, size=9, sha256=hashlib.sha256(b"not a zip").hexdigest()) == "invalid_archive"


def test_expected_tree_is_sealed_closed_and_collision_free() -> None:
    wheel = synthetic_wheel("demo", FILES)
    members = verify_wheel(wheel.data, wheel.lock).members
    tree = expected_install_tree([members], b"helper", [("core-notice", b"n")])
    modes = {entry.path: (entry.kind, entry.mode) for entry in tree}
    assert modes[""] == modes["site"] == modes["site/demo"] == ("d", 0o555)
    assert modes["site/demo/_binding.so"] == ("f", 0o555) and modes["site/demo/__init__.py"] == ("f", 0o444)
    assert modes["helper/parse_helper.py"] == modes["notices/core-notice"] == ("f", 0o444)
    assert [entry.path for entry in tree] == sorted(entry.path for entry in tree)
    rows = [[entry.path, entry.kind, entry.mode, None if entry.data is None else hashlib.sha256(entry.data).hexdigest(),
             None if entry.data is None else len(entry.data)] for entry in tree]
    assert expected_tree_digest(tuple(reversed(tree))) == hashlib.sha256(canonical_json(sorted(rows))).hexdigest()
    assert expected_tree_digest(tree) != expected_tree_digest(expected_install_tree([members], b"other", []))
    with pytest.raises(ArchiveRefused, match="install_path_collision"):
        expected_install_tree([members, members], b"h", [])
    for name in ("", "/abs", "a/../b", "a//b", "dir/"):
        with pytest.raises(ArchiveRefused):
            require_member_name(name)


def test_record_csv_read_failure_takes_precedence_over_earlier_bad_row() -> None:
    first = "demo/__init__.py,sha256=bad,10"
    unreadable = "x" * (csv.field_size_limit() + 1)
    data, contents = build_wheel(FILES, DIST, record_rows=[first, unreadable, f"{DIST}/RECORD,,"])
    assert _code(data, contents) == "record_invalid"


@pytest.mark.parametrize("invalid_tail", (b"\xff", b"\xe2\x82"))
def test_record_invalid_utf8_after_an_earlier_bad_row_is_a_framing_refusal(invalid_tail: bytes) -> None:
    record_name = f"{DIST}/RECORD"
    raw = b"unknown,sha256=bad,1\n" + b"other,,\n" * 1500 + invalid_tail
    with pytest.raises(ArchiveRefused, match="record_invalid"):
        _verify_record(raw, record_name, {})


@pytest.mark.parametrize("count", (0, 1, 255, 256, 257, 513))
def test_tree_digest_preserves_canonical_array_framing_across_batches(count: int) -> None:
    entries = [(f"fixture/é-{index:04d}", "d", 0o555, None) if index % 2 else
               (f"fixture/é-{index:04d}", "f", 0o444, b"bytes") for index in reversed(range(count))]
    rows = [[path, kind, mode, None if data is None else hashlib.sha256(data).hexdigest(),
             None if data is None else len(data)] for path, kind, mode, data in entries]
    assert tree_digest(entries) == hashlib.sha256(canonical_json(sorted(rows))).hexdigest()
