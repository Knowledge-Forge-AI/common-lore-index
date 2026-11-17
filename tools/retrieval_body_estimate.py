"""Pure bounded accounting for supplied v1/v2 semantic-text partitions."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from retrieval_body_record_types import (
    BodyRecordError,
    MAX_COMBINED_RECORDS,
    MAX_DOCUMENTS_PER_REPRESENTATION,
    MAX_QUERIES_PER_REPRESENTATION,
    PROSPECTIVE_MAX_TOKENS,
    _require,
    _text,
)
from retrieval_semantic_generator_kernel_contracts import MAX_TOTAL_TEXT_BYTES, QUERY_PREFIX, SemanticMode, SemanticText
from retrieval_semantic_generator_kernel_validation import validated_inputs


@dataclass(frozen=True, slots=True)
class PartitionEstimate:
    """Actual counts and UTF-8 byte totals for one representation."""

    documents: int
    queries: int
    records: int
    text_utf8_bytes: int
    source_utf8_bytes: int

    def to_dict(self) -> dict[str, int]:
        return {
            "documents": self.documents,
            "queries": self.queries,
            "records": self.records,
            "text_utf8_bytes": self.text_utf8_bytes,
            "source_utf8_bytes": self.source_utf8_bytes,
        }


@dataclass(frozen=True, slots=True)
class BodyRecordEstimate:
    """Actual counts plus explicitly unobserved prospective costs."""

    v1: PartitionEstimate
    v2: PartitionEstimate
    combined_records: int
    combined_text_utf8_bytes: int
    combined_source_utf8_bytes: int
    combined_model_text_utf8_bytes: int
    prospective_max_tokens: int = PROSPECTIVE_MAX_TOKENS
    embedding_count_planned: int = 0
    embedding_count_status: str = "PLANNED"
    token_counts: str = "unobserved"
    truncation: str = "unobserved"
    runtime_costs: str = "unobserved"

    @property
    def total_records(self) -> int:
        return self.combined_records

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "cloe-body-record-estimate-v1",
            "representations": {
                "canonical-node-v1": self.v1.to_dict(),
                "canonical-node-public-text-v2": self.v2.to_dict(),
            },
            "combined_records": self.combined_records,
            "combined_text_utf8_bytes": self.combined_text_utf8_bytes,
            "combined_source_utf8_bytes": self.combined_source_utf8_bytes,
            "combined_model_text_utf8_bytes": self.combined_model_text_utf8_bytes,
            "prospective_max_tokens": self.prospective_max_tokens,
            "embedding_count": {
                "value": self.embedding_count_planned,
                "status": self.embedding_count_status,
            },
            "embedding_count_planned": self.embedding_count_planned,
            "embedding_count_status": self.embedding_count_status,
            "token_counts": self.token_counts,
            "truncation": self.truncation,
            "runtime_costs": self.runtime_costs,
            "executed_embeddings": False,
        }


def _partition(records: Sequence[SemanticText], expected: SemanticMode) -> PartitionEstimate:
    _require(
        isinstance(records, Sequence) and not isinstance(records, (str, bytes, bytearray)),
        "partition_sequence",
    )
    if records:
        try:
            validated_inputs(tuple(records))
        except (TypeError, ValueError) as error:
            raise BodyRecordError("partition_semantic_input") from error
    seen: set[str] = set()
    documents = queries = text_bytes = source_bytes = 0
    for record in records:
        _require(type(record) is SemanticText, "partition_record_type")
        _require(record.record_id not in seen, "partition_duplicate_record_id")
        seen.add(record.record_id)
        _text(record.record_id, "partition_record_id")
        text = _text(record.text, "partition_text")
        try:
            mode = SemanticMode(record.mode)
        except (TypeError, ValueError) as exc:
            raise BodyRecordError("partition_mode") from exc
        _require(mode is expected, "partition_mode")
        documents += mode is SemanticMode.DOCUMENT
        queries += mode is SemanticMode.QUERY
        size = len(text.encode("utf-8"))
        text_bytes += size
        source_bytes += len(record.record_id.encode("utf-8")) + size
    _require(documents <= MAX_DOCUMENTS_PER_REPRESENTATION, "document_cap")
    _require(queries <= MAX_QUERIES_PER_REPRESENTATION, "query_cap")
    return PartitionEstimate(documents, queries, len(records), text_bytes, source_bytes)


def _representation(
    documents: Sequence[SemanticText], queries: Sequence[SemanticText]
) -> PartitionEstimate:
    docs = _partition(documents, SemanticMode.DOCUMENT)
    query_rows = _partition(queries, SemanticMode.QUERY)
    ids = [item.record_id for item in (*documents, *queries)]
    _require(len(ids) == len(set(ids)), "partition_duplicate_record_id")
    result = PartitionEstimate(
        docs.documents,
        query_rows.queries,
        docs.records + query_rows.records,
        docs.text_utf8_bytes + query_rows.text_utf8_bytes,
        docs.source_utf8_bytes + query_rows.source_utf8_bytes,
    )
    _require(result.records == result.documents + result.queries, "partition_count_sum")
    return result


def estimate_body_records(
    v1_documents: Sequence[SemanticText],
    v1_queries: Sequence[SemanticText],
    v2_documents: Sequence[SemanticText],
    v2_queries: Sequence[SemanticText],
) -> BodyRecordEstimate:
    """Count actual partitions without tokenization, model, or runtime."""
    v1 = _representation(v1_documents, v1_queries)
    v2 = _representation(v2_documents, v2_queries)
    combined = v1.records + v2.records
    _require(combined <= MAX_COMBINED_RECORDS, "combined_record_cap")
    model_bytes = (v1.text_utf8_bytes + v2.text_utf8_bytes
                   + (v1.queries + v2.queries) * len(QUERY_PREFIX.encode("utf-8")))
    _require(v1.source_utf8_bytes + v2.source_utf8_bytes <= MAX_TOTAL_TEXT_BYTES
             and model_bytes <= MAX_TOTAL_TEXT_BYTES, "combined_text_byte_cap")
    return BodyRecordEstimate(
        v1,
        v2,
        combined,
        v1.text_utf8_bytes + v2.text_utf8_bytes,
        v1.source_utf8_bytes + v2.source_utf8_bytes,
        model_bytes,
        embedding_count_planned=combined,
    )


estimate_body_record_budget = estimate_body_records

__all__ = ["BodyRecordEstimate", "PartitionEstimate", "estimate_body_record_budget", "estimate_body_records"]
