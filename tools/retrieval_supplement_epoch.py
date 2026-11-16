"""Study-only preparation epochs; no model or execution authority is minted."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any

from retrieval_supplement_study_contracts import INPUT_PIN_SHA256, refuse
from retrieval_supplement_primitives import canonical_bytes, digest_bytes, strict_json

EPOCH_SCHEMA = "cloe-supplement-preparation-epoch-v1"
ACK_SCHEMA = "cloe-supplement-preparation-ack-v1"
EPOCH_PREFIX = "CLOE_SUPPLEMENT_PREPARATION_EPOCH "
EPOCH_ROOT = Path("/sandbox-scratch/supplement-study/epochs")
MAX_FRAME = 4096
REPRESENTATIONS = ("canonical-node-v1", "canonical-node-public-text-v2")
FIELDS = frozenset({"schema", "attempt_id", "ordinal", "representation", "nonce",
    "request_attempt_id", "input_pin_sha256", "evaluator_closure_sha256"})
_ATTEMPT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,76}\Z")
_NONCE = re.compile(r"[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class PreparationEpoch:
    attempt_id: str
    ordinal: int
    representation: str
    nonce: str
    request_attempt_id: str
    input_pin_sha256: str
    evaluator_closure_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"schema": EPOCH_SCHEMA, "attempt_id": self.attempt_id, "ordinal": self.ordinal,
            "representation": self.representation, "nonce": self.nonce,
            "request_attempt_id": self.request_attempt_id, "input_pin_sha256": self.input_pin_sha256,
            "evaluator_closure_sha256": self.evaluator_closure_sha256}

    @property
    def raw(self) -> bytes:
        return canonical_bytes(self.to_dict()) + b"\n"

    @property
    def identity(self) -> str:
        return digest_bytes(self.raw)

    @property
    def relative_directory(self) -> str:
        return f"{self.attempt_id}/{self.ordinal}"


def parse_epoch(value: Mapping[str, Any]) -> PreparationEpoch:
    refuse(isinstance(value, Mapping) and set(value) == FIELDS and value["schema"] == EPOCH_SCHEMA,
           "epoch:closed_schema")
    refuse(type(value["attempt_id"]) is str and _ATTEMPT.fullmatch(value["attempt_id"]) is not None,
           "epoch:attempt")
    ordinal = value["ordinal"]
    refuse(type(ordinal) is int and ordinal in (1, 2), "epoch:ordinal")
    refuse(value["representation"] == REPRESENTATIONS[ordinal - 1], "epoch:representation")
    refuse(value["request_attempt_id"] == value["attempt_id"] + f"-v{ordinal}", "epoch:request_attempt")
    refuse(type(value["nonce"]) is str and _NONCE.fullmatch(value["nonce"]) is not None, "epoch:nonce")
    refuse(value["input_pin_sha256"] == INPUT_PIN_SHA256, "epoch:input_pin")
    refuse(type(value["evaluator_closure_sha256"]) is str
           and _DIGEST.fullmatch(value["evaluator_closure_sha256"]) is not None, "epoch:evaluator")
    return PreparationEpoch(**{name: value[name] for name in FIELDS if name != "schema"})


def acknowledgement(epoch: PreparationEpoch) -> bytes:
    parse_epoch(epoch.to_dict())
    return canonical_bytes({"schema": ACK_SCHEMA, "epoch_sha256": epoch.identity,
        "attempt_id": epoch.attempt_id, "ordinal": epoch.ordinal, "nonce": epoch.nonce,
        "request_attempt_id": epoch.request_attempt_id}) + b"\n"


def _directory(path: Path, *, create: bool = False) -> int:
    refuse(path.is_absolute() and ".." not in path.parts, "epoch:directory_absolute")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o700, dir_fd=descriptor)
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        refuse(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
               and stat.S_IMODE(info.st_mode) == 0o700, "epoch:directory_custody")
        result, descriptor = descriptor, -1
        return result
    except OSError:
        raise RuntimeError("epoch:directory_unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write(directory: int, name: str, raw: bytes, charge: Callable[[int], None]) -> None:
    refuse(0 < len(raw) <= MAX_FRAME, "epoch:frame_bounds")
    charge(len(raw))
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory)


def prepare_epoch(*, attempt_id: str, ordinal: int, evaluator_closure_sha256: str,
                  root: Path = EPOCH_ROOT, charge: Callable[[int], None],
                  nonce: str | None = None) -> PreparationEpoch:
    """Publish a fresh epoch before SDK preparation; no request/model starts."""
    refuse(type(ordinal) is int and ordinal in (1, 2), "epoch:ordinal")
    epoch = parse_epoch({"schema": EPOCH_SCHEMA, "attempt_id": attempt_id, "ordinal": ordinal,
        "representation": REPRESENTATIONS[ordinal - 1], "nonce": secrets.token_hex(32) if nonce is None else nonce,
        "request_attempt_id": attempt_id + f"-v{ordinal}", "input_pin_sha256": INPUT_PIN_SHA256,
        "evaluator_closure_sha256": evaluator_closure_sha256})
    parent_fd = _directory(root, create=True)
    attempt_fd = epoch_fd = None
    try:
        try:
            os.mkdir(attempt_id, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        attempt_fd = os.open(attempt_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        info = os.fstat(attempt_fd)
        refuse(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, "epoch:attempt_custody")
        os.mkdir(str(ordinal), 0o700, dir_fd=attempt_fd)
        epoch_fd = os.open(str(ordinal), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=attempt_fd)
        _write(epoch_fd, "epoch.json", epoch.raw, charge)
    finally:
        for descriptor in (epoch_fd, attempt_fd, parent_fd):
            if descriptor is not None:
                os.close(descriptor)
    print("\n" + EPOCH_PREFIX + canonical_bytes(epoch.to_dict()).decode("utf-8"), flush=True)
    return epoch


def publish_acknowledgement(epoch: PreparationEpoch, *, root: Path = EPOCH_ROOT,
                            charge: Callable[[int], None]) -> None:
    """Publish only a matching already-created epoch; never a model grant."""
    parse_epoch(epoch.to_dict())
    directory = _directory(root / epoch.relative_directory)
    descriptor = None
    try:
        descriptor = os.open("epoch.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        info = os.fstat(descriptor)
        refuse(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
               and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= MAX_FRAME,
               "epoch:published_custody")
        charge(info.st_size + 1)
        raw = os.read(descriptor, info.st_size + 1)
        after = os.fstat(descriptor)
        refuse((info.st_ino, info.st_size, info.st_mtime_ns) ==
               (after.st_ino, after.st_size, after.st_mtime_ns), "epoch:published_changed")
        refuse(raw == epoch.raw, "epoch:published_join")
        _write(directory, "ack.json", acknowledgement(epoch), charge)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def await_acknowledgement(epoch: PreparationEpoch, *, root: Path = EPOCH_ROOT,
                          charge: Callable[[int], None], require_current: Callable[[], None],
                          clock: Callable[[], float] = time.monotonic,
                          sleep: Callable[[float], None] = time.sleep) -> None:
    """Wait at most120s for the exact host ACK before any pre-request work."""
    parse_epoch(epoch.to_dict())
    expected = acknowledgement(epoch)
    deadline = clock() + 120
    directory = _directory(root / epoch.relative_directory)
    try:
        require_current()
        while clock() < deadline:
            try:
                descriptor = os.open("ack.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            except FileNotFoundError:
                sleep(.05)
                continue
            try:
                before = os.fstat(descriptor)
                refuse(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                       and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                       and 0 <= before.st_size <= MAX_FRAME, "epoch:ack_custody")
                charge(before.st_size + 1)
                raw = os.read(descriptor, before.st_size + 1)
                after = os.fstat(descriptor)
                stable = (before.st_ino, before.st_size, before.st_mtime_ns) == (
                    after.st_ino, after.st_size, after.st_mtime_ns)
                if len(raw) < len(expected) or not stable:
                    refuse(expected.startswith(raw), "epoch:ack_join")
                    sleep(.05)
                    continue
                refuse(raw == expected, "epoch:ack_join")
                require_current()
                return
            finally:
                os.close(descriptor)
        raise RuntimeError("epoch:ack_timeout")
    finally:
        os.close(directory)


def decode_epoch_line(raw: bytes) -> PreparationEpoch:
    prefix = EPOCH_PREFIX.encode()
    refuse(type(raw) is bytes and raw.startswith(prefix) and len(raw) <= MAX_FRAME, "epoch:frame")
    value = strict_json(raw[len(prefix):].strip())
    refuse(isinstance(value, Mapping), "epoch:object")
    return parse_epoch(value)
