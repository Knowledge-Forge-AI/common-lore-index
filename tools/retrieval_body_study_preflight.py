"""Preflight and common-positive freeze for the actual body study."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from types import MappingProxyType
from typing import Any, cast

from retrieval_body_study_common import CommonPositiveSet, select_common_positives
from retrieval_body_study_queries import require_query_admission_row
from retrieval_body_study_requests import BodyStudyRequest
from retrieval_body_study_values import (
    ARM_REPRESENTATIONS,
    COMPARISON_ARMS,
    MAX_TOKENS,
    PROSPECTIVE_RECORDS,
    PROSPECTIVE_CENSUS,
    STUDY_PHASE,
    digest_value,
    require,
)
from retrieval_semantic_generator_kernel_contracts import SemanticMode

_ISSUED = object()

@dataclass(frozen=True, slots=True)
class WorkerEpochReceipt:
    """Host/worker evidence retained before any query slot can start."""

    request_identity: str
    response_identity: str
    representation: str
    token_receipts: tuple[Mapping[str, Any], ...]
    eligibility: tuple[Mapping[str, Any], ...]
    admission_identity: str
    reachability_identity: str
    token_provenance: tuple[Mapping[str, Any], ...] = field(default=(), repr=False, compare=False)
    transport_receipt: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)
    _origin: object = field(default=None, repr=False, compare=False)
    _seal: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "token_receipts", tuple(
            MappingProxyType(dict(row)) for row in self.token_receipts
        ))
        object.__setattr__(self, "eligibility", tuple(
            MappingProxyType(dict(row)) for row in self.eligibility
        ))
        object.__setattr__(self, "token_provenance", tuple(
            MappingProxyType(dict(row)) for row in self.token_provenance
        ))
        object.__setattr__(self, "transport_receipt", MappingProxyType(dict(self.transport_receipt)))

    @staticmethod
    def is_factory_issued(value: object) -> bool:
        return (type(value) is WorkerEpochReceipt and value._origin is _ISSUED
                and value._seal == _receipt_identity(value))

    def require_current(self, request: BodyStudyRequest) -> None:
        require(request.identity == self.request_identity, "body.preflight:request_identity")
        require(self.representation == request.representation, "body.preflight:representation")
        require(self.admission_identity.startswith("sha256:"), "body.preflight:admission_identity")
        require(self.reachability_identity.startswith("sha256:"), "body.preflight:reachability_identity")
        require(len(self.token_receipts) == len(request.records), "body.preflight:token_receipt_count")
        seen: set[str] = set()
        for row in self.token_receipts:
            require(set(row) == {
                "record_id", "mode", "token_count", "admitted", "captured_text_reachable", "truncated",
                "representation_identity",
            }, "body.preflight:token_receipt_fields")
            record_id = row["record_id"]
            require(type(record_id) is str and record_id in {r.record_id for r in request.records},
                    "body.preflight:token_record_id")
            require(record_id not in seen, "body.preflight:token_duplicate")
            seen.add(record_id)
            require(row["representation_identity"] == request.representation, "body.preflight:token_representation")
            require(type(row["mode"]) is str and row["mode"] in {"document", "query"}, "body.preflight:token_mode")
            token_count = row["token_count"]
            require(token_count is None or (
                type(token_count) is int and 0 <= token_count <= MAX_TOKENS
            ), "body.preflight:token_count")
            for name in ("admitted", "captured_text_reachable", "truncated"):
                require(type(row[name]) is bool, "body.preflight:token_flag")
            require(not (row["admitted"] and row["captured_text_reachable"] and token_count is None),
                    "body.preflight:token_missing")
        require(len(self.eligibility) == 0 or len(self.eligibility) == 48,
                "body.preflight:eligibility_count")
        if self.token_provenance:
            require(len(self.token_provenance) == len(self.token_receipts),
                    "body.preflight:token_provenance_count")
            for row in self.token_provenance:
                require(set(row) == {
                    "record_id", "mode", "token_count_before_limit", "token_count_used",
                    "overflow_chunks", "truncated",
                }, "body.preflight:token_provenance_fields")
                require(type(row["record_id"]) is str and type(row["mode"]) is str,
                        "body.preflight:token_provenance_identity")
                require(type(row["token_count_before_limit"]) is int and row["token_count_before_limit"] >= 0,
                        "body.preflight:token_before_limit")
                require(type(row["token_count_used"]) is int and 0 <= row["token_count_used"] <= MAX_TOKENS,
                        "body.preflight:token_used")
                require(type(row["overflow_chunks"]) is int and row["overflow_chunks"] >= 0,
                        "body.preflight:overflow_chunks")
                require(type(row["truncated"]) is bool, "body.preflight:provenance_truncated")
        if self.transport_receipt:
            require(set(self.transport_receipt) == {
                "attempt_id", "request_sha256", "admission_sha256", "response_sha256",
            }, "body.preflight:transport_fields")
            require(self.transport_receipt["attempt_id"] == request.request_attempt_id,
                    "body.preflight:transport_attempt")
            require(all(type(value) is str and value.startswith("sha256:")
                        for key, value in self.transport_receipt.items() if key != "attempt_id"),
                    "body.preflight:transport_digest")

    @property
    def identity(self) -> str:
        return _receipt_identity(self)

def _receipt_identity(value: WorkerEpochReceipt) -> str:
    return digest_value({
        "request_identity": value.request_identity,
        "response_identity": value.response_identity,
        "representation": value.representation,
        "token_receipts": list(value.token_receipts),
        "eligibility": list(value.eligibility),
        "token_provenance": list(value.token_provenance),
        "transport_receipt": dict(value.transport_receipt),
        "admission_identity": value.admission_identity,
        "reachability_identity": value.reachability_identity,
    })


@dataclass(frozen=True, slots=True)
class CommonFreezeReceipt:
    """The rank-blind common set and the exact evidence used to freeze it."""

    common: CommonPositiveSet
    eligibility_identity: str
    worker_receipt_identities: tuple[str, ...]
    frozen_before_ranking: bool
    freeze_identity: str

    def require_current(self) -> None:
        require(CommonPositiveSet.is_factory_issued(self.common), "body.preflight:common_factory")
        require(self.frozen_before_ranking is True, "body.preflight:freeze_order")
        require(self.eligibility_identity.startswith("sha256:"), "body.preflight:eligibility_identity")
        require(len(self.worker_receipt_identities) > 0, "body.preflight:worker_receipts")
        require(self.freeze_identity == digest_value({
            "common_identity": self.common.identity,
            "eligibility_identity": self.eligibility_identity,
            "worker_receipt_identities": list(self.worker_receipt_identities),
            "frozen_before_ranking": True,
        }), "body.preflight:freeze_identity")

def _response_mapping(response: object) -> Mapping[str, Any]:
    require(isinstance(response, Mapping), "body.preflight:response_mapping")
    assert isinstance(response, Mapping)
    return response


def _mapping_rows(value: object, message: str) -> tuple[Mapping[str, Any], ...]:
    require(isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray)), message)
    rows = tuple(cast(Sequence[object], value))
    require(all(isinstance(row, Mapping) for row in rows), message + ":mapping")
    return tuple(cast(Mapping[str, Any], row) for row in rows)


def parse_worker_epoch_receipt(
    request: BodyStudyRequest, response: Mapping[str, Any]
) -> WorkerEpochReceipt:
    """Require explicit worker token/admission/reachability evidence."""
    request.require_current()
    value = _response_mapping(response)
    require(value.get("phase") == STUDY_PHASE, "body.preflight:response_phase")
    require(value.get("representation") == request.representation, "body.preflight:response_representation")
    require(value.get("request_identity") == request.identity, "body.preflight:response_request")
    require(value.get("status") == "completed", "body.preflight:worker_status")
    tokens = tuple(dict(row) for row in _mapping_rows(
        value.get("token_receipts"), "body.preflight:token_receipts"))
    eligibility = tuple(dict(row) for row in _mapping_rows(
        value.get("eligibility_receipts", ()), "body.preflight:eligibility_receipts"))
    response_identity = value.get("response_identity")
    admission_identity = value.get("admission_identity")
    reachability_identity = value.get("reachability_identity")
    require(type(response_identity) is str and response_identity.startswith("sha256:"),
            "body.preflight:response_identity")
    require(type(admission_identity) is str and admission_identity.startswith("sha256:"),
            "body.preflight:admission_identity")
    require(type(reachability_identity) is str and reachability_identity.startswith("sha256:"),
            "body.preflight:reachability_identity")
    assert isinstance(response_identity, str)
    assert isinstance(admission_identity, str)
    assert isinstance(reachability_identity, str)
    receipt = WorkerEpochReceipt(
        request.identity, response_identity, request.representation, tokens, eligibility,
        admission_identity, reachability_identity,
    )
    receipt.require_current(request)
    return receipt

def receipt_from_verified_epoch(
    request: BodyStudyRequest, evidence: Any, *, preparation: Any | None = None,
) -> WorkerEpochReceipt:
    """Project only an issued BodyEpochEvidence/VerifiedWorkerResponse pair.

    The generic mapping parser above remains a synthetic seam.  Native calls
    must use this adapter: no caller-supplied flags can become origin proof.
    """
    from retrieval_body_study_exchange import BodyEpochEvidence
    from retrieval_semantic_generator_output_receipt import VerifiedWorkerResponse, _response_identity
    require(type(evidence) is BodyEpochEvidence, "body.preflight:epoch_evidence_type")
    assert isinstance(evidence, BodyEpochEvidence)
    issued = getattr(BodyEpochEvidence, "is_factory_issued", None)
    require(callable(issued) and issued(evidence), "body.preflight:epoch_evidence_origin")
    response = getattr(evidence, "response", None)
    require(type(response) is VerifiedWorkerResponse, "body.preflight:verified_response_type")
    assert isinstance(response, VerifiedWorkerResponse)
    require(response.status == "completed" and response._sealed_identity == _response_identity(response),
            "body.preflight:verified_response_seal")
    require(evidence.attempt_id == request.request_attempt_id
            and evidence.representation == request.representation
            and evidence.request_sha256 == "sha256:" + hashlib.sha256(request.raw).hexdigest(),
            "body.preflight:epoch_request_join")
    require(response.attempt_id == request.request_attempt_id
            and response.request_records == request.records
            and response.input_identity == request.input_identity
            and response.representation_report_identity == request.descriptor["representation_report_identity"]
            and response.publication_identity == request.publication_identity,
            "body.preflight:response_request_join")
    generation = response.generation
    require(generation is not None, "body.preflight:generation_required")
    assert generation is not None
    embeddings = generation.embeddings
    require(isinstance(embeddings, tuple) and len(embeddings) == len(request.records),
            "body.preflight:generation_count")
    embeddings = cast(tuple[Any, ...], embeddings)
    tokens: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for record, embedding in zip(request.records, embeddings, strict=True):
        mode = SemanticMode(record.mode).value
        require(embedding.record_id == record.record_id and embedding.mode.value == mode,
                "body.preflight:generation_record_join")
        before = embedding.token_count_before_limit
        used = embedding.token_count_used
        overflow = embedding.overflow_chunks
        truncated = embedding.truncated
        require(type(before) is int and before >= 0 and type(used) is int
                and 0 <= used <= MAX_TOKENS and type(overflow) is int and overflow >= 0
                and type(truncated) is bool, "body.preflight:generation_token_fields")
        tokens.append({
            "record_id": record.record_id, "mode": mode, "token_count": used,
            "admitted": True, "captured_text_reachable": True, "truncated": truncated,
            "representation_identity": request.representation,
        })
        provenance.append({
            "record_id": record.record_id, "mode": mode,
            "token_count_before_limit": before, "token_count_used": used,
            "overflow_chunks": overflow, "truncated": truncated,
        })
    eligibility: tuple[Mapping[str, Any], ...] = ()
    admission_identity = getattr(response, "controller_admission_sha256", None)
    require(type(admission_identity) is str and admission_identity.startswith("sha256:"),
            "body.preflight:response_admission")
    assert isinstance(admission_identity, str)
    if request.ordinal == 2:
        require(preparation is not None, "body.preflight:preparation_for_eligibility")
        assert preparation is not None
        # The request retains the exact factory admission objects and their
        # query-ledger witness.  Do not recreate admissions or promote inert
        # preparation mappings into actual eligibility evidence here.
        selected_rows = preparation.matrix["selected_rows"]
        require(isinstance(selected_rows, Sequence), "body.preflight:selected_rows")
        raw_selected_rows = tuple(cast(Sequence[object], selected_rows))
        require(all(isinstance(row, Mapping) for row in raw_selected_rows),
                "body.preflight:selected_row_mapping")
        selected_rows = tuple(cast(Mapping[str, Any], row) for row in raw_selected_rows)
        rows = {
            (str(row["task_id"]), str(row["arm"])): dict(row)
            for row in selected_rows
        }
        actual_rows = {
            (str(row["task_id"]), str(row["arm"])): dict(row)
            for row in request.actual_query_rows()
        }
        for key, row in actual_rows.items():
            require_query_admission_row(rows.get(key), row)
        token_by_task = {
            record.record_id.removeprefix("query:"): token
            for record, token in zip(request.records, tokens, strict=True)
            if token["mode"] == "query"
        }
        body_cases = [case for case in preparation.catalog.cases if case.kind == "body_positive"]
        arms = ("native_tools_baseline", "postgres_native_fts:postgresql",
                "exact_vector:canonical-node-public-text-v2", "hybrid_rrf:canonical-node-public-text-v2")
        documents_reachable = all(
            token["admitted"] and token["captured_text_reachable"] and not token["truncated"]
            for token in tokens if token["mode"] == "document"
        )
        built: list[Mapping[str, Any]] = []
        for case in body_cases:
            query = token_by_task.get(case.task.task_id)
            require(query is not None, "body.preflight:body_query_receipt")
            assert query is not None
            for arm in arms:
                admission_row = rows.get((case.task.task_id, arm))
                require(admission_row is not None, "body.preflight:body_admission_row")
                assert admission_row is not None
                ready = admission_row.get("preparation") == "ready"
                reachable = (ready and documents_reachable
                             and admission_row.get("actual_surface") == "captured_text")
                built.append({
                    "task_id": case.task.task_id, "arm": arm, "admitted": ready and query["admitted"],
                    "captured_text_reachable": reachable and query["captured_text_reachable"],
                    "token_count": query["token_count"], "truncated": query["truncated"],
                    "representation_identity": ARM_REPRESENTATIONS[arm],
                })
        require(len(built) == 48, "body.preflight:eligibility_count")
        eligibility = tuple(built)
    response_identity = response._sealed_identity
    require(type(response_identity) is str and response_identity.startswith("sha256:"),
            "body.preflight:response_seal_identity")
    assert isinstance(response_identity, str)
    source_closure_digests = tuple(response.source_closure_digests)
    require(all(type(pair) is tuple and len(pair) == 2
                and type(pair[0]) is str and type(pair[1]) is str
                for pair in source_closure_digests),
            "body.preflight:source_closure_digest_type")
    reachability_identity = digest_value({
        "response_identity": response_identity,
        "eligibility": list(eligibility),
        "source_closure_digests": list(source_closure_digests),
    })
    receipt = WorkerEpochReceipt(
        request.identity, response_identity, request.representation,
        tuple(tokens), eligibility, admission_identity, reachability_identity,
        tuple(provenance),
        {"attempt_id": evidence.attempt_id, "request_sha256": evidence.request_sha256,
         "admission_sha256": evidence.admission_sha256, "response_sha256": evidence.response_sha256},
        _origin=_ISSUED,
    )
    receipt.require_current(request)
    object.__setattr__(receipt, "_seal", receipt.identity)
    return receipt


def validate_record_census(v1: WorkerEpochReceipt, v2: WorkerEpochReceipt) -> dict[str, int]:
    """Check both actual epochs and their aggregate 134-record census."""
    counts = {
        "v1_documents": sum(row["mode"] == "document" for row in v1.token_receipts),
        "v1_queries": sum(row["mode"] == "query" for row in v1.token_receipts),
        "v2_documents": sum(row["mode"] == "document" for row in v2.token_receipts),
        "v2_queries": sum(row["mode"] == "query" for row in v2.token_receipts),
    }
    require(counts == dict(PROSPECTIVE_CENSUS), "body.preflight:record_census")
    require(sum(counts.values()) == PROSPECTIVE_RECORDS, "body.preflight:record_total")
    return counts

def freeze_common_positive(
    *,
    task_meta: Sequence[Mapping[str, Any]],
    eligibility: Sequence[Mapping[str, Any]],
    worker_receipts: Sequence[WorkerEpochReceipt],
) -> CommonFreezeReceipt:
    """Freeze common positives from explicit pre-ranked admission evidence."""
    require(len(worker_receipts) == 2, "body.preflight:worker_epoch_count")
    rows = tuple(dict(row) for row in eligibility)
    require(len(rows) == 48, "body.preflight:common_receipt_count")
    for row in rows:
        require(set(row) == {
            "task_id", "arm", "admitted", "captured_text_reachable", "token_count",
            "truncated", "representation_identity",
        }, "body.preflight:common_receipt_fields")
        require(row["arm"] in COMPARISON_ARMS, "body.preflight:common_arm")
    common = select_common_positives(task_meta, rows)
    eligibility_identity = digest_value(rows)
    worker_ids = tuple(item.identity for item in worker_receipts)
    freeze_identity = digest_value({
        "common_identity": common.identity,
        "eligibility_identity": eligibility_identity,
        "worker_receipt_identities": list(worker_ids),
        "frozen_before_ranking": True,
    })
    receipt = CommonFreezeReceipt(common, eligibility_identity, worker_ids, True, freeze_identity)
    receipt.require_current()
    return receipt

__all__ = [
    "CommonFreezeReceipt",
    "WorkerEpochReceipt",
    "freeze_common_positive",
    "parse_worker_epoch_receipt",
    "receipt_from_verified_epoch",
    "validate_record_census",
]
