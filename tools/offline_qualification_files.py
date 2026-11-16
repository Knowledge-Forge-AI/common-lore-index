"""Private custody and bounded regular-file reads for offline qualification."""
from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Callable, Protocol

from offline_qualification_values import refuse


class ReadMeter(Protocol):
    def remaining(self, key: str) -> int: ...

    def read(self, operation: Callable[[int], bytes], count: int, *, transfer: bool = False) -> bytes: ...


def private_file(path: Path) -> None:
    refuse(path.is_absolute(), "relative private input")
    refuse(not any(p.is_symlink() for p in (path, *path.parents)), "private input link")
    info = path.stat()
    refuse(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
           and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
           "private input custody")


def _read_regular(path: Path, maximum: int, meter: ReadMeter | None = None) -> bytes:
    refuse(not any(p.is_symlink() for p in (path, *path.parents)), "input link")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        refuse(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= maximum,
               "input size/type")
        result = bytearray()
        while len(result) < before.st_size:
            count = min(65536, before.st_size - len(result))
            if meter is not None:
                count = min(count, meter.remaining("read_bytes"))
                refuse(count > 0, "read allocation exhausted")
                block = meter.read(source.read, count)
            else:
                block = source.read(count)
            refuse(bool(block), "short regular input")
            result.extend(block)
        after = os.fstat(source.fileno())
    refuse(len(result) == before.st_size and len(result) <= maximum
           and (before.st_size, before.st_mtime_ns, before.st_ino) ==
               (after.st_size, after.st_mtime_ns, after.st_ino), "input changed")
    return bytes(result)


def read_regular(path: Path, maximum: int, meter: ReadMeter | None = None) -> bytes:
    try:
        return _read_regular(path, maximum, meter)
    except OSError:
        raise RuntimeError("pgvector prebuilt refused: regular input unavailable") from None
