"""Fixed-exec and private-custody transport for the W5 host exchange."""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Any

from retrieval_semantic_bridge_admission import (
    admission_publish_argv, encode_admission_marker, encode_admission_tar,
)
from retrieval_semantic_bridge_transport import (
    BridgeRefusal, ReadyEvent, encode_response_tar, parse_readiness_line,
    read_request_tar, reply_exec_argv, request_exec_argv,
)
from retrieval_supplement_epoch import PreparationEpoch, parse_epoch

ACK_PREFIX = "CLOE_W5_COMPONENT_ADMISSION_ACK "
ACK_SCHEMA = "cloe-w5-component-admission-ack-v1"
EPOCH_ACK_MAX_BYTES = 4096
_CID = re.compile(r"[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ATTEMPT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")

EPOCH_ACK_REMOTE_SOURCE = r'''import sys
sys.path[:0]=["/workspace/tools"]
from retrieval_supplement_epoch import parse_epoch,publish_acknowledgement
from retrieval_supplement_primitives import strict_json
raw=sys.stdin.buffer.read(4097)
if not 0<len(raw)<=4096: raise SystemExit(2)
epoch=parse_epoch(strict_json(raw))
if raw!=epoch.raw: raise SystemExit(2)
publish_acknowledgement(epoch,charge=lambda count: None)
'''


class HostTransportError(ValueError):
    """Public-safe transport refusal without paths, payloads, or daemon text."""

    def __init__(self, code: str) -> None:
        self.code = code if re.fullmatch(r"[a-z0-9_]+", code) else "host_transport_refused"
        super().__init__(self.code)


def _canonical(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise HostTransportError("json_invalid") from None


def _cid(value: object) -> str:
    if type(value) is not str or _CID.fullmatch(value) is None:
        raise HostTransportError("container_identity")
    return value


def _digest(value: object, code: str = "digest_invalid") -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise HostTransportError(code)
    return value


def epoch_ack_plan(container_id: str, epoch: PreparationEpoch) -> tuple[tuple[str, ...], bytes]:
    """Return fixed SDK code and its closed epoch frame, never a shell command."""
    _cid(container_id)
    try:
        parse_epoch(epoch.to_dict())
    except Exception:
        raise HostTransportError("epoch_invalid") from None
    return (
        ("docker", "exec", "-i", container_id, "/usr/local/bin/python3", "-I", "-B",
         "-c", EPOCH_ACK_REMOTE_SOURCE), epoch.raw,
    )


def _bound_docker(require_outer: Any, argv: tuple[str, ...], payload: bytes | None, limit: int,
                  *, deadline: float | None = None) -> object:
    owner = getattr(require_outer, "__self__", None)
    method = getattr(owner, "_docker_input" if payload is not None else "_docker", None)
    if not callable(method):
        raise HostTransportError("bounded_docker_unavailable")
    try:
        options = {} if deadline is None else {"deadline": deadline}
        if payload is None:
            return method(list(argv), limit, **options)
        return method(list(argv), payload, limit, **options)
    except Exception:
        raise HostTransportError("bounded_docker_failed") from None


def publish_epoch_ack(
    epoch: PreparationEpoch, *, require_outer: Any, charge_bytes: Any,
    charge_operation: Any, deadline: float,
) -> None:
    """Publish the exact epoch ACK through the parent's bounded Docker lease."""
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= time.monotonic():
        raise HostTransportError("epoch_ack_deadline")
    cid = _cid(require_outer())
    argv, payload = epoch_ack_plan(cid, epoch)
    charge_bytes(2 * EPOCH_ACK_MAX_BYTES)
    result = _bound_docker(require_outer, argv, payload, EPOCH_ACK_MAX_BYTES, deadline=deadline)
    if not isinstance(result, bytes) and getattr(result, "returncode", None) != 0:
        raise HostTransportError("epoch_ack_publish_failed")
    if time.monotonic() > deadline:
        raise HostTransportError("epoch_ack_timeout")
    require_outer()


def request_read_plan(container_id: str, ready: ReadyEvent) -> tuple[str, ...]:
    _cid(container_id)
    if type(ready) is not ReadyEvent:
        raise HostTransportError("readiness_invalid")
    try:
        return request_exec_argv(container_id, ready.request_relative_path)
    except Exception:
        raise HostTransportError("request_path") from None


def admission_publish_plan(
    container_id: str, ready: ReadyEvent, admission_raw: bytes,
) -> tuple[tuple[str, ...], bytes]:
    _cid(container_id)
    if type(ready) is not ReadyEvent:
        raise HostTransportError("readiness_invalid")
    try:
        marker = encode_admission_marker(
            admission_raw, nonce=ready.nonce, attempt_id=ready.attempt_id,
            request_sha256=ready.request_sha256,
        )
        return (
            admission_publish_argv(container_id, ready.request_relative_path),
            encode_admission_tar(admission_raw, marker),
        )
    except Exception:
        raise HostTransportError("admission_transport") from None


def response_publish_plan(
    container_id: str, ready: ReadyEvent, response_zip: bytes,
) -> tuple[tuple[str, ...], bytes]:
    _cid(container_id)
    if type(ready) is not ReadyEvent:
        raise HostTransportError("readiness_invalid")
    try:
        return (
            reply_exec_argv(container_id, ready.request_relative_path),
            encode_response_tar(response_zip, ready.nonce),
        )
    except Exception:
        raise HostTransportError("response_transport") from None


def parse_admission_ack_line(
    line: str | bytes, *, ready: ReadyEvent, admission_sha256: str,
) -> None:
    """Accept only the exact request-bound ACK emitted by ComponentExchange."""
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            raise HostTransportError("admission_ack_encoding") from None
    if type(line) is not str or line.endswith("\r"):
        raise HostTransportError("admission_ack_line")
    if line.endswith("\n"):
        line = line[:-1]
    if not line.startswith(ACK_PREFIX):
        raise HostTransportError("admission_ack_prefix")
    try:
        document = json.loads(line[len(ACK_PREFIX):], object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise HostTransportError("admission_ack_json") from None
    expected = {
        "schema": ACK_SCHEMA, "nonce": ready.nonce, "attempt_id": ready.attempt_id,
        "request_sha256": ready.request_sha256, "admission_sha256": _digest(admission_sha256),
    }
    if document != expected or line != ACK_PREFIX + _canonical(expected).decode("ascii"):
        raise HostTransportError("admission_ack_identity")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError("nonfinite")


def decode_request_result(stdout: bytes, ready: ReadyEvent) -> bytes:
    result = read_request_tar(stdout, ready)
    if isinstance(result, BridgeRefusal):
        raise HostTransportError("request_" + result.code)
    if type(result) is not bytes:
        raise HostTransportError("request_transport")
    return result


def write_private_once(path: Path, raw: bytes, *, maximum: int, charge_bytes: Any) -> None:
    """Write one private evidence file atomically, refusing replacement or symlinks."""
    if (
        not isinstance(path, Path) or not path.is_absolute() or "\x00" in str(path)
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise HostTransportError("private_path")
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        raise HostTransportError("private_bounds")
    parent = path.parent
    try:
        info = os.lstat(parent)
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise HostTransportError("private_parent")
    except OSError:
        raise HostTransportError("private_parent") from None
    charge_bytes(len(raw))
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
        checked = os.fstat(descriptor)
        if not stat.S_ISREG(checked.st_mode) or checked.st_size != len(raw) or stat.S_IMODE(checked.st_mode) != 0o600:
            raise HostTransportError("private_custody")
    except FileExistsError:
        raise HostTransportError("private_exists") from None
    except HostTransportError:
        raise
    except (OSError, ValueError):
        raise HostTransportError("private_write") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


__all__ = [
    "ACK_PREFIX", "ACK_SCHEMA", "EPOCH_ACK_REMOTE_SOURCE", "HostTransportError",
    "_bound_docker", "admission_publish_plan", "decode_request_result", "epoch_ack_plan",
    "parse_admission_ack_line", "parse_readiness_line", "request_read_plan",
    "response_publish_plan", "publish_epoch_ack", "write_private_once",
]
