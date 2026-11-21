"""Outer host/controller exchange for body-study model siblings."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, cast

from retrieval_public_text_sources import closure_identity
from retrieval_semantic_bridge_transport import (
    BridgeRefusal, ReadyEvent, READY_PREFIX, REQUEST_MAX_BYTES, RESPONSE_MAX_BYTES,
    decode_response_bundle, parse_readiness_line,
)
from retrieval_semantic_generator_output_parser import parse_verified_worker_response
from retrieval_semantic_generator_worker_protocol import WorkerRequestError, parse_controller_admission, parse_worker_request
from retrieval_body_study_contracts import BODY_PHASE, BODY_ROLE
from retrieval_body_study_epoch import BODY_INPUT_PIN_SHA256, PreparationEpoch, REPRESENTATIONS, parse_epoch
from retrieval_body_study_model_contract import (
    ADMISSION_SCHEMA, ARTIFACT_PROFILE_ID, ModelHostPaths, ModelHostRefusal,
    REQUEST_SCHEMA, ValidatedStudyRequest, WORKER_CLOSURE_ID, WORKER_SOURCE_FILES,
    query_manifest_identity,
)
from retrieval_body_study_model_host import run_model_sibling
from retrieval_supplement_study_closure import require_verified_artifact_profile
from retrieval_body_study_host_transport import (
    ACK_PREFIX, HostTransportError, _bound_docker, admission_publish_plan, decode_request_result,
    parse_admission_ack_line, request_read_plan, response_publish_plan, write_private_once,
)
from retrieval_body_study_host_runner_settlement import worker_record as _worker_record

_CID = re.compile(r"[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REQUEST_MAX, _RESPONSE_MAX = REQUEST_MAX_BYTES, RESPONSE_MAX_BYTES

class HostExchangeError(ValueError):
    """Stable public-safe exchange refusal without payloads or private paths."""

    def __init__(self, code: str, *, primary_code: str | None = None,
                 cleanup_code: str | None = None, worker: Mapping[str, Any] | None = None) -> None:
        self.code = code if re.fullmatch(r"[a-z0-9_]+", code) else "host_exchange_refused"
        self.primary_code, self.cleanup_code = primary_code, cleanup_code
        self.worker = dict(worker) if isinstance(worker, Mapping) else None
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
        source_guard(); cid = require_outer()
    except Exception:
        raise HostExchangeError("outer_authority_lost") from None
    _refuse(type(cid) is str and _CID.fullmatch(cid) is not None, "outer_identity")
    return cid


def _result_parts(result: object) -> tuple[int, bytes, bytes]:
    if isinstance(result, bytes):
        return 0, result, b""
    stdout, stderr, code = getattr(result, "stdout", None), getattr(result, "stderr", None), getattr(result, "returncode", None)
    _refuse(type(stdout) is bytes and type(stderr) is bytes and type(code) is int, "bounded_result")
    return cast(int, code), cast(bytes, stdout), cast(bytes, stderr)


def _invoke(*, label: str, argv: tuple[str, ...], payload: bytes | None, limit: int,
            require_outer: Callable[[], str], source_guard: Callable[[], None],
            charge_bytes: Callable[[int], None], deadline: float | None = None) -> bytes:
    if deadline is not None:
        _refuse(type(deadline) in (int, float) and math.isfinite(deadline) and time.monotonic() < deadline, label + "_deadline")
    _guard(source_guard, require_outer)
    _refuse(len(argv) >= 2 and argv[0] == "docker", "docker_invocation")
    if payload is not None:
        charge_bytes(len(payload))
    try:
        result = _bound_docker(require_outer, argv, payload, limit, deadline=deadline)
    except HostTransportError as exc:
        raise HostExchangeError(exc.code) from None
    code, stdout, _stderr = _result_parts(result)
    _refuse(code == 0, label + "_failed"); _guard(source_guard, require_outer)
    if deadline is not None:
        _refuse(time.monotonic() < deadline, label + "_deadline")
    return stdout


def _ready(raw: bytes, epoch: PreparationEpoch) -> ReadyEvent:
    parsed = parse_readiness_line(raw)
    if not isinstance(parsed, ReadyEvent):
        raise HostExchangeError("readiness_invalid" if isinstance(parsed, BridgeRefusal) else "readiness_missing")
    _refuse(parsed.attempt_id == epoch.request_attempt_id, "readiness_attempt")
    parts = parsed.request_relative_path.split("/")
    _refuse(len(parts) >= 3 and parts[-3] == epoch.request_attempt_id and parts[-2] == "w5-component-" + parsed.nonce and parts[-1] == "request.json", "readiness_path")
    _digest(parsed.request_sha256, "readiness_digest")
    return parsed


def _query_manifest(plan: Mapping[str, Any], representation: str, count: int) -> list[dict[str, str]]:
    declaration = plan.get("declaration")
    owner = plan if "query_manifest" in plan else declaration
    manifest = owner.get("query_manifest") if isinstance(owner, Mapping) else None
    committed = owner.get("query_manifest_sha256") if isinstance(owner, Mapping) else None
    if committed is None and isinstance(declaration, Mapping):
        committed = declaration.get("query_manifest_sha256")
    selected: object = manifest
    if isinstance(manifest, Mapping):
        full_manifest = manifest
        _refuse(set(full_manifest) == set(REPRESENTATIONS), "query_manifest_representations")
        selected = manifest.get(representation)
        if committed is None:
            committed = plan.get("query_manifest_sha256")
        _refuse(query_manifest_identity(full_manifest) == committed, "query_manifest_commitment")
    else:
        _refuse(query_manifest_identity(manifest) == committed, "query_manifest_commitment")
    _refuse(isinstance(selected, list) and len(selected) == count, "query_manifest_shape")
    rows = cast(list[Mapping[str, object]], selected)
    result: list[dict[str, str]] = []
    for row in rows:
        _refuse(isinstance(row, Mapping) and set(row) == {"record_id", "text_sha256"}, "query_manifest_row")
        record_id_value = row["record_id"]
        _refuse(type(record_id_value) is str, "query_manifest_id")
        record_id = cast(str, record_id_value)
        _refuse(record_id.startswith("query:") and bool(record_id[6:]), "query_manifest_id")
        result.append({"record_id": record_id, "text_sha256": _digest(row["text_sha256"], "query_manifest_text")})
    _refuse(len({row["record_id"] for row in result}) == count, "query_manifest_unique")
    return result


def _request_document(raw: bytes, ready: ReadyEvent, epoch: PreparationEpoch,
                      plan: Mapping[str, Any]) -> dict[str, object]:
    _refuse("sha256:" + hashlib.sha256(raw).hexdigest() == ready.request_sha256, "request_identity")
    try:
        document, records = parse_worker_request(raw)
    except (WorkerRequestError, ValueError, TypeError):
        raise HostExchangeError("request_invalid") from None
    expected = (66, 4) if epoch.ordinal == 1 else (44, 20)
    _refuse(document.get("schema") == REQUEST_SCHEMA and document.get("attempt_id") == ready.attempt_id, "request_join")
    for field_name in ("input_identity", "representation_report_identity", "publication_identity", "artifact_profile_identity"):
        _digest(document.get(field_name), "request_identity")
    _refuse(epoch.input_pin_sha256 == BODY_INPUT_PIN_SHA256, "request_input_pin")
    _refuse(document.get("artifact_profile_identity") == ARTIFACT_PROFILE_ID, "request_artifact_identity")
    _refuse(len(records) == sum(expected), "request_record_count")
    rows_value = document.get("records")
    _refuse(isinstance(rows_value, list) and len(rows_value) == len(records), "request_records")
    rows = cast(list[object], rows_value)
    _refuse(all(isinstance(row, dict) and row.get("mode") == "document" for row in rows[:expected[0]])
            and all(isinstance(row, dict) and row.get("mode") == "query" for row in rows[expected[0]:]), "request_record_order")
    _refuse(not any(isinstance(row, dict) and row.get("record_id") in {"cal-23", "query:cal-23", "ptx.q.cal-23"} for row in rows), "request_old_cohort")
    request_rows = cast(list[Mapping[str, object]], rows)
    expected_queries = _query_manifest(plan, epoch.representation, expected[1])
    actual_queries = [{"record_id": cast(str, row["record_id"]),
                       "text_sha256": "sha256:" + hashlib.sha256(cast(str, row["text"]).encode("utf-8")).hexdigest()}
                      for row in request_rows[expected[0]:]]
    _refuse(actual_queries == expected_queries, "request_query_manifest")
    for name in ("input_identity", "representation_report_identity", "publication_identity"):
        declaration = plan.get("declaration")
        expected_identity = plan.get(name)
        if expected_identity is None and isinstance(declaration, Mapping):
            expected_identity = declaration.get(name)
        if expected_identity is not None:
            _refuse(document.get(name) == _digest(expected_identity, "request_plan_join"), "request_plan_join")
    return document


def _source_digests(worker_digests: Mapping[str, str]) -> dict[str, str]:
    _refuse(isinstance(worker_digests, Mapping) and set(worker_digests) == set(WORKER_SOURCE_FILES), "worker_closure_shape")
    result = {name: _digest(worker_digests[name], "worker_closure_digest") for name in WORKER_SOURCE_FILES}
    _refuse(closure_identity("worker", result) == WORKER_CLOSURE_ID, "worker_closure_identity")
    return dict(sorted(result.items()))


def _admission(*, document: Mapping[str, object], request_sha: str, plan: Mapping[str, Any],
               gate_references: Mapping[str, str], source_closure: Mapping[str, str]) -> bytes:
    authority = plan.get("operator_decision_sha256")
    _digest(authority, "operator_authority")
    _refuse(plan.get("phase") == BODY_PHASE and plan.get("role") == BODY_ROLE, "admission_phase")
    _refuse(isinstance(gate_references, Mapping) and bool(gate_references), "admission_gates")
    gates = {str(key): _digest(value, "admission_gate") for key, value in gate_references.items()}
    fields = {"schema": ADMISSION_SCHEMA, "phase": BODY_PHASE, "operator_authority_sha256": authority,
        "gate_references": dict(sorted(gates.items())), "bounded_component_execution_admitted": True,
        "request_sha256": request_sha, "input_identity": document["input_identity"],
        "representation_report_identity": document["representation_report_identity"],
        "publication_identity": document["publication_identity"], "artifact_profile_identity": document["artifact_profile_identity"],
        "candidate_closure_digests": dict(sorted(source_closure.items())), "scope": BODY_PHASE + " " + BODY_ROLE + " representation worker"}
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _entry_digest(path: Path, charge_bytes: Callable[[int], None]) -> str:
    if not isinstance(path, Path) or not path.is_absolute():
        raise HostExchangeError("worker_entry_path")
    try:
        info = os.lstat(path)
        _refuse(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and 0 < info.st_size <= 8 * 1024**2, "worker_entry_file")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd); charge_bytes(before.st_size + 1); raw = os.read(fd, before.st_size + 1); after = os.fstat(fd)
        finally:
            os.close(fd)
    except (OSError, HostExchangeError):
        raise HostExchangeError("worker_entry_file") from None
    _refuse(len(raw) == before.st_size and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "worker_entry_changed")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _artifact_verifier(gate_references: Mapping[str, str]) -> Callable[[Path], object]:
    _refuse(isinstance(gate_references, Mapping), "artifact_gate_binding")
    receipt = gate_references.get("artifact_gate_receipt_sha256")
    _refuse(type(receipt) is str and _DIGEST.fullmatch(receipt) is not None and receipt not in {ARTIFACT_PROFILE_ID, BODY_INPUT_PIN_SHA256, WORKER_CLOSURE_ID}, "artifact_profile_unbound")
    from retrieval_semantic_generator_artifacts import verify_artifact_directory
    def verify(root: Path) -> object:
        try:
            profile = verify_artifact_directory(root)
            require_verified_artifact_profile(profile)
            return profile
        except Exception:
            raise ModelHostRefusal("artifact_verification_failed") from None
    return verify


def _token_evidence(response: Any, epoch: PreparationEpoch) -> dict[str, Any]:
    generation = getattr(response, "generation", None)
    embeddings = getattr(generation, "embeddings", None)
    cost = getattr(generation, "cost", None)
    if not isinstance(embeddings, tuple) or cost is None:
        raise HostExchangeError("response_token_evidence")
    rows = [{"record_id": item.record_id, "mode": item.mode.value,
             "token_count_before_limit": item.token_count_before_limit,
             "token_count_used": item.token_count_used, "overflow_chunks": item.overflow_chunks,
             "truncated": item.truncated} for item in embeddings]
    cost_fields = ("input_records", "input_bytes", "model_input_bytes", "token_count_before_limit",
                   "token_count_used", "overflow_chunks", "truncated_records", "load_seconds",
                   "inference_seconds", "total_seconds")
    return {"phase": BODY_PHASE, "representation": epoch.representation, "record_count": response.record_count,
            "embedding_count": response.embedding_count, "rows": rows,
            "cost": {name: getattr(cost, name) for name in cost_fields}, "report_sha256": response.report_sha256,
            "terminal_sha256": response.terminal_sha256, "origin": "verified_worker_response",
            "model_provenance": {"artifact_profile_identity": response.artifact_profile_identity,
                                  "controller_admission_sha256": response.controller_admission_sha256,
                                  "input_identity": response.input_identity,
                                  "representation_report_identity": response.representation_report_identity,
                                  "publication_identity": response.publication_identity,
                                  "source_closure_digests": list(response.source_closure_digests)}}


def _worker_error(error: BaseException, worker: Mapping[str, Any] | None = None) -> HostExchangeError:
    if isinstance(error, HostExchangeError):
        return HostExchangeError(error.code, primary_code=error.primary_code,
                                 cleanup_code=error.cleanup_code, worker=worker or error.worker)
    code = getattr(error, "code", None)
    safe = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]+", code) else "worker_exchange_failed"
    return HostExchangeError(safe, primary_code=safe, worker=worker)


def run_exchange(*, epoch: PreparationEpoch, epoch_started: float, reader: Any,
    require_outer: Callable[[], str], source_guard: Callable[[], None], charge_bytes: Callable[[int], None],
    charge_operation: Callable[[str], None], before_worker: Callable[[], None], plan: Mapping[str, Any],
    artifact_root: Path, worker_source: Path, worker_entry: Path, output_root: Path,
    worker_digests: Mapping[str, str], gate_references: Mapping[str, str],
    charge_docker_bytes: Callable[[int], None] | None = None, clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Run one real body sibling; signature is compatible with old host composition."""
    if not isinstance(epoch, PreparationEpoch):
        raise HostExchangeError("epoch_invalid")
    _refuse(isinstance(plan, Mapping), "study_plan_invalid")
    try:
        parse_epoch(epoch.to_dict())
    except Exception:
        raise HostExchangeError("epoch_invalid") from None
    _refuse(epoch.representation == REPRESENTATIONS[epoch.ordinal - 1], "epoch_representation")
    _refuse(plan.get("phase") == BODY_PHASE and plan.get("role") == BODY_ROLE, "study_plan_identity")
    source_closure = _digest(plan.get("source_closure_sha256"), "epoch_source_closure")
    _refuse(epoch.attempt_id == plan.get("attempt_id") and epoch.evaluator_closure_sha256 == source_closure, "epoch_plan_join")
    _refuse(type(epoch_started) in (int, float) and math.isfinite(epoch_started) and epoch_started <= clock(), "epoch_started")
    _guard(source_guard, require_outer)
    initialization_deadline = epoch_started + 600.0
    raw_ready = reader.await_frame(READY_PREFIX, deadline=min(clock() + 120.0, initialization_deadline), require_current=require_outer)
    ready = _ready(raw_ready, epoch)
    admission_deadline = min(clock() + 110.0, initialization_deadline)
    cid = _guard(source_guard, require_outer)
    request_tar = _invoke(label="request_read", argv=request_read_plan(cid, ready), payload=None, limit=ready.request_bytes + 20480,
                          require_outer=require_outer, source_guard=source_guard, charge_bytes=charge_bytes, deadline=admission_deadline)
    try:
        request_raw = decode_request_result(request_tar, ready)
    except HostTransportError as exc:
        raise HostExchangeError(exc.code) from None
    document = _request_document(request_raw, ready, epoch, plan); request_path = output_root / "request.json"
    write_private_once(request_path, request_raw, maximum=_REQUEST_MAX, charge_bytes=charge_bytes)
    worker_source_closure = _source_digests(worker_digests)
    admission_raw = _admission(document=document, request_sha=ready.request_sha256, plan=plan,
                               gate_references=gate_references, source_closure=worker_source_closure)
    admission_sha = "sha256:" + hashlib.sha256(admission_raw).hexdigest()
    try:
        parse_controller_admission(admission_raw, expected_admission_sha256=admission_sha, request=document,
                                   request_sha256=ready.request_sha256, source_closure=worker_source_closure)
    except (WorkerRequestError, ValueError, TypeError):
        raise HostExchangeError("admission_invalid") from None
    write_private_once(output_root / "admission.json", admission_raw, maximum=512 * 1024, charge_bytes=charge_bytes)
    cid = _guard(source_guard, require_outer)
    try:
        argv, payload = admission_publish_plan(cid, ready, admission_raw)
    except HostTransportError as exc:
        raise HostExchangeError(exc.code) from None
    _invoke(label="admission_publish", argv=argv, payload=payload, limit=8192, require_outer=require_outer,
            source_guard=source_guard, charge_bytes=charge_bytes, deadline=admission_deadline)
    ack = reader.await_frame(ACK_PREFIX, deadline=admission_deadline, require_current=require_outer)
    try:
        parse_admission_ack_line(ack, ready=ready, admission_sha256=admission_sha)
    except HostTransportError as exc:
        raise HostExchangeError(exc.code) from None
    response_deadline = clock() + 600.0; model_deadline = response_deadline - 10.0
    _refuse(model_deadline > clock(), "model_response_deadline"); _guard(source_guard, require_outer)
    entry_sha = _entry_digest(worker_entry, charge_bytes); _guard(source_guard, require_outer)
    records = document.get("records"); _refuse(isinstance(records, list), "request_records")
    assert isinstance(records, list)
    doc_count, query_count = (66, 4) if epoch.ordinal == 1 else (44, 20)
    request = ValidatedStudyRequest(ready.attempt_id, ready.request_sha256, admission_sha,
        str(document["input_identity"]), str(document["representation_report_identity"]), str(document["publication_identity"]),
        epoch.representation, doc_count, query_count, str(document["artifact_profile_identity"]), WORKER_CLOSURE_ID,
        len(records), worker_source_closure, entry_sha)
    model_output = output_root / "model-output"
    try:
        os.mkdir(model_output, 0o700)
    except OSError:
        raise HostExchangeError("model_output_root") from None
    verifier = _artifact_verifier(gate_references); _guard(source_guard, require_outer); before_worker()
    try:
        result = run_model_sibling(request, ModelHostPaths(request_path, output_root / "admission.json", artifact_root,
            worker_source, worker_entry, model_output), nonce=ready.nonce, require_outer=require_outer,
            charge_bytes=charge_bytes, verify_artifacts=verifier, charge_operation=charge_operation,
            epoch_started=epoch_started, response_deadline=model_deadline, clock=clock, charge_docker_bytes=charge_docker_bytes)
    except ModelHostRefusal as exc:
        raise HostExchangeError("model_" + exc.code, primary_code=exc.code,
                                worker=_worker_record(None, unknown=True)) from None
    except BaseException as exc:
        raise _worker_error(exc, _worker_record(None, unknown=True)) from None
    worker = _worker_record(result)
    try:
        write_private_once(output_root / "model-result.json", json.dumps({
            "status": result.status, "code": result.code, "worker_cid": result.model_container_id,
            "cleanup_proven": result.cleanup_proven, "primary_code": result.primary_code, "cleanup_code": result.cleanup_code},
            sort_keys=True, separators=(",", ":")).encode("ascii"), maximum=65536, charge_bytes=charge_bytes)
        if result.response_bundle is not None:
            write_private_once(output_root / "response.zip", result.response_bundle, maximum=_RESPONSE_MAX, charge_bytes=charge_bytes)
        if result.status != "completed" or not result.cleanup_proven:
            raise HostExchangeError("model_" + result.code, primary_code=result.primary_code,
                                    cleanup_code=result.cleanup_code, worker=worker)
        response = result.response_bundle
        if type(response) is not bytes or not 0 < len(response) <= _RESPONSE_MAX:
            raise HostExchangeError("model_response_missing", worker=worker)
        try:
            bundle = decode_response_bundle(response)
        except Exception:
            raise HostExchangeError("response_bundle_invalid", worker=worker) from None
        if (isinstance(bundle, BridgeRefusal) or not isinstance(bundle, Mapping)
                or bundle.get("request.json") != request_raw or bundle.get("admission.json") != admission_raw):
            raise HostExchangeError("response_bundle_identity", worker=worker)
        try:
            verified = parse_verified_worker_response(request_raw=request_raw, report_raw=bundle["report.json"], terminal_raw=bundle["terminal.json"],
                declaration_raw=bundle["declaration.json"], source_manifest_raw=bundle["source-manifest.json"], sources_archive_raw=bundle["sources.zip"],
                expected_controller_admission_sha256=admission_sha, expected_source_closure=worker_source_closure)
        except Exception:
            raise HostExchangeError("response_receipt_invalid", worker=worker) from None
        _refuse(verified.input_identity == document["input_identity"] and verified.representation_report_identity == document["representation_report_identity"] and verified.publication_identity == document["publication_identity"] and verified.artifact_profile_identity == document["artifact_profile_identity"], "response_identity")
        _refuse(verified.document_count == doc_count and verified.query_count == query_count, "response_census")
        token_evidence = _token_evidence(verified, epoch); cid = _guard(source_guard, require_outer)
        try:
            argv, payload = response_publish_plan(cid, ready, response)
        except HostTransportError as exc:
            raise HostExchangeError(exc.code, worker=worker) from None
        _invoke(label="response_publish", argv=argv, payload=payload, limit=8192, require_outer=require_outer,
                source_guard=source_guard, charge_bytes=charge_bytes, deadline=response_deadline)
        return {"phase": BODY_PHASE, "role": BODY_ROLE, "ordinal": epoch.ordinal, "representation": epoch.representation,
                "attempt_id": ready.attempt_id, "request_sha256": ready.request_sha256, "nonce": ready.nonce,
                "admission_sha256": admission_sha, "response_sha256": "sha256:" + hashlib.sha256(response).hexdigest(),
                "worker_cid": result.model_container_id, "worker_count": worker["worker_count"],
                "cleanup": worker["cleanup"], "worker_cleanup": worker["cleanup"],
                "worker_count_known": worker["worker_count_known"], "worker_count_upper_bound": worker["worker_count_upper_bound"],
                "cleanup_proven": True, "status": "completed",
                "token_evidence": token_evidence, "scientific_acceptance_claimed": False}
    except BaseException as exc:
        raise _worker_error(exc, worker) from None

__all__ = ["HostExchangeError", "run_exchange"]
