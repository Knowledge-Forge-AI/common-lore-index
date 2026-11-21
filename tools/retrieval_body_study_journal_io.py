"""Private append-only body attempt events with exclusive custody and hash joins."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterator

MAX_EVENTS = 900
MAX_EVENT_BYTES = 16 * 1024
_MEMBER = re.compile(r"[0-9]{5}\.json\Z")


class JournalRefusal(RuntimeError):
    """An unknown or nonlocal journal never admits more work."""


def require(condition: object, code: str) -> None:
    if not condition:
        raise JournalRefusal(code)


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise JournalRefusal("journal_noncanonical_value") from None


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def metadata(path: Path, *, directory: bool = False) -> tuple[int, ...]:
    try:
        info = path.lstat()
    except OSError:
        raise JournalRefusal("journal_custody_unavailable") from None
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and not path.is_symlink() and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
            and (directory or info.st_nlink == 1), "journal_custody")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def root_identity(root: Path) -> tuple[int, int]:
    require(isinstance(root, Path) and root.is_absolute() and root.resolve() == root,
            "journal_root_path")
    require(not any(parent.is_symlink() for parent in root.parents), "journal_parent_link")
    meta = metadata(root, directory=True)
    return meta[0], meta[1]


def members(root: Path) -> tuple[str, ...]:
    names = sorted(path.name for path in root.iterdir())
    require(all(name == ".lock" or _MEMBER.fullmatch(name) for name in names),
            "journal_unknown_member")
    rows = tuple(name for name in names if name != ".lock")
    require(len(rows) <= MAX_EVENTS and rows == tuple(f"{i:05d}.json" for i in range(len(rows))),
            "journal_noncontiguous_history")
    return rows


@contextmanager
def locked(root: Path, expected_root: tuple[int, int]) -> Iterator[None]:
    require(root_identity(root) == expected_root, "journal_root_replaced")
    path = root / ".lock"
    metadata(path)
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        require((opened.st_dev, opened.st_ino) == metadata(path)[:2], "journal_lock_replaced")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise JournalRefusal("journal_busy") from None
        require(root_identity(root) == expected_root, "journal_root_replaced")
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def read_event(path: Path) -> dict[str, Any]:
    before = metadata(path)
    require(0 < before[4] <= MAX_EVENT_BYTES, "journal_event_bound")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        require((opened.st_dev, opened.st_ino) == before[:2], "journal_event_replaced")
        chunks = []
        count = 0
        while chunk := os.read(descriptor, min(8192, MAX_EVENT_BYTES + 1 - count)):
            count += len(chunk)
            require(count <= MAX_EVENT_BYTES, "journal_event_bound")
            chunks.append(chunk)
        require(metadata(path) == before, "journal_event_changed")
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            require(key not in result, "journal_duplicate_member")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise JournalRefusal("journal_event_malformed") from None
    require(isinstance(value, dict) and canonical(value) + b"\n" == raw,
            "journal_event_noncanonical")
    return value


def publish(root: Path, index: int, value: Mapping[str, Any]) -> tuple[int, ...]:
    require(type(index) is int and 0 <= index < MAX_EVENTS, "journal_event_limit")
    raw = canonical(dict(value)) + b"\n"
    require(len(raw) <= MAX_EVENT_BYTES, "journal_event_bound")
    path = root / f"{index:05d}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as target:
            descriptor = -1
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return metadata(path)
