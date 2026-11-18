"""Body-owned boundary around the unchanged semantic-worker wire format."""
from __future__ import annotations
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any
from retrieval_public_text_sources import closure_identity
from retrieval_semantic_generator_worker_contracts import (
    CONTROLLER_ADMISSION_SCHEMA as _GENERIC_ADMISSION_SCHEMA,
    REQUEST_SCHEMA as _GENERIC_REQUEST_SCHEMA, WORKER_SOURCE_FILES,
)
from retrieval_body_study_contracts import (
    BODY_ARTIFACT_FILE_COUNT, BODY_ARTIFACT_PIN_DOCUMENT_SHA256,
    BODY_ARTIFACT_SOURCE_IDENTITY_SHA256, BODY_ARTIFACT_TOTAL_BYTES,
    BODY_INPUT_PIN_SHA256, BODY_PHASE, BODY_ROLE, BODY_SDK_IMAGE_SHA256,
    BODY_WORKER_CLOSURE_SHA256, BODY_WORKER_MEMORY_BYTES, BODY_WORKER_SCRATCH_BYTES,
    BODY_WORKER_TMP_BYTES, V1_DOCUMENTS, V1_QUERIES, V2_DOCUMENTS, V2_QUERIES,
)
SDK_IMAGE_ID = BODY_SDK_IMAGE_SHA256
ARTIFACT_PROFILE_ID = BODY_ARTIFACT_SOURCE_IDENTITY_SHA256
ARTIFACT_PIN_ID = BODY_ARTIFACT_PIN_DOCUMENT_SHA256
WORKER_CLOSURE_ID = BODY_WORKER_CLOSURE_SHA256
ARTIFACT_FILES = BODY_ARTIFACT_FILE_COUNT
ARTIFACT_BYTES = BODY_ARTIFACT_TOTAL_BYTES
MAX_JSON_BYTES = 8 * 1024**2
MAX_ADMISSION_BYTES = 512 * 1024
REQUEST_SCHEMA = _GENERIC_REQUEST_SCHEMA
ADMISSION_SCHEMA = _GENERIC_ADMISSION_SCHEMA
TIMEOUTS = {"initialization": 600.0, "readiness": 120.0, "model": 550.0,
            "admission": 120.0, "response": 600.0, "aggregate": 1700.0}
MODEL_MEMORY = BODY_WORKER_MEMORY_BYTES
MODEL_SCRATCH = BODY_WORKER_SCRATCH_BYTES
MODEL_TMP = BODY_WORKER_TMP_BYTES
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ATTEMPT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
_CID = re.compile(r"[0-9a-f]{64}\Z")
class ModelHostRefusal(RuntimeError):
    """Stable public-safe refusal without paths, payloads or daemon text."""
    def __init__(self, code: str) -> None:
        self.code = code if re.fullmatch(r"[a-z0-9_]+", code) else "model_host_refused"
        super().__init__(self.code)
class _Budget:
    def __init__(self, clock: Callable[[], float], epoch_started: float,
                 response_deadline: float) -> None:
        now = clock()
        if (type(epoch_started) not in (int, float) or not math.isfinite(epoch_started)
                or epoch_started > now or type(response_deadline) not in (int, float)
                or not math.isfinite(response_deadline) or response_deadline <= now):
            raise ModelHostRefusal("epoch_invalid")
        self.clock, self.epoch_started = clock, epoch_started
        self.response_deadline = response_deadline
        self.aggregate_deadline = epoch_started + TIMEOUTS["aggregate"]
        self.phase_deadline = self.aggregate_deadline
        self.phase_name = "aggregate"
        self.phase_deadlines: dict[str, float] = {}
    def begin(self, name: str) -> None:
        if name not in TIMEOUTS or name == "aggregate":
            raise ModelHostRefusal("phase_unknown")
        self.phase_name = name
        if name == "initialization":
            self.phase_deadline = self.epoch_started + TIMEOUTS[name]
        else:
            self.phase_deadline = min(self.clock() + TIMEOUTS[name], self.response_deadline,
                                      self.aggregate_deadline)
        self.phase_deadlines[name] = self.phase_deadline
        self.check()
    def resume(self, name: str) -> None:
        if name not in self.phase_deadlines:
            raise ModelHostRefusal("phase_unknown")
        self.phase_name, self.phase_deadline = name, self.phase_deadlines[name]
        self.check()
    def check(self) -> None:
        now = self.clock()
        if now >= self.response_deadline:
            raise ModelHostRefusal("response_timeout")
        if now >= self.aggregate_deadline:
            raise ModelHostRefusal("aggregate_timeout")
        if now >= self.phase_deadline:
            raise ModelHostRefusal(self.phase_name + "_timeout")
    def remaining(self) -> float:
        self.check()
        return min(self.response_deadline, self.aggregate_deadline, self.phase_deadline) - self.clock()
@dataclass(frozen=True)
class ValidatedStudyRequest:
    attempt_id: str
    request_sha256: str
    admission_sha256: str
    input_identity: str
    representation_report_identity: str
    publication_identity: str
    representation: str
    document_count: int
    query_count: int
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
def query_manifest_identity(manifest: object) -> str:
    try:
        raw = json.dumps({"schema": "cloe-w5-body-query-manifest-v1", "rows": manifest}, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ModelHostRefusal("body_request_query_manifest") from None
    return "sha256:" + hashlib.sha256(raw).hexdigest()
def _json_object(raw: bytes, maximum: int, code: str) -> dict[str, object]:
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        raise ModelHostRefusal(code + "_bounds")
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        out: dict[str, object] = {}
        for key, value in items:
            if key in out:
                raise ModelHostRefusal(code + "_duplicate")
            out[key] = value
        return out
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
        if (len(raw) != before.st_size or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
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
    if closure_identity("worker", expected) != WORKER_CLOSURE_ID:
        raise ModelHostRefusal("worker_closure_identity")
    _fixed_path(paths.source_root, directory=True, code="worker_source_root")
    try:
        entries = tuple(sorted(item.name for item in paths.source_root.iterdir()))
    except OSError:
        raise ModelHostRefusal("worker_source_root") from None
    if set(entries) != set(WORKER_SOURCE_FILES):
        raise ModelHostRefusal("worker_closure_shape")
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
    if len(files) != ARTIFACT_FILES or sum(path.stat().st_size for path in files) != ARTIFACT_BYTES:
        raise ModelHostRefusal("artifact_bank_identity")
    if not callable(verify):
        raise ModelHostRefusal("artifact_verifier_missing")
    charge(ARTIFACT_BYTES)
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
    for _ in range(4):
        charge(ARTIFACT_BYTES)
        if guard is not None:
            guard()
def _merge_labels(info: Mapping[str, object], labels: Mapping[str, str]) -> dict[str, str]:
    config = info.get("Config")
    inherited = config.get("Labels") if isinstance(config, Mapping) else None
    if not isinstance(config, Mapping) or config.get("Volumes") not in (None, {}) or not isinstance(inherited, Mapping):
        raise ModelHostRefusal("sdk_image_labels")
    result = dict(inherited)
    if any(key in result and result[key] != value for key, value in labels.items()):
        raise ModelHostRefusal("sdk_image_label_collision")
    result.update(labels)
    return result
def validate_body_model_request(request: Mapping[str, Any]) -> dict[str, Any]:
    required = {"phase", "role", "ordinal", "attempt_id", "request_attempt_id", "representation", "document_count", "query_count", "total_record_count", "input_identity", "representation_report_identity", "publication_identity", "query_task_ids", "query_manifest", "query_manifest_sha256", "backend", "generation", "input_pin_sha256", "execution_authority_granted", "model_executed"}
    if not isinstance(request, Mapping) or not required <= set(request) or not set(request) <= required | {"public_text_identity", "wire_request", "wire"}:
        raise ModelHostRefusal("body_request_descriptor")
    if request["phase"] != BODY_PHASE: raise ModelHostRefusal("body_request_phase")
    if request["role"] != BODY_ROLE: raise ModelHostRefusal("body_request_role")
    expected = {"canonical-node-v1": (1, V1_DOCUMENTS, V1_QUERIES), "canonical-node-public-text-v2": (2, V2_DOCUMENTS, V2_QUERIES)}
    if request["representation"] not in expected:
        raise ModelHostRefusal("body_request_representation")
    ordinal, documents, queries = expected[request["representation"]]
    if request["ordinal"] != ordinal or request["document_count"] != documents or request["query_count"] != queries:
        raise ModelHostRefusal("body_request_census")
    if request["total_record_count"] != documents + queries or documents > 80 or queries > 24:
        raise ModelHostRefusal("body_request_total")
    for name in ("attempt_id", "request_attempt_id"):
        if type(request[name]) is not str or _ATTEMPT.fullmatch(request[name]) is None:
            raise ModelHostRefusal("body_request_attempt")
    if request["request_attempt_id"] != request["attempt_id"] + f"-v{ordinal}":
        raise ModelHostRefusal("body_request_attempt_join")
    for name in ("input_identity", "representation_report_identity", "publication_identity", "query_manifest_sha256", "input_pin_sha256"):
        _digest(request[name], "body_request_identity")
    if request["input_pin_sha256"] != BODY_INPUT_PIN_SHA256: raise ModelHostRefusal("body_request_input_pin")
    if request["backend"] != "postgresql" or type(request["generation"]) is not int or request["generation"] < 1:
        raise ModelHostRefusal("body_request_publication")
    if request["representation"] == "canonical-node-public-text-v2" and "public_text_identity" not in request: raise ModelHostRefusal("body_request_public_text")
    ids = request["query_task_ids"]
    manifest = request["query_manifest"]
    if not isinstance(ids, (list, tuple)) or any(type(item) is not str or not item or item.startswith("query:") for item in ids) or len(ids) != queries or len(set(ids)) != queries:
        raise ModelHostRefusal("body_request_query_ids")
    if not isinstance(manifest, (list, tuple)) or len(manifest) != queries or query_manifest_identity(manifest) != request["query_manifest_sha256"]:
        raise ModelHostRefusal("body_request_query_manifest")
    manifest_ids: list[str] = []
    for row in manifest:
        if not isinstance(row, Mapping) or set(row) != {"record_id", "text_sha256"}:
            raise ModelHostRefusal("body_request_query_manifest")
        record_id = row["record_id"]
        if type(record_id) is not str or not record_id.startswith("query:") or not record_id[6:]:
            raise ModelHostRefusal("body_request_query_manifest")
        _digest(row["text_sha256"], "body_request_query_text")
        manifest_ids.append(record_id[6:])
    if tuple(manifest_ids) != tuple(ids):
        raise ModelHostRefusal("body_request_query_join")
    if "public_text_identity" in request:
        _digest(request["public_text_identity"], "body_request_public_text")
    if request["execution_authority_granted"] is not False or request["model_executed"] is not False: raise ModelHostRefusal("body_request_execution_claim")
    for name in ("wire_request", "wire"):
        if name in request and not isinstance(request[name], Mapping): raise ModelHostRefusal("body_request_wire")
    if "wire_request" in request and "wire" in request: raise ModelHostRefusal("body_request_wire")
    return dict(request)
def validate_body_model_admission(admission: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(admission, Mapping) or admission.get("phase") != BODY_PHASE:
        raise ModelHostRefusal("body_admission_phase")
    if admission.get("role", BODY_ROLE) != BODY_ROLE or admission.get("representation") not in (None, request.get("representation")):
        raise ModelHostRefusal("body_admission_join")
    for field_name in ("request_sha256", "input_identity", "publication_identity", "epoch_sha256"):
        if field_name in admission:
            _digest(admission[field_name], "body_admission_identity")
    return dict(admission)
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
        if document[key] != getattr(expected, key):
            raise ModelHostRefusal("request_identity")
    records = document.get("records")
    if not isinstance(records, list) or len(records) != expected.record_count:
        raise ModelHostRefusal("request_records")
    modes = [record.get("mode") if isinstance(record, dict) else None for record in records]
    if any(mode not in {"document", "query"} for mode in modes):
        raise ModelHostRefusal("request_record_input")
    if modes.count("document") != expected.document_count or modes.count("query") != expected.query_count:
        raise ModelHostRefusal("request_record_count")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"record_id", "text", "mode"}:
            raise ModelHostRefusal("request_record_schema")
        identifier, text_value = record["record_id"], record["text"]
        if type(identifier) is not str or not identifier or identifier in seen or type(text_value) is not str or not text_value.strip():
            raise ModelHostRefusal("request_record_input")
        seen.add(identifier)
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
    if document.get("bounded_component_execution_admitted") is not True or document.get("phase") != BODY_PHASE:
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
    if not isinstance(scope, str) or not scope.startswith(BODY_PHASE + " "):
        raise ModelHostRefusal("admission_scope")
__all__ = ["ADMISSION_SCHEMA", "ARTIFACT_PIN_ID", "ARTIFACT_PROFILE_ID", "BODY_INPUT_PIN_SHA256", "BODY_PHASE", "BODY_ROLE", "MAX_ADMISSION_BYTES", "MAX_JSON_BYTES", "MODEL_MEMORY", "MODEL_SCRATCH", "MODEL_TMP", "ModelHostPaths", "ModelHostRefusal", "ModelHostResult", "REQUEST_SCHEMA", "SDK_IMAGE_ID", "TIMEOUTS", "ValidatedStudyRequest", "WORKER_CLOSURE_ID", "WORKER_SOURCE_FILES", "_ATTEMPT", "_Budget", "_CID", "_DIGEST", "_artifact_bank", "_digest", "_fixed_path", "_json_object", "_merge_labels", "_read_stable", "_source_closure", "_validate_admission", "_validate_request", "query_manifest_identity", "validate_body_model_admission", "validate_body_model_request"]
