"""Private file validation and reads for owned service artifacts."""
from __future__ import annotations
import errno
import os
import stat
import tempfile
from pathlib import Path
from collections.abc import Callable
from contextlib import suppress
from common_lore_index.service_package._artifact_contracts import (
    ServiceArtifactError, OwnedArtifact, _MAX_DEFINITION_BYTES,
)
from common_lore_index.coordinator.windows_security import (
    WindowsSecurityError, apply_owner_private_acl, reject_reparse_path,
    validate_owner_private_acl,
)


def inspect_owned_artifact(
    path: Path,
    validator: Callable[[bytes], bool],
    *,
    owner_uid: int | None = None,
) -> OwnedArtifact | None:
    """Read one recognized private regular file without following symlinks."""

    target = _absolute_path(path)
    expected_uid = None if os.name == "nt" else (
        os.getuid() if owner_uid is None else owner_uid
    )
    try:
        if os.name == "nt":
            reject_reparse_path(target)
        details = os.lstat(target)
    except FileNotFoundError:
        return None
    except (OSError, WindowsSecurityError):
        raise ServiceArtifactError("service_definition_unsafe") from None
    if os.name == "nt":  # pragma: no cover - native Windows runner
        try:
            validate_owner_private_acl(target)
        except WindowsSecurityError:
            raise ServiceArtifactError("service_definition_unsafe") from None
    if not _safe_file_details(details, expected_uid):
        raise ServiceArtifactError("service_definition_unsafe")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if os.name != "nt":
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)
    try:
        descriptor = os.open(target, flags)
        try:
            content = _read_owned_file(descriptor, details, expected_uid)
        finally:
            with suppress(OSError):
                os.close(descriptor)
    except OSError:
        raise ServiceArtifactError("service_definition_unsafe") from None
    try:
        recognized = validator(content)
    except Exception:
        recognized = False
    if not recognized:
        raise ServiceArtifactError("service_definition_unsafe")
    return OwnedArtifact(
        content=content,
        mode=stat.S_IMODE(details.st_mode),
        device=details.st_dev,
        inode=details.st_ino,
    )


def _version(details: os.stat_result) -> tuple[int, int, int, int, int]:
    return details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns, details.st_ctime_ns


def _read_owned_file(
    descriptor: int, details: os.stat_result, owner_uid: int | None,
) -> bytes:
    opened = os.fstat(descriptor)
    if not _safe_file_details(opened, owner_uid) or _version(opened) != _version(details):
        raise ServiceArtifactError("service_definition_unsafe")
    content = bytearray()
    while len(content) <= opened.st_size:
        chunk = os.read(descriptor, opened.st_size + 1 - len(content))
        if not chunk:
            break
        content.extend(chunk)
    after = os.fstat(descriptor)
    if (
        not _safe_file_details(after, owner_uid)
        or _version(after) != _version(opened)
        or len(content) != opened.st_size
    ):
        raise ServiceArtifactError("service_definition_unsafe")
    return bytes(content)


def _absolute_path(path: Path) -> Path:
    raw = os.fspath(path)
    if "\x00" in raw or not Path(raw).is_absolute():
        raise ServiceArtifactError("service_definition_unsafe")
    return Path(raw)


def _safe_file_details(details: os.stat_result, owner_uid: int | None) -> bool:
    if os.name == "nt":  # pragma: no cover - native Windows runner
        return stat.S_ISREG(details.st_mode) and details.st_size <= _MAX_DEFINITION_BYTES
    return (
        stat.S_ISREG(details.st_mode)
        and details.st_uid == owner_uid
        and stat.S_IMODE(details.st_mode) == 0o600
        and details.st_size <= _MAX_DEFINITION_BYTES
    )


def _validate_parent(path: Path, owner_uid: int | None) -> None:
    if os.name == "nt":  # pragma: no cover - native Windows runner
        try:
            reject_reparse_path(path)
            details = path.lstat()
            if not stat.S_ISDIR(details.st_mode):
                raise ServiceArtifactError("service_directory_unsafe")
            validate_owner_private_acl(path)
            return
        except (OSError, WindowsSecurityError):
            raise ServiceArtifactError("service_directory_unsafe") from None
    expected_uid = os.getuid() if owner_uid is None else owner_uid
    try:
        details = path.lstat()
    except OSError:
        raise ServiceArtifactError("service_directory_unavailable") from None
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != expected_uid
        or stat.S_IMODE(details.st_mode) & 0o022
    ):
        raise ServiceArtifactError("service_directory_unsafe")


def _validate_new_content(content: bytes, validator: Callable[[bytes], bool]) -> None:
    if not isinstance(content, bytes) or len(content) > _MAX_DEFINITION_BYTES:
        raise ServiceArtifactError("service_definition_invalid")
    try:
        valid = validator(content)
    except Exception:
        valid = False
    if not valid:
        raise ServiceArtifactError("service_definition_invalid")


def _write_private_temporary(parent: Path, content: bytes) -> Path:
    descriptor = -1
    temporary_path = ""
    identity: tuple[int, int] | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".repomap-service-",
            dir=parent,
        )
        details = os.fstat(descriptor)
        identity = details.st_dev, details.st_ino
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if not 0 < written <= len(view):
                raise OSError(errno.EIO, "service definition write made invalid progress")
            view = view[written:]
        os.fsync(descriptor)
        closing_descriptor, descriptor = descriptor, -1
        os.close(closing_descriptor)
        temporary = Path(temporary_path)
        if os.name == "nt":  # pragma: no cover - native Windows runner
            apply_owner_private_acl(temporary)
        return temporary
    except BaseException as error:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        if identity is not None:
            with suppress(OSError):
                current = os.lstat(temporary_path)
                if (current.st_dev, current.st_ino) == identity:
                    os.unlink(temporary_path)
        if isinstance(error, (OSError, WindowsSecurityError)):
            raise ServiceArtifactError("service_definition_write_failed") from None
        raise
