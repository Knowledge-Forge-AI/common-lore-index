"""Fail-closed BODY file custody using directory file descriptors.

Every path component is opened with ``O_NOFOLLOW`` and ``O_DIRECTORY`` relative
to its parent descriptor. Reads are bounded and identity-checked before/after.
Publication is exclusive: an owned same-directory temporary file is written,
fsynced and hard-linked into place (``link`` never overwrites). This leaf has no
supplement, semantic, public-text, network, or execution dependency.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
from typing import NoReturn, cast

from retrieval_body_study_contracts import (
    BODY_FAILURE_BYTES, BODY_FAILURE_EVIDENCE_MEMBER, BODY_FAILURE_MEMBER, BODY_REPORT_BYTES,
    BODY_REPORT_MEMBER, BODY_RESULT_MEMBERS_BYTES, BodyStudyRefusal, refuse,
)

MAX_MEMBER_BYTES = 16 * 1024 * 1024

MAX_PATH_BYTES = 512
_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


def _fail(code: str) -> NoReturn:
    raise BodyStudyRefusal(code)


def _digest_bytes(raw: bytes) -> str:
    refuse(type(raw) is bytes, "digest_bytes_required")
    return "sha256:" + hashlib.sha256(raw).hexdigest()




def _digest_value(value: object, label: str) -> str:
    refuse(type(value) is str and _DIGEST.fullmatch(value) is not None,
           f"{label}:invalid_sha256")
    return str(value)


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    refuse(type(value) is int, f"{label}:integer_required")
    number = cast(int, value)
    refuse(minimum <= number <= maximum, f"{label}:out_of_range")
    return number


def _safe_relative_path(value: object) -> str:
    refuse(type(value) is str and bool(value), "path:invalid")
    path = str(value)
    try:
        size = len(path.encode("utf-8", "surrogatepass"))
    except UnicodeEncodeError:
        _fail("path:invalid_unicode")
    refuse(size <= MAX_PATH_BYTES, "path:too_long")
    refuse(not any(ord(char) < 0x20 or char == "\x7f" for char in path),
           "path:control_char")
    refuse("\\" not in path and ":" not in path, "path:forbidden_char")
    refuse(not path.startswith("/"), "path:absolute")
    parts = path.split("/")
    refuse(all(part not in {"", ".", ".."} for part in parts), "path:traversal")
    refuse(all(len(part.encode("utf-8", "surrogatepass")) <= 255 for part in parts),
           "path:part_too_long")
    canonical = PurePosixPath(path).as_posix()
    refuse(canonical == path, "path:noncanonical")
    return canonical


_IDENTITY_FIELDS = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")


def _flags() -> tuple[int, int]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    refuse(nofollow is not None and directory is not None, "io:nofollow_unsupported")
    for function in (os.open, os.stat, os.mkdir, os.unlink, os.link):
        refuse(function in os.supports_dir_fd, "io:dir_fd_unsupported")
    refuse(os.stat in os.supports_follow_symlinks, "io:nofollow_stat_unsupported")
    refuse(os.listdir in os.supports_fd, "io:fd_listing_unsupported")
    cloexec = getattr(os, "O_CLOEXEC", 0)
    assert nofollow is not None and directory is not None
    return nofollow | cloexec, directory | nofollow | cloexec | os.O_RDONLY


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return tuple(int(getattr(info, name)) for name in _IDENTITY_FIELDS)


def _open_child_dir(parent: int, name: str) -> int:
    _nofollow, dir_flags = _flags()
    try:
        return os.open(name, dir_flags, dir_fd=parent)
    except OSError:
        _fail("io:unsafe_directory_component")


def _open_root(root: Path) -> int:
    """Open an absolute root walking every component with no-follow semantics."""
    _nofollow, dir_flags = _flags()
    refuse(isinstance(root, Path), "io:root_path_required")
    raw = os.fspath(root)
    refuse(os.path.isabs(raw), "io:root_must_be_absolute")
    parts = raw.split("/")[1:]
    refuse(all(part not in {".", ".."} for part in parts), "io:root_traversal")
    fd = os.open("/", dir_flags)
    try:
        for part in (p for p in parts if p):
            child = _open_child_dir(fd, part)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextmanager
def _parent_dir(root: Path, parts: list[str], *, create: bool) -> Iterator[int]:
    fd = _open_root(root)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                except OSError:
                    _fail("io:directory_create_failed")
            child = _open_child_dir(fd, part)
            os.close(fd)
            fd = child
            if create:
                refuse(os.fstat(fd).st_uid == os.geteuid(), "io:directory_not_owned")
        yield fd
    finally:
        os.close(fd)


_RESULT_MEMBERS = {
    BODY_REPORT_MEMBER: BODY_REPORT_BYTES,
    BODY_FAILURE_MEMBER: BODY_FAILURE_BYTES,
    BODY_FAILURE_EVIDENCE_MEMBER: BODY_FAILURE_BYTES,
}


def _member_ceiling(path: str) -> int:
    return _RESULT_MEMBERS.get(path, MAX_MEMBER_BYTES)


def _reserve_result_member(parent: int, path: str, size: int) -> None:
    if path not in _RESULT_MEMBERS:
        return
    # Hold the directory lock until the exclusive publication is complete.
    try:
        fcntl.flock(parent, fcntl.LOCK_EX)
        for sibling, maximum in _RESULT_MEMBERS.items():
            if sibling == path:
                continue
            try:
                info = os.stat(sibling.rsplit("/", 1)[1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            refuse(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                   and info.st_nlink == 1 and 0 < info.st_size <= maximum, "io:result_member_custody")
            size += info.st_size
    except OSError:
        _fail("io:result_members_unavailable")
    refuse(size <= BODY_RESULT_MEMBERS_BYTES, "io:result_members_too_large")


def read_member(
    root: Path, relative: str, *, max_bytes: int, expected_sha256: str | None = None
) -> bytes:
    """Read one exact regular file under ``root`` with bounded, identity-checked IO."""
    nofollow, _dir_flags = _flags()
    path = _safe_relative_path(relative)
    limit = _integer(max_bytes, "max_bytes", 0, _member_ceiling(path))
    expected = None if expected_sha256 is None else _digest_value(expected_sha256, "member")
    *parents, leaf = path.split("/")
    with _parent_dir(root, parents, create=False) as parent:
        try:
            before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except OSError:
            _fail("io:member_missing")
        refuse(stat.S_ISREG(before.st_mode), "io:member_not_regular")
        refuse(before.st_size <= limit, "io:member_too_large")
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NONBLOCK | nofollow, dir_fd=parent)
        except OSError:
            _fail("io:member_open_failed")
        try:
            opened = os.fstat(fd)
            refuse(stat.S_ISREG(opened.st_mode), "io:member_not_regular")
            refuse(_identity(opened) == _identity(before), "io:member_changed")
            chunks: list[bytes] = []
            total = 0
            while True:
                try:
                    chunk = os.read(fd, min(65536, limit + 1 - total))
                except OSError:
                    _fail("io:member_read_failed")
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                refuse(total <= limit, "io:member_too_large")
            after = os.fstat(fd)
        finally:
            os.close(fd)
        refuse(_identity(after) == _identity(before), "io:member_changed")
        refuse(total == before.st_size, "io:member_partial_read")
        try:
            relinked = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except OSError:
            _fail("io:member_changed")
        refuse(_identity(relinked) == _identity(before), "io:member_changed")
    data = b"".join(chunks)
    if expected is not None:
        refuse(_digest_bytes(data) == expected, "io:member_digest_mismatch")
    return data


def _write_all(fd: int, raw: bytes) -> None:
    view = memoryview(raw)
    while view:
        written = os.write(fd, view)
        refuse(written > 0, "io:short_write")
        view = view[written:]


def _remove_owned_temp(parent: int, name: str, owned: tuple[int, int] | None) -> None:
    if owned is None:
        return
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (info.st_dev, info.st_ino) == owned and stat.S_ISREG(info.st_mode):
        os.unlink(name, dir_fd=parent)


def publish_member(root: Path, relative: str, raw: bytes) -> None:
    """Exclusively publish complete ``raw`` bytes; never overwrite, truncate or reuse."""
    nofollow, _dir_flags = _flags()
    refuse(type(raw) is bytes, "io:bytes_required")
    path = _safe_relative_path(relative)
    refuse(len(raw) <= _member_ceiling(path), "io:member_too_large")
    *parents, leaf = path.split("/")
    with _parent_dir(root, parents, create=True) as parent:
        _reserve_result_member(parent, path, len(raw))
        try:
            os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError:
            _fail("io:destination_check_failed")
        else:
            _fail("io:destination_exists")
        temp = f".{secrets.token_hex(12)}.body-tmp"
        owned: tuple[int, int] | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
            fd = os.open(temp, flags, 0o600, dir_fd=parent)
            try:
                info = os.fstat(fd)
                owned = (info.st_dev, info.st_ino)
                _write_all(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.link(temp, leaf, src_dir_fd=parent, dst_dir_fd=parent)
            except FileExistsError:
                _fail("io:destination_exists")
            published = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            refuse((published.st_dev, published.st_ino) == owned, "io:publication_identity")
        except BodyStudyRefusal:
            _remove_owned_temp(parent, temp, owned)
            raise
        except OSError:
            _remove_owned_temp(parent, temp, owned)
            _fail("io:publication_failed")
        try:
            _remove_owned_temp(parent, temp, owned)
            os.fsync(parent)
        except OSError:
            _fail("io:temporary_cleanup_failed")








__all__ = ["publish_member", "read_member"]
