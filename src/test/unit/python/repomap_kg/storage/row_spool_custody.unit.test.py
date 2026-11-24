"""A producer failure must retain no unowned private row file or handle."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from common_lore_index.storage.row_spool import RowSpool, RowSpoolWriter


def test_timing_observer_failure_reclaims_the_created_spool(tmp_path: Path) -> None:
    marker = RuntimeError("synthetic timing observer refusal")

    def refuse(_elapsed: int) -> None:
        raise marker

    leftovers: tuple[Path, ...] = ()
    try:
        with pytest.raises(RuntimeError) as caught:
            RowSpool.from_rows(({"value": "public fixture"},), dir=tmp_path, timing_observer=refuse)
        assert caught.value is marker
        leftovers = tuple(tmp_path.iterdir())
    finally:
        for path in leftovers:
            path.unlink()
    assert leftovers == ()


def test_unfinished_writer_context_reclaims_its_private_file(tmp_path: Path) -> None:
    writer = RowSpoolWriter(dir=tmp_path)
    try:
        with writer:
            writer.write_row({"value": "public fixture"})
        left_on_exit = writer.path.exists()
    finally:
        writer.abort()
    assert left_on_exit is False



def test_replay_and_cleanup_preserve_a_same_bytes_replacement(tmp_path: Path) -> None:
    spool = RowSpool.from_rows(({"value": "public fixture"},), dir=tmp_path)
    original = tmp_path / "original"
    spool.path.rename(original)
    spool.path.write_bytes(original.read_bytes())

    with pytest.raises(ValueError, match="identity"):
        tuple(spool)
    with pytest.raises(ValueError, match="identity"):
        spool.close()
    assert spool.path.read_bytes() == original.read_bytes()


def test_writer_does_not_transfer_or_remove_a_replaced_file(tmp_path: Path) -> None:
    writer = RowSpoolWriter(dir=tmp_path)
    writer.write_row({"value": "public fixture"})
    original = tmp_path / "original"
    writer.path.rename(original)
    writer.path.write_bytes(b"unrelated")

    with pytest.raises(ValueError, match="identity"):
        writer.finish()
    with pytest.raises(ValueError, match="identity"):
        writer.abort()
    assert writer.path.read_bytes() == b"unrelated"


@pytest.mark.parametrize("count, expected", [(1, "extent"), (3, "invalid")])
def test_replay_rejects_incorrect_record_count(tmp_path: Path, count: int, expected: str) -> None:
    with RowSpool.from_rows(({"value": "first"}, {"value": "second"}), dir=tmp_path) as spool:
        spool.count = count
        with pytest.raises(ValueError, match=expected):
            tuple(spool)



def test_spool_exit_retains_primary_error_and_preserves_replacement(tmp_path: Path) -> None:
    spool = RowSpool.from_rows(({"value": "public fixture"},), dir=tmp_path)
    marker = RuntimeError("body refused")
    with pytest.raises(RuntimeError) as caught:
        with spool:
            spool.path.rename(tmp_path / "original")
            spool.path.write_bytes(b"unrelated")
            raise marker
    assert caught.value is marker
    assert spool.path.read_bytes() == b"unrelated"


@pytest.mark.parametrize("incremental", [False, True])
def test_interrupted_spool_identity_check_reclaims_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, incremental: bool,
) -> None:
    fstat = os.fstat
    marker = KeyboardInterrupt("identity check interrupted")
    calls = 0

    def interrupt_first(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise marker
        return fstat(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fstat", interrupt_first)
        with pytest.raises(KeyboardInterrupt) as caught:
            if incremental:
                RowSpoolWriter(dir=tmp_path)
            else:
                RowSpool.from_rows((), dir=tmp_path)
    assert caught.value is marker and calls == 2
    assert tuple(tmp_path.iterdir()) == ()
