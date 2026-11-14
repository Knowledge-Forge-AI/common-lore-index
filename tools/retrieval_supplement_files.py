"""Fail-closed fixture/source file custody using directory file descriptors.

Every path component (root ancestors included) is opened with ``O_NOFOLLOW`` and
``O_DIRECTORY`` relative to its parent descriptor, so symlinked or non-directory
components are refused. Reads are bounded, non-blocking and identity-checked
before/after. Publication is exclusive: an owned same-directory temporary file is
written, fsynced and hard-linked into place (``link`` never overwrites), and only
that temporary artifact is ever removed. Files are never executed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import stat

from retrieval_supplement_values import (
    SupplementError,
    digest_bytes,
    digest_value,
    fail,
    integer,
    require,
    safe_relative_path,
)

MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_INVENTORY_FILES = 4096
MAX_INVENTORY_DEPTH = 32
_IDENTITY_FIELDS = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")


def _flags() -> tuple[int, int]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    require(nofollow is not None and directory is not None, "io:nofollow_unsupported")
    for function in (os.open, os.stat, os.mkdir, os.unlink, os.link):
        require(function in os.supports_dir_fd, "io:dir_fd_unsupported")
    require(os.stat in os.supports_follow_symlinks, "io:nofollow_stat_unsupported")
    require(os.listdir in os.supports_fd, "io:fd_listing_unsupported")
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
        fail("io:unsafe_directory_component")


def _open_root(root: Path) -> int:
    """Open an absolute root walking every component with no-follow semantics."""
    _nofollow, dir_flags = _flags()
    require(isinstance(root, Path), "io:root_path_required")
    raw = os.fspath(root)
    require(os.path.isabs(raw), "io:root_must_be_absolute")
    parts = raw.split("/")[1:]
    require(all(part not in {".", ".."} for part in parts), "io:root_traversal")
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
                    fail("io:directory_create_failed")
            child = _open_child_dir(fd, part)
            os.close(fd)
            fd = child
            if create:
                require(os.fstat(fd).st_uid == os.geteuid(), "io:directory_not_owned")
        yield fd
    finally:
        os.close(fd)


def read_member(
    root: Path, relative: str, *, max_bytes: int, expected_sha256: str | None = None
) -> bytes:
    """Read one exact regular file under ``root`` with bounded, identity-checked IO."""
    nofollow, _dir_flags = _flags()
    path = safe_relative_path(relative)
    limit = integer(max_bytes, "max_bytes", 0, MAX_MEMBER_BYTES)
    expected = None if expected_sha256 is None else digest_value(expected_sha256, "member")
    *parents, leaf = path.split("/")
    with _parent_dir(root, parents, create=False) as parent:
        try:
            before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except OSError:
            fail("io:member_missing")
        require(stat.S_ISREG(before.st_mode), "io:member_not_regular")
        require(before.st_size <= limit, "io:member_too_large")
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NONBLOCK | nofollow, dir_fd=parent)
        except OSError:
            fail("io:member_open_failed")
        try:
            opened = os.fstat(fd)
            require(stat.S_ISREG(opened.st_mode), "io:member_not_regular")
            require(_identity(opened) == _identity(before), "io:member_changed")
            chunks: list[bytes] = []
            total = 0
            while True:
                try:
                    chunk = os.read(fd, min(65536, limit + 1 - total))
                except OSError:
                    fail("io:member_read_failed")
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                require(total <= limit, "io:member_too_large")
            after = os.fstat(fd)
        finally:
            os.close(fd)
        require(_identity(after) == _identity(before), "io:member_changed")
        require(total == before.st_size, "io:member_partial_read")
        try:
            relinked = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except OSError:
            fail("io:member_changed")
        require(_identity(relinked) == _identity(before), "io:member_changed")
    data = b"".join(chunks)
    if expected is not None:
        require(digest_bytes(data) == expected, "io:member_digest_mismatch")
    return data


def _write_all(fd: int, raw: bytes) -> None:
    view = memoryview(raw)
    while view:
        written = os.write(fd, view)
        require(written > 0, "io:short_write")
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
    require(type(raw) is bytes, "io:bytes_required")
    require(len(raw) <= MAX_MEMBER_BYTES, "io:member_too_large")
    path = safe_relative_path(relative)
    *parents, leaf = path.split("/")
    with _parent_dir(root, parents, create=True) as parent:
        try:
            os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError:
            fail("io:destination_check_failed")
        else:
            fail("io:destination_exists")
        temp = f".{secrets.token_hex(12)}.supplement-tmp"
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
                fail("io:destination_exists")
            published = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            require((published.st_dev, published.st_ino) == owned, "io:publication_identity")
        except SupplementError:
            _remove_owned_temp(parent, temp, owned)
            raise
        except OSError:
            _remove_owned_temp(parent, temp, owned)
            fail("io:publication_failed")
        try:
            _remove_owned_temp(parent, temp, owned)
            os.fsync(parent)
        except OSError:
            fail("io:temporary_cleanup_failed")


def _walk(fd: int, prefix: str, depth: int, out: list[str]) -> None:
    require(depth <= MAX_INVENTORY_DEPTH, "io:inventory_too_deep")
    for name in sorted(os.listdir(fd)):
        relative = f"{prefix}{name}"
        safe_relative_path(relative)
        try:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError:
            fail("io:inventory_changed")
        if stat.S_ISDIR(info.st_mode):
            child = _open_child_dir(fd, name)
            try:
                _walk(child, relative + "/", depth + 1, out)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode):
            out.append(relative)
            require(len(out) <= MAX_INVENTORY_FILES, "io:inventory_too_large")
        else:
            fail("io:nonregular_member")


def file_inventory(root: Path) -> tuple[str, ...]:
    """Exact sorted regular-file inventory; any symlink or special file fails closed."""
    fd = _open_root(root)
    try:
        out: list[str] = []
        _walk(fd, "", 0, out)
    finally:
        os.close(fd)
    return tuple(sorted(out))


def require_empty_owned_dir(root: Path) -> None:
    """Require ``root`` to be an existing, empty, caller-owned ordinary directory."""
    fd = _open_root(root)
    try:
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode), "io:root_not_directory")
        require(info.st_uid == os.geteuid(), "io:root_not_owned")
        require(not os.listdir(fd), "io:root_not_empty")
    finally:
        os.close(fd)


__all__ = ["file_inventory", "publish_member", "read_member", "require_empty_owned_dir"]
