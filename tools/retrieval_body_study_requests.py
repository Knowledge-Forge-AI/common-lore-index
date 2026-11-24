"""Body-specific v1/v2 model request construction."""
from __future__ import annotations
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any
from retrieval_body_records import build_body_query_records
from retrieval_body_study_prepare import BodyPublicationPreparation, selected_admissions
from retrieval_body_study_values import (
    PROSPECTIVE_CENSUS,
    PROSPECTIVE_RECORDS,
    STUDY_PHASE,
    V1_REPRESENTATION,
    V2_ARM,
    V2_REPRESENTATION,
    digest_value,
    require,
)
from retrieval_body_study_model_contract import query_manifest_identity, validate_body_model_request
from retrieval_public_text_census import retained_task_sha256
from retrieval_public_text_contracts import PublicTextRefusal
from retrieval_public_text_descriptors import PublicTextPreparation, require_unchanged_recomputation
from retrieval_public_text_request import ARM_PTX
from retrieval_public_text_response import (
    ArmSeal, SealedRecord, StudySealRefusal, check_embedding, check_generation_cost,
)
from retrieval_semantic_generator_kernel import QUERY_PREFIX, SemanticGeneration
from retrieval_semantic_generator_output_receipt import VerifiedWorkerResponse, _response_identity
from retrieval_semantic_generator_inputs import (
    PublicGenerationInput,
    build_worker_request,
    prepare_public_generation_input,
)
from retrieval_semantic_generator_kernel_contracts import SemanticMode, SemanticText
from retrieval_semantic_generator_worker_protocol import REQUEST_SCHEMA, parse_worker_request
from retrieval_semantic_provenance import VerifiedPublicationPin
def canonical_body_wire_bytes(wire: Mapping[str, Any]) -> bytes:
    """Encode the request bytes used by the worker transport contract."""
    return json.dumps(
        dict(wire), sort_keys=True, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")
@dataclass(frozen=True, slots=True)
class BodyStudyRequest:
    """A body admission descriptor paired with an independently parsed wire request."""
    ordinal: int
    representation: str
    descriptor: Mapping[str, Any]
    wire: Mapping[str, Any]
    raw: bytes
    records: tuple[SemanticText, ...]
    document_count: int
    query_count: int
    query_task_ids: tuple[str, ...]
    input_identity: str
    publication_identity: str
    query_admissions: tuple[Any, ...] = ()
    query_admission_identity: str = ""
    @property
    def study_attempt_id(self) -> str:
        return str(self.descriptor["attempt_id"])
    @property
    def request_attempt_id(self) -> str:
        return str(self.descriptor["request_attempt_id"])
    def actual_query_rows(self) -> tuple[Mapping[str, Any], ...]:
        from retrieval_body_admission import BodyTaskAdmission
        require(self.ordinal == 2 and len(self.query_admissions) == 24, "body.request:query_admission_witness")
        rows: list[Mapping[str, Any]] = []; identities: list[str] = []
        for admission in self.query_admissions:
            require(BodyTaskAdmission.is_factory_issued(admission), "body.request:query_admission_origin")
            identities.append(admission.identity)
            rows.append(admission.to_dict())
        require(len({row.get("task_id") for row in rows}) == 24, "body.request:query_admission_tasks")
        require(digest_value(identities) == self.query_admission_identity, "body.request:query_admission_seal")
        return tuple(rows)
    def require_current(self) -> None:
        try:
            validate_body_model_request(self.descriptor)
        except Exception as exc:
            raise ValueError("body.request:descriptor") from exc
        require(self.descriptor.get("phase") == STUDY_PHASE, "body.request:phase")
        require(self.wire.get("schema") == REQUEST_SCHEMA, "body.request:wire_schema")
        require(self.wire.get("attempt_id") == self.descriptor.get("request_attempt_id"), "body.request:attempt_id")
        require(self.wire.get("input_identity") == self.input_identity, "body.request:input_identity")
        require(self.wire.get("publication_identity") == self.publication_identity, "body.request:publication_identity")
        require(self.raw == canonical_body_wire_bytes(self.wire), "body.request:wire_canonical")
        parsed, parsed_records = parse_worker_request(self.raw)
        require(parsed == dict(self.wire), "body.request:wire_drift")
        require(tuple(parsed_records) == self.records, "body.request:record_drift")
        require(self.document_count + self.query_count == len(self.records), "body.request:census")
        require(self.descriptor.get("total_record_count") == len(self.records),
                "body.request:total_record_count")
        query_ids = tuple(item.record_id.removeprefix("query:") for item in self.records if item.mode is SemanticMode.QUERY)
        require(query_ids == self.query_task_ids, "body.request:query_identity")
        manifest = [{"record_id": item.record_id, "text_sha256": "sha256:" + hashlib.sha256(item.text.encode("utf-8")).hexdigest()} for item in self.records if item.mode is SemanticMode.QUERY]
        require(self.descriptor.get("query_manifest") == manifest and
                self.descriptor.get("query_manifest_sha256") == query_manifest_identity(manifest),
                "body.request:query_manifest")
        if self.ordinal == 2:
            self.actual_query_rows()
    @property
    def identity(self) -> str:
        self.require_current()
        return digest_value({
            "phase": STUDY_PHASE,
            "ordinal": self.ordinal,
            "representation": self.representation,
            "wire": self.wire,
        })
@dataclass(frozen=True, slots=True)
class BodyStudyRequests:
    v1: BodyStudyRequest
    v2: BodyStudyRequest
    def require_current(self) -> None:
        require(self.v1.ordinal == 1 and self.v2.ordinal == 2, "body.request:ordinals")
        require(self.v1.representation == V1_REPRESENTATION, "body.request:v1_representation")
        require(self.v2.representation == V2_REPRESENTATION, "body.request:v2_representation")
        self.v1.require_current()
        self.v2.require_current()
        require(self.v1.descriptor["attempt_id"] == self.v2.descriptor["attempt_id"],
                "body.request:study_attempt_identity")
        require(self.v1.descriptor["request_attempt_id"] ==
                self.v1.descriptor["attempt_id"] + "-v1", "body.request:v1_attempt")
        require(self.v2.descriptor["request_attempt_id"] ==
                self.v2.descriptor["attempt_id"] + "-v2", "body.request:v2_attempt")
        require(self.v1.input_identity != self.v2.input_identity, "body.request:identity_reuse")
        require(len(self.v1.records) + len(self.v2.records) == PROSPECTIVE_RECORDS,
                "body.request:total_records")
    def ordered(self) -> tuple[BodyStudyRequest, BodyStudyRequest]:
        self.require_current()
        return self.v1, self.v2
def _descriptor(
    *, ordinal: int, representation: str, attempt_id: str, pin: VerifiedPublicationPin,
    input_identity: str, report_identity: str, document_count: int, query_count: int,
    query_task_ids: Sequence[str], input_pin_sha256: str,
) -> dict[str, Any]:
    descriptor = {
        "phase": STUDY_PHASE,
        "role": "body-study",
        "ordinal": ordinal,
        "attempt_id": attempt_id,
        "request_attempt_id": f"{attempt_id}-v{ordinal}",
        "representation": representation,
        "document_count": document_count,
        "query_count": query_count,
        "total_record_count": document_count + query_count,
        "input_identity": input_identity,
        "representation_report_identity": report_identity,
        "publication_identity": digest_value(pin.to_dict()),
        "query_task_ids": tuple(query_task_ids),
        "backend": pin.backend,
        "generation": pin.generation,
        "input_pin_sha256": input_pin_sha256,
        "execution_authority_granted": False,
        "model_executed": False,
    }
    require(len(set(query_task_ids)) == len(tuple(query_task_ids)), "body.request:query_identity")
    return descriptor
def _request_from_records(
    *, ordinal: int, representation: str, descriptor: Mapping[str, Any], records: Sequence[SemanticText],
    request_attempt_id: str, artifact_profile_identity: str, query_admissions: Sequence[Any] = (),
) -> BodyStudyRequest:
    checked = tuple(records)
    descriptor = dict(descriptor)
    descriptor["query_manifest"] = [
        {"record_id": item.record_id, "text_sha256": "sha256:" + hashlib.sha256(item.text.encode("utf-8")).hexdigest()}
        for item in checked if item.mode is SemanticMode.QUERY
    ]
    descriptor["query_manifest_sha256"] = query_manifest_identity(descriptor["query_manifest"])
    validate_body_model_request(descriptor)
    document_count = sum(item.mode is SemanticMode.DOCUMENT for item in checked)
    query_count = sum(item.mode is SemanticMode.QUERY for item in checked)
    require(document_count == descriptor["document_count"], "body.request:document_count")
    require(query_count == descriptor["query_count"], "body.request:query_count")
    wire = {
        "schema": REQUEST_SCHEMA,
        "attempt_id": request_attempt_id,
        "input_identity": descriptor["input_identity"],
        "representation_report_identity": descriptor["representation_report_identity"],
        "publication_identity": descriptor["publication_identity"],
        "artifact_profile_identity": artifact_profile_identity,
        "records": [
            {"record_id": item.record_id, "text": item.text, "mode": SemanticMode(item.mode).value}
            for item in checked
        ],
    }
    raw = canonical_body_wire_bytes(wire)
    parsed, parsed_records = parse_worker_request(raw)
    require(parsed == wire and tuple(parsed_records) == checked, "body.request:parser_roundtrip")
    result = BodyStudyRequest(
        ordinal, representation, dict(descriptor), dict(wire), raw, checked,
        document_count, query_count, tuple(descriptor["query_task_ids"]),
        str(descriptor["input_identity"]), str(descriptor["publication_identity"]),
        tuple(query_admissions), digest_value([item.identity for item in query_admissions])
        if query_admissions else "",
    )
    result.require_current()
    return result
def _v1_request(
    *, preparation: BodyPublicationPreparation, store: Any, attempt_id: str,
    artifact_profile_identity: str, input_pin_sha256: str | None,
) -> BodyStudyRequest:
    pin = store.pin
    require(type(pin) is VerifiedPublicationPin and pin.backend == "postgresql", "body.request:v1_pin")
    tasks = tuple(case.task for case in preparation.catalog.cases)
    generation_input: PublicGenerationInput = prepare_public_generation_input(
        store.representations(),
        tasks,
        read_current=lambda: store.pin.revalidate_current(),
        read_representations=store.representations,
    )
    records = tuple(generation_input.records)
    docs = sum(item.mode is SemanticMode.DOCUMENT for item in records)
    queries = sum(item.mode is SemanticMode.QUERY for item in records)
    require((docs, queries) == (PROSPECTIVE_CENSUS["v1_documents"], PROSPECTIVE_CENSUS["v1_queries"]),
            "body.request:v1_census")
    query_ids = tuple(item.record_id.removeprefix("query:") for item in records if item.mode is SemanticMode.QUERY)
    descriptor = _descriptor(
        ordinal=1, representation=V1_REPRESENTATION, attempt_id=attempt_id, pin=pin,
        input_identity=generation_input.input_identity,
        report_identity=generation_input.representation_report_identity,
        document_count=docs, query_count=queries, query_task_ids=query_ids,
        input_pin_sha256=(preparation.inputs.pin_sha256 if input_pin_sha256 is None else input_pin_sha256),
    )
    wire = build_worker_request(
        generation_input, attempt_id=f"{attempt_id}-v1",
        artifact_profile_identity=artifact_profile_identity,
    )
    require(wire["attempt_id"] == f"{attempt_id}-v1", "body.request:v1_attempt")
    require(wire["input_identity"] == descriptor["input_identity"], "body.request:v1_input_identity")
    require(wire["representation_report_identity"] == descriptor["representation_report_identity"],
            "body.request:v1_report_identity")
    require(wire["publication_identity"] == descriptor["publication_identity"],
            "body.request:v1_publication_identity")
    return _request_from_records(
        ordinal=1, representation=V1_REPRESENTATION, descriptor=descriptor,
        records=records, request_attempt_id=f"{attempt_id}-v1",
        artifact_profile_identity=artifact_profile_identity,
    )
def _v2_request(
    *, preparation: BodyPublicationPreparation, store: Any, scope: Any, attempt_id: str,
    artifact_profile_identity: str, validate: bool, input_pin_sha256: str | None,
) -> BodyStudyRequest:
    pin = store.pin
    require(type(pin) is VerifiedPublicationPin and pin.backend == "postgresql", "body.request:v2_pin")
    public_text = store.public_text(scope)
    docs = tuple(public_text.documents)
    admissions = selected_admissions(preparation, V2_ARM, validate=validate)
    ledger = build_body_query_records(admissions)
    queries = tuple(item.semantic_text for item in ledger.records)
    require(len(docs) == PROSPECTIVE_CENSUS["v2_documents"], "body.request:v2_documents")
    require(len(queries) == PROSPECTIVE_CENSUS["v2_queries"], "body.request:v2_queries")
    doc_records = tuple(
        SemanticText(
            "document:" + digest_value(doc.descriptor.to_dict())[7:],
            doc.model_text,
            SemanticMode.DOCUMENT,
        )
        for doc in docs
    )
    records = doc_records + queries
    input_identity = digest_value({
        "phase": STUDY_PHASE,
        "representation": V2_REPRESENTATION,
        "publication": pin.to_dict(),
        "documents": [doc.descriptor.to_dict() for doc in docs],
        "queries": [item.to_dict() for item in ledger.records],
    })
    report_identity = digest_value(public_text.comparable_state())
    descriptor = _descriptor(
        ordinal=2, representation=V2_REPRESENTATION, attempt_id=attempt_id, pin=pin,
        input_identity=input_identity, report_identity=report_identity,
        document_count=len(doc_records), query_count=len(queries),
        query_task_ids=[item.task_id for item in ledger.records],
        input_pin_sha256=(preparation.inputs.pin_sha256 if input_pin_sha256 is None else input_pin_sha256),
    )
    descriptor["public_text_identity"] = digest_value(public_text.to_dict())
    return _request_from_records(
        ordinal=2, representation=V2_REPRESENTATION, descriptor=descriptor,
        records=records, request_attempt_id=f"{attempt_id}-v2",
        artifact_profile_identity=artifact_profile_identity, query_admissions=admissions,
    )
def build_body_study_requests(
    preparation: BodyPublicationPreparation,
    *,
    postgres_store: Any,
    scope: Any,
    attempt_id: str,
    artifact_profile_identity: str,
    validate: bool = True,
    input_pin_sha256: str | None = None,
) -> BodyStudyRequests:
    """Prepare v1 and v2 independently; no model exchange occurs here."""
    require(type(validate) is bool, "body.request:validate_flag")
    if validate:
        preparation.require_current()
    v1 = _v1_request(
        preparation=preparation, store=postgres_store, attempt_id=attempt_id,
        artifact_profile_identity=artifact_profile_identity, input_pin_sha256=input_pin_sha256,
    )
    postgres_store.pin.revalidate_current()
    v2 = _v2_request(
        preparation=preparation, store=postgres_store, scope=scope, attempt_id=attempt_id,
        artifact_profile_identity=artifact_profile_identity, validate=validate,
        input_pin_sha256=input_pin_sha256,
    )
    result = BodyStudyRequests(v1, v2)
    result.require_current()
    return result
def seal_body_v2_worker_response(
    request: BodyStudyRequest,
    response: VerifiedWorkerResponse,
    *,
    preparation: BodyPublicationPreparation,
    public_text: PublicTextPreparation,
    fresh_public_text: Callable[[], PublicTextPreparation],
    scope: Any,
) -> ArmSeal:
    """Seal the actual v2 response against current body public-text rows."""
    request.require_current()
    require(type(response) is VerifiedWorkerResponse and response.status == "completed"
            and response._sealed_identity == _response_identity(response),
            "body.request:v2_response_identity")
    require(response.request_records == request.records
            and response.attempt_id == request.request_attempt_id
            and response.input_identity == request.input_identity
            and response.representation_report_identity == request.descriptor["representation_report_identity"]
            and response.publication_identity == request.publication_identity
            and response.artifact_profile_identity == request.wire["artifact_profile_identity"],
            "body.request:v2_response_join")
    try:
        refreshed = fresh_public_text()
        require_unchanged_recomputation(public_text, refreshed, scope=scope)
    except PublicTextRefusal as exc:
        raise ValueError("body.request:v2_public_text_recomputation") from exc
    generation = response.generation
    require(type(generation) is SemanticGeneration and len(generation.embeddings) == len(request.records),
            "body.request:v2_generation_count")
    assert isinstance(generation, SemanticGeneration)
    try:
        vectors = tuple(
            check_embedding(record, observed)
            for record, observed in zip(request.records, generation.embeddings, strict=True)
        )
        check_generation_cost(
            generation.cost, request.records, generation.embeddings,
            status=generation.status, partial=generation.partial,
        )
    except StudySealRefusal as exc:
        raise ValueError("body.request:v2_seal") from exc
    source_ids = tuple(doc.identity for doc in public_text.documents)
    by_task = {case.task.task_id: case.task for case in preparation.catalog.cases}
    source_ids += tuple(retained_task_sha256(by_task[item]) for item in request.query_task_ids)
    require(len(source_ids) == len(request.records), "body.request:v2_source_count")
    sealed: list[SealedRecord] = []
    truncated_docs: list[str] = []
    truncated_queries: list[str] = []
    query_index = 0
    for record, source, embedding, vector in zip(
        request.records, source_ids, generation.embeddings, vectors, strict=True,
    ):
        mode = SemanticMode(record.mode)
        sealed.append(SealedRecord(
            record.record_id, ARM_PTX, mode, source, vector,
            embedding.token_count_before_limit, embedding.token_count_used,
            embedding.overflow_chunks, embedding.truncated,
        ))
        if mode is SemanticMode.DOCUMENT and embedding.truncated:
            truncated_docs.append(source)
        elif mode is SemanticMode.QUERY:
            task_id = request.query_task_ids[query_index]
            query_index += 1
            if embedding.truncated:
                truncated_queries.append(task_id)
    counts = {"records": len(sealed), "documents": len(public_text.documents),
              "queries": len(sealed) - len(public_text.documents)}
    exact_cost = {
        **counts,
        "input_bytes": sum(len(item.record_id.encode()) + len(item.text.encode()) for item in request.records),
        "model_input_bytes": sum(len(((QUERY_PREFIX if item.mode is SemanticMode.QUERY else "")
                                      + item.text).encode()) for item in request.records),
        "token_count_before_limit": sum(item.token_count_before_limit for item in sealed),
        "token_count_used": sum(item.token_count_used for item in sealed),
        "overflow_chunks": sum(item.overflow_chunks for item in sealed),
        "truncated_records": sum(item.truncated for item in sealed),
    }
    verified_pairs = int(getattr(public_text, "v1_verified_pair_count", len(public_text.documents)))
    partial = bool(public_text.exclusions) or len(public_text.documents) < verified_pairs
    return ArmSeal(
        ARM_PTX, "token_partial" if any(item.truncated for item in sealed) else "complete", None,
        tuple(sealed), MappingProxyType(counts), MappingProxyType(exact_cost),
        tuple(truncated_docs), tuple(truncated_queries), "partial" if partial else "complete",
    )
__all__ = [
    "BodyStudyRequest", "BodyStudyRequests", "build_body_study_requests",
    "canonical_body_wire_bytes",
    "seal_body_v2_worker_response",
]
