"""Source-bound host-side request/admission/model/response exchange for W5."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
import os
from pathlib import Path
import re
import stat
import time
from retrieval_public_text_sources import closure_identity
from typing import Any

from retrieval_semantic_generator_worker_protocol import (
    WorkerRequestError, parse_controller_admission, parse_worker_request,
)
from retrieval_semantic_bridge_transport import (
    BridgeRefusal, ReadyEvent, READY_PREFIX, REQUEST_MAX_BYTES, RESPONSE_MAX_BYTES,
    decode_response_bundle, parse_readiness_line,
)
from retrieval_semantic_generator_output_parser import parse_verified_worker_response
from retrieval_supplement_epoch import PreparationEpoch, REPRESENTATIONS, parse_epoch
from retrieval_supplement_model_contract import (
    ADMISSION_SCHEMA, ARTIFACT_PROFILE_ID, ModelHostPaths, ModelHostRefusal,
    REQUEST_SCHEMA, ValidatedStudyRequest, WORKER_CLOSURE_ID, WORKER_SOURCE_FILES,
)
from retrieval_supplement_model_host import run_model_sibling
from retrieval_supplement_study_closure import require_verified_artifact_profile
from retrieval_supplement_study_contracts import (
    ARTIFACT_PIN_DOCUMENT_SHA256, ARTIFACT_SOURCE_IDENTITY_SHA256, INPUT_PIN_SHA256,
    STUDY_PHASE, V1_DOCUMENTS, V1_QUERIES, V2_DOCUMENTS, V2_QUERIES,
    WORKER_CLOSURE_SHA256,
)
from retrieval_supplement_study_preflight import extract_worker_token_evidence
from retrieval_supplement_primitives import canonical_bytes, digest_bytes
from retrieval_supplement_host_transport import (
    ACK_PREFIX, HostTransportError, _bound_docker, admission_publish_plan,
    decode_request_result, parse_admission_ack_line, request_read_plan,
    response_publish_plan, write_private_once,
)

_CID = re.compile(r"[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CAL23 = frozenset({"cal-23", "ptx.q.cal-23", "query:cal-23"})
_REQUEST_MAX = REQUEST_MAX_BYTES
_RESPONSE_MAX = RESPONSE_MAX_BYTES


class HostExchangeError(ValueError):
    """Stable public-safe exchange refusal without payloads or private paths."""

    def __init__(self, code: str, *, primary_code: str | None = None,
                 cleanup_code: str | None = None) -> None:
        self.code = code if re.fullmatch(r"[a-z0-9_]+", code) else "host_exchange_refused"
        self.primary_code = primary_code
        self.cleanup_code = cleanup_code
        super().__init__(self.code)


def _refuse(condition: object, code: str) -> None:
    if not condition:
        raise HostExchangeError(code)


def _digest(value: object, code: str = "digest_invalid") -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise HostExchangeError(code)
    return value


def _guard(source_guard: Callable[[], None], require_outer: Callable[[], str]) -> str:
    try:
        source_guard()
        cid = require_outer()
    except Exception:
        raise HostExchangeError("outer_authority_lost") from None
    _refuse(type(cid) is str and _CID.fullmatch(cid) is not None, "outer_identity")
    return cid


def _result_parts(result: object) -> tuple[int, bytes, bytes]:
    if isinstance(result, bytes):
        return 0, result, b""
    stdout, stderr = getattr(result, "stdout", None), getattr(result, "stderr", None)
    if type(stdout) is not bytes or type(stderr) is not bytes:
        raise HostExchangeError("bounded_result")
    code = getattr(result, "returncode", None)
    _refuse(type(code) is int, "bounded_result")
    assert isinstance(code, int)
    return code, stdout, stderr


def _invoke(
    *, label: str, argv: tuple[str, ...], payload: bytes | None, limit: int,
    require_outer: Callable[[], str], source_guard: Callable[[], None],
    charge_bytes: Callable[[int], None],
    deadline: float | None = None,
) -> bytes:
    if deadline is not None:
        _refuse(type(deadline) in (int, float) and math.isfinite(deadline)
                and time.monotonic() < deadline, label + "_deadline")
    _guard(source_guard, require_outer)
    _refuse(len(argv) >= 2 and argv[0] == "docker", "docker_invocation")
    if payload is not None:
        charge_bytes(len(payload))
    try:
        result = _bound_docker(require_outer, argv, payload, limit, deadline=deadline)
    except HostTransportError as exc:
        raise HostExchangeError(exc.code) from None
    code, stdout, _stderr = _result_parts(result)
    _refuse(code == 0, label + "_failed")
    _guard(source_guard, require_outer)
    if deadline is not None:
        _refuse(time.monotonic() < deadline, label + "_deadline")
    return stdout


def _ready(raw: bytes, epoch: PreparationEpoch) -> ReadyEvent:
    parsed = parse_readiness_line(raw)
    if not isinstance(parsed, ReadyEvent):
        raise HostExchangeError("readiness_invalid" if isinstance(parsed, BridgeRefusal) else "readiness_missing")
    _refuse(parsed.attempt_id == epoch.request_attempt_id, "readiness_attempt")
    parts = parsed.request_relative_path.split("/")
    _refuse(len(parts) >= 3 and parts[-3] == epoch.request_attempt_id
            and parts[-2] == "w5-component-" + parsed.nonce and parts[-1] == "request.json",
            "readiness_path")
    _digest(parsed.request_sha256, "readiness_digest")
    return parsed


def _request_document(raw: bytes, ready: ReadyEvent, epoch: PreparationEpoch) -> dict[str, object]:
    _refuse(digest_bytes(raw) == ready.request_sha256, "request_identity")
    try:
        document, records = parse_worker_request(raw)
    except (WorkerRequestError, ValueError, TypeError):
        raise HostExchangeError("request_invalid") from None
    expected = (V1_DOCUMENTS, V1_QUERIES) if epoch.ordinal == 1 else (V2_DOCUMENTS, V2_QUERIES)
    _refuse(document.get("schema") == REQUEST_SCHEMA and document.get("attempt_id") == ready.attempt_id,
            "request_join")
    for field in ("input_identity", "representation_report_identity", "publication_identity"):
        _digest(document.get(field), "request_" + field)
    _refuse(epoch.input_pin_sha256 == INPUT_PIN_SHA256, "request_input_pin")
    _refuse(document.get("artifact_profile_identity") == ARTIFACT_PROFILE_ID == ARTIFACT_SOURCE_IDENTITY_SHA256,
            "request_artifact_identity")
    _refuse(len(records) == sum(expected), "request_record_count")
    rows = document.get("records")
    _refuse(isinstance(rows, list) and len(rows) == len(records), "request_records")
    assert isinstance(rows, list)
    _refuse(all(isinstance(row, dict) and row.get("mode") == "document" for row in rows[:expected[0]])
            and all(isinstance(row, dict) and row.get("mode") == "query" for row in rows[expected[0]:]),
            "request_record_order")
    _refuse(sum(isinstance(row, dict) and row.get("mode") == "query" for row in rows) == expected[1],
            "request_query_count")
    _refuse(not any(isinstance(row, dict) and row.get("record_id") in _CAL23 for row in rows),
            "request_legacy_query")
    return document


def _source_digests(worker_digests: Mapping[str, str]) -> dict[str, str]:
    _refuse(isinstance(worker_digests, Mapping) and set(worker_digests) == set(WORKER_SOURCE_FILES),
            "worker_closure_shape")
    result = {}
    for name in WORKER_SOURCE_FILES:
        result[name] = _digest(worker_digests[name], "worker_closure_digest")
    _refuse(dict(sorted(result.items())) == dict(sorted(worker_digests.items())), "worker_closure_extra")
    _refuse(WORKER_CLOSURE_ID == WORKER_CLOSURE_SHA256, "worker_closure_identity")
    _refuse(closure_identity("worker", result) == WORKER_CLOSURE_ID, "worker_closure_identity")
    return dict(sorted(result.items()))


def _admission(
    *, document: Mapping[str, object], request_sha: str, plan: Mapping[str, Any],
    gate_references: Mapping[str, str], source_closure: Mapping[str, str],
) -> bytes:
    authority = _digest(plan.get("operator_decision_sha256"), "operator_authority")
    _refuse(plan.get("phase") == STUDY_PHASE, "admission_phase")
    _refuse(isinstance(gate_references, Mapping) and bool(gate_references), "admission_gates")
    gates = {str(key): _digest(value, "admission_gate") for key, value in gate_references.items()}
    fields = {
        "schema": ADMISSION_SCHEMA, "phase": STUDY_PHASE,
        "operator_authority_sha256": authority, "gate_references": dict(sorted(gates.items())),
        "bounded_component_execution_admitted": True, "request_sha256": request_sha,
        "input_identity": document["input_identity"],
        "representation_report_identity": document["representation_report_identity"],
        "publication_identity": document["publication_identity"],
        "artifact_profile_identity": document["artifact_profile_identity"],
        "candidate_closure_digests": dict(sorted(source_closure.items())),
        "scope": STUDY_PHASE + " sibling representation worker",
    }
    return canonical_bytes(fields)


def _entry_digest(path: Path, charge_bytes: Callable[[int], None]) -> str:
    if not isinstance(path, Path) or not path.is_absolute():
        raise HostExchangeError("worker_entry_path")
    try:
        info = os.lstat(path)
        _refuse(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and 0 < info.st_size <= 8 * 1024**2,
                "worker_entry_file")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(fd)
            charge_bytes(before.st_size + 1)
            raw = os.read(fd, before.st_size + 1)
            after = os.fstat(fd)
        finally:
            os.close(fd)
    except (OSError, HostExchangeError):
        raise HostExchangeError("worker_entry_file") from None
    _refuse(len(raw) == before.st_size
            and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            "worker_entry_changed")
    return digest_bytes(raw)


def _artifact_verifier(plan: Mapping[str, Any], gate_references: Mapping[str, str]) -> Callable[[Path], object]:
    receipt = gate_references.get("artifact_gate_receipt_sha256") if isinstance(gate_references, Mapping) else None
    _refuse(type(receipt) is str and _DIGEST.fullmatch(receipt) is not None and receipt not in {
        ARTIFACT_SOURCE_IDENTITY_SHA256, ARTIFACT_PIN_DOCUMENT_SHA256, WORKER_CLOSURE_SHA256, INPUT_PIN_SHA256,
    }, "artifact_profile_unbound")
    from retrieval_semantic_generator_artifacts import verify_artifact_directory
    def verify(root: Path) -> object:
        try:
            profile = verify_artifact_directory(root)
            require_verified_artifact_profile(profile)
            return profile
        except Exception:
            raise ModelHostRefusal("artifact_verification_failed") from None
    return verify


def _token_evidence(
    response: bytes, request_raw: bytes, admission_raw: bytes,
    source_closure: Mapping[str, str], epoch: PreparationEpoch, admission_sha: str,
) -> dict[str, Any]:
    bundle = decode_response_bundle(response)
    if isinstance(bundle, BridgeRefusal) or not isinstance(bundle, Mapping):
        raise HostExchangeError("response_bundle_invalid")
    members = {"request.json", "admission.json", "declaration.json", "source-manifest.json",
               "sources.zip", "report.json", "terminal.json"}
    if set(bundle) != members or bundle["request.json"] != request_raw or bundle["admission.json"] != admission_raw:
        raise HostExchangeError("response_bundle_identity")
    try:
        verified = parse_verified_worker_response(
            request_raw=bundle["request.json"], report_raw=bundle["report.json"],
            terminal_raw=bundle["terminal.json"], declaration_raw=bundle["declaration.json"],
            source_manifest_raw=bundle["source-manifest.json"], sources_archive_raw=bundle["sources.zip"],
            expected_controller_admission_sha256=admission_sha,
            expected_source_closure=source_closure,
        )
        prefix = "v1." if epoch.ordinal == 1 else ""
        return extract_worker_token_evidence(epoch.representation, verified, id_prefix=prefix)
    except Exception:
        raise HostExchangeError("response_receipt_invalid") from None


def run_exchange(
    *, epoch: PreparationEpoch, epoch_started: float, reader: Any,
    require_outer: Callable[[], str], source_guard: Callable[[], None],
    charge_bytes: Callable[[int], None], charge_operation: Callable[[str], None],
    before_worker: Callable[[], None], plan: Mapping[str, Any], artifact_root: Path,
    worker_source: Path, worker_entry: Path, output_root: Path,
    worker_digests: Mapping[str, str], gate_references: Mapping[str, str],
    charge_docker_bytes: Callable[[int], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Execute one real host sibling exchange, refusing every unbound seam."""
    if not isinstance(epoch, PreparationEpoch):
        raise HostExchangeError("epoch_invalid")
    _refuse(isinstance(plan, Mapping), "study_plan_invalid")
    try:
        parse_epoch(epoch.to_dict())
    except Exception:
        raise HostExchangeError("epoch_invalid") from None
    _refuse(epoch.representation == REPRESENTATIONS[epoch.ordinal - 1], "epoch_representation")
    _refuse(epoch.attempt_id == plan.get("attempt_id")
            and epoch.evaluator_closure_sha256 == plan.get("evaluator_closure_sha256"),
            "epoch_plan_join")
    _refuse(type(epoch_started) in (int, float) and math.isfinite(epoch_started)
            and epoch_started <= clock(), "epoch_started")
    _guard(source_guard, require_outer)
    initialization_deadline = epoch_started + 600.
    raw_ready = reader.await_frame(READY_PREFIX, deadline=min(clock() + 120., initialization_deadline),
        require_current=require_outer)
    ready = _ready(raw_ready, epoch)
    admission_deadline = min(clock() + 110., initialization_deadline)
    cid = _guard(source_guard, require_outer)
    request_tar = _invoke(label="request_read", argv=request_read_plan(cid, ready), payload=None,
                          limit=ready.request_bytes + 20480, require_outer=require_outer,
                          source_guard=source_guard, charge_bytes=charge_bytes, deadline=admission_deadline)
    try:
        request_raw = decode_request_result(request_tar, ready)
    except HostTransportError as exc:
        raise HostExchangeError(exc.code) from None
    document = _request_document(request_raw, ready, epoch)
    request_path = output_root / "request.json"
    write_private_once(request_path, request_raw, maximum=_REQUEST_MAX, charge_bytes=charge_bytes)
    source_closure = _source_digests(worker_digests)
    admission_raw = _admission(document=document, request_sha=ready.request_sha256, plan=plan,
                               gate_references=gate_references, source_closure=source_closure)
    admission_sha = digest_bytes(admission_raw)
    try:
        parse_controller_admission(admission_raw, expected_admission_sha256=admission_sha,
            request=document, request_sha256=ready.request_sha256, source_closure=source_closure)
    except (WorkerRequestError, ValueError, TypeError):
        raise HostExchangeError("admission_invalid") from None
    write_private_once(output_root / "admission.json", admission_raw, maximum=512 * 1024, charge_bytes=charge_bytes)
    cid = _guard(source_guard, require_outer)
    try:
        argv, payload = admission_publish_plan(cid, ready, admission_raw)
    except HostTransportError as exc:
        raise HostExchangeError(exc.code) from None
    _invoke(label="admission_publish", argv=argv, payload=payload, limit=8192,
            require_outer=require_outer, source_guard=source_guard, charge_bytes=charge_bytes,
            deadline=admission_deadline)
    ack = reader.await_frame(ACK_PREFIX, deadline=admission_deadline, require_current=require_outer)
    try:
        parse_admission_ack_line(ack, ready=ready, admission_sha256=admission_sha)
    except HostTransportError as exc:
        raise HostExchangeError(exc.code) from None
    response_deadline = clock() + 600.0
    _refuse(math.isfinite(response_deadline) and response_deadline > clock(), "response_deadline")
    model_deadline = response_deadline - 10.0
    _refuse(model_deadline > clock(), "model_response_deadline")
    _guard(source_guard, require_outer)
    entry_sha = _entry_digest(worker_entry, charge_bytes)
    _guard(source_guard, require_outer)
    request_records = document.get("records")
    _refuse(isinstance(request_records, list), "request_records")
    assert isinstance(request_records, list)
    request = ValidatedStudyRequest(
        attempt_id=ready.attempt_id, request_sha256=ready.request_sha256,
        admission_sha256=admission_sha, input_identity=str(document["input_identity"]),
        representation_report_identity=str(document["representation_report_identity"]),
        publication_identity=str(document["publication_identity"]),
        artifact_profile_identity=str(document["artifact_profile_identity"]),
        worker_closure_sha256=WORKER_CLOSURE_ID, record_count=len(request_records),
        source_closure=source_closure, worker_entry_sha256=entry_sha,
    )
    model_output = output_root / "model-output"
    try:
        os.mkdir(model_output, 0o700)
    except OSError:
        raise HostExchangeError("model_output_root") from None
    verifier = _artifact_verifier(plan, gate_references)
    _guard(source_guard, require_outer)
    before_worker()
    try:
        result = run_model_sibling(
            request, ModelHostPaths(request_path, output_root / "admission.json", artifact_root,
                                    worker_source, worker_entry, model_output), nonce=ready.nonce,
            require_outer=require_outer, charge_bytes=charge_bytes,
            verify_artifacts=verifier, charge_operation=charge_operation,
            epoch_started=epoch_started, response_deadline=model_deadline, clock=clock,
            charge_docker_bytes=charge_docker_bytes,
        )
    except ModelHostRefusal as exc:
        raise HostExchangeError("model_" + exc.code) from None
    except Exception:
        raise HostExchangeError("model_host_refused") from None
    response = result.response_bundle
    write_private_once(output_root / "model-result.json", canonical_bytes({
        "status": result.status, "code": result.code, "worker_cid": result.model_container_id,
        "cleanup_proven": result.cleanup_proven, "primary_code": result.primary_code,
        "cleanup_code": result.cleanup_code}), maximum=65536, charge_bytes=charge_bytes)
    if response is not None:
        write_private_once(output_root / "response.zip", response, maximum=_RESPONSE_MAX, charge_bytes=charge_bytes)
    if result.status != "completed" or not result.cleanup_proven:
        raise HostExchangeError(
            "model_" + result.code,
            primary_code=result.primary_code,
            cleanup_code=result.cleanup_code,
        )
    if type(response) is not bytes or not 0 < len(response) <= _RESPONSE_MAX:
        raise HostExchangeError("model_response_missing")
    response_bytes = response
    token_evidence = _token_evidence(response_bytes, request_raw, admission_raw,
                                     source_closure, epoch, admission_sha)
    cid = _guard(source_guard, require_outer)
    try:
        argv, payload = response_publish_plan(cid, ready, response_bytes)
    except HostTransportError as exc:
        raise HostExchangeError(exc.code) from None
    _invoke(label="response_publish", argv=argv, payload=payload, limit=8192,
            require_outer=require_outer, source_guard=source_guard, charge_bytes=charge_bytes,
            deadline=response_deadline)
    return {"ordinal": epoch.ordinal, "representation": epoch.representation,
            "attempt_id": ready.attempt_id, "request_sha256": ready.request_sha256,
            "nonce": ready.nonce,
            "admission_sha256": admission_sha, "response_sha256": digest_bytes(response_bytes),
            "worker_cid": result.model_container_id, "cleanup_proven": True,
            "status": "completed", "token_evidence": token_evidence,
            "scientific_acceptance_claimed": False}


__all__ = ["HostExchangeError", "run_exchange"]
