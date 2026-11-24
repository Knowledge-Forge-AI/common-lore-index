"""Inner worker-side body exchange with real response provenance checks."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from offline_qualification_values import INNER_PLAN
from retrieval_body_study_epoch import (
    INNER_EPOCH_IO_BYTES, REPRESENTATIONS, await_acknowledgement, prepare_epoch,
)
from retrieval_body_study_plan import read_inner_body_plan

from retrieval_semantic_bridge_transport import (
    BridgeRefusal, RESPONSE_MAX_BYTES, decode_response_bundle, decode_response_ready,
)
from retrieval_semantic_bridge_transport_io import _owned_exchange_directory, _read_regular
from retrieval_semantic_experiment_bridge import ComponentExchange
from retrieval_semantic_generator_output_parser import parse_verified_worker_response
from retrieval_semantic_generator_kernel import SemanticMode
from retrieval_semantic_generator_worker_protocol import WorkerRequestError, parse_worker_request
from retrieval_body_study_model_contract import (
    ADMISSION_SCHEMA, ARTIFACT_PROFILE_ID, BODY_INPUT_PIN_SHA256, BODY_PHASE, BODY_ROLE, V1_DOCUMENTS, V1_QUERIES, V2_DOCUMENTS, V2_QUERIES,
    WORKER_CLOSURE_ID, WORKER_SOURCE_FILES, ModelHostRefusal, validate_body_model_request,
)

_DIGEST = "sha256:"
_ISSUED = object()


class BodyExchangeError(ValueError):
    """Stable body exchange refusal without payload or private path text."""

    def __init__(self, code: str) -> None:
        self.code = code if code.isascii() and code.replace("_", "").isalnum() else "body_exchange_refused"
        super().__init__(self.code)


@dataclass(frozen=True)
class BodyEpochEvidence:
    attempt_id: str
    representation: str
    request_sha256: str
    admission_sha256: str
    response_sha256: str
    evidence_root: Path = field(repr=False, compare=False)
    exchange: ComponentExchange = field(repr=False, compare=False)
    members: Mapping[str, bytes] = field(repr=False, compare=False)
    response: Any = field(repr=False, compare=False)
    _origin: object = field(default=None, repr=False, compare=False)
    _seal: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "members", MappingProxyType(dict(self.members)))

    @staticmethod
    def is_factory_issued(value: object) -> bool:
        if type(value) is not BodyEpochEvidence or value._origin is not _ISSUED:
            return False
        return value._seal == _evidence_identity(value)

    @property
    def diagnostics(self) -> dict[str, object]:
        return {"member_names": tuple(sorted(self.members)),
                "member_sha256": {name: _digest(value) for name, value in sorted(self.members.items())},
                "response_sha256": self.response_sha256}


def _canonical(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise BodyExchangeError("request_not_canonical") from None


def _digest(raw: bytes) -> str:
    return _DIGEST + hashlib.sha256(raw).hexdigest()


def _evidence_identity(value: BodyEpochEvidence) -> str:
    return _digest(_canonical({
        "attempt_id": value.attempt_id, "representation": value.representation,
        "request_sha256": value.request_sha256, "admission_sha256": value.admission_sha256,
        "response_sha256": value.response_sha256, "root": str(value.evidence_root),
        "members": {key: _digest(raw) for key, raw in sorted(value.members.items())},
        "worker_response_identity": getattr(value.response, "_sealed_identity", None),
    }))


def _expected(representation: str) -> tuple[int, int]:
    expected = {"canonical-node-v1": (V1_DOCUMENTS, V1_QUERIES),
                "canonical-node-public-text-v2": (V2_DOCUMENTS, V2_QUERIES)}
    if representation not in expected:
        raise BodyExchangeError("representation")
    return expected[representation]


def _closure(worker_digests: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(worker_digests, Mapping) or set(worker_digests) != set(WORKER_SOURCE_FILES):
        raise BodyExchangeError("worker_closure_shape")
    result: dict[str, str] = {}
    for name in WORKER_SOURCE_FILES:
        digest = worker_digests[name]
        if type(digest) is not str or len(digest) != 71 or not digest.startswith(_DIGEST):
            raise BodyExchangeError("worker_closure_digest")
        result[name] = digest
    from retrieval_public_text_sources import closure_identity
    if closure_identity("worker", result) != WORKER_CLOSURE_ID:
        raise BodyExchangeError("worker_closure_identity")
    return dict(sorted(result.items()))


def _wire_request(request_map: Mapping[str, Any], request_raw: bytes,
                  representation: str) -> tuple[dict[str, object], dict[str, object], bytes]:
    if not isinstance(request_map, Mapping):
        raise BodyExchangeError("descriptor_missing")
    descriptor = dict(request_map)
    try:
        validate_body_model_request(descriptor)
    except ModelHostRefusal as exc:
        raise BodyExchangeError(exc.code) from None
    if descriptor.get("representation") != representation:
        raise BodyExchangeError("descriptor_representation")
    wire_value = descriptor.get("wire_request", descriptor.get("wire"))
    if not isinstance(wire_value, Mapping):
        raise BodyExchangeError("wire_request_missing")
    wire = dict(wire_value)
    try:
        parsed, records = parse_worker_request(request_raw)
    except (WorkerRequestError, TypeError, ValueError):
        raise BodyExchangeError("request_invalid") from None
    canonical = _canonical(wire)
    try:
        canonical_parsed, canonical_records = parse_worker_request(canonical)
    except (WorkerRequestError, TypeError, ValueError):
        raise BodyExchangeError("request_invalid") from None
    if request_raw != canonical or canonical_parsed != parsed or tuple(canonical_records) != tuple(records):
        raise BodyExchangeError("request_bytes_identity")
    documents, queries = _expected(representation)
    modes = tuple(SemanticMode(record.mode).value for record in records)
    if len(records) != documents + queries or modes.count("document") != documents or modes.count("query") != queries:
        raise BodyExchangeError("request_census")
    if parsed.get("attempt_id") != descriptor.get("request_attempt_id"):
        raise BodyExchangeError("request_attempt")
    if parsed.get("input_identity") != descriptor.get("input_identity") or parsed.get("representation_report_identity") != descriptor.get("representation_report_identity") or parsed.get("publication_identity") != descriptor.get("publication_identity"):
        raise BodyExchangeError("request_descriptor_join")
    if parsed.get("artifact_profile_identity") != ARTIFACT_PROFILE_ID:
        raise BodyExchangeError("request_artifact_identity")
    if descriptor.get("input_pin_sha256") != BODY_INPUT_PIN_SHA256 or descriptor.get("total_record_count") != len(records):
        raise BodyExchangeError("request_descriptor_census")
    manifest = descriptor.get("query_manifest")
    query_rows = [{"record_id": item.record_id, "text_sha256": _digest(item.text.encode("utf-8"))}
                  for item in records if SemanticMode(item.mode).value == "query"]
    if not isinstance(manifest, (list, tuple)) or list(manifest) != query_rows:
        raise BodyExchangeError("request_query_manifest")
    return descriptor, parsed, canonical


def _body_admission(raw: bytes, *, request_sha256: str, representation: str) -> str:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BodyExchangeError("admission_invalid") from None
    scope = value.get("scope") if isinstance(value, Mapping) else None
    if (not isinstance(value, Mapping) or value.get("schema") != ADMISSION_SCHEMA
            or value.get("phase") != BODY_PHASE or not isinstance(scope, str)
            or not scope.startswith(BODY_PHASE + " ")):
        raise BodyExchangeError("admission_phase")
    if value.get("role", BODY_ROLE) != BODY_ROLE or value.get("request_sha256") != request_sha256:
        raise BodyExchangeError("admission_join")
    if value.get("representation") not in (None, representation):
        raise BodyExchangeError("admission_representation")
    return _digest(raw)


def _response_digest(exchange: ComponentExchange, members: Mapping[str, bytes]) -> str:
    if set(members) != {"request.json", "admission.json", "declaration.json", "source-manifest.json", "sources.zip", "report.json", "terminal.json"}:
        raise BodyExchangeError("response_members")
    if any(type(members[name]) is not bytes for name in members):
        raise BodyExchangeError("response_members")
    # Identity belongs to the received ZIP bytes. Re-encoding decoded members
    # can change compression or metadata while preserving the same content.
    directory = _owned_exchange_directory(exchange.directory, exchange.nonce)
    if isinstance(directory, BridgeRefusal):
        raise BodyExchangeError("response_directory")
    marker = _read_regular(directory / "response.ready.json", limit=4096)
    if isinstance(marker, BridgeRefusal):
        raise BodyExchangeError("response_marker")
    ready = decode_response_ready(marker, expected_nonce=exchange.nonce)
    if isinstance(ready, BridgeRefusal):
        raise BodyExchangeError("response_marker")
    raw = _read_regular(directory / "response.zip", limit=RESPONSE_MAX_BYTES)
    if isinstance(raw, BridgeRefusal):
        raise BodyExchangeError("response_bundle")
    _nonce, expected_sha256, expected_bytes = ready
    if len(raw) != expected_bytes or _digest(raw) != expected_sha256:
        raise BodyExchangeError("response_identity")
    decoded = decode_response_bundle(raw)
    if isinstance(decoded, BridgeRefusal) or decoded != dict(members):
        raise BodyExchangeError("response_member_identity")
    return expected_sha256


def _emit_ack(exchange: ComponentExchange) -> None:
    if exchange.admission_sha256 is None:
        raise BodyExchangeError("admission_missing")
    print("CLOE_W5_BODY_COMPONENT_ADMISSION_ACK " + json.dumps({
        "admission_sha256": exchange.admission_sha256, "attempt_id": exchange.attempt_id,
        "nonce": exchange.nonce, "request_sha256": exchange.ready.request_sha256,
        "schema": "cloe-w5-body-component-admission-ack-v1",
    }, sort_keys=True, separators=(",", ":")), flush=True)


def _await_preparation_ack(
    descriptor: Mapping[str, Any], representation: str, require_current: Callable[[], None],
) -> None:
    plan = read_inner_body_plan(Path(INNER_PLAN))
    ordinal = REPRESENTATIONS.index(representation) + 1
    attempt = plan["attempt_id"]
    if (descriptor.get("attempt_id") != attempt
            or descriptor.get("request_attempt_id") != f"{attempt}-v{ordinal}"):
        raise BodyExchangeError("epoch_attempt_join")

    remaining = INNER_EPOCH_IO_BYTES

    def charge(count: int) -> None:
        nonlocal remaining
        if type(count) is not int or count < 0 or count > remaining:
            raise BodyExchangeError("epoch_byte_budget")
        remaining -= count

    epoch = prepare_epoch(attempt_id=attempt, ordinal=ordinal,
                          evaluator_closure_sha256=plan["evaluator_closure_sha256"], charge=charge)
    await_acknowledgement(epoch, charge=charge, require_current=require_current)


def run_exchange_epoch(
    request_map: dict[str, Any], request_raw: bytes, *, worker_digests: dict[str, str],
    delivery: Any | None, representation: str, evidence_root: Path, evidence: Any,
    require_current: Callable[[], None] | None = None,
) -> BodyEpochEvidence:
    """Run one real body worker exchange; signature matches the old support API."""
    if require_current is not None:
        require_current()
    if not isinstance(evidence_root, Path) or not evidence_root.is_absolute():
        raise BodyExchangeError("evidence_root")
    closure = _closure(worker_digests)
    descriptor, wire, actual_request_raw = _wire_request(request_map, request_raw, representation)
    attempt_id = descriptor.get("request_attempt_id")
    if type(attempt_id) is not str:
        raise BodyExchangeError("request_attempt")
    if delivery is None:
        if require_current is None:
            raise BodyExchangeError("epoch_source_guard_required")
        _await_preparation_ack(descriptor, representation, require_current)
    try:
        exchange = ComponentExchange.publish_request(attempt_id=attempt_id, request=wire,
                                                     expected_source_closure=closure)
    except Exception:
        raise BodyExchangeError("request_publish") from None
    if exchange.request_raw != actual_request_raw:
        raise BodyExchangeError("request_bytes_identity")
    exchange.emit_request_readiness()
    if delivery is not None:
        admit = getattr(delivery, "admit", None)
        if not callable(admit):
            raise BodyExchangeError("delivery_seam")
        try:
            admit(exchange)
        except Exception:
            raise BodyExchangeError("delivery_admit") from None
    try:
        exchange = exchange.await_controller_admission(timeout_seconds=120.0)
    except Exception:
        raise BodyExchangeError("controller_admission_refused") from None
    admission_sha256 = _body_admission(exchange.admission_raw or b"", request_sha256=exchange.ready.request_sha256,
                                       representation=representation)
    if admission_sha256 != exchange.admission_sha256:
        raise BodyExchangeError("admission_identity")
    _emit_ack(exchange)
    if delivery is not None:
        respond = getattr(delivery, "respond", None)
        if not callable(respond):
            raise BodyExchangeError("delivery_seam")
        try:
            respond(exchange)
        except Exception:
            raise BodyExchangeError("delivery_respond") from None
    try:
        members = exchange.await_response(timeout_seconds=600.0)
    except Exception:
        raise BodyExchangeError("response_unavailable") from None
    if (not isinstance(members, Mapping) or members.get("request.json") != actual_request_raw
            or members.get("admission.json") != exchange.admission_raw):
        raise BodyExchangeError("response_input_identity")
    try:
        response = parse_verified_worker_response(
            request_raw=actual_request_raw, report_raw=members["report.json"], terminal_raw=members["terminal.json"],
            declaration_raw=members["declaration.json"], source_manifest_raw=members["source-manifest.json"],
            sources_archive_raw=members["sources.zip"], expected_controller_admission_sha256=admission_sha256,
            expected_source_closure=closure,
        )
    except Exception:
        raise BodyExchangeError("response_receipt_invalid") from None
    documents, queries = _expected(representation)
    if response.document_count != documents or response.query_count != queries or response.status != "completed":
        raise BodyExchangeError("response_census")
    result = BodyEpochEvidence(attempt_id, representation, exchange.ready.request_sha256,
                               admission_sha256, _response_digest(exchange, members), evidence_root, exchange, members, response)
    object.__setattr__(result, "_seal", _evidence_identity(result))
    object.__setattr__(result, "_origin", _ISSUED)
    if evidence is not None:
        record = getattr(evidence, "record_exchange", None)
        if callable(record):
            try:
                accepted = record(result)
            except Exception:
                raise BodyExchangeError("evidence_rejected") from None
            if accepted is False:
                raise BodyExchangeError("evidence_rejected")
    if require_current is not None:
        require_current()
    return result


__all__ = ["BodyEpochEvidence", "BodyExchangeError", "run_exchange_epoch"]
