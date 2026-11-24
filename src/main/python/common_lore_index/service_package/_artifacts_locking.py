"""Process and platform locks serialize owned service-artifact mutations."""
from __future__ import annotations
import os
import stat
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, RLock, local
from typing import Protocol, cast
from types import ModuleType
from common_lore_index.service_package._artifact_contracts import ServiceArtifactError
from common_lore_index.service_package._artifacts_io import _absolute_path, _validate_parent
from common_lore_index.coordinator.windows_security import (
    WindowsSecurityError, apply_owner_private_acl, reject_reparse_path,
    validate_owner_private_acl,
)

# Platform modules are optional, and their use remains guarded below.
fcntl: ModuleType | None
msvcrt: ModuleType | None
try:
    import fcntl as _fcntl
    fcntl = _fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt as _msvcrt
    msvcrt = _msvcrt
except ImportError:
    msvcrt = None

@dataclass
class _ProcessLock:
    mutex: RLock = field(default_factory=RLock)
    users: int = 0


_LOCK_REGISTRY: dict[Path, _ProcessLock] = {}
_LOCK_REGISTRY_GUARD = Lock()
_LOCK_STATE = local()


class _Msvcrt(Protocol):
    LK_NBLCK: int
    LK_UNLCK: int

    def locking(self, fd: int, mode: int, nbytes: int) -> None: ...


@contextmanager
def _mutation_lock(target: Path, owner_uid: int | None):
    lock_path = target.parent / f".{target.name}.lock"
    with _process_lock(lock_path):
        held: set[Path] = getattr(_LOCK_STATE, "paths", set())
        if lock_path in held:
            yield
            return
        if os.name == "nt":  # pragma: no cover - native Windows runner
            with _windows_mutation_lock(lock_path, held):
                yield
            return
        if fcntl is None:
            raise ServiceArtifactError("service_mutation_lock_unavailable")
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = -1
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            details = os.fstat(descriptor)
            expected_uid = os.getuid() if owner_uid is None else owner_uid
            if not _safe_lock_details(details, expected_uid):
                raise ServiceArtifactError("service_mutation_lock_unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                current = os.lstat(lock_path)
            except FileNotFoundError:
                raise ServiceArtifactError("service_mutation_lock_unsafe") from None
            if (
                not _safe_lock_details(current, expected_uid)
                or (current.st_dev, current.st_ino) != (details.st_dev, details.st_ino)
            ):
                raise ServiceArtifactError("service_mutation_lock_unsafe")
            _LOCK_STATE.paths = {*held, lock_path}
            yield
        except ServiceArtifactError:
            raise
        except OSError:
            raise ServiceArtifactError("service_mutation_lock_unavailable") from None
        finally:
            if descriptor >= 0:
                _LOCK_STATE.paths = held
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                with suppress(OSError):
                    os.close(descriptor)


@contextmanager
def _process_lock(path: Path):
    with _LOCK_REGISTRY_GUARD:
        entry = _LOCK_REGISTRY.get(path)
        if entry is None:
            entry = _LOCK_REGISTRY[path] = _ProcessLock()
        entry.users += 1
    try:
        with entry.mutex:
            yield
    finally:
        with _LOCK_REGISTRY_GUARD:
            entry.users -= 1
            if entry.users == 0:
                del _LOCK_REGISTRY[path]


@contextmanager
def _windows_mutation_lock(path: Path, held: set[Path]):  # pragma: no cover - native Windows runner
    if msvcrt is None:
        raise ServiceArtifactError("service_mutation_lock_unavailable")
    windows_lock = cast(_Msvcrt, msvcrt)
    descriptor = -1
    created = False
    try:
        reject_reparse_path(path.parent)
        try:
            descriptor = os.open(
                path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600
            )
            created = True
        except FileExistsError:
            reject_reparse_path(path)
            descriptor = os.open(path, os.O_RDWR)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ServiceArtifactError("service_mutation_lock_unsafe")
        if created:
            apply_owner_private_acl(path)
        validate_owner_private_acl(path)
        if details.st_size == 0:
            os.write(descriptor, b"0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        deadline = time.monotonic() + 30
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                windows_lock.locking(descriptor, windows_lock.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ServiceArtifactError("service_mutation_lock_unavailable")
                time.sleep(0.05)
        _LOCK_STATE.paths = {*held, path}
        yield
    except ServiceArtifactError:
        raise
    except (OSError, WindowsSecurityError):
        raise ServiceArtifactError("service_mutation_lock_unavailable") from None
    finally:
        if descriptor >= 0:
            _LOCK_STATE.paths = held
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                windows_lock.locking(descriptor, windows_lock.LK_UNLCK, 1)
            except OSError:
                pass
            os.close(descriptor)


@contextmanager
def service_mutation_lock(path: Path, *, owner_uid: int | None = None):
    """Serialize one complete cooperating service-package lifecycle mutation."""

    target = _absolute_path(path)
    _validate_parent(target.parent, owner_uid)
    with _mutation_lock(target, owner_uid):
        yield


def _safe_lock_details(details: os.stat_result, owner_uid: int) -> bool:
    return (
        stat.S_ISREG(details.st_mode)
        and details.st_uid == owner_uid
        and stat.S_IMODE(details.st_mode) == 0o600
    )
