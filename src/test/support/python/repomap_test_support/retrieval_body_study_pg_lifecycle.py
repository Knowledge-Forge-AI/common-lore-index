"""Body-specific socket-only PostgreSQL lifecycle checks.

This module deliberately owns a separate lifecycle: labels,
database name, cleanup evidence, and the study role are separate domains.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import time
from typing import Any, IO, cast

from retrieval_body_study_values import STUDY_PHASE, require

CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
MAX_COMMAND_OUTPUT_BYTES = 64 * 1024
MAX_READINESS_PROBES = 120
COMMAND_RESERVATION_BYTES = 2 * (MAX_COMMAND_OUTPUT_BYTES + 1)
# The actual recovery path is 129 calls at most; the public envelope retains
# eleven calls for future bounded setup/cleanup joins without becoming open.
BODY_PG_COMMAND_LIMIT = MAX_READINESS_PROBES + 20
MAX_BODY_LIFECYCLE_COMMANDS = BODY_PG_COMMAND_LIMIT
MAX_BODY_LIFECYCLE_RESERVED_BYTES = (
    MAX_BODY_LIFECYCLE_COMMANDS * COMMAND_RESERVATION_BYTES
)
BODY_PG_LABELS = {
    "org.repomap.test": "true",
    "org.repomap.component": "postgres",
    "org.repomap.purpose": "body-study",
    "org.repomap.body.phase": STUDY_PHASE,
}
BODY_POSTGRES_ENV = {
    "POSTGRES_HOST_AUTH_METHOD": "trust",
    "POSTGRES_DB": "repomap_body_study",
    "POSTGRES_USER": "postgres",
    "PGPORT": "55433",
}


@dataclass(frozen=True, slots=True)
class BodyProcessResult:
    returncode: int
    stdout: str
    stderr: str
    measured_bytes: int
    reserved_bytes: int = 0


class BodyProcessError(RuntimeError):
    """A bounded subprocess ended without a trustworthy complete result."""

    def __init__(self, code: str, *, measured_bytes: int = 0,
                 reserved_bytes: int = 0) -> None:
        self.code = code
        self.measured_bytes = measured_bytes
        self.reserved_bytes = reserved_bytes
        super().__init__(code)


def run_bounded_body_command(
    command: Sequence[str], *, timeout: float, max_output_bytes: int = MAX_COMMAND_OUTPUT_BYTES,
    byte_charger: Callable[[int], None] | None = None,
) -> BodyProcessResult:
    """Run one lifecycle command with finite time and per-stream output bounds."""
    require(isinstance(command, (list, tuple)) and all(isinstance(item, str) for item in command),
            "body.pg:command_shape")
    require(type(timeout) in (int, float) and not isinstance(timeout, bool)
            and 0 < timeout <= 120, "body.pg:command_timeout")
    require(type(max_output_bytes) is int and 0 < max_output_bytes <= 1024 * 1024,
            "body.pg:command_output_bound")
    require(byte_charger is None or callable(byte_charger), "body.pg:byte_charger")
    reservation = 2 * (max_output_bytes + 1)
    if byte_charger is not None:
        try:
            byte_charger(reservation)
        except BaseException as error:
            raise BodyProcessError("body.pg:byte_budget") from error
    try:
        process = subprocess.Popen(
            list(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise BodyProcessError("body.pg:command_start", reserved_bytes=reservation) from error
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    measured = 0
    failure: str | None = None
    deadline = time.monotonic() + float(timeout)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "body.pg:command_timeout"
                break
            events = selector.select(min(remaining, 0.25))
            if not events:
                continue
            for key, _ in events:
                stream = cast(IO[bytes], key.fileobj)
                try:
                    remaining = max_output_bytes - len(streams[stream]) + 1
                    chunk = os.read(stream.fileno(), min(8192, remaining))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                measured += len(chunk)
                buffer = streams[stream]
                buffer.extend(chunk)
                if len(buffer) > max_output_bytes:
                    failure = "body.pg:command_output_bound"
                    break
            if failure is not None:
                break
        if failure is not None:
            try:
                process.kill()
            finally:
                process.wait(timeout=5)
            raise BodyProcessError(failure, measured_bytes=measured,
                                   reserved_bytes=reservation)
        process.wait(timeout=max(0.001, deadline - time.monotonic()))
        return BodyProcessResult(
            process.returncode or 0,
            bytes(streams[process.stdout]).decode("utf-8", "replace"),
            bytes(streams[process.stderr]).decode("utf-8", "replace"),
            measured,
            reservation,
        )
    except subprocess.TimeoutExpired as error:
        try:
            process.kill()
        finally:
            process.wait(timeout=5)
        raise BodyProcessError("body.pg:command_timeout", measured_bytes=measured,
                               reserved_bytes=reservation) from error
    except BaseException:
        # A reservation/charger failure is also a process failure.  Do not
        # leave a child alive merely because the caller rejected its bytes.
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, ChildProcessError):
            pass
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def body_socket_dir_identity(path: Path) -> tuple[int, int]:
    """Capture the exact directory identity before handing it to a container."""
    require(path.is_absolute() and path.resolve() == path, "body.pg:socket_path")
    try:
        info = os.lstat(path)
    except OSError as error:
        raise RuntimeError("body.pg:socket_unavailable") from error
    require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o700, "body.pg:socket_custody")
    return (info.st_dev, info.st_ino)


def cleanup_body_socket_dir(
    path: Path, *, identity: tuple[int, int], cleanup: dict[str, Any],
) -> None:
    """Remove only the exact owned directory; mismatches remain unknown."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        cleanup["socket_absent"] = True
        return
    except OSError as error:
        cleanup["socket_cleanup_unknown"] = True
        raise RuntimeError("body.pg:socket_cleanup_unknown") from error
    owned = (
        stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
        and (info.st_dev, info.st_ino) == identity
        and stat.S_IMODE(info.st_mode) == 0o700
        and info.st_uid in {os.getuid(), 999}
    )
    if not owned:
        cleanup["socket_cleanup_unknown"] = True
        raise RuntimeError("body.pg:socket_identity_changed")
    try:
        shutil.rmtree(path)
    except BaseException as error:
        cleanup["socket_cleanup_unknown"] = True
        raise RuntimeError("body.pg:socket_cleanup_failed") from error
    try:
        os.lstat(path)
    except FileNotFoundError:
        cleanup["socket_absent"] = True
        return
    cleanup["socket_cleanup_unknown"] = True
    raise RuntimeError("body.pg:socket_cleanup_incomplete")


def body_postgres_command(sock_dir: Path) -> list[str]:
    require(sock_dir.is_absolute() and sock_dir.resolve() == sock_dir, "body.pg:socket_path")
    return [
        "-c", "listen_addresses=", "-c", "port=55433",
        "-c", f"unix_socket_directories={sock_dir}", "-c", "jit=off",
    ]


def body_create_command(
    runtime_image_id: str, sock_dir: Path, *, labels: Mapping[str, str] | None = None,
    container_name: str | None = None,
) -> list[str]:
    require(type(runtime_image_id) is str and runtime_image_id.startswith("sha256:"), "body.pg:runtime_image")
    require(container_name is None or CONTAINER_NAME.fullmatch(container_name) is not None,
            "body.pg:container_name")
    merged = dict(BODY_PG_LABELS)
    if labels:
        require(not set(labels).intersection(merged), "body.pg:label_collision")
        merged.update(labels)
    command = [
        "docker", "container", "create", "--pull=never", "--network", "none",
        "--user", "999:999", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only", "--memory", "2g", "--cpus", "2", "--pids-limit", "256",
        "--shm-size", "64m", "--tmpfs", "/tmp:rw,mode=1777,size=32m",
        "--tmpfs", "/var/lib/postgresql/data:rw,mode=1777,size=256m",
        "--mount", f"type=bind,src={sock_dir},dst={sock_dir}",
        "--mount", f"type=bind,src={sock_dir},dst=/var/run/postgresql,readonly",
    ]
    if container_name is not None:
        command.extend(("--name", container_name))
    for key, value in sorted(merged.items()):
        command.extend(("--label", f"{key}={value}"))
    for key, value in sorted(BODY_POSTGRES_ENV.items()):
        command.extend(("--env", f"{key}={value}"))
    command.extend(("--env", "PGDATA=/var/lib/postgresql/data/pgdata", runtime_image_id))
    command.extend(body_postgres_command(sock_dir))
    return command


def parse_body_container_id(raw: str) -> str:
    value = raw.strip()
    require(CONTAINER_ID.fullmatch(value) is not None, "body.pg:container_id")
    return value


def validate_body_container_info(
    info: Mapping[str, Any], *, container_id: str, runtime_image_id: str,
    socket_dir: Path, running: bool, container_name: str | None = None,
    expected_labels: Mapping[str, str] | None = None,
) -> None:
    """Validate an already-read owned-container record before cleanup."""
    require(info.get("Id") == container_id and info.get("Image") == runtime_image_id,
            "body.pg:container_identity")
    if container_name is not None:
        require(str(info.get("Name", "")).removeprefix("/") == container_name,
                "body.pg:container_name")
    config, host, state = info.get("Config"), info.get("HostConfig"), info.get("State")
    require(isinstance(config, Mapping) and isinstance(host, Mapping) and isinstance(state, Mapping),
            "body.pg:container_sections")
    assert isinstance(config, Mapping) and isinstance(host, Mapping) and isinstance(state, Mapping)
    labels = config.get("Labels")
    require(isinstance(labels, Mapping), "body.pg:container_labels")
    assert isinstance(labels, Mapping)
    required_labels = dict(BODY_PG_LABELS)
    if expected_labels:
        required_labels.update(expected_labels)
    require(all(labels.get(key) == value for key, value in required_labels.items()),
            "body.pg:container_labels")
    require(config.get("User") == "999:999" and host.get("NetworkMode") == "none"
            and host.get("ReadonlyRootfs") is True, "body.pg:container_containment")
    require(host.get("CapDrop") == ["ALL"]
            and host.get("SecurityOpt") == ["no-new-privileges"],
            "body.pg:container_security")
    require(host.get("Memory") == 2 * 1024**3 and host.get("NanoCpus") == 2_000_000_000
            and host.get("PidsLimit") == 256 and host.get("ShmSize") == 64 * 1024**2,
            "body.pg:container_limits")
    require(not host.get("PortBindings") and host.get("PublishAllPorts") is not True,
            "body.pg:container_network")
    require(host.get("Tmpfs") == {
        "/tmp": "rw,mode=1777,size=32m",
        "/var/lib/postgresql/data": "rw,mode=1777,size=256m",
    }, "body.pg:container_tmpfs")
    require(state.get("Running") is running, "body.pg:container_state")
    mounts = info.get("Mounts")
    require(isinstance(mounts, list), "body.pg:container_mounts")
    assert isinstance(mounts, list)
    binds = [item for item in mounts if isinstance(item, Mapping) and item.get("Type") == "bind"]
    require(len(binds) == 2 and {item.get("Source") for item in binds} == {str(socket_dir)},
            "body.pg:socket_mounts")
    require({item.get("Destination") for item in binds} == {str(socket_dir), "/var/run/postgresql"},
            "body.pg:socket_destinations")


def cleanup_body_absence(cleanup: Mapping[str, Any] | None, prior: bool = False) -> bool:
    """Preserve a proven absence fact; an unknown cleanup never becomes clean."""
    if cleanup is None:
        return prior
    return bool(cleanup.get("absent_after_rm")) or prior


__all__ = [
    "BODY_PG_LABELS", "BODY_POSTGRES_ENV", "BodyProcessError", "BodyProcessResult",
    "BODY_PG_COMMAND_LIMIT", "COMMAND_RESERVATION_BYTES", "CONTAINER_NAME",
    "MAX_BODY_LIFECYCLE_COMMANDS",
    "MAX_BODY_LIFECYCLE_RESERVED_BYTES", "MAX_COMMAND_OUTPUT_BYTES", "MAX_READINESS_PROBES",
    "body_create_command",
    "body_postgres_command", "body_socket_dir_identity", "cleanup_body_absence",
    "cleanup_body_socket_dir", "parse_body_container_id", "run_bounded_body_command",
    "validate_body_container_info",
]
