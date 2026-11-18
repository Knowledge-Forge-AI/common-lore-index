from __future__ import annotations
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import sys
import threading
from types import CodeType, FrameType
from typing import Any, Literal, cast
SNAPSHOT_COUNTER_SCHEMA = "cloe-w5-body-snapshot-counter-v1"
CANONICAL_RESERVED_SNAPSHOTS = 201
MAX_RESERVED_SNAPSHOTS = CANONICAL_RESERVED_SNAPSHOTS
MAX_CALL_SITES = 64
MAX_RECEIPT_BYTES = 16 * 1024
COUNTER_BOOKKEEPING_BYTES = 2 * MAX_RECEIPT_BYTES + 4096
_AUDIT_PROBE_EVENT = "cloe.body.snapshot_counter.probe"
_AUDIT_HOOK_REGISTERED = False
_AUDIT_PROBE_SEEN = False
_ACTIVE: "BodySnapshotCounter | None" = None
_ACTIVE_LOCK = threading.RLock()
_WRITER_STATE = threading.local()
_TEMP_SEQUENCE = 0
class SnapshotCounterRefusal(RuntimeError):
    """A snapshot counter cannot prove a bounded, restored execution."""
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
def _require(condition: object, code: str) -> None:
    if not condition:
        raise SnapshotCounterRefusal(code)
def _text(value: object, code: str, maximum: int = 256) -> str:
    _require(type(value) is str and 0 < len(value) <= maximum, code)
    return cast(str, value)
def _digest(value: object, code: str) -> str:
    result = _text(value, code, 71)
    _require(result.startswith("sha256:") and len(result) == 71 and not any(char not in "0123456789abcdef" for char in result[7:]), code)
    return result
@dataclass(frozen=True, slots=True)
class SnapshotCounterBinding:
    """Trusted caller binding for one BODY counter receipt."""
    attempt_id: str
    plan_sha256: str
    source_closure_sha256: str
    candidate_snapshot_sha256: str
    input_pin_sha256: str
    counter_root: Path
    receipt_name: str = "body-study-snapshot-counter.json"
    def validate(self) -> None:
        _text(self.attempt_id, "snapshot.binding.attempt")
        for value, code in (
            (self.plan_sha256, "snapshot.binding.plan"),
            (self.source_closure_sha256, "snapshot.binding.source"),
            (self.candidate_snapshot_sha256, "snapshot.binding.candidate"),
            (self.input_pin_sha256, "snapshot.binding.pin"),
        ):
            _digest(value, code)
        _require(isinstance(self.counter_root, Path) and self.counter_root.is_absolute()
                 and self.counter_root.resolve() == self.counter_root
                 and all(part not in {".", ".."} for part in self.counter_root.parts),
                 "snapshot.binding.root")
        _text(self.receipt_name, "snapshot.binding.receipt", 128)
        _require(Path(self.receipt_name).name == self.receipt_name
                 and self.receipt_name not in {".", ".."}, "snapshot.binding.receipt_name")
    def public_dict(self) -> dict[str, str]:
        return {
            "attempt_id": self.attempt_id,
            "plan_sha256": self.plan_sha256,
            "source_closure_sha256": self.source_closure_sha256,
            "candidate_snapshot_sha256": self.candidate_snapshot_sha256,
            "input_pin_sha256": self.input_pin_sha256,
            "receipt_name": self.receipt_name,
        }
@dataclass(frozen=True, slots=True)
class SnapshotCounterReceipt:
    """Measured evidence; a receipt is valid only after profile restoration."""
    binding: SnapshotCounterBinding
    reserved_count: int
    observed_count: int
    call_map: tuple[tuple[str, int], ...]
    violation_code: str | None
    audit_rejections: int
    profile_attested: bool
    profile_restored: bool
    audit_hook_attested: bool
    valid: bool
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SNAPSHOT_COUNTER_SCHEMA,
            "binding": self.binding.public_dict(),
            "reserved_count": self.reserved_count,
            "observed_count": self.observed_count,
            "observed_count_kind": "measured_exact_code_object_calls",
            "call_map": [
                {"caller": caller, "count": count}
                for caller, count in self.call_map
            ],
            "violation_code": self.violation_code,
            "audit_rejections": self.audit_rejections,
            "profile_attested": self.profile_attested,
            "profile_restored": self.profile_restored,
            "audit_hook_attested": self.audit_hook_attested, "audit_hook_inert": _ACTIVE is None,
            "counter_scope": "inner-sdk",
            "bookkeeping_bytes": COUNTER_BOOKKEEPING_BYTES,
            "valid": self.valid,
        }
def _writer_depth() -> int:
    return int(getattr(_WRITER_STATE, "depth", 0))
def _writer_enter() -> None:
    _WRITER_STATE.depth = _writer_depth() + 1
def _writer_leave() -> None:
    depth = _writer_depth()
    _WRITER_STATE.depth = max(0, depth - 1)
def _snapshot_frame_active(code: CodeType) -> bool:
    frame: FrameType | None = sys._getframe().f_back
    try:
        while frame is not None:
            if frame.f_code is code:
                return True
            frame = frame.f_back
        return False
    finally:
        frame = None
def _audit_hook(event: str, _arguments: tuple[object, ...]) -> None:
    """Reject snapshot I/O after a profile violation; inert outside BODY scope."""
    global _AUDIT_PROBE_SEEN
    if event == _AUDIT_PROBE_EVENT:
        _AUDIT_PROBE_SEEN = True
        return
    if event not in {"open", "os.open"} or _writer_depth() > 0:
        return
    state = _ACTIVE
    if state is None or not _snapshot_frame_active(state.snapshot_code):
        return
    if threading.get_ident() != state.owner_thread_id:
        state.mark_violation("foreign_thread_snapshot")
    elif sys.getprofile() is not state.profile_hook:
        state.mark_violation("profile_lost")
    if state.violation_code is not None:
        state.audit_rejections += 1
        raise SnapshotCounterRefusal(state.violation_code)
def _install_audit_hook() -> None:
    global _AUDIT_HOOK_REGISTERED
    if _AUDIT_HOOK_REGISTERED:
        return
    try:
        sys.addaudithook(_audit_hook)
        sys.audit(_AUDIT_PROBE_EVENT)
    except BaseException:
        return
    _AUDIT_HOOK_REGISTERED = _AUDIT_PROBE_SEEN
def audit_hook_available() -> bool:
    """Return whether the one-time audit hook installation was probed."""
    return _AUDIT_HOOK_REGISTERED and _AUDIT_PROBE_SEEN
def _caller_key(frame: Any) -> str:
    caller = frame.f_back
    if caller is None: return "<root>:<unknown>:0"
    name = os.path.basename(caller.f_code.co_filename)[:96]
    function = caller.f_code.co_name[:96]
    return f"{name}:{function}:{caller.f_lineno}"
class BodySnapshotCounter:
    """Observe the original snapshot code without replacing or caching it."""
    __slots__ = (
        "snapshot_code", "binding", "reserved_count", "owner_thread_id",
        "profile_hook", "prior_sys_profile", "prior_thread_profile", "entered",
        "closed", "observed_count", "callers", "violation_code",
        "audit_rejections", "profile_attested", "profile_restored", "receipt",
    )
    def __init__(
        self,
        snapshot_code: CodeType,
        *,
        binding: SnapshotCounterBinding,
        reserved_count: int = CANONICAL_RESERVED_SNAPSHOTS,
    ) -> None:
        _require(isinstance(snapshot_code, CodeType), "snapshot.code")
        binding.validate()
        _require(type(reserved_count) is int and 0 < reserved_count <= MAX_RESERVED_SNAPSHOTS,
                 "snapshot.reserved_count")
        self.snapshot_code = snapshot_code
        self.binding = binding
        self.reserved_count = reserved_count
        self.owner_thread_id = 0
        self.profile_hook: Any = None
        self.prior_sys_profile: Any = None
        self.prior_thread_profile: Any = None
        self.entered = False
        self.closed = False
        self.observed_count = 0
        self.callers: dict[str, int] = {}
        self.violation_code: str | None = None
        self.audit_rejections = 0
        self.profile_attested = False
        self.profile_restored = False
        self.receipt: SnapshotCounterReceipt | None = None
    def mark_violation(self, code: str) -> None:
        if self.violation_code is None: self.violation_code = code
    def _profile(self, frame: Any, event: str, _argument: object) -> None:
        """Never raise: audit backstop handles refusal after this callback."""
        if _ACTIVE is not self or self.closed or event != "call" or frame.f_code is not self.snapshot_code: return
        if threading.get_ident() != self.owner_thread_id:
            self.mark_violation("foreign_thread_snapshot")
            return
        if self.observed_count <= self.reserved_count:
            self.observed_count += 1
        caller = _caller_key(frame)
        if caller in self.callers:
            self.callers[caller] += 1
        elif len(self.callers) < MAX_CALL_SITES:
            self.callers[caller] = 1
        else:
            self.mark_violation("snapshot_call_map_bound")
        if self.observed_count > self.reserved_count:
            self.mark_violation("snapshot_limit_exceeded")
    def _restore_profiles(self) -> None:
        current_sys = sys.getprofile()
        current_thread = threading.getprofile()
        self.profile_attested = current_sys is self.profile_hook and current_thread is self.profile_hook
        try:
            sys.setprofile(self.prior_sys_profile)
        except BaseException:
            self.mark_violation("sys_profile_restore")
        try:
            threading.setprofile(self.prior_thread_profile)
        except BaseException:
            self.mark_violation("thread_profile_restore")
        self.profile_restored = (
            sys.getprofile() is self.prior_sys_profile
            and threading.getprofile() is self.prior_thread_profile
        )
        if not self.profile_restored:
            self.mark_violation("profile_restore_mismatch")
    def __enter__(self) -> "BodySnapshotCounter":
        _install_audit_hook()
        _require(audit_hook_available(), "audit_hook_unavailable")
        prior_sys = sys.getprofile()
        prior_thread = threading.getprofile()
        _require(prior_sys is None and prior_thread is None, "prior_profile_present")
        with _ACTIVE_LOCK:
            global _ACTIVE
            _require(_ACTIVE is None, "snapshot_counter_nested")
            self.owner_thread_id = threading.get_ident()
            self.prior_sys_profile = prior_sys
            self.prior_thread_profile = prior_thread
            self.profile_hook = self._profile
            _ACTIVE = self
            try:
                sys.setprofile(self.profile_hook)
                threading.setprofile(self.profile_hook)
                _require(sys.getprofile() is self.profile_hook
                         and threading.getprofile() is self.profile_hook,
                         "profile_install_failed")
                self.entered = True
            except BaseException:
                self._restore_profiles()
                _ACTIVE = None
                self.profile_hook = None
                raise
        return self
    def _build_receipt(self, *, execution_failed: bool) -> SnapshotCounterReceipt:
        valid = (
            not execution_failed
            and self.violation_code is None
            and self.observed_count == self.reserved_count
            and self.profile_attested
            and self.profile_restored
            and audit_hook_available() and _ACTIVE is None
        )
        return SnapshotCounterReceipt(
            self.binding, self.reserved_count, self.observed_count,
            tuple(sorted(self.callers.items())), self.violation_code,
            self.audit_rejections, self.profile_attested, self.profile_restored,
            audit_hook_available(), valid,
        )
    def finish(self, *, execution_failed: bool = False) -> SnapshotCounterReceipt:
        with _ACTIVE_LOCK:
            if self.closed:
                _require(self.receipt is not None, "snapshot.receipt_missing")
                assert self.receipt is not None
                return self.receipt
            _require(self.entered, "snapshot_counter_not_entered")
            self._restore_profiles()
            global _ACTIVE
            if _ACTIVE is self: _ACTIVE = None
            self.profile_hook = None
            self.closed = True
        receipt = self._build_receipt(execution_failed=execution_failed)
        self.receipt = receipt
        persist_snapshot_counter_receipt(self.binding, receipt)
        if not receipt.valid:
            raise SnapshotCounterRefusal(receipt.violation_code or "snapshot_receipt_invalid")
        return receipt
    def checkpoint(self) -> SnapshotCounterReceipt:
        """Return measured interim evidence; it is never a valid attestation."""
        _require(self.entered and not self.closed, "snapshot_checkpoint_state")
        return self._build_receipt(execution_failed=False)
    def __exit__(self, exc_type: Any, _exc: Any, _traceback: Any) -> Literal[False]:
        try:
            self.finish(execution_failed=exc_type is not None)
        except SnapshotCounterRefusal:
            if exc_type is None: raise
        return False
def _canonical_receipt_bytes(receipt: SnapshotCounterReceipt) -> bytes:
    raw = json.dumps(receipt.to_dict(), sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    _require(len(raw) + 1 <= MAX_RECEIPT_BYTES, "snapshot.receipt_bound")
    return raw + b"\n"
def _open_root_fd(root: Path) -> int:
    _require(isinstance(root, Path) and root.is_absolute()
             and root.resolve() == root
             and all(part not in {".", ".."} for part in root.parts),
             "snapshot.journal_root")
    directory = getattr(os, "O_DIRECTORY", 0); nofollow = getattr(os, "O_NOFOLLOW", 0)
    _require(directory and nofollow and os.open in os.supports_dir_fd, "snapshot.journal_open")
    flags = os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0)
    fd = -1
    try:
        fd = os.open(root.parts[0], flags)
        for part in root.parts[1:]:
            child = os.open(part, flags, dir_fd=fd); os.close(fd); fd = child
        return fd
    except (OSError, TypeError) as error:
        if fd >= 0: os.close(fd)
        raise SnapshotCounterRefusal("snapshot.journal_open") from error
def _open_private_directory(root: Path) -> int:
    fd = _open_root_fd(root)
    try:
        info = os.fstat(fd)
        _require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700, "snapshot.journal_custody")
        if hasattr(os, "getuid"):
            _require(info.st_uid == os.getuid(), "snapshot.journal_owner")
    except BaseException:
        os.close(fd)
        raise
    return fd
def _temp_name(receipt_name: str) -> str:
    global _TEMP_SEQUENCE
    _TEMP_SEQUENCE += 1
    return f".{receipt_name}.tmp-{os.getpid()}-{threading.get_ident()}-{_TEMP_SEQUENCE}"
def _write_all(fd: int, raw: bytes) -> None:
    view = memoryview(raw)
    while view:
        written = os.write(fd, view)
        _require(type(written) is int and written > 0, "snapshot.journal_write")
        view = view[written:]
def _remove_temp(temporary: str, directory_fd: int) -> None:
    if temporary and directory_fd >= 0:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except OSError:
            pass
def persist_snapshot_counter_receipt(
    binding: SnapshotCounterBinding,
    receipt: SnapshotCounterReceipt,
) -> Path:
    """Persist one bounded receipt using a private directory file descriptor."""
    binding.validate()
    _require(receipt.binding == binding, "snapshot.receipt_binding")
    raw = _canonical_receipt_bytes(receipt)
    _writer_enter()
    directory_fd = -1
    temporary = ""
    descriptor = -1
    try:
        directory_fd = _open_private_directory(binding.counter_root)
        temporary = _temp_name(binding.receipt_name)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        _write_all(descriptor, raw)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, binding.receipt_name, src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd, follow_symlinks=False)
        except FileExistsError as error:
            raise SnapshotCounterRefusal("snapshot.receipt_exists") from error
        os.unlink(temporary, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except SnapshotCounterRefusal:
        if descriptor >= 0:
            os.close(descriptor)
        _remove_temp(temporary, directory_fd)
        raise
    except (OSError, TypeError, ValueError) as error:
        if descriptor >= 0: os.close(descriptor)
        _remove_temp(temporary, directory_fd)
        raise SnapshotCounterRefusal("snapshot.journal_persist") from error
    finally:
        _writer_leave()
        if directory_fd >= 0: os.close(directory_fd)
    return binding.counter_root / binding.receipt_name
_install_audit_hook()
__all__ = ["BodySnapshotCounter", "CANONICAL_RESERVED_SNAPSHOTS", "COUNTER_BOOKKEEPING_BYTES", "MAX_RECEIPT_BYTES", "SNAPSHOT_COUNTER_SCHEMA", "SnapshotCounterBinding", "SnapshotCounterReceipt", "SnapshotCounterRefusal", "audit_hook_available", "persist_snapshot_counter_receipt"]
