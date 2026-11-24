"""Live store-bound supplement worker-request builder and Epoch 2 public-text seal.

Binds ``prepare_supplement`` exchanges directly to the maintained
``PublicGenerationInput`` / ``build_worker_request`` owner (Epoch 1:
``canonical-node-v1``, 66 documents + 20 queries = 86 records) and
``PublicTextPreparation`` / ``parse_worker_request`` owner (Epoch 2:
``canonical-node-public-text-v2``, 40 documents + 20 queries = 60 records),
keeping the closed worker request/response schemas and 17-file worker closure
completely unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from types import MappingProxyType
from typing import Any

from retrieval_evaluation_schema import EvaluationTask
from retrieval_public_text_census import retained_task_sha256
from retrieval_public_text_contracts import PrivateMarkerScope, PublicTextRefusal
from retrieval_public_text_descriptors import (
    PublicTextPreparation, require_unchanged_recomputation,
)
from retrieval_public_text_request import ARM_PTX, check_request_caps
from retrieval_public_text_response import (
    ArmSeal, SealedRecord, StudySealRefusal, check_embedding, check_generation_cost,
)
from retrieval_semantic_generator_inputs import (
    PublicGenerationInput, build_worker_request,
)
from retrieval_semantic_generator_kernel import (
    QUERY_PREFIX, SemanticGeneration, SemanticMode, SemanticText,
)
from retrieval_semantic_generator_output_receipt import (
    VerifiedWorkerResponse, _response_identity,
)
from retrieval_semantic_generator_worker_contracts import (
    ATTEMPT_ID_PATTERN, REQUEST_SCHEMA, WorkerRequestError,
)
from retrieval_semantic_generator_worker_protocol import parse_worker_request
from retrieval_supplement_study_adapter import (
    EXPECTED_COUNTS, WorkerExchange, attempt_for_exchange,
    parse_exchanges, validate_built_request_records,
)
from retrieval_supplement_study_contracts import SLOT_COUNT, TOTAL_RECORDS, refuse
from retrieval_supplement_primitives import canonical_bytes, digest_json


@dataclass(frozen=True)
class LiveStudyRequests:
    """Two validated worker requests bound to current store pins and descriptors."""

    v1_exchange: WorkerExchange
    v2_exchange: WorkerExchange
    v1_request: Mapping[str, Any]
    v2_request: Mapping[str, Any]
    v1_request_raw: bytes = field(repr=False)
    v2_request_raw: bytes = field(repr=False)
    v2_records: tuple[SemanticText, ...] = field(repr=False)
    v2_source_identities: tuple[str, ...]
    v2_query_task_ids: tuple[str, ...]


def build_live_study_requests(
    preparation: Mapping[str, Any], *,
    v1_input: PublicGenerationInput,
    public_text: PublicTextPreparation,
    tasks: Sequence[EvaluationTask],
    attempt_id: str,
    artifact_profile_identity: str,
    expected_counts: Sequence[tuple[int, int]] = EXPECTED_COUNTS,
    expected_total: int = TOTAL_RECORDS,
    expected_slots: int = SLOT_COUNT,
) -> LiveStudyRequests:
    """Bind real ``PublicGenerationInput`` and ``PublicTextPreparation`` into two worker requests."""
    refuse(type(v1_input) is PublicGenerationInput, "adapter:v1_input_type")
    refuse(type(public_text) is PublicTextPreparation, "adapter:public_text_type")
    refuse(type(attempt_id) is str and ATTEMPT_ID_PATTERN.fullmatch(attempt_id) is not None,
           "adapter:attempt_required")
    v1_ex, v2_ex = parse_exchanges(
        preparation, expected_counts=expected_counts,
        expected_total=expected_total, expected_slots=expected_slots,
    )
    v1_doc_pairs = {
        (doc.representation.source_binding_id, doc.representation.canonical_key)
        for doc in v1_input.documents
    }
    expected_v1_pairs = {
        (str(row["binding_id"]), str(row["canonical_key"])) for row in v1_ex.documents
    }
    refuse(len(v1_input.documents) == len(v1_ex.documents) and v1_doc_pairs == expected_v1_pairs,
           "adapter:v1_document_set_mismatch")
    v1_admitted_tasks = [d.task_id for d in v1_input.query_decisions if d.record_id is not None]
    refuse(v1_admitted_tasks == [str(q["task_id"]) for q in v1_ex.queries],
           "adapter:v1_query_order_mismatch")
    v1_attempt = attempt_for_exchange(attempt_id, v1_ex.representation)
    v1_request = build_worker_request(
        v1_input, attempt_id=v1_attempt, artifact_profile_identity=artifact_profile_identity,
    )
    validate_built_request_records(v1_request, v1_ex, v1_input)
    v1_raw = canonical_bytes(dict(v1_request))
    parse_worker_request(v1_raw)

    ptx_tuples = {
        (str(doc.descriptor["source_binding_id"]), str(doc.descriptor["canonical_key"]),
         str(doc.descriptor["source_relative_path"]), int(doc.descriptor["start_line"]),
         int(doc.descriptor["end_line"]))
        for doc in public_text.documents
    }
    expected_v2_tuples = {
        (str(row["binding_id"]), str(row["canonical_key"]),
         str(row["source_relative_path"]), int(row["start_line"]), int(row["end_line"]))
        for row in v2_ex.documents
    }
    refuse(len(public_text.documents) == len(v2_ex.documents) and ptx_tuples == expected_v2_tuples,
           "adapter:v2_document_set_mismatch")
    tasks_by_id = {task.task_id: task for task in tasks}
    v2_records: list[SemanticText] = []
    v2_sources: list[str] = []
    for ordinal, doc in enumerate(public_text.documents, start=1):
        v2_records.append(SemanticText(f"ptx.d.{ordinal:04d}", doc.model_text, SemanticMode.DOCUMENT))
        v2_sources.append(doc.identity)
    v2_query_tasks: list[str] = []
    for query in v2_ex.queries:
        task_id = str(query["task_id"])
        task = tasks_by_id.get(task_id)
        if task is None:
            refuse(False, "adapter:v2_query_task_mismatch")
            continue
        refuse(task.question == query["text"], "adapter:v2_query_task_mismatch")
        v2_records.append(SemanticText(f"ptx.q.{task_id}", task.question, SemanticMode.QUERY))
        v2_sources.append(retained_task_sha256(task))
        v2_query_tasks.append(task_id)
    prep_identity = digest_json(public_text.to_dict())
    v2_manifest = {
        "schema": "cloe-supplement-study-v2-input-v1",
        "preparation_identity": prep_identity,
        "publication_identity": public_text.publication_identity,
        "records": [
            [rec.record_id, SemanticMode(rec.mode).value,
             "sha256:" + hashlib.sha256(rec.text.encode("utf-8")).hexdigest(), src]
            for rec, src in zip(v2_records, v2_sources, strict=True)
        ],
    }
    v2_attempt = attempt_for_exchange(attempt_id, v2_ex.representation)
    v2_request: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "attempt_id": v2_attempt,
        "input_identity": digest_json(v2_manifest),
        "representation_report_identity": prep_identity,
        "publication_identity": public_text.publication_identity,
        "artifact_profile_identity": artifact_profile_identity,
        "records": [
            {"record_id": rec.record_id, "text": rec.text, "mode": SemanticMode(rec.mode).value}
            for rec in v2_records
        ],
    }
    validate_built_request_records(v2_request, v2_ex, None)
    v2_raw = canonical_bytes(v2_request)
    check_request_caps(v2_records, request_bytes=len(v2_raw))
    try:
        _parsed_v2, parsed_records = parse_worker_request(v2_raw)
    except WorkerRequestError as exc:
        raise ValueError(f"adapter:v2_worker_request_{exc.code}") from exc
    refuse(parsed_records == tuple(v2_records), "adapter:v2_roundtrip_mismatch")
    return LiveStudyRequests(
        v1_ex, v2_ex, v1_request, v2_request, v1_raw, v2_raw,
        tuple(v2_records), tuple(v2_sources), tuple(v2_query_tasks),
    )


def seal_v2_worker_response(
    live: LiveStudyRequests,
    response: VerifiedWorkerResponse,
    *,
    public_text: PublicTextPreparation,
    fresh_public_text: Callable[[], PublicTextPreparation],
    scope: PrivateMarkerScope,
) -> ArmSeal:
    """Validate and seal the Epoch 2 public-text worker response into a genuine ``ArmSeal``."""
    refuse(type(response) is VerifiedWorkerResponse and response.status == "completed"
           and response._sealed_identity == _response_identity(response),
           "adapter:v2_response_identity")
    expected = live.v2_request
    refuse(response.request_records == live.v2_records
           and response.attempt_id == expected["attempt_id"]
           and response.input_identity == expected["input_identity"]
           and response.representation_report_identity == expected["representation_report_identity"]
           and response.publication_identity == expected["publication_identity"]
           and response.artifact_profile_identity == expected["artifact_profile_identity"],
           "adapter:v2_response_request_mismatch")
    try:
        refreshed = fresh_public_text()
        require_unchanged_recomputation(public_text, refreshed, scope=scope)
    except PublicTextRefusal as exc:
        raise ValueError(f"adapter:v2_recomputation_{exc.code}") from exc
    generation = response.generation
    refuse(type(generation) is SemanticGeneration
           and len(generation.embeddings) == len(live.v2_records),
           "adapter:v2_generation_count")
    assert isinstance(generation, SemanticGeneration)
    try:
        vectors = tuple(
            check_embedding(record, observed)
            for record, observed in zip(live.v2_records, generation.embeddings, strict=True)
        )
        check_generation_cost(
            generation.cost, live.v2_records, generation.embeddings,
            status=generation.status, partial=generation.partial,
        )
    except StudySealRefusal as exc:
        raise ValueError(f"adapter:v2_seal_{exc.code}") from exc
    sealed_records: list[SealedRecord] = []
    truncated_docs: list[str] = []
    truncated_queries: list[str] = []
    query_idx = 0
    for rec, src, emb, vec in zip(
        live.v2_records, live.v2_source_identities, generation.embeddings, vectors, strict=True,
    ):
        mode = SemanticMode(rec.mode)
        sealed_records.append(SealedRecord(
            rec.record_id, ARM_PTX, mode, src, vec,
            emb.token_count_before_limit, emb.token_count_used,
            emb.overflow_chunks, emb.truncated,
        ))
        if mode is SemanticMode.DOCUMENT:
            if emb.truncated:
                truncated_docs.append(src)
        else:
            task_id = live.v2_query_task_ids[query_idx]
            query_idx += 1
            if emb.truncated:
                truncated_queries.append(task_id)
    any_truncated = any(item.truncated for item in sealed_records)
    doc_count = len(public_text.documents)
    counts = {"records": len(sealed_records), "documents": doc_count,
              "queries": len(sealed_records) - doc_count}
    exact_cost = {
        **counts,
        "input_bytes": sum(len(r.record_id.encode("utf-8")) + len(r.text.encode("utf-8"))
                           for r in live.v2_records),
        "model_input_bytes": sum(
            len(((QUERY_PREFIX if r.mode is SemanticMode.QUERY else "") + r.text).encode("utf-8"))
            for r in live.v2_records
        ),
        "token_count_before_limit": sum(r.token_count_before_limit for r in sealed_records),
        "token_count_used": sum(r.token_count_used for r in sealed_records),
        "overflow_chunks": sum(r.overflow_chunks for r in sealed_records),
        "truncated_records": sum(1 for r in sealed_records if r.truncated),
    }
    ptx_partial = bool(public_text.exclusions) or doc_count < public_text.v1_verified_pair_count
    return ArmSeal(
        ARM_PTX, "token_partial" if any_truncated else "complete", None,
        tuple(sealed_records), MappingProxyType(counts), MappingProxyType(exact_cost),
        tuple(truncated_docs), tuple(truncated_queries),
        "partial" if ptx_partial else "complete",
    )


__all__ = ["LiveStudyRequests", "build_live_study_requests", "seal_v2_worker_response"]
