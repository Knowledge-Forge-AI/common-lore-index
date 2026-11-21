"""Closed identities, input checks and metered file helpers for W5 model host."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
from retrieval_public_text_sources import closure_identity

from retrieval_supplement_model_budget import ModelHostRefusal, TIMEOUTS, _Budget

SDK_IMAGE_ID = "sha256:575e0004d8be9a5a8fa73e3301d1e7033280208b9020c1a3155b5d4866598c42"
ARTIFACT_PROFILE_ID = "sha256:033cf41ffc5ca108b6e26638573554bac64b84be744903809904ef3364722180"
ARTIFACT_PIN_ID = "sha256:f0237e38018f3524b87f85f7fff2bef41eda125b8f954d575aaee233541896b6"
WORKER_CLOSURE_ID = "sha256:3ef59123fd9281409d42a8e0f4b02471ec3088c54429033d62cbd6b51af01e23"
PHASE = "CLOE-W5-SUPPLEMENT-STUDY1"
REQUEST_SCHEMA = "cloe-w5-native-generation-request-v1"
ADMISSION_SCHEMA = "cloe-w5-native-generation-admission-v1"
WORKER_SOURCE_FILES = (
    "retrieval_evaluation_attempts.py", "retrieval_semantic_generator_artifacts.py",
    "retrieval_semantic_generator_inputs.py", "retrieval_semantic_generator_kernel.py",
    "retrieval_semantic_generator_kernel_contracts.py", "retrieval_semantic_generator_kernel_runtime_factory.py",
    "retrieval_semantic_generator_kernel_runtime_types.py", "retrieval_semantic_generator_kernel_runtime_validation.py",
    "retrieval_semantic_generator_kernel_validation.py", "retrieval_semantic_generator_output.py",
    "retrieval_semantic_generator_output_parser.py", "retrieval_semantic_generator_output_receipt.py",
    "retrieval_semantic_generator_pins.json", "retrieval_semantic_generator_runtime.py",
    "retrieval_semantic_generator_worker.py", "retrieval_semantic_generator_worker_contracts.py",
    "retrieval_semantic_generator_worker_protocol.py",
)
ARTIFACT_FILES = 30
ARTIFACT_BYTES = 182_037_753
MAX_JSON_BYTES = 8 * 1024**2
MAX_ADMISSION_BYTES = 512 * 1024
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NONCE = re.compile(r"[0-9a-f]{64}\Z")
_ATTEMPT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
_CID = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ValidatedStudyRequest:
    attempt_id: str
    request_sha256: str
    admission_sha256: str
    input_identity: str
    representation_report_identity: str
    publication_identity: str
    artifact_profile_identity: str = ARTIFACT_PROFILE_ID
    worker_closure_sha256: str = WORKER_CLOSURE_ID
    record_count: int = 0
    source_closure: Mapping[str, str] = field(default_factory=dict, repr=False)
    worker_entry_sha256: str | None = None


@dataclass(frozen=True)
class ModelHostPaths:
    request: Path
    admission: Path
    artifact_root: Path
    source_root: Path
    worker_entry: Path
    output_root: Path


@dataclass(frozen=True)
class ModelHostResult:
    status: str
    code: str
    response_bundle: bytes | None = field(default=None, repr=False)
    model_container_id: str | None = field(default=None, repr=False)
    cleanup_proven: bool = False
    primary_code: str | None = None
    cleanup_code: str | None = None


def _digest(value: object, code: str = "digest_invalid") -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ModelHostRefusal(code)
    return value


def _json_object(raw: bytes, maximum: int, code: str) -> dict[str, object]:
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        raise ModelHostRefusal(code + "_bounds")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ModelHostRefusal(code + "_duplicate")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise ModelHostRefusal(code + "_json") from None
    if not isinstance(value, dict):
        raise ModelHostRefusal(code + "_object")
    return value


def _fixed_path(path: Path, *, directory: bool, code: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or "\x00" in str(path) or "," in str(path):
        raise ModelHostRefusal(code)
    current = path
    while True:
        try:
            info = os.lstat(current)
        except OSError:
            raise ModelHostRefusal(code) from None
        if stat.S_ISLNK(info.st_mode):
            raise ModelHostRefusal(code)
        if current == path and ((directory and not stat.S_ISDIR(info.st_mode))
                                or (not directory and not stat.S_ISREG(info.st_mode))):
            raise ModelHostRefusal(code)
        if current.parent == current:
            return path
        current = current.parent


def _read_stable(path: Path, maximum: int, code: str, charge: Callable[[int], None]) -> bytes:
    _fixed_path(path, directory=False, code=code)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise ModelHostRefusal(code) from None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise ModelHostRefusal(code + "_bounds")
        charge(before.st_size)
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        if (len(raw) != before.st_size or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise ModelHostRefusal(code + "_changed")
        return raw
    except OSError:
        raise ModelHostRefusal(code) from None
    finally:
        os.close(fd)


def _source_closure(paths: ModelHostPaths, expected: Mapping[str, str], charge: Callable[[int], None],
                    guard: Callable[[], object] | None = None) -> None:
    if set(expected) != set(WORKER_SOURCE_FILES):
        raise ModelHostRefusal("worker_closure_shape")
    if guard is not None:
        guard()
    _fixed_path(paths.source_root, directory=True, code="worker_source_root")
    try:
        entries = tuple(sorted(item.name for item in paths.source_root.iterdir()))
    except OSError:
        raise ModelHostRefusal("worker_source_root") from None
    if guard is not None:
        guard()
    if set(entries) != set(WORKER_SOURCE_FILES):
        raise ModelHostRefusal("worker_closure_shape")
    if closure_identity("worker", expected) != WORKER_CLOSURE_ID:
        raise ModelHostRefusal("worker_closure_identity")
    for name in WORKER_SOURCE_FILES:
        if guard is not None:
            guard()
        raw = _read_stable(paths.source_root / name, MAX_JSON_BYTES, "worker_source", charge)
        if guard is not None:
            guard()
        if "sha256:" + hashlib.sha256(raw).hexdigest() != expected[name]:
            raise ModelHostRefusal("worker_closure_changed")


def _artifact_bank(root: Path, charge: Callable[[int], None], guard: Callable[[], object] | None = None,
                   verify: Callable[[Path], object] | None = None) -> None:
    _fixed_path(root, directory=True, code="artifact_root")
    files: list[Path] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        if guard is not None:
            guard()
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            raise ModelHostRefusal("artifact_root") from None
        for child in children:
            if guard is not None:
                guard()
            try:
                info = os.lstat(child)
            except OSError:
                raise ModelHostRefusal("artifact_root") from None
            if stat.S_ISLNK(info.st_mode):
                raise ModelHostRefusal("artifact_symlink")
            if stat.S_ISDIR(info.st_mode):
                stack.append(child)
            elif stat.S_ISREG(info.st_mode):
                files.append(child)
            else:
                raise ModelHostRefusal("artifact_member")
    total = sum(path.stat().st_size for path in files)
    if len(files) != ARTIFACT_FILES or total != ARTIFACT_BYTES:
        raise ModelHostRefusal("artifact_bank_identity")
    if not callable(verify):
        raise ModelHostRefusal("artifact_verifier_missing")
    charge(total)
    if guard is not None:
        guard()
    try:
        profile = verify(root)
        source = profile.get("source_identity_sha256") if isinstance(profile, Mapping) else getattr(profile, "source_identity_sha256", None)
        pin = profile.get("pinned_profile_sha256") if isinstance(profile, Mapping) else getattr(profile, "pinned_profile_sha256", None)
        verified = profile.get("verified_files") if isinstance(profile, Mapping) else getattr(profile, "verified_files", None)
    except Exception:
        raise ModelHostRefusal("artifact_verification_failed") from None
    if source != ARTIFACT_PROFILE_ID or pin != ARTIFACT_PIN_ID or not isinstance(verified, (list, tuple)) or len(verified) != ARTIFACT_FILES:
        raise ModelHostRefusal("artifact_profile_identity")
    # The unchanged worker performs two complete pin verifications: bootstrap
    # preflight and run_worker. Both are reserved before the sibling starts.
    charge(total)
    charge(total)
    # Reserve two further complete-bank passes for offline wheel installation
    # and runtime input loading. Native OS/cache I/O remains unmeasured.
    charge(total)
    charge(total)
    if guard is not None:
        guard()

def _merge_labels(info: Mapping[str, object], labels: Mapping[str, str]) -> dict[str, str]:
    config = info.get("Config") if isinstance(info, Mapping) else None; inherited = config.get("Labels") if isinstance(config, Mapping) else {}
    if not isinstance(config, Mapping) or config.get("Volumes") not in (None, {}) or inherited is None or not isinstance(inherited, Mapping): raise ModelHostRefusal("sdk_image_labels")
    result = dict(inherited)
    if any(key in result and result[key] != value for key, value in labels.items()): raise ModelHostRefusal("sdk_image_label_collision")
    result.update(labels); return result


def _bounded_process(argv: tuple[str, ...], timeout: float, limit: int) -> tuple[int, bytes, bytes, bool, bool]:
    """Run one Docker CLI with selector-drained, cap-before-memory streams."""
    if not argv or argv[0] != "docker" or type(timeout) not in (int, float) or timeout <= 0 or type(limit) is not int or limit < 0:
        raise ModelHostRefusal("docker_invocation")
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, close_fds=True)
    except OSError:
        raise ModelHostRefusal("docker_unavailable") from None
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    stdout_fd, stderr_fd = process.stdout.fileno(), process.stderr.fileno()
    streams = {stdout_fd: process.stdout, stderr_fd: process.stderr}
    buffers = {fd: bytearray() for fd in streams}
    for stream in streams.values():
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, stream.fileno())
    deadline = time.monotonic() + float(timeout)
    timed_out = output_limited = False
    try:
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
            process.kill()
            process.wait(timeout=2)
    finally:
        selector.close()
        for stream in streams.values():
            if not stream.closed:
                stream.close()
    return (process.returncode if process.returncode is not None else 124,
            bytes(buffers[stdout_fd]), bytes(buffers[stderr_fd]), timed_out, output_limited)


def _validate_request(raw: bytes, expected: ValidatedStudyRequest) -> dict[str, object]:
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected.request_sha256:
        raise ModelHostRefusal("request_identity")
    document = _json_object(raw, MAX_JSON_BYTES, "request")
    fields = {"schema", "attempt_id", "input_identity", "representation_report_identity",
              "publication_identity", "artifact_profile_identity", "records"}
    if set(document) != fields or document.get("schema") != REQUEST_SCHEMA:
        raise ModelHostRefusal("request_schema")
    if document.get("attempt_id") != expected.attempt_id or _ATTEMPT.fullmatch(expected.attempt_id) is None:
        raise ModelHostRefusal("request_attempt")
    for key in ("input_identity", "representation_report_identity", "publication_identity", "artifact_profile_identity"):
        _digest(document.get(key), "request_identity")
    if any(document[key] != getattr(expected, key) for key in
           ("input_identity", "representation_report_identity", "publication_identity", "artifact_profile_identity")):
        raise ModelHostRefusal("request_identity")
    records = document.get("records")
    if not isinstance(records, list) or not 1 <= len(records) <= 500:
        raise ModelHostRefusal("request_records")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"record_id", "text", "mode"}:
            raise ModelHostRefusal("request_record_schema")
        identifier, text, mode = record["record_id"], record["text"], record["mode"]
        if (type(identifier) is not str or not identifier or identifier in seen
                or type(text) is not str or not text.strip() or mode not in {"document", "query"}):
            raise ModelHostRefusal("request_record_input")
        seen.add(identifier)
    if expected.record_count and len(records) != expected.record_count:
        raise ModelHostRefusal("request_record_count")
    return document


def _validate_admission(raw: bytes, expected: ValidatedStudyRequest, request: Mapping[str, object]) -> None:
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected.admission_sha256:
        raise ModelHostRefusal("admission_identity")
    document = _json_object(raw, MAX_ADMISSION_BYTES, "admission")
    fields = {"schema", "phase", "operator_authority_sha256", "gate_references",
              "bounded_component_execution_admitted", "request_sha256", "input_identity",
              "representation_report_identity", "publication_identity", "artifact_profile_identity",
              "candidate_closure_digests", "scope"}
    if set(document) != fields or document.get("schema") != ADMISSION_SCHEMA:
        raise ModelHostRefusal("admission_schema")
    if document.get("bounded_component_execution_admitted") is not True or document.get("phase") != PHASE:
        raise ModelHostRefusal("admission_scope")
    _digest(document.get("operator_authority_sha256"), "admission_authority")
    _digest(document.get("request_sha256"), "admission_request")
    if document["request_sha256"] != expected.request_sha256:
        raise ModelHostRefusal("admission_request")
    for key in ("input_identity", "representation_report_identity", "publication_identity", "artifact_profile_identity"):
        if document.get(key) != request.get(key):
            raise ModelHostRefusal("admission_request")
    gates = document.get("gate_references")
    if not isinstance(gates, dict) or not gates or any(_DIGEST.fullmatch(str(value)) is None for value in gates.values()):
        raise ModelHostRefusal("admission_gates")
    closure = document.get("candidate_closure_digests")
    if not isinstance(closure, dict) or dict(sorted(closure.items())) != dict(sorted(expected.source_closure.items())):
        raise ModelHostRefusal("admission_closure")
    scope = document.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        raise ModelHostRefusal("admission_scope")

__all__ = ["ADMISSION_SCHEMA", "ARTIFACT_PIN_ID", "ARTIFACT_PROFILE_ID", "ModelHostPaths", "ModelHostRefusal",
           "ModelHostResult", "PHASE", "REQUEST_SCHEMA", "SDK_IMAGE_ID", "TIMEOUTS",
           "ValidatedStudyRequest", "WORKER_CLOSURE_ID", "WORKER_SOURCE_FILES", "_ATTEMPT",
           "_Budget", "_CID", "_NONCE", "_artifact_bank", "_digest", "_fixed_path", "_json_object",
           "_read_stable", "_source_closure", "_validate_admission", "_validate_request"]
