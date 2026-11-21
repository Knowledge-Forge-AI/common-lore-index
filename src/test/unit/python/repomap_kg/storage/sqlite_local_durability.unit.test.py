"""SQLite Local filesystem durability helper (LOCAL7).

Sync failures are injected with fakes (no real filesystem is damaged). A host
or filesystem without directory sync semantics is a bounded, path-free
``local-durability-unavailable`` refusal; any other sync error is
``local-durability-failed``; neither is ever suppressed by the helper. A chain
resolves its leaf first, so a relative or symlinked path syncs the real
directories, nearest first.
"""

from __future__ import annotations

import errno
import os
import sqlite3
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from common_lore_index.storage._preparation_iterators import checked_preparation_rows
from common_lore_index.storage.sqlite_local import durability, publisher
from common_lore_index.storage.sqlite_local.durability import (
    DURABILITY_FAILED,
    DURABILITY_UNAVAILABLE,
    fsync_descriptor,
    fsync_directory,
    fsync_directory_chain,
    fsync_file,
)
from common_lore_index.storage.sqlite_local.schema import (
    GENERATION_ADVANCED,
    PUBLICATION_INTERNAL_ERROR,
    PUBLICATION_REJECTED,
    LocalStoreError,
)
from common_lore_index.storage.staging_family_contracts import STAGING_FAMILY_DESCRIPTORS
from common_lore_index.storage.staging_family_rows import StageFamily


def _refusal(call: Any, *args: Any) -> str:
    with pytest.raises(LocalStoreError) as caught:
        call(*args)
    # A translated OSError is never chained into the (path-carrying) original.
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__
    return str(caught.value)


def _failing_fsync(code: int) -> Any:
    def fsync(_descriptor: int) -> None:
        raise OSError(code, os.strerror(code))

    return fsync


def test_real_file_and_directory_sync_succeed(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_bytes(b"data")
    fsync_file(target)
    fsync_directory(tmp_path)
    fsync_directory_chain(tmp_path, 1)


@pytest.mark.parametrize(
    ("code", "expected"),
    (
        (errno.EIO, f"{DURABILITY_FAILED}: directory sync failed"),
        (errno.ENOSPC, f"{DURABILITY_FAILED}: directory sync failed"),
        (errno.EINVAL, f"{DURABILITY_UNAVAILABLE}: directory sync is not supported"),
        (errno.ENOTSUP, f"{DURABILITY_UNAVAILABLE}: directory sync is not supported"),
    ),
)
def test_directory_sync_errors_are_classified_and_never_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, expected: str
) -> None:
    monkeypatch.setattr(os, "fsync", _failing_fsync(code))
    assert _refusal(fsync_directory, tmp_path) == expected


def test_file_sync_failure_is_not_suppressed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "file"
    target.write_bytes(b"data")
    monkeypatch.setattr(os, "fsync", _failing_fsync(errno.EIO))
    assert _refusal(fsync_file, target) == f"{DURABILITY_FAILED}: file sync failed"
    descriptor = os.open(target, os.O_RDONLY)
    try:
        assert _refusal(fsync_descriptor, descriptor) == f"{DURABILITY_FAILED}: file sync failed"
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    ("code", "expected"),
    ((errno.EOPNOTSUPP, DURABILITY_UNAVAILABLE), (errno.EACCES, DURABILITY_FAILED), (errno.ENOENT, DURABILITY_FAILED)),
)
def test_directory_open_errors_are_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int, expected: str
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> int:
        raise OSError(code, os.strerror(code), str(tmp_path))

    monkeypatch.setattr(os, "open", refuse)
    message = _refusal(fsync_directory, tmp_path)
    assert message.startswith(f"{expected}: ") and str(tmp_path) not in message


def test_host_without_directory_sync_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(os, "O_DIRECTORY")
    assert _refusal(fsync_directory, tmp_path) == f"{DURABILITY_UNAVAILABLE}: directory sync is not supported"
    monkeypatch.undo()
    monkeypatch.setattr(durability.os, "name", "nt")
    assert _refusal(fsync_directory, tmp_path) == f"{DURABILITY_UNAVAILABLE}: directory sync is not supported"


def test_a_symlink_is_not_synced_as_a_directory(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert _refusal(fsync_directory, tmp_path / "link").startswith(f"{DURABILITY_FAILED}: ")


def test_chain_resolves_relative_and_symlinked_leaves_nearest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "home" / "state" / "graphs"
    real.mkdir(parents=True)
    (tmp_path / "alias").symlink_to(tmp_path / "home")
    synced: list[Path] = []
    monkeypatch.setattr(durability, "fsync_directory", synced.append)
    fsync_directory_chain(tmp_path / "alias" / "state" / "graphs", 2)
    base = tmp_path.resolve()
    assert synced == [base / "home" / "state" / "graphs", base / "home" / "state", base / "home"]
    synced.clear()
    monkeypatch.chdir(tmp_path / "home")
    fsync_directory_chain(Path("state") / "graphs", 0)
    assert synced == [base / "home" / "state" / "graphs"]


def test_chain_stops_at_the_first_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    synced: list[Path] = []

    def fail_second(path: Path) -> None:
        synced.append(path)
        if len(synced) == 2:
            raise LocalStoreError(DURABILITY_FAILED, "directory sync failed")

    monkeypatch.setattr(durability, "fsync_directory", fail_second)
    with pytest.raises(LocalStoreError) as caught:
        fsync_directory_chain(tmp_path / "a" / "b", 2)
    assert caught.value.code == DURABILITY_FAILED and len(synced) == 2


class _StageRow(dict[str, object]):
    pass


class _CallbackAbortError(Exception):
    pass


@dataclass
class _TrackedIterator:
    items: Iterator[Mapping[str, object]]
    on_next: Callable[[int], None] | None = None
    close_error: BaseException | None = None
    next_calls: int = 0
    closed: int = 0

    def __iter__(self) -> _TrackedIterator:
        return self

    def __next__(self) -> Mapping[str, object]:
        if self.on_next is not None:
            self.on_next(self.next_calls)
        item = next(self.items)
        self.next_calls += 1
        return item

    def close(self) -> None:
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


@dataclass
class _TrackedStream:
    family: StageFamily
    items: Iterable[Mapping[str, object]]
    opened: list[StageFamily]
    on_next: Callable[[int], None] | None = None
    close_error: BaseException | None = None
    iterator: _TrackedIterator | None = field(init=False, default=None)

    def __iter__(self) -> _TrackedIterator:
        assert self.iterator is None, f"{self.family} consumed more than once"
        self.opened.append(self.family)
        self.iterator = _TrackedIterator(iter(self.items), self.on_next, self.close_error)
        return self.iterator


def _streams(opened: list[StageFamily], **named: Iterable[Mapping[str, object]]) -> Any:
    return {f: named[f] if f in named else _TrackedStream(f, [], opened) for f in publisher.FAMILY_ORDER}


def _row(family: StageFamily, ordinal: int, **overrides: object) -> _StageRow:
    desc = STAGING_FAMILY_DESCRIPTORS[family]
    ints = {desc.technical_ordinal, desc.semantic_ordinal, "graph_key_version"}
    cols = (c for c in desc.copy_columns if c != "stage_id")
    base = {c: None if c in desc.nullable_columns else (1 if c == "graph_key_version" else ordinal)
            if c in ints else f"{family}:{c}:{ordinal}" for c in cols}
    return _StageRow(base | overrides)


def _publication() -> publisher.LocalPublication:
    receipt = type("_Receipt", (), {"portable": object(), "validate": lambda self: self})()
    return publisher.LocalPublication(cast(Any, object()), "repo", cast(Any, receipt), "public")


def _writer_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, generation: int = 0, max_length: int = 0
) -> tuple[Path, list[str], list[sqlite3.Connection]]:
    path = tmp_path / "writer.sqlite3"
    with closing(sqlite3.connect(path, autocommit=True)) as setup:
        setup.execute("BEGIN")
        setup.execute("CREATE TABLE accepted_publication(singleton INT PRIMARY KEY, generation INT, run_id INT)")
        setup.execute("CREATE TABLE runs(id INTEGER PRIMARY KEY)")
        if generation:
            setup.execute("INSERT INTO accepted_publication VALUES (1, ?, ?)", (generation, generation))
        setup.execute("COMMIT")
    sql_log: list[str] = []
    conns: list[sqlite3.Connection] = []

    def open_test_writer(_target: Path, _binding: object) -> sqlite3.Connection:
        conn = sqlite3.connect(path, autocommit=True)
        if max_length:
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, max_length)
        conn.set_trace_callback(sql_log.append)
        conns.append(conn)
        return conn

    monkeypatch.setattr(publisher, "open_writer", open_test_writer)
    return path, sql_log, conns


def test_stage_streams_one_row_at_a_time_and_counts_empty_families() -> None:
    opened: list[StageFamily] = []
    refs: list[weakref.ReferenceType[_StageRow]] = []
    admitted: list[int] = []
    live_prior: list[int] = []
    expected = {
        "files": 1005, "raw_observations": 2, "canonical_nodes": 3, "canonical_evidence": 1,
        "canonical_edges": 0, "canonical_node_evidence": 0, "canonical_edge_evidence": 0,
    }
    with closing(sqlite3.connect(":memory:", autocommit=True)) as conn:
        def make_file_row(idx: int) -> _StageRow:
            extra = {"path": True, "content_hash": {"z": 1, "a": "café", "ok": False}} if idx == 1 else {}
            row = _row("files", idx, **extra)
            refs.append(weakref.ref(row))
            return row

        def on_file_next(_index: int) -> None:
            admitted.append(conn.execute("SELECT count(*) FROM temp.stage_files").fetchone()[0])
            live_prior.append(sum(ref() is not None for ref in refs))

        file_gen = (make_file_row(i) for i in range(1, 1006))
        streams = {
            f: _TrackedStream(f, file_gen, opened, on_file_next) if f == "files"
            else _TrackedStream(f, [_row(f, i + 1) for i in range(expected[f])], opened)
            for f in publisher.FAMILY_ORDER
        }
        assert publisher._stage(conn, streams) == expected and not conn.in_transaction
        assert opened == list(publisher.FAMILY_ORDER)
        assert len(admitted) == 1006 and admitted == list(range(1006)) and max(live_prior) == 0
        assert all(s.iterator is not None and s.iterator.closed == 1 for s in streams.values())
        head = conn.execute("SELECT path, content_hash FROM temp.stage_files ORDER BY rowid LIMIT 2").fetchall()
        assert head == [(1, '{"a":"café","ok":false,"z":1}'), ("files:path:2", None)]
        cols = [info[1] for info in conn.execute("PRAGMA temp.table_info(stage_files)").fetchall()]
        assert tuple(cols) == publisher._columns("files") and "stage_id" not in cols


def test_stage_malformed_row_after_admitted_prefix_closes_iterator_and_rolls_back() -> None:
    opened: list[StageFamily] = []
    prefix_counts: list[tuple[int, int]] = []
    with closing(sqlite3.connect(":memory:", autocommit=True)) as conn:
        def inspect_prefix(index: int) -> None:
            if index == 1:
                files_n = conn.execute("SELECT count(*) FROM temp.stage_files").fetchone()[0]
                raw_n = conn.execute("SELECT count(*) FROM temp.stage_raw_observations").fetchone()[0]
                prefix_counts.append((files_n, raw_n))

        raw_rows: list[Mapping[str, object]] = [_row("raw_observations", 1), {}, _row("raw_observations", 3)]
        raw = _TrackedStream("raw_observations", raw_rows, opened, inspect_prefix, RuntimeError("close boom"))
        files = _TrackedStream("files", [_row("files", 1), _row("files", 2)], opened)
        streams = _streams(opened, files=files, raw_observations=raw)
        with pytest.raises(LocalStoreError) as caught:
            publisher._stage(conn, streams)
        assert str(caught.value) == f"{PUBLICATION_REJECTED}: raw_observations row is malformed"
        assert isinstance(caught.value.__cause__, KeyError)
        assert prefix_counts == [(2, 1)] and opened == ["files", "raw_observations"]
        assert raw.iterator is not None and (raw.iterator.next_calls, raw.iterator.closed) == (2, 1)
        assert not conn.in_transaction
        assert conn.execute("SELECT name FROM sqlite_temp_master WHERE type = 'table'").fetchall() == []


@pytest.mark.parametrize(
    ("bad_value", "max_length", "expected_code"),
    ((["unbindable"], 0, PUBLICATION_INTERNAL_ERROR), ("x" * 2000, 1024, PUBLICATION_REJECTED),
     ("step-boom", 0, PUBLICATION_REJECTED)),
    ids=("bind-type-error", "bind-limit-error", "step-insert-error"),
)
def test_stage_binding_or_insert_failure_stops_before_later_malformed_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_value: object, max_length: int, expected_code: str
) -> None:
    path, sql_log, conns = _writer_db(tmp_path, monkeypatch, max_length=max_length)
    opened: list[StageFamily] = []

    def arm_step_trigger(index: int) -> None:
        if index == 0 and bad_value == "step-boom":
            conns[-1].execute(
                "CREATE TEMP TRIGGER fail_step BEFORE INSERT ON stage_files "
                "WHEN NEW.path = 'step-boom' BEGIN SELECT RAISE(ABORT, 'step refused'); END"
            )

    file_rows: list[Mapping[str, object]] = [_row("files", 1), _row("files", 2, path=bad_value), {}]
    files = _TrackedStream("files", file_rows, opened, arm_step_trigger, RuntimeError("close ignored"))
    streams = _streams(opened, files=files)
    monkeypatch.setattr(publisher, "_merge", lambda *_a: pytest.fail("merge must not run"))
    with pytest.raises(LocalStoreError) as caught:
        publisher.publish_generation(path, _publication(), streams, expected_generation=0, clock=lambda: pytest.fail())
    assert caught.value.code == expected_code and "malformed" not in str(caught.value)
    assert publisher.publication_not_committed(caught.value) and opened == ["files"]
    assert files.iterator is not None and (files.iterator.next_calls, files.iterator.closed) == (2, 1)
    assert "ROLLBACK" in sql_log and not any("accepted_publication" in s or "BEGIN IMMEDIATE" in s for s in sql_log)


@pytest.mark.parametrize(
    ("on_close", "error_cls"),
    ((False, _CallbackAbortError), (False, KeyError), (True, _CallbackAbortError), (True, KeyError)),
    ids=("callback-custom", "callback-key", "clean-close-custom", "clean-close-key"),
)
def test_typed_preparation_callback_error_preserves_type_and_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_close: bool, error_cls: type[Exception]
) -> None:
    path, sql_log, _conns = _writer_db(tmp_path, monkeypatch)
    opened: list[StageFamily] = []
    failure = error_cls("abort")
    checks = 0

    def check() -> None:
        nonlocal checks
        checks += 1
        if not on_close and checks == 4:
            raise failure

    close_err = failure if on_close else RuntimeError("secondary-close-ignored")
    underlying = _TrackedStream("files", [_row("files", 1), _row("files", 2)], opened, close_error=close_err)
    files_input = underlying if on_close else checked_preparation_rows(underlying, check)
    with pytest.raises(error_cls) as caught:
        publisher.publish_generation(path, _publication(), _streams(opened, files=files_input), expected_generation=0)
    assert caught.value is failure and publisher.publication_not_committed(caught.value)
    assert opened == ["files"] and underlying.iterator is not None
    assert (underlying.iterator.next_calls, underlying.iterator.closed) == (2 if on_close else 1, 1)
    assert "ROLLBACK" in sql_log and not any("BEGIN IMMEDIATE" in s for s in sql_log)


def test_publish_generation_preserves_stage_guard_and_generation_fence_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, sql_log, _conns = _writer_db(tmp_path, monkeypatch, generation=1)
    opened: list[StageFamily] = []
    dup_rows = [_row("files", 1, path="d.py", content_hash="h1"), _row("files", 2, path="d.py", content_hash="h2")]
    conflict = _streams(opened, files=_TrackedStream("files", dup_rows, opened))
    monkeypatch.setattr(publisher, "_merge", lambda *_a: pytest.fail("merge must not run"))
    with pytest.raises(LocalStoreError) as guard_err:
        publisher.publish_generation(path, _publication(), conflict, expected_generation=1, clock=lambda: pytest.fail())
    assert guard_err.value.code == PUBLICATION_REJECTED and publisher.publication_not_committed(guard_err.value)
    assert opened == list(publisher.FAMILY_ORDER) and not any("BEGIN IMMEDIATE" in s for s in sql_log)

    sql_log.clear()
    valid = {f: [_row(f, 1)] if f in ("files", "raw_observations") else [] for f in publisher.FAMILY_ORDER}
    with pytest.raises(LocalStoreError) as fence_err:
        publisher.publish_generation(path, _publication(), valid, expected_generation=0)
    assert fence_err.value.code == GENERATION_ADVANCED and publisher.publication_not_committed(fence_err.value)
    assert "BEGIN IMMEDIATE" in sql_log and "ROLLBACK" in sql_log

    monkeypatch.setattr(publisher, "_merge", lambda *_a: publisher.LocalPublicationResult(2, 2, 1, {}))
    result = publisher.publish_generation(path, _publication(), valid, expected_generation=1)
    assert (result.generation, result.run_id, result.previous_run_id) == (2, 2, 1)
    expected_counts = {f: (1 if f in ("files", "raw_observations") else 0) for f in publisher.FAMILY_ORDER}
    assert result.family_counts == expected_counts
