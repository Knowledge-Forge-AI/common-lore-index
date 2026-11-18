"""Real Docker sibling lifecycle for one body-study model exchange."""
from __future__ import annotations
from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time
from typing import IO

from retrieval_body_study_contracts import BODY_MODEL_POLL_SECONDS, BODY_PHASE, BODY_ROLE
from retrieval_body_study_model_contract import (
    MODEL_MEMORY, SDK_IMAGE_ID, WORKER_CLOSURE_ID, ModelHostPaths, ModelHostRefusal, ModelHostResult,
    ValidatedStudyRequest, _Budget,
    _artifact_bank, _fixed_path, _merge_labels, _read_stable, _source_closure, _validate_admission,
    _validate_request,
)
from retrieval_body_study_model_response import MAX_RESPONSE_BYTES, _response_bundle

_CID = re.compile(r"[0-9a-f]{64}\Z")
MODEL_LEASE_POLL_SECONDS = BODY_MODEL_POLL_SECONDS


class _DockerResult:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes,
                 timed_out: bool = False, output_limited: bool = False) -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
        self.timed_out, self.output_limited = timed_out, output_limited


def _mount(source: Path, destination: str, readonly: bool) -> str:
    text = str(source)
    if not text.startswith("/") or "," in text or "\x00" in text:
        raise ModelHostRefusal("mount_path")
    return f"type=bind,src={text},dst={destination}{',readonly' if readonly else ''}"


def _model_command(paths: ModelHostPaths, admission_sha256: str,
                   labels: Mapping[str, str], name: str) -> tuple[str, ...]:
    uidgid = f"{os.getuid()}:{os.getgid()}"
    command: list[str] = ["docker", "create", "--pull=never", "--name", name, "--network=none", "--read-only",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit", "64", "--memory", "2g",
        "--shm-size", "64m",
        "--cpus", "1", "--user", uidgid, "--tmpfs", "/scratch:rw,exec,nosuid,nodev,size=768m,mode=1777",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777", "--mount",
        _mount(paths.artifact_root, "/artifacts", True), "--mount", _mount(paths.request, "/request.json", True),
        "--mount", _mount(paths.admission, "/admission.json", True), "--mount",
        _mount(paths.source_root, "/worker-ro", True), "--mount",
        _mount(paths.worker_entry, "/bridge-ro/w5_bridge_worker.py", True), "--mount",
        _mount(paths.output_root, "/output", False)]
    for key, value in sorted(labels.items()):
        command.extend(("--label", f"{key}={value}"))
    command.extend(("--env", "LC_ALL=C", "--env", "TOKENIZERS_PARALLELISM=false", "--env", "OMP_NUM_THREADS=1",
        "--env", "OPENBLAS_NUM_THREADS=1", "--env", "HF_HUB_OFFLINE=1", "--env", "HF_HUB_DISABLE_TELEMETRY=1",
        "--entrypoint", "python3", SDK_IMAGE_ID, "-I", "-B", "-S", "/bridge-ro/w5_bridge_worker.py",
        "--request", "/request.json", "--artifacts", "/artifacts", "--output", "/output", "--admission",
        "/admission.json", "--admission-sha256", admission_sha256))
    return tuple(command)


def _settle_owned_process(process: subprocess.Popen[bytes]) -> None:
    """Kill, reap, and prove absence of this exact Docker CLI child."""
    try:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired as first:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except BaseException as second:
                raise ModelHostRefusal("docker_process_cleanup_unknown") from second
            if process.poll() is None:
                raise ModelHostRefusal("docker_process_cleanup_unknown") from first
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        if process.poll() is None:
            raise ModelHostRefusal("docker_process_cleanup_unknown")
    except ModelHostRefusal:
        raise
    except BaseException as error:
        raise ModelHostRefusal("docker_process_cleanup_unknown") from error


def _bounded_process(argv: tuple[str, ...], timeout: float, limit: int) -> tuple[int, bytes, bytes, bool, bool]:
    if not argv or argv[0] != "docker" or type(timeout) not in (int, float) or timeout <= 0 or type(limit) is not int or limit < 0:
        raise ModelHostRefusal("docker_invocation")
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, close_fds=True)
    except OSError:
        raise ModelHostRefusal("docker_unavailable") from None
    selector = None
    streams: dict[int, IO[bytes]] = {}
    buffers: dict[int, bytearray] = {}
    settled = False
    timed_out = output_limited = False
    result: tuple[int, bytes, bytes, bool, bool] | None = None
    try:
        assert process.stdout is not None and process.stderr is not None
        stdout_fd, stderr_fd = process.stdout.fileno(), process.stderr.fileno()
        selector = selectors.DefaultSelector()
        streams = {stdout_fd: process.stdout, stderr_fd: process.stderr}
        buffers = {fd: bytearray() for fd in streams}
        for stream in streams.values():
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, stream.fileno())
        deadline = time.monotonic() + float(timeout)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                process.kill()
                break
            for key, _events in selector.select(min(remaining, 0.1)):
                fd = key.data
                try:
                    chunk = os.read(fd, min(64 * 1024, limit + 1 - len(buffers[fd])))
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(streams[fd])
                    streams[fd].close()
                    continue
                if len(buffers[fd]) + len(chunk) > limit:
                    output_limited = True
                    process.kill()
                    break
                buffers[fd].extend(chunk)
            if timed_out or output_limited:
                break
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _settle_owned_process(process)
        settled = process.poll() is not None
        if not settled:
            _settle_owned_process(process)
            settled = True
        result = (process.returncode if process.returncode is not None else 124,
                  bytes(buffers[stdout_fd]), bytes(buffers[stderr_fd]), timed_out, output_limited)
    finally:
        try:
            if not settled:
                _settle_owned_process(process)
        finally:
            if selector is not None:
                selector.close()
            for stream in streams.values():
                if not stream.closed:
                    stream.close()
    assert result is not None
    return result


def _bounded_docker(argv: tuple[str, ...], timeout: float, limit: int,
                    charge_operation: Callable[[str], None], charge_bytes: Callable[[int], None]) -> _DockerResult:
    if not argv or argv[0] != "docker" or any(type(arg) is not str or not arg for arg in argv):
        raise ModelHostRefusal("docker_invocation")
    charge_operation("docker." + (argv[1] if len(argv) > 1 else "unknown"))
    charge_bytes(2 * (limit + 1) + 65536)
    result = _DockerResult(*_bounded_process(argv, timeout, limit))
    if result.timed_out:
        raise ModelHostRefusal("docker_timeout")
    if result.output_limited:
        raise ModelHostRefusal("docker_output_bounds")
    return result


def _inspect(raw: bytes) -> Mapping[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ModelHostRefusal("docker_inspection") from None
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
        raise ModelHostRefusal("docker_inspection")
    return value[0]


def _verify_model(info: Mapping[str, object], cid: str, command: tuple[str, ...],
                  labels: Mapping[str, str], name: str, paths: ModelHostPaths) -> None:
    if info.get("Id") != cid or info.get("Image") != SDK_IMAGE_ID or info.get("Name") not in {name, "/" + name}:
        raise ModelHostRefusal("model_identity")
    config, host, mounts, state = info.get("Config"), info.get("HostConfig"), info.get("Mounts"), info.get("State")
    if not isinstance(config, Mapping) or not isinstance(host, Mapping) or not isinstance(mounts, list) or not isinstance(state, Mapping) or type(state.get("Running")) is not bool:
        raise ModelHostRefusal("model_inspection")
    if info.get("Path") != "python3" or config.get("Path") not in {None, "python3"} or config.get("Entrypoint") != ["python3"]:
        raise ModelHostRefusal("model_command")
    if config.get("Labels") != dict(labels) or host.get("NetworkMode") != "none" or host.get("ReadonlyRootfs") is not True:
        raise ModelHostRefusal("model_containment")
    if (config.get("User") != f"{os.getuid()}:{os.getgid()}" or host.get("Privileged") is not False
            or host.get("Memory") != MODEL_MEMORY or host.get("NanoCpus") != 1_000_000_000
            or host.get("PidsLimit") != 64 or host.get("ShmSize") != 64 * 1024**2
            or host.get("CapDrop") != ["ALL"]
            or host.get("SecurityOpt") != ["no-new-privileges"]):
        raise ModelHostRefusal("model_limits")
    image_index = command.index(SDK_IMAGE_ID)
    if config.get("Cmd") != list(command[image_index + 1:]):
        raise ModelHostRefusal("model_command")
    expected_sources = {"/artifacts": (str(paths.artifact_root), False), "/request.json": (str(paths.request), False),
        "/admission.json": (str(paths.admission), False), "/worker-ro": (str(paths.source_root), False),
        "/bridge-ro/w5_bridge_worker.py": (str(paths.worker_entry), False), "/output": (str(paths.output_root), True)}
    seen: set[str] = set(); tmpfs: set[str] = set()
    for mount in mounts:
        if not isinstance(mount, Mapping):
            raise ModelHostRefusal("model_mounts")
        destination = mount.get("Destination")
        if mount.get("Type") == "bind":
            expected = expected_sources.get(destination) if isinstance(destination, str) else None
            if destination in seen or expected is None or mount.get("Source") != expected[0] or mount.get("RW") is not expected[1]:
                raise ModelHostRefusal("model_mounts")
            seen.add(str(destination))
        elif mount.get("Type") == "tmpfs":
            if destination not in {"/scratch", "/tmp"} or destination in tmpfs or mount.get("RW") is not True:
                raise ModelHostRefusal("model_tmpfs")
            tmpfs.add(str(destination))
        else:
            raise ModelHostRefusal("model_mounts")
    if seen != set(expected_sources) or tmpfs not in (set(), {"/scratch", "/tmp"}) or (not tmpfs and state["Running"] is not False):
        raise ModelHostRefusal("model_mounts")
    configured = host.get("Tmpfs")
    if not isinstance(configured, Mapping) or set(configured) != {"/scratch", "/tmp"}:
        raise ModelHostRefusal("model_tmpfs")
    expected_tmpfs = {"/scratch": {"rw", "exec", "nosuid", "nodev", "size=768m", "mode=1777"},
                      "/tmp": {"rw", "noexec", "nosuid", "nodev", "size=64m", "mode=1777"}}
    for destination, tokens in expected_tmpfs.items():
        options = configured[destination]
        if type(options) is not str or set(options.split(",")) != tokens:
            raise ModelHostRefusal("model_tmpfs")


def run_model_sibling(request: ValidatedStudyRequest, paths: ModelHostPaths, *, nonce: str,
                      require_outer: Callable[[], str], charge_bytes: Callable[[int], None],
                      verify_artifacts: Callable[[Path], object], charge_operation: Callable[[str], None],
                      epoch_started: float, response_deadline: float,
                      clock: Callable[[], float] = time.monotonic,
                      sleep: Callable[[float], None] = time.sleep,
                      charge_docker_bytes: Callable[[int], None] | None = None) -> ModelHostResult:
    if not isinstance(request, ValidatedStudyRequest) or not isinstance(paths, ModelHostPaths):
        raise ModelHostRefusal("model_host_input")
    if (not callable(require_outer) or not callable(charge_bytes) or not callable(charge_operation)
            or not callable(verify_artifacts)):
        raise ModelHostRefusal("model_host_callbacks")
    if request.worker_closure_sha256 != WORKER_CLOSURE_ID or request.representation not in {"canonical-node-v1", "canonical-node-public-text-v2"} or not re.fullmatch(r"[0-9a-f]{64}\Z", nonce):
        raise ModelHostRefusal("model_identity")
    directories = {paths.artifact_root, paths.source_root, paths.output_root}
    for path in (paths.request, paths.admission, paths.artifact_root, paths.source_root, paths.worker_entry, paths.output_root):
        _fixed_path(path, directory=path in directories, code="model_path")
    budget = _Budget(clock, epoch_started, response_deadline)
    model_cid: str | None = None; model_owned = False; creation_uncertain = False
    process_cleanup_uncertain = False
    primary: str | None = None; response: bytes | None = None
    labels = {"repomap.phase": BODY_PHASE, "repomap.role": BODY_ROLE, "repomap.component": "model-sibling",
              "repomap.attempt": request.attempt_id, "repomap.nonce": nonce, "repomap.owner": "body-study-host-controller",
              "repomap.request": request.request_sha256, "repomap.admission": request.admission_sha256,
              "repomap.artifact-profile": request.artifact_profile_identity,
              "repomap.worker-closure": request.worker_closure_sha256,
              "repomap.representation": request.representation}
    name = "repomap-body-model-" + nonce
    outer_cid: str | None = None

    def guard() -> str:
        nonlocal outer_cid
        budget.check()
        try:
            current = require_outer()
        except Exception:
            raise ModelHostRefusal("outer_authority_lost") from None
        budget.check()
        if type(current) is not str or _CID.fullmatch(current) is None:
            raise ModelHostRefusal("outer_attestation_invalid")
        if outer_cid is None:
            outer_cid = current
        elif current != outer_cid:
            raise ModelHostRefusal("outer_authority_changed")
        return current

    def docker(argv: tuple[str, ...], *, timeout: float = 30.0, limit: int = 64 * 1024,
               outer_required: bool = True, postcheck: bool = True) -> _DockerResult:
        nonlocal creation_uncertain
        if outer_required:
            guard(); timeout = min(timeout, budget.remaining())
        if len(argv) > 1 and argv[1] == "create":
            creation_uncertain = True
        result = _bounded_docker(argv, timeout, limit, charge_operation, charge_docker_bytes or charge_bytes)
        if outer_required and postcheck:
            guard()
        return result

    cleanup_code: str | None = None
    try:
        try:
            budget.begin("initialization"); guard(); _source_closure(paths, request.source_closure, charge_bytes, guard)
            _artifact_bank(paths.artifact_root, charge_bytes, guard, verify_artifacts); guard()
            entry_raw = _read_stable(paths.worker_entry, 8 * 1024**2, "worker_entry", charge_bytes); guard()
            if not entry_raw or request.worker_entry_sha256 != "sha256:" + hashlib.sha256(entry_raw).hexdigest():
                raise ModelHostRefusal("worker_entry_identity")
            request_raw = _read_stable(paths.request, MAX_RESPONSE_BYTES, "request_file", charge_bytes); guard()
            request_document = _validate_request(request_raw, request)
            budget.begin("admission"); guard()
            admission_raw = _read_stable(paths.admission, 512 * 1024, "admission_file", charge_bytes); guard()
            _validate_admission(admission_raw, request, request_document); budget.resume("initialization")
            image = _inspect(docker(("docker", "image", "inspect", SDK_IMAGE_ID), limit=256 * 1024).stdout)
            if image.get("Id") != SDK_IMAGE_ID or image.get("Architecture") not in {"arm64", "aarch64"}:
                raise ModelHostRefusal("sdk_image_identity")
            labels = _merge_labels(image, labels)
            command = _model_command(paths, request.admission_sha256, labels, name)
            created = docker(command, timeout=30, limit=4096, postcheck=False)
            try:
                created_text = created.stdout.decode("ascii").strip()
            except UnicodeDecodeError:
                raise ModelHostRefusal("model_container_identity") from None
            if created.returncode != 0 or _CID.fullmatch(created_text) is None:
                raise ModelHostRefusal("model_create_failed")
            model_cid, model_owned, creation_uncertain = created_text, True, False; guard()
            _verify_model(_inspect(docker(("docker", "inspect", model_cid), limit=512 * 1024).stdout), model_cid, command, labels, name, paths)
            budget.begin("readiness")
            if docker(("docker", "start", model_cid), timeout=30, limit=4096).returncode != 0:
                raise ModelHostRefusal("model_start_failed")
            state = _inspect(docker(("docker", "inspect", model_cid), limit=512 * 1024).stdout).get("State")
            if not isinstance(state, Mapping) or state.get("Running") is not True:
                raise ModelHostRefusal("model_not_ready")
            budget.begin("model")
            while True:
                state = _inspect(docker(("docker", "inspect", model_cid), limit=512 * 1024).stdout).get("State")
                if not isinstance(state, Mapping) or type(state.get("Running")) is not bool:
                    raise ModelHostRefusal("model_inspection")
                if state["Running"] is False:
                    break
                sleep(min(MODEL_LEASE_POLL_SECONDS, budget.remaining()))
            waited = docker(("docker", "wait", model_cid), timeout=30, limit=4096); budget.check()
            if waited.returncode != 0:
                raise ModelHostRefusal("model_wait_failed")
            try:
                exit_code = int(waited.stdout.decode("ascii").strip())
            except (UnicodeDecodeError, ValueError):
                raise ModelHostRefusal("model_exit_identity") from None
            if exit_code != 0:
                raise ModelHostRefusal("model_execution_failed")
            budget.begin("response"); guard()
            response = _response_bundle(paths, request, request_raw, admission_raw, charge_bytes, guard); guard()
        except ModelHostRefusal as exc:
            primary = exc.code
            process_cleanup_uncertain = exc.code == "docker_process_cleanup_unknown"
        except Exception:
            primary = "model_host_refused"
        except BaseException as exc:
            code = getattr(exc, "code", None)
            if code == "docker_process_cleanup_unknown": process_cleanup_uncertain = True
            primary = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]+", code) else "model_base_exception"
    finally:
        cleanup_code = "creation_unknown" if creation_uncertain else None
        if model_cid is not None and model_owned:
            try:
                info = _inspect(docker(("docker", "inspect", model_cid), limit=512 * 1024, outer_required=False).stdout)
                _verify_model(info, model_cid, _model_command(paths, request.admission_sha256, labels, name), labels, name, paths)
                state = info.get("State")
                if isinstance(state, Mapping) and state.get("Running") is True:
                    if docker(("docker", "stop", "--time", "10", model_cid), timeout=30, limit=4096, outer_required=False).returncode != 0:
                        raise ModelHostRefusal("model_cleanup_failed")
                if docker(("docker", "rm", model_cid), timeout=30, limit=4096, outer_required=False).returncode != 0:
                    raise ModelHostRefusal("model_cleanup_failed")
                probe = docker(("docker", "container", "inspect", model_cid), timeout=30, limit=4096, outer_required=False)
                if probe.returncode == 0 or b"No such container" not in probe.stderr:
                    raise ModelHostRefusal("model_cleanup_residual")
            except BaseException as exc:
                if getattr(exc, "code", None) == "docker_process_cleanup_unknown":
                    process_cleanup_uncertain = True
                cleanup_code = "cleanup_failed"
        if process_cleanup_uncertain:
            cleanup_code = "docker_process_cleanup_unknown"
    if primary is None:
        try:
            budget.check()
        except ModelHostRefusal as exc:
            primary = exc.code
    if cleanup_code is not None:
        return ModelHostResult("failed", cleanup_code, response_bundle=response, model_container_id=model_cid,
                               cleanup_proven=False, primary_code=primary, cleanup_code=cleanup_code)
    if primary is not None:
        return ModelHostResult("failed", primary, response_bundle=response, model_container_id=model_cid,
                               cleanup_proven=not process_cleanup_uncertain and (model_cid is None or model_owned),
                               primary_code=primary)
    return ModelHostResult("completed", "completed", response_bundle=response, model_container_id=model_cid, cleanup_proven=True)


__all__ = ["ModelHostPaths", "ModelHostRefusal", "ModelHostResult", "BODY_PHASE", "SDK_IMAGE_ID", "ValidatedStudyRequest", "run_model_sibling"]
