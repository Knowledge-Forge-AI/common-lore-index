"""Selection rule rust-pilot-selection-v1 over a discovered inventory (spec 4, Q06)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from common_lore_index.extractors.rust_pilot.selection import MAX_INPUT_BYTES, select_inputs
from common_lore_index.graph.discovery_records import FileInfo


def _info(path: str, data: bytes) -> FileInfo:
    return FileInfo(path=path, language="unknown", role="source", content_hash=hashlib.sha256(data).hexdigest(),
                    executable=False, generated=False)


def _write(root: Path, relative: str, data: bytes) -> FileInfo:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return _info(relative, data)


def test_only_case_sensitive_rs_paths_get_one_record_each(tmp_path: Path) -> None:
    infos = [_write(tmp_path, "src/lib.rs", b"fn a() {}\n"), _write(tmp_path, "src/UPPER.RS", b"fn b() {}\n"),
             _write(tmp_path, "README.md", b"# x\n"), _write(tmp_path, "tools/build.rs", b"fn main() {}\n")]
    records = select_inputs(tmp_path, infos)
    assert [(r.path, r.outcome, r.build_script_name) for r in records] == [
        ("src/lib.rs", "selected", False), ("tools/build.rs", "selected", True)]
    assert records[0].data == b"fn a() {}\n" and records[0].input_sha256 == infos[0].content_hash


def test_bom_and_crlf_bytes_are_retained_exactly(tmp_path: Path) -> None:
    data = b"\xef\xbb\xbfpub struct A;\r\n"
    record = select_inputs(tmp_path, [_write(tmp_path, "a.rs", data)])[0]
    assert (record.outcome, record.data) == ("selected", data)


def test_symlinks_and_non_regular_entries_are_selection_refused(tmp_path: Path) -> None:
    real = _write(tmp_path, "real/lib.rs", b"fn a() {}\n")
    (tmp_path / "link.rs").symlink_to(tmp_path / "real" / "lib.rs")
    (tmp_path / "linked_dir").symlink_to(tmp_path / "real", target_is_directory=True)
    (tmp_path / "dir.rs").mkdir()
    os.mkfifo(tmp_path / "pipe.rs")
    infos = [_info("link.rs", b"fn a() {}\n"), _info("linked_dir/lib.rs", b"fn a() {}\n"),
             _info("dir.rs", b""), _info("pipe.rs", b""), _info("../escape.rs", b""), _info("missing.rs", b""), real]
    outcomes = {r.path: r.outcome for r in select_inputs(tmp_path, infos)}
    assert outcomes == {"link.rs": "selection_refused", "linked_dir/lib.rs": "selection_refused",
                        "dir.rs": "selection_refused", "pipe.rs": "selection_refused",
                        "../escape.rs": "selection_refused", "missing.rs": "selection_refused",
                        "real/lib.rs": "selected"}


def test_size_digest_and_encoding_refusals_retain_no_bytes(tmp_path: Path) -> None:
    big = _write(tmp_path, "big.rs", b"a" * (MAX_INPUT_BYTES + 1))
    edge = _write(tmp_path, "edge.rs", b"b" * MAX_INPUT_BYTES)
    changed = _write(tmp_path, "changed.rs", b"fn a() {}\n")
    (tmp_path / "changed.rs").write_bytes(b"fn b() {}\n")
    latin = _write(tmp_path, "latin.rs", b"const S: &str = \"caf\xe9\";\n")
    records = {r.path: r for r in select_inputs(tmp_path, [big, edge, changed, latin, edge])}
    assert records["big.rs"].outcome == "resource_bound_exceeded"
    assert records["changed.rs"].outcome == "source_changed"
    assert records["latin.rs"].outcome == "input_not_utf8"
    assert all(records[p].data is None for p in ("big.rs", "changed.rs", "latin.rs"))
    duplicates = [r.outcome for r in select_inputs(tmp_path, [edge, edge])]
    assert duplicates == ["selected", "selection_refused"]


def test_frozen_invalid_utf8_fixture_is_refused_before_any_container(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parents[5] / "fixtures" / "rust_pilot" / "q06_invalid_utf8.rs"
    info = _write(tmp_path, "q06.rs", fixture.read_bytes())
    assert select_inputs(tmp_path, [info])[0].outcome == "input_not_utf8"


def test_nested_selection_keeps_constant_number_of_directory_descriptors(tmp_path: Path, monkeypatch) -> None:
    from common_lore_index.extractors.rust_pilot import selection

    info = _write(tmp_path, "a/b/c/d/e/f/lib.rs", b"fn fixture() {}\n")
    actual_open, actual_close = os.open, os.close
    directories: set[int] = set()
    peak = 0

    def open_directory(path, flags, *args, **kwargs):
        nonlocal peak
        descriptor = actual_open(path, flags, *args, **kwargs)
        if flags & os.O_DIRECTORY:
            directories.add(descriptor)
            peak = max(peak, len(directories))
        return descriptor

    def close(descriptor):
        directories.discard(descriptor)
        actual_close(descriptor)

    with monkeypatch.context() as local:
        local.setattr(selection.os, "open", open_directory)
        local.setattr(selection.os, "close", close)
        selected, = select_inputs(tmp_path, [info])
    assert selected.outcome == "selected" and selected.input_sha256 == info.content_hash
    assert peak == 3 and not directories


def test_selection_does_not_read_past_admitted_extent(tmp_path, monkeypatch):
    from common_lore_index.extractors.rust_pilot import selection

    data = b"fn fixture() {}\n"
    info = _write(tmp_path, "lib.rs", data)
    path = tmp_path / "lib.rs"
    before = path.stat()
    read = os.read
    used: list[int] = []

    def grow_before_read(descriptor, length):
        stat = os.fstat(descriptor)
        if (stat.st_dev, stat.st_ino) == (before.st_dev, before.st_ino):
            if not used:
                with path.open("ab") as f:
                    f.write(b"// appended\n")
            used.append(length)
        return read(descriptor, length)

    with monkeypatch.context() as local:
        local.setattr(selection.os, "read", grow_before_read)
        record, = select_inputs(tmp_path, [info])
    assert record.outcome == "source_changed" and record.data is None
    assert used == [len(data)]


def test_read_fault_is_closed_and_does_not_discard_the_next_selection(tmp_path, monkeypatch):
    from common_lore_index.extractors.rust_pilot import selection

    first = _write(tmp_path, "first.rs", b"fn first() {}\n")
    second = _write(tmp_path, "second.rs", b"fn second() {}\n")
    inode = (tmp_path / "first.rs").stat().st_ino
    read = os.read

    def refuse_first(descriptor, count):
        if os.fstat(descriptor).st_ino == inode:
            raise OSError("fixture read")
        return read(descriptor, count)

    with monkeypatch.context() as local:
        local.setattr(selection.os, "read", refuse_first)
        refused, selected = select_inputs(tmp_path, [first, second])
    assert refused.outcome == "selection_refused" and refused.data is None
    assert selected.outcome == "selected" and selected.input_sha256 == second.content_hash


def test_utf8_validation_preserves_chunk_boundary_and_requires_complete_final_character(tmp_path):
    text = b" " * 65_535 + "漢".encode()
    valid = _write(tmp_path, "valid.rs", text)
    truncated = _write(tmp_path, "truncated.rs", text[:-1])
    passed, rejected = select_inputs(tmp_path, [valid, truncated])
    assert passed.outcome == "selected" and passed.data == text
    assert rejected.outcome == "input_not_utf8" and rejected.data is None
