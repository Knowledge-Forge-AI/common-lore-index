"""Body-study preparation epochs and their one-use host acknowledgements."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from math import ceil
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any, cast

from retrieval_body_study_contracts import BODY_INPUT_PIN_SHA256, refuse
EPOCH_SCHEMA = "cloe-body-study-preparation-epoch-v1"
ACK_SCHEMA = "cloe-body-study-preparation-ack-v1"
EPOCH_PREFIX = "CLOE_BODY_STUDY_PREPARATION_EPOCH "
EPOCH_ROOT = Path("/sandbox-scratch/body-study/epochs")
MAX_FRAME = 4096
ACK_SECONDS = 120
ACK_POLL_SECONDS = 0.05
# Cover the epoch write and the maximum number of partial ACK reads.
INNER_EPOCH_IO_BYTES = MAX_FRAME + (ceil(ACK_SECONDS / ACK_POLL_SECONDS) + 1) * (MAX_FRAME + 1)
REPRESENTATIONS = ("canonical-node-v1", "canonical-node-public-text-v2")
FIELDS = frozenset({"schema", "attempt_id", "ordinal", "representation", "nonce", "request_attempt_id",
                    "input_pin_sha256", "evaluator_closure_sha256"})
_ATTEMPT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,76}\Z")
_NONCE = re.compile(r"[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ValueError("epoch:json") from None


def _strict_json(raw: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        out: dict[str, object] = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate")
            out[key] = value
        return out
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                       parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    if not isinstance(value, Mapping) or _canonical(value) != raw:
        raise ValueError("epoch:noncanonical")
    return value


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


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
        return _canonical(self.to_dict()) + b"\n"

    @property
    def identity(self) -> str:
        return _digest(self.raw)

    @property
    def relative_directory(self) -> str:
        return f"{self.attempt_id}/{self.ordinal}"


def parse_epoch(value: Mapping[str, Any]) -> PreparationEpoch:
    refuse(isinstance(value, Mapping) and set(value) == FIELDS and value.get("schema") == EPOCH_SCHEMA,
           "body_epoch:closed_schema")
    refuse(type(value["attempt_id"]) is str and _ATTEMPT.fullmatch(value["attempt_id"]) is not None, "body_epoch:attempt")
    ordinal = value["ordinal"]
    refuse(type(ordinal) is int and ordinal in (1, 2), "body_epoch:ordinal")
    refuse(value["representation"] == REPRESENTATIONS[ordinal - 1], "body_epoch:representation")
    refuse(value["request_attempt_id"] == value["attempt_id"] + f"-v{ordinal}", "body_epoch:request_attempt")
    refuse(type(value["nonce"]) is str and _NONCE.fullmatch(value["nonce"]) is not None, "body_epoch:nonce")
    refuse(value["input_pin_sha256"] == BODY_INPUT_PIN_SHA256, "body_epoch:input_pin")
    refuse(type(value["evaluator_closure_sha256"]) is str and _DIGEST.fullmatch(value["evaluator_closure_sha256"]) is not None,
           "body_epoch:evaluator")
    return PreparationEpoch(**{name: value[name] for name in FIELDS if name != "schema"})


def acknowledgement(epoch: PreparationEpoch) -> bytes:
    parse_epoch(epoch.to_dict())
    return _canonical({"schema": ACK_SCHEMA, "epoch_sha256": epoch.identity, "attempt_id": epoch.attempt_id,
                       "ordinal": epoch.ordinal, "nonce": epoch.nonce, "request_attempt_id": epoch.request_attempt_id}) + b"\n"


def _directory(path: Path, *, create: bool = False) -> int:
    refuse(path.is_absolute() and ".." not in path.parts, "body_epoch:directory_absolute")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o700, dir_fd=descriptor)
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor); descriptor = child
        info = os.fstat(descriptor)
        refuse(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
               "body_epoch:directory_custody")
        result, descriptor = descriptor, -1
        return result
    except OSError:
        raise RuntimeError("body_epoch:directory_unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write(directory: int, name: str, raw: bytes, charge: Callable[[int], None]) -> None:
    refuse(0 < len(raw) <= MAX_FRAME, "body_epoch:frame_bounds")
    charge(len(raw))
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.fsync(directory)


def prepare_epoch(*, attempt_id: str, ordinal: int, evaluator_closure_sha256: str,
                  root: Path = EPOCH_ROOT, charge: Callable[[int], None], nonce: str | None = None) -> PreparationEpoch:
    refuse(type(ordinal) is int and ordinal in (1, 2), "body_epoch:ordinal")
    epoch = parse_epoch({"schema": EPOCH_SCHEMA, "attempt_id": attempt_id, "ordinal": ordinal,
        "representation": REPRESENTATIONS[ordinal - 1], "nonce": secrets.token_hex(32) if nonce is None else nonce,
        "request_attempt_id": attempt_id + f"-v{ordinal}", "input_pin_sha256": BODY_INPUT_PIN_SHA256,
        "evaluator_closure_sha256": evaluator_closure_sha256})
    parent_fd = _directory(root, create=True); attempt_fd = epoch_fd = None
    try:
        try:
            os.mkdir(attempt_id, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        attempt_fd = os.open(attempt_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        info = os.fstat(attempt_fd)
        refuse(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, "body_epoch:attempt_custody")
        os.mkdir(str(ordinal), 0o700, dir_fd=attempt_fd)
        epoch_fd = os.open(str(ordinal), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=attempt_fd)
        _write(epoch_fd, "epoch.json", epoch.raw, charge)
    finally:
        for descriptor in (epoch_fd, attempt_fd, parent_fd):
            if descriptor is not None:
                os.close(descriptor)
    print("\n" + EPOCH_PREFIX + _canonical(epoch.to_dict()).decode("utf-8"), flush=True)
    return epoch


def publish_acknowledgement(epoch: PreparationEpoch, *, root: Path = EPOCH_ROOT,
                            charge: Callable[[int], None]) -> None:
    parse_epoch(epoch.to_dict()); directory = _directory(root / epoch.relative_directory); descriptor = None
    try:
        descriptor = os.open("epoch.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        info = os.fstat(descriptor)
        refuse(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
               and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= MAX_FRAME, "body_epoch:published_custody")
        charge(info.st_size + 1); raw = os.read(descriptor, info.st_size + 1); after = os.fstat(descriptor)
        refuse((info.st_ino, info.st_size, info.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns), "body_epoch:published_changed")
        refuse(raw == epoch.raw, "body_epoch:published_join")
        _write(directory, "ack.json", acknowledgement(epoch), charge)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def await_acknowledgement(epoch: PreparationEpoch, *, root: Path = EPOCH_ROOT,
                          charge: Callable[[int], None], require_current: Callable[[], None],
                          clock: Callable[[], float] = time.monotonic,
                          sleep: Callable[[float], None] = time.sleep) -> None:
    parse_epoch(epoch.to_dict()); expected = acknowledgement(epoch); deadline = clock() + ACK_SECONDS
    directory = _directory(root / epoch.relative_directory)
    try:
        require_current()
        while clock() < deadline:
            try:
                descriptor = os.open("ack.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            except FileNotFoundError:
                sleep(ACK_POLL_SECONDS); continue
            try:
                before = os.fstat(descriptor)
                refuse(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and stat.S_IMODE(before.st_mode) == 0o600
                       and before.st_nlink == 1 and 0 <= before.st_size <= MAX_FRAME, "body_epoch:ack_custody")
                charge(before.st_size + 1); raw = os.read(descriptor, before.st_size + 1); after = os.fstat(descriptor)
                stable = (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns)
                if len(raw) < len(expected) or not stable:
                    refuse(expected.startswith(raw), "body_epoch:ack_join"); sleep(ACK_POLL_SECONDS); continue
                refuse(raw == expected, "body_epoch:ack_join"); require_current(); return
            finally:
                os.close(descriptor)
        raise RuntimeError("body_epoch:ack_timeout")
    finally:
        os.close(directory)


def decode_epoch_line(raw: bytes) -> PreparationEpoch:
    prefix = EPOCH_PREFIX.encode()
    refuse(type(raw) is bytes and raw.startswith(prefix) and len(raw) <= MAX_FRAME, "body_epoch:frame")
    value = _strict_json(raw[len(prefix):].strip())
    refuse(isinstance(value, Mapping), "body_epoch:object")
    return parse_epoch(cast(Mapping[str, Any], value))


__all__ = ["ACK_SCHEMA", "BODY_INPUT_PIN_SHA256", "EPOCH_PREFIX", "EPOCH_ROOT",
           "EPOCH_SCHEMA", "MAX_FRAME", "PreparationEpoch", "REPRESENTATIONS", "acknowledgement",
           "await_acknowledgement", "decode_epoch_line", "parse_epoch", "prepare_epoch", "publish_acknowledgement"]
