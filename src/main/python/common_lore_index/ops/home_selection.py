"""Read-only default-home recognition; explicit selectors retain their authority."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import NoReturn

from common_lore_index.ops.config_layout import classify_config_filename
from common_lore_index.ops.config_records import OpsConfigDiagnostic, OpsConfigError

MAX_RECOGNITION_FILE_BYTES = 1 << 20
PayloadRecognizer = Callable[[Sequence[tuple[str, bytes]]], bool]


@dataclass(frozen=True)
class HomeSelection:
    path: Path
    source: str


def _refuse(code: str) -> NoReturn:
    raise OpsConfigError((OpsConfigDiagnostic(
        "error", code, "config-home",
        "default home is unsafe or unrecognized; select a product home explicitly with CLOE_HOME",
    ),))


def _owned(info: os.stat_result) -> bool:
    return not hasattr(os, "getuid") or info.st_uid == os.getuid()


def _directory_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid


def _witness_signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_config(directory: int, name: str) -> bytes:
    named_before = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if not stat.S_ISREG(named_before.st_mode):
        raise ValueError("non-regular configuration witness")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or not _owned(info) or info.st_nlink != 1
                or info.st_mode & 0o022 or info.st_size > MAX_RECOGNITION_FILE_BYTES):
            raise ValueError("unsafe configuration witness")
        signature = _witness_signature(info)
        if _witness_signature(named_before) != signature:
            raise ValueError("configuration witness changed")
        chunks: list[bytes] = []
        remaining = info.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (len(content) != info.st_size or _witness_signature(after) != signature
                or _witness_signature(named) != signature):
            raise ValueError("configuration witness changed")
        return content
    finally:
        os.close(descriptor)


def _classify(path: Path, recognize: PayloadRecognizer) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unsafe"
    if not stat.S_ISDIR(info.st_mode):
        return "unsafe"
    if not _owned(info) or info.st_mode & 0o077:
        return "foreign"
    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
        return "unsafe"
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        identity = _directory_identity(opened)
        if _directory_identity(info) != identity:
            return "unsafe"
        names = os.listdir(descriptor)
        state = "empty"
        if names:
            selected = [(classification, name) for name in names
                        if (classification := classify_config_filename(name)) is not None]
            if not selected or len({value[0][0] for value in selected}) != 1:
                return "foreign"
            payloads = [(name, _read_config(descriptor, name))
                        for _classification, name in sorted(selected, key=lambda value: (value[0][1], value[1]))]
            state = "recognized" if recognize(payloads) else "foreign"
        return state if _directory_identity(path.lstat()) == identity else "unsafe"
    except (OSError, ValueError, UnicodeError, RecursionError):
        return "unsafe"
    finally:
        if descriptor is not None:
            os.close(descriptor)


def select_home(value: str | Path | None, *, recognize: PayloadRecognizer) -> HomeSelection:
    """Inspect the canonical default without creating files or opening a database."""
    if value is not None:
        return HomeSelection(Path(value).expanduser(), "explicit")
    for variable in ("CLOE_HOME", "REPOMAP_HOME"):
        selected = os.environ.get(variable)
        if selected:
            return HomeSelection(Path(selected).expanduser(), variable)
    current = Path("~/.cloe").expanduser()
    current_state = _classify(current, recognize)
    if current_state == "recognized":
        return HomeSelection(current, "cloe-default")
    if current_state == "unsafe":
        _refuse("default-home-unsafe")
    if current_state in ("absent", "empty"):
        return HomeSelection(current, "fresh-default")
    _refuse("default-home-unrecognized")
