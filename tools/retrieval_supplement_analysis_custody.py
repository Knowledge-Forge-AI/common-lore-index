"""Dir-fd custody for retained input reading and atomic archive publication.

All path resolution uses ``O_NOFOLLOW`` and ``O_DIRECTORY`` relative to open
directory descriptors. Retained input reads reject symlinks, non-regular files,
hardlink aliases (``st_nlink != 1``) and mid-read changes. One exclusive link
publishes a complete deterministic archive with two logical members. Failure
cleanup only removes inode-verified owned files; an uncatchable process crash
may leave an unpublished temporary file, never half of a published pair.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import secrets
import stat
import zipfile

from retrieval_supplement_analysis_values import (
    MAX_BUNDLE_BYTES,
    MAX_OUTPUT_JSON_BYTES,
    MAX_REPORT_BYTES,
    AnalysisRefusal,
    as_digest,
    as_int,
    fail,
    refuse,
)
from retrieval_supplement_primitives import (
    SupplementError,
    digest_bytes,
    safe_relative_path,
)


def _flags() -> tuple[int, int]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        fail("analysis:custody_nofollow_unsupported")
    for fn in (os.open, os.stat, os.mkdir, os.unlink, os.link, os.rmdir):
        refuse(fn in os.supports_dir_fd, "analysis:custody_dir_fd_unsupported")
    refuse(os.stat in os.supports_follow_symlinks, "analysis:custody_nofollow_stat_unsupported")
    cloexec = getattr(os, "O_CLOEXEC", 0)
    return nofollow | cloexec, directory | nofollow | cloexec | os.O_RDONLY


def _safe_rel(relative: str) -> str:
    try:
        return safe_relative_path(relative)
    except SupplementError:
        fail("analysis:custody_invalid_relative_path")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        int(info.st_dev), int(info.st_ino), int(info.st_size),
        int(info.st_mtime_ns), int(info.st_ctime_ns), int(info.st_nlink),
    )


def _open_root(root: Path) -> int:
    _nofollow, dir_flags = _flags()
    refuse(isinstance(root, Path), "analysis:custody_root_required")
    raw = os.fspath(root)
    refuse(os.path.isabs(raw), "analysis:custody_root_must_be_absolute")
    parts = raw.split("/")[1:]
    refuse(all(part not in {".", ".."} for part in parts), "analysis:custody_root_traversal")
    fd = os.open("/", dir_flags)
    try:
        for part in (p for p in parts if p):
            try:
                child = os.open(part, dir_flags, dir_fd=fd)
            except OSError:
                fail("analysis:custody_unsafe_directory")
            os.close(fd)
            fd = child
        refuse(os.fstat(fd).st_uid == os.geteuid(), "analysis:custody_not_owned")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _walk_parents(
    root_fd: int,
    parts: list[str],
    *,
    create: bool,
    created_dirs: list[tuple[int, str, tuple[int, int]]],
) -> int:
    _nofollow, dir_flags = _flags()
    fd = os.dup(root_fd)
    try:
        for part in parts:
            made = False
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    made = True
                except FileExistsError:
                    made = False
                except OSError:
                    fail("analysis:custody_directory_create_failed")
            try:
                child = os.open(part, dir_flags, dir_fd=fd)
            except OSError:
                if made:
                    try:
                        os.rmdir(part, dir_fd=fd)
                    except OSError:
                        pass
                fail("analysis:custody_unsafe_directory")
            info = os.fstat(child)
            if info.st_uid != os.geteuid():
                os.close(child)
                fail("analysis:custody_not_owned")
            if made:
                created_dirs.append((os.dup(fd), part, (int(info.st_dev), int(info.st_ino))))
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_retained_bundle(
    input_root: Path,
    member: str,
    *,
    expected_sha256: str,
    max_bytes: int = MAX_BUNDLE_BYTES,
) -> bytes:
    """Read one owned regular unaliased bundle file with before/after stat checks."""
    nofollow, _dir_flags = _flags()
    expected = as_digest(expected_sha256, "expected_raw")
    limit = as_int(max_bytes, "max_bytes", 1, MAX_BUNDLE_BYTES)
    rel = _safe_rel(member)
    *parents, leaf = rel.split("/")
    root_fd = _open_root(input_root)
    created: list[tuple[int, str, tuple[int, int]]] = []
    try:
        parent = _walk_parents(root_fd, parents, create=False, created_dirs=created)
        try:
            try:
                before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            except OSError:
                fail("analysis:custody_member_missing")
            refuse(stat.S_ISREG(before.st_mode), "analysis:custody_not_regular")
            refuse(before.st_uid == os.geteuid(), "analysis:custody_not_owned")
            refuse(before.st_nlink == 1, "analysis:custody_hardlink_refused")
            refuse(0 < before.st_size <= limit, "analysis:bundle_too_large")
            try:
                fd = os.open(leaf, os.O_RDONLY | os.O_NONBLOCK | nofollow, dir_fd=parent)
            except OSError:
                fail("analysis:custody_open_failed")
            try:
                opened = os.fstat(fd)
                refuse(stat.S_ISREG(opened.st_mode), "analysis:custody_not_regular")
                refuse(opened.st_nlink == 1, "analysis:custody_hardlink_refused")
                refuse(_identity(opened) == _identity(before), "analysis:custody_member_changed")
                chunks: list[bytes] = []
                total = 0
                while True:
                    try:
                        chunk = os.read(fd, min(65536, limit + 1 - total))
                    except OSError:
                        fail("analysis:custody_read_failed")
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    refuse(total <= limit, "analysis:bundle_too_large")
                after = os.fstat(fd)
            finally:
                os.close(fd)
            refuse(_identity(after) == _identity(before), "analysis:custody_member_changed")
            refuse(total == before.st_size, "analysis:custody_partial_read")
            try:
                rechecked = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            except OSError:
                fail("analysis:custody_member_changed")
            refuse(_identity(rechecked) == _identity(before), "analysis:custody_member_changed")
        finally:
            os.close(parent)
    finally:
        os.close(root_fd)
    data = b"".join(chunks)
    refuse(digest_bytes(data) == expected, "analysis:raw_digest_mismatch")
    return data


def _write_temp(parent_fd: int, raw: bytes, nofollow: int) -> tuple[str, tuple[int, int]]:
    name = f".{secrets.token_hex(12)}.analysis-tmp"
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=parent_fd)
    owned: tuple[int, int] | None = None
    try:
        info = os.fstat(fd)
        owned = (int(info.st_dev), int(info.st_ino))
        _write_all(fd, raw)
        os.fsync(fd)
        return name, owned
    except BaseException as primary:
        notes: list[str] = []
        _unlink_owned(parent_fd, name, owned, notes)
        if notes:
            primary.add_note(f"secondary_cleanup_codes={notes}")
        raise
    finally:
        os.close(fd)


def _write_all(fd: int, raw: bytes) -> None:
    view = memoryview(raw)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short_write")
        view = view[written:]


def _unlink_owned(
    parent_fd: int, name: str | None, owned: tuple[int, int] | None, notes: list[str]
) -> None:
    if name is None or owned is None:
        return
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        notes.append("stat_cleanup_failed")
        return
    if (int(info.st_dev), int(info.st_ino)) == owned and stat.S_ISREG(info.st_mode):
        try:
            os.unlink(name, dir_fd=parent_fd)
        except OSError:
            notes.append("unlink_cleanup_failed")
    else:
        notes.append("cleanup_identity_changed")


def _check_absent(parent_fd: int, leaf: str) -> None:
    try:
        os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        fail("analysis:output_destination_check_failed")
    fail("analysis:output_destination_exists")


def _archive(json_rel: str, report_rel: str, json_raw: bytes, report_raw: bytes) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for member, raw in ((json_rel, json_raw), (report_rel, report_raw)):
            info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, raw)
    result = stream.getvalue()
    refuse(len(result) <= MAX_OUTPUT_JSON_BYTES + MAX_REPORT_BYTES + 4096,
           "analysis:archive_too_large")
    return result


def publish_analysis_pair(
    output_root: Path,
    *,
    json_member: str,
    report_member: str,
    json_raw: bytes,
    report_raw: bytes,
) -> dict[str, str]:
    """Publish two logical members in one exclusive deterministic archive."""
    nofollow, _dir_flags = _flags()
    refuse(type(json_raw) is bytes and 0 < len(json_raw) <= MAX_OUTPUT_JSON_BYTES,
           "analysis:output_too_large")
    refuse(type(report_raw) is bytes and 0 < len(report_raw) <= MAX_REPORT_BYTES,
           "analysis:report_too_large")
    json_rel = _safe_rel(json_member)
    report_rel = _safe_rel(report_member)
    refuse(json_rel != report_rel, "analysis:output_members_must_differ")
    raw = _archive(json_rel, report_rel, json_raw, report_raw)
    bundle_leaf = "analysis-bundle.zip"
    root_fd = _open_root(output_root)
    temp: str | None = None
    owned: tuple[int, int] | None = None
    linked = False
    try:
        _check_absent(root_fd, bundle_leaf)
        try:
            temp, owned = _write_temp(root_fd, raw, nofollow)
            try:
                os.link(temp, bundle_leaf, src_dir_fd=root_fd, dst_dir_fd=root_fd)
                linked = True
            except FileExistsError:
                fail("analysis:output_destination_exists")
            published = os.stat(bundle_leaf, dir_fd=root_fd, follow_symlinks=False)
            refuse((int(published.st_dev), int(published.st_ino)) == owned,
                   "analysis:output_publication_identity")
        except AnalysisRefusal:
            raise
        except OSError:
            fail("analysis:output_publication_failed")

        cleanup_notes: list[str] = []
        _unlink_owned(root_fd, temp, owned, cleanup_notes)
        refuse(not cleanup_notes, "analysis:output_temp_cleanup_failed")
        try:
            os.fsync(root_fd)
        except OSError:
            fail("analysis:output_directory_sync_failed")
    except BaseException as primary:
        notes: list[str] = []
        if linked:
            _unlink_owned(root_fd, bundle_leaf, owned, notes)
        _unlink_owned(root_fd, temp, owned, notes)
        if notes:
            primary.add_note(f"secondary_cleanup_codes={notes}")
        raise
    finally:
        os.close(root_fd)

    return {
        "json_sha256": digest_bytes(json_raw),
        "report_sha256": digest_bytes(report_raw),
        "json_member": json_rel,
        "report_member": report_rel,
        "bundle_member": bundle_leaf,
        "bundle_sha256": digest_bytes(raw),
    }


__all__ = ["publish_analysis_pair", "read_retained_bundle"]
