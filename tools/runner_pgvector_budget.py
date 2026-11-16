"""Conservative, failure-persistent budgets for the opt-in offline qualifier."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterator, cast

from offline_qualification_files import private_file as private_file
from offline_qualification_values import PROOF_CAPS as PROOF_CAPS, STUDY_CAPS as STUDY_CAPS, refuse as refuse

INITIAL_CAPS = {
    "read_bytes": 4 * 1024**3, "transfer_bytes": 4 * 1024**3,
    "materializations": 1, "sdk_core": 1, "sdk_native": 1,
    "native_lifecycles": 1, "base_loads": 1, "runtime_exports": 1,
    "native_runtime_loads": 1,
}
RECOVERY2_CAPS = INITIAL_CAPS | {"read_bytes": 8 * 1024**3, "sdk_core": 2,
                                "base_loads": 2, "runtime_exports": 2}
CAPS = RECOVERY2_CAPS | {"read_bytes": 12 * 1024**3, "transfer_bytes": 6 * 1024**3,
                         "sdk_core": 3, "base_loads": 3, "runtime_exports": 3}
_RECORD_NAMES = {"derived_archive", "core_export", "core_result", "native_result",
                 "recovery_parent", "study_result"}
_PROOF_RECORD_NAMES = {"proof_result"}
MAX_ARCHIVE = 768 * 1024**2
MAX_SELECTED = 4 * 1024**2


class Allocation:
    """One serial parent owns large reads; reservations are journaled first.

    Every mutation made while a session is active is committed through an
    exclusive ``.next`` file before the caller is allowed to touch the guarded
    resource.  A leftover journal is deliberately treated as ambiguous state:
    it may describe a mutation whose rename or directory sync was interrupted,
    so the next owner must stop instead of guessing or resetting the ledger.
    """

    def __init__(self, path: Path, caps: dict[str, int] | None = None) -> None:
        self.path = path
        self.caps = CAPS if caps is None else caps
        refuse(self.caps in (CAPS, STUDY_CAPS, PROOF_CAPS), "unknown allocation caps")
        self.used: dict[str, int] = {}
        self.records: dict[str, Any] = {}
        self._lock: Any | None = None
        self._active = False

    @property
    def _pending_path(self) -> Path:
        return self.path.with_suffix(".next")

    def _refuse_pending_state(self) -> None:
        try:
            os.lstat(self._pending_path)
        except FileNotFoundError:
            return
        except OSError as error:
            raise RuntimeError("pgvector prebuilt refused: allocation journal state unavailable") from error
        raise RuntimeError("pgvector prebuilt refused: ambiguous pending allocation state")

    def _state(self) -> dict[str, Any]:
        return {"schema": "cloe-pgvector-allocation-v1", "caps": self.caps,
                "used": self.used, "records": self.records}

    def _persist(self) -> None:
        """Write, fsync, atomically replace, and sync the owning directory.

        The temporary file is intentionally retained when any pre-rename step
        fails.  Its presence is an explicit recovery signal; no future session
        may silently delete it or reopen a spent reservation.
        """
        refuse(self._active and self._lock is not None, "allocation session required")
        temporary = self._pending_path
        try:
            os.lstat(temporary)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise RuntimeError("pgvector prebuilt refused: allocation journal state unavailable") from error
        else:
            raise RuntimeError("pgvector prebuilt refused: ambiguous pending allocation state")
        payload = (json.dumps(self._state(), sort_keys=True, allow_nan=False) + "\n").encode()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as target:
                descriptor = -1
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException as error:
            if descriptor >= 0:
                os.close(descriptor)
            raise RuntimeError("pgvector prebuilt refused: allocation state persistence failed") from error

    @staticmethod
    def _strict_state(raw: bytes) -> dict[str, Any]:
        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                refuse(key not in result, "duplicate allocation state member")
                result[key] = value
            return result

        def nonfinite(value: str) -> None:
            raise RuntimeError("pgvector prebuilt refused: nonfinite allocation state")

        value = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
        refuse(isinstance(value, dict), "allocation state object")
        return value

    @contextmanager
    def session(self) -> Iterator[Allocation]:
        import fcntl
        private_file(self.path)
        refuse(self.path.stat().st_size <= 2 * 1024**2, "allocation metadata cap")
        self._refuse_pending_state()
        lock_path = self.path.with_suffix(".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            private_file(lock_path)
            self._refuse_pending_state()
            state = self._strict_state(self.path.read_bytes())
            refuse(state.get("schema") == "cloe-pgvector-allocation-v1"
                   and state.get("caps") == self.caps
                   and all(type(state["caps"][key]) is int for key in self.caps), "allocation shape")
            used = state.get("used")
            refuse(isinstance(used, dict) and set(used) == set(self.caps), "allocation keys")
            used = cast(dict[str, int], used)
            refuse(all(type(used[k]) is int and 0 <= used[k] <= v for k, v in self.caps.items()),
                   "allocation values")
            self.used = used
            records = state.get("records", {})
            allowed_records = _PROOF_RECORD_NAMES if self.caps == PROOF_CAPS else _RECORD_NAMES
            refuse(isinstance(records, dict) and set(records) <= allowed_records, "allocation records")
            self.records = records
            self._lock = lock
            self._active = True
            try:
                yield self
            finally:
                try:
                    self._persist()
                finally:
                    self._active = False
                    self._lock = None

    def charge(self, key: str, amount: int) -> None:
        refuse(key in self.caps and type(amount) is int and amount >= 0, "invalid charge")
        refuse(self.used[key] + amount <= self.caps[key], "exhausted " + key)
        self.used[key] += amount
        try:
            self._persist()
        except BaseException:
            # Leave the in-memory value untouched.  The durable journal (or
            # the persistence error itself) is the fail-closed recovery signal.
            raise

    def remaining(self, key: str) -> int:
        return self.caps[key] - self.used[key]

    def bind(self, name: str, value: dict[str, Any]) -> None:
        allowed_records = _PROOF_RECORD_NAMES if self.caps == PROOF_CAPS else _RECORD_NAMES
        refuse(name in allowed_records and name not in self.records, "already bound allocation record")
        refuse(isinstance(value, dict), "allocation record object")
        refuse(len(json.dumps(value, allow_nan=False).encode()) <= 256 * 1024,
               "allocation record cap")
        self.records[name] = value
        self._persist()

    def read(self, operation: Callable[[int], bytes], count: int, *, transfer: bool = False) -> bytes:
        """Reserve before bounded I/O; reconcile only bytes proved not consumed.

        Failed/unknown reads retain their whole reservation. Attempt counters
        and foreign-command reservations are never released by this method.
        """
        keys = ("read_bytes", "transfer_bytes") if transfer else ("read_bytes",)
        refuse(self._active, "allocation session required")
        for key in keys:
            refuse(key in self.caps and type(count) is int and count >= 0
                   and self.used[key] + count <= self.caps[key], "exhausted " + key)
        for key in keys:
            self.used[key] += count
        self._persist()
        try:
            raw = operation(count)
        except BaseException:
            # The full reservation is already durable.  Unknown consumption is
            # never reconciled after an exception.
            raise
        refuse(isinstance(raw, bytes) and len(raw) <= count, "bounded reader contract")
        for key in keys:
            self.used[key] -= count - len(raw)
        self._persist()
        return raw
