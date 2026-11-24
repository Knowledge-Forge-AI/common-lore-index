"""Selection-file bounded I/O regressions for W4 context records."""

from __future__ import annotations

import errno
from pathlib import Path
from types import SimpleNamespace

import pytest

from common_lore_index.retrieval.context_contracts import ContextRequestError
from common_lore_index.retrieval.source_selection import (
    MAX_SELECTION_FILE_BYTES,
    SourceBindingSelection,
    SourceSelectionRecord,
)
import common_lore_index.retrieval.source_selection as source_selection


def _record() -> SourceSelectionRecord:
    return SourceSelectionRecord.build(
        graph_id="selection-io-graph",
        repository_id="repo1:selection-io-graph",
        backend="sqlite",
        bindings=(
            SourceBindingSelection(
                binding_id="bind1:core",
                revision=1,
                snapshot_id=None,
                selection_policy_id="select1:default",
                source_definition_id="src1:core",
            ),
        ),
    )


def test_write_selection_file_handles_short_os_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "selection.json"
    record = _record()
    real_write = source_selection.os.write
    calls: list[int] = []

    def short_write(fd: int, data: memoryview) -> int:
        calls.append(len(data))
        return real_write(fd, data[:1] if len(data) > 1 else data)

    monkeypatch.setattr(source_selection.os, "write", short_write)
    raw = source_selection.write_selection_file(record, output)

    assert len(calls) > 1
    assert output.read_bytes() == raw


@pytest.mark.parametrize("grow", (False, True))
def test_read_selection_file_refuses_changed_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, grow: bool,
) -> None:
    output = tmp_path / "selection.json"
    output.write_bytes(
        (source_selection.json.dumps(_record().to_dict(), separators=(",", ":")) + "\n").encode()
    )
    real_fstat = source_selection.os.fstat
    calls = 0

    def changed_fstat(fd: int) -> object:
        nonlocal calls
        calls += 1
        stat_result = real_fstat(fd)
        if calls == 3:
            return SimpleNamespace(
                st_mode=stat_result.st_mode,
                st_size=stat_result.st_size + int(grow),
                st_mtime_ns=stat_result.st_mtime_ns + int(not grow),
                st_ctime_ns=stat_result.st_ctime_ns,
            )
        return stat_result

    monkeypatch.setattr(source_selection.os, "fstat", changed_fstat)
    with pytest.raises(ContextRequestError) as exc:
        source_selection.read_selection_file(output)
    assert exc.value.refusal_reason == "invalid_selection_record"
    assert output.stat().st_size <= MAX_SELECTION_FILE_BYTES


@pytest.mark.parametrize("raw", [b"", b"not-json\n", b"[]\n", b"{\"schema\": \"wrong\"}\n"])
def test_read_selection_file_rejects_malformed_and_open_schema_payloads(
    tmp_path: Path, raw: bytes,
) -> None:
    path = tmp_path / "malformed.json"
    path.write_bytes(raw)
    with pytest.raises(ContextRequestError) as exc:
        source_selection.read_selection_file(path)
    assert exc.value.refusal_reason == "invalid_selection_record"


def test_read_selection_file_rejects_directory_and_invalid_utf8(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ContextRequestError) as exc_dir:
        source_selection.read_selection_file(directory)
    assert exc_dir.value.refusal_reason == "invalid_selection_record"

    bad_utf8 = tmp_path / "bad-utf8.json"
    bad_utf8.write_bytes(b"\xff\xfe")
    with pytest.raises(ContextRequestError) as exc_utf8:
        source_selection.read_selection_file(bad_utf8)
    assert exc_utf8.value.refusal_reason == "invalid_selection_record"


@pytest.mark.parametrize("replaced", [False, True])
def test_selection_write_failure_cleans_only_the_opened_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replaced: bool,
) -> None:
    output = tmp_path / "selection.json"
    winner = tmp_path / "winner"
    winner.write_bytes(b"keep")
    original = output.with_suffix(".retained")

    def failed_write(_fd: int, _data: memoryview) -> int:
        if replaced:
            output.rename(original)
            winner.replace(output)
        return 0

    monkeypatch.setattr(source_selection.os, "write", failed_write)
    with pytest.raises(ContextRequestError, match="made no progress"):
        source_selection.write_selection_file(_record(), output)
    if replaced:
        assert output.read_bytes() == b"keep"
    else:
        assert not output.exists()


def test_selection_read_error_reports_a_bounded_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "selection.json"
    source_selection.write_selection_file(_record(), path)

    def refuse_read(_fd: int, _size: int) -> bytes:
        raise OSError(errno.EIO, "fixture-diagnostic-canary")

    with monkeypatch.context() as stubs:
        stubs.setattr(source_selection.os, "read", refuse_read)
        with pytest.raises(ContextRequestError, match="cannot read selection file: EIO") as caught:
            source_selection.read_selection_file(path)
    assert caught.value.refusal_reason == "invalid_selection_record"
    assert "fixture-diagnostic-canary" not in str(caught.value)


def test_selection_file_admits_no_bytes_beyond_the_opened_size(tmp_path, monkeypatch):
    path = tmp_path / "selection.json"
    data = source_selection.write_selection_file(_record(), path)
    read = source_selection.os.read
    reads: list[int] = []

    def grow(fd, count):
        if not reads:
            with path.open("ab") as stream:
                stream.write(b" ")
        reads.append(count)
        return read(fd, count)

    with monkeypatch.context() as stubs:
        stubs.setattr(source_selection.os, "read", grow)
        with pytest.raises(ContextRequestError, match="selection file changed while reading"):
            source_selection.read_selection_file(path)
    assert reads == [len(data)]


@pytest.mark.parametrize("kind", ("duplicate", "deep", "oversized_integer"))
def test_decoder_limits_are_reported_as_closed_selection_refusals(tmp_path, kind):
    path = tmp_path / "selection.json"
    if kind == "duplicate":
        data = source_selection.write_selection_file(_record(), path)
        data = data.replace(b'"backend":"sqlite"', b'"backend":"untrusted","backend":"sqlite"', 1)
        assert b'"backend":"untrusted"' in data
    elif kind == "deep":
        data = b"[" * 5000 + b"]" * 5000
    else:
        data = b"9" * 20_000
    path.write_bytes(data)
    with pytest.raises(ContextRequestError) as caught:
        source_selection.read_selection_file(path)
    assert caught.value.refusal_reason == "invalid_selection_record"
