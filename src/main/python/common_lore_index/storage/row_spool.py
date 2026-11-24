"""Private disk-backed replay for bounded staged-ingestion rows."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from collections.abc import Callable, Generator, Iterable, Mapping
from pathlib import Path
from typing import BinaryIO

from common_lore_index._utf8 import byte_length


def _spool_row_text(row: Mapping[str, object]) -> str:
    """Serialize one staged row to compact, deterministic JSON text."""
    if not isinstance(row, Mapping):
        raise ValueError("staged row is invalid")
    return json.dumps(
        row if type(row) is dict else dict(row),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _spool_row_byte_count(row: Mapping[str, object]) -> int:
    """Return the compact JSONL extent without allocating a wide byte string."""
    return byte_length(_spool_row_text(row)) + 1


def _write_spool_row(handle: BinaryIO, encoded: bytes) -> None:
    if handle.write(encoded) != len(encoded) or handle.write(b"\n") != 1:
        raise OSError("staged row spool write was incomplete")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _version(info: os.stat_result) -> tuple[int, ...]:
    return (*_identity(info), info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _release_unwrapped_descriptor(
    descriptor: int, owner: tuple[int, int] | None,
) -> tuple[int, int] | None:
    if owner is None:
        try:
            owner = _identity(os.fstat(descriptor))
        except BaseException:
            pass
    try:
        os.close(descriptor)
    except BaseException:
        pass
    return owner


def _sealed_spool(
    path: Path, owner: tuple[int, int] | None, byte_count: int,
) -> os.stat_result:
    info = path.lstat()
    if (
        (owner is not None and _identity(info) != owner) or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1 or info.st_size != byte_count
    ):
        raise ValueError("staged row spool identity or extent changed")
    return info


def _discard_owned(path: Path, owner: tuple[int, int] | None) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if owner is None or _identity(info) != owner or not stat.S_ISREG(info.st_mode):
        raise ValueError("staged row spool identity changed")
    path.unlink(missing_ok=True)


class RowSpool:
    """Replay one deterministic row sequence from a private JSONL file."""

    def __init__(
        self,
        path: Path,
        count: int,
        byte_count: int,
        allocated_byte_count: int = 0,
        *,
        sealed: os.stat_result | None = None,
    ) -> None:
        info = _sealed_spool(path, None, byte_count) if sealed is None else sealed
        self._owner = _identity(info)
        self._version = _version(info)
        self.path = path
        self.count = count
        self.byte_count = byte_count
        self.allocated_byte_count = allocated_byte_count
        self._closed = False

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[Mapping[str, object]],
        *,
        dir: Path | str | None = None,
        artifact_observer: Callable[[int], None] | None = None,
        timing_observer: Callable[[int], None] | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> RowSpool:
        if dir is not None:
            descriptor, raw_path = tempfile.mkstemp(prefix="repomap-stage-rows-", suffix=".jsonl", dir=str(dir))
        else:
            descriptor, raw_path = tempfile.mkstemp(prefix="repomap-stage-rows-", suffix=".jsonl")
        path = Path(raw_path)
        count = 0
        byte_count = 0
        elapsed_ns = 0
        descriptor_owned = True
        owner: tuple[int, int] | None = None
        try:
            owner = _identity(os.fstat(descriptor))
            handle = os.fdopen(descriptor, "wb")
            descriptor_owned = False
            try:
                for row in rows:
                    started = monotonic_ns() if timing_observer is not None else 0
                    encoded = _spool_row_text(row).encode("utf-8")
                    projected_bytes = byte_count + len(encoded) + 1
                    if artifact_observer is not None:
                        artifact_observer(projected_bytes)
                    _write_spool_row(handle, encoded)
                    count += 1
                    byte_count = projected_bytes
                    if timing_observer is not None:
                        elapsed_ns += max(0, monotonic_ns() - started)
            except BaseException:
                try:
                    handle.close()
                except BaseException:
                    pass
                raise
            else:
                handle.close()
            if timing_observer is not None:
                timing_observer(elapsed_ns)
            sealed = _sealed_spool(path, owner, byte_count)
            return cls(path, count, byte_count, sealed.st_blocks * 512, sealed=sealed)
        except BaseException:
            if descriptor_owned:
                owner = _release_unwrapped_descriptor(descriptor, owner)
            try:
                _discard_owned(path, owner)
            except BaseException:
                pass
            raise

    def __len__(self) -> int:
        return self.count

    def __iter__(self) -> Generator[dict[str, object], None, None]:
        if self._closed:
            raise RuntimeError("row spool is closed")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(self.path, flags)
        try:
            if _version(os.fstat(descriptor)) != self._version:
                raise ValueError("staged row spool identity or extent changed")
            handle = os.fdopen(descriptor, "rb")
            descriptor = -1
            with handle:
                remaining = self.byte_count
                for _ in range(self.count):
                    line = handle.readline(remaining + 1)
                    if len(line) > remaining or not line.endswith(b"\n"):
                        raise ValueError("staged row spool is invalid")
                    remaining -= len(line)
                    try:
                        row = json.loads(line.decode("utf-8"))
                    except (ValueError, RecursionError) as error:
                        raise ValueError("staged row spool is invalid") from error
                    if not isinstance(row, dict):
                        raise ValueError("staged row spool is invalid")
                    yield row
                if remaining or handle.read(1) or _version(os.fstat(handle.fileno())) != self._version:
                    raise ValueError("staged row spool identity or extent changed")
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def close(self) -> None:
        if self._closed:
            return
        _discard_owned(self.path, self._owner)
        self._closed = True

    def __enter__(self) -> RowSpool:
        return self

    def __exit__(self, exc_type: object, _exc: object, _traceback: object) -> None:
        if exc_type is None:
            self.close()
        else:
            try:
                self.close()
            except BaseException:
                pass


class RowSpoolWriter:
    """Incrementally write rows into a private JSONL spool file."""

    def __init__(
        self,
        *,
        dir: Path | str | None = None,
        artifact_observer: Callable[[int], None] | None = None,
    ) -> None:
        if dir is not None:
            Path(dir).mkdir(parents=True, exist_ok=True)
            descriptor, raw_path = tempfile.mkstemp(prefix="repomap-stage-rows-", suffix=".jsonl", dir=str(dir))
        else:
            descriptor, raw_path = tempfile.mkstemp(prefix="repomap-stage-rows-", suffix=".jsonl")
        self.path = Path(raw_path)
        owner: tuple[int, int] | None = None
        try:
            owner = _identity(os.fstat(descriptor))
            self._handle = os.fdopen(descriptor, "wb")
        except BaseException:
            owner = _release_unwrapped_descriptor(descriptor, owner)
            try:
                _discard_owned(self.path, owner)
            except BaseException:
                pass
            raise
        self._owner = owner
        self._count = 0
        self._byte_count = 0
        self._artifact_observer = artifact_observer
        self._finished = False
        self._transferred = False
        self._aborted = False

    def write_row(self, row: Mapping[str, object]) -> None:
        if self._finished:
            raise RuntimeError("writer already finished")
        encoded = _spool_row_text(row).encode("utf-8")
        projected_bytes = self._byte_count + len(encoded) + 1
        if self._artifact_observer is not None:
            self._artifact_observer(projected_bytes)
        _write_spool_row(self._handle, encoded)
        self._count += 1
        self._byte_count = projected_bytes

    def finish(self) -> RowSpool:
        if self._finished:
            raise RuntimeError("writer already finished")
        self._handle.flush()
        self._handle.close()
        self._finished = True
        sealed = _sealed_spool(self.path, self._owner, self._byte_count)
        spool = RowSpool(self.path, self._count, self._byte_count, sealed.st_blocks * 512, sealed=sealed)
        self._transferred = True
        return spool

    def abort(self) -> None:
        if self._transferred or self._aborted:
            return
        error: BaseException | None = None
        try:
            self._handle.close()
        except BaseException as caught:
            error = caught
        self._finished = True
        try:
            _discard_owned(self.path, self._owner)
            self._aborted = True
        except BaseException:
            if error is None:
                raise
        if error is not None:
            raise error

    def __enter__(self) -> RowSpoolWriter:
        return self

    def __exit__(self, exc_type: object, _exc: object, _traceback: object) -> None:
        if exc_type is None:
            self.abort()
        else:
            try:
                self.abort()
            except BaseException:
                pass


__all__ = ["RowSpool", "RowSpoolWriter"]
