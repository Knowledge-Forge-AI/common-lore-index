"""Inert anchor-only v1 query preparation and record-census limits.

Outputs are data only: no model or tokenizer receipt is claimed.  The v2
query ledger reuses the existing body record machinery unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from retrieval_body_catalog import BodyCatalog, BodyCase
from retrieval_body_record_types import INPUT_POLICY
from retrieval_body_study_values import (
    ANCHOR_COUNT, INERT_FLAGS, MAX_COMBINED_RECORDS, MAX_DOCUMENTS_PER_REPRESENTATION,
    MAX_EXCERPT_BYTES, MAX_QUERIES_PER_REPRESENTATION, MAX_STREAM_BYTES, MAX_TOKENS,
    PROSPECTIVE_CENSUS, PROSPECTIVE_RECORDS, REGISTERED_INPUT_PIN_SHA256, STUDY_CLASS_ID, V1_ARM,
    V1_REPRESENTATION, BodyStudyError, digest_value, is_digest, require,
)
from retrieval_body_values import digest_bytes
from retrieval_semantic_generator_kernel_contracts import SemanticMode, SemanticText
from retrieval_semantic_generator_kernel_validation import validated_inputs
from retrieval_supplement_tasks import task_digests


@dataclass(frozen=True, slots=True)
class AnchorV1QueryRecord:
    """One inert canonical-node-v1 query for a metadata anchor only."""

    semantic_text: SemanticText
    task_id: str
    case_sha256: str
    task_sha256: str
    catalog_sha256: str
    input_pin_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.semantic_text.record_id, "mode": "query",
            "representation_identity": V1_REPRESENTATION,
            "study_class_id": STUDY_CLASS_ID, "task_id": self.task_id,
            "case_sha256": self.case_sha256, "task_sha256": self.task_sha256,
            "catalog_sha256": self.catalog_sha256,
            "input_pin_sha256": self.input_pin_sha256,
            "text_sha256": digest_bytes(self.semantic_text.text.encode("utf-8")),
            "text_utf8_bytes": len(self.semantic_text.text.encode("utf-8")),
            "input_policy": INPUT_POLICY, "token_counts": "unobserved",
            "truncation": "unobserved", **INERT_FLAGS,
        }

    @property
    def identity(self) -> str:
        return digest_value(self.to_dict())


def prepare_anchor_v1_queries(
    catalog: BodyCatalog, selected_rows: Sequence[Mapping[str, Any]],
) -> tuple[AnchorV1QueryRecord, ...]:
    """Prepare exactly the ready metadata-anchor v1 queries (prospective 4).

    Body positives, privacy and unsupported cases can never acquire a v1
    semantic query here; a ready v1 row for any of them is a refusal, not a
    silently skipped record.
    """
    require(BodyCatalog.is_factory_issued(catalog), "body_study:catalog_not_issued")
    cases: dict[str, BodyCase] = {case.task.task_id: case for case in catalog.cases}
    records: list[AnchorV1QueryRecord] = []
    seen: set[str] = set()
    frame_pin: str | None = None
    frame_publication: str | None = None
    for row in selected_rows:
        require(isinstance(row, Mapping), "body_study:selected_row_mapping")
        if row.get("arm") != V1_ARM:
            continue
        task_id = row.get("task_id")
        require(type(task_id) is str and task_id in cases, "body_study:v1_task_membership")
        assert isinstance(task_id, str)
        require(task_id not in seen, "body_study:v1_duplicate_row")
        seen.add(task_id)
        case = cases[task_id]
        require(row.get("class_id") == catalog.identity.class_id
                and row.get("catalog_sha256") == catalog.digest
                and row.get("case_sha256") == case.digest, "body_study:v1_case_identity")
        digests = task_digests(case.task)
        require(all(row.get(key) == value for key, value in digests.items()),
                "body_study:v1_task_digest")
        pin = row.get("input_pin_sha256")
        require(is_digest(pin), "body_study:v1_input_pin")
        require(not catalog.registered or pin == REGISTERED_INPUT_PIN_SHA256,
                "body_study:v1_registered_pin")
        if frame_pin is None:
            frame_pin = str(pin)
        require(pin == frame_pin, "body_study:v1_mixed_pin")
        if row.get("preparation") != "ready":
            continue
        require(case.kind == "metadata_anchor", "body_study:v1_anchor_only")
        require(row.get("case_kind") == "metadata_anchor", "body_study:v1_row_kind")
        require(case.task.requested_mode == "ranked_discovery", "body_study:v1_mode")
        require(row.get("representation_identity") == V1_REPRESENTATION,
                "body_study:v1_representation")
        require(row.get("selected_backend") == "postgresql", "body_study:v1_backend")
        publication = row.get("publication_identity")
        require(isinstance(publication, Mapping) and publication.get("backend") == "postgresql"
                and publication.get("graph_id") == catalog.identity.graph_id,
                "body_study:v1_publication")
        identity = digest_value(publication)
        if frame_publication is None:
            frame_publication = identity
        require(identity == frame_publication, "body_study:v1_mixed_publication")
        source = SemanticText("query:" + task_id, case.task.question, SemanticMode.QUERY)
        try:
            checked = validated_inputs((source,))[0]
        except (KeyError, TypeError, ValueError) as exc:
            raise BodyStudyError("body_study:v1_semantic_input") from exc
        require(len(checked) == 1 and checked[0].text == case.task.question,
                "body_study:v1_question_rewritten")
        records.append(AnchorV1QueryRecord(
            checked[0], task_id, case.digest, digests["task_sha256"],
            catalog.digest, str(pin)))
    require(seen == set(cases), "body_study:v1_row_completeness")
    require(len(records) <= ANCHOR_COUNT, "body_study:v1_anchor_count")
    require(len(records) <= MAX_QUERIES_PER_REPRESENTATION, "body_study:v1_query_cap")
    records.sort(key=lambda item: item.task_id.encode())
    return tuple(records)


def validate_record_census(
    *, v1_documents: int, v1_queries: int, v2_documents: int, v2_queries: int,
) -> dict[str, Any]:
    """Enforce closed caps and report distance from the prospective census."""
    counts = {"v1_documents": v1_documents, "v1_queries": v1_queries,
              "v2_documents": v2_documents, "v2_queries": v2_queries}
    for name, value in counts.items():
        require(type(value) is int and value >= 0, f"body_study:census_{name}")
    require(v1_documents <= MAX_DOCUMENTS_PER_REPRESENTATION
            and v2_documents <= MAX_DOCUMENTS_PER_REPRESENTATION, "body_study:document_cap")
    require(v1_queries <= MAX_QUERIES_PER_REPRESENTATION
            and v2_queries <= MAX_QUERIES_PER_REPRESENTATION, "body_study:query_cap")
    combined = sum(counts.values())
    require(combined <= MAX_COMBINED_RECORDS, "body_study:combined_record_cap")
    return {
        **counts, "combined_records": combined,
        "prospective_census": dict(PROSPECTIVE_CENSUS),
        "prospective_records": PROSPECTIVE_RECORDS,
        "matches_prospective_census": counts == PROSPECTIVE_CENSUS,
        "census_status": "prospective_source_count_not_execution_evidence",
        "token_counts": "unobserved", **INERT_FLAGS,
    }


def require_query_admission_row(
    selected: Mapping[str, Any] | None, admission: Mapping[str, Any],
) -> None:
    """Join the projected matrix row to the full live admission decision."""
    require(
        selected is not None
        and selected.get("admission_identity") == "mapping:" + digest_value(admission)
        and {"preparation", "actual_surface"} <= selected.keys()
        and all(selected[name] == admission[name] for name in selected.keys() & admission.keys()),
        "body.preflight:query_admission_matrix_join",
    )


def validate_text_limits(
    *, token_count: int | None, excerpt_bytes: int, stream_bytes: int,
) -> dict[str, Any]:
    """Pure supplied-count limit check; ``None`` stays unobserved, never fit."""
    require(type(excerpt_bytes) is int and 0 <= excerpt_bytes <= MAX_EXCERPT_BYTES,
            "body_study:excerpt_cap")
    require(type(stream_bytes) is int and 0 <= stream_bytes <= MAX_STREAM_BYTES,
            "body_study:stream_cap")
    if token_count is not None:
        require(type(token_count) is int and 0 <= token_count <= MAX_TOKENS,
                "body_study:token_cap")
    return {
        "token_count": token_count,
        "tokenizer_observed": False,
        "tokenizer_origin_proven": False,
        "supplied_count_present": token_count is not None,
        "excerpt_bytes": excerpt_bytes, "stream_bytes": stream_bytes,
        "truncated": "unobserved",
    }


__all__ = [
    "AnchorV1QueryRecord", "prepare_anchor_v1_queries",
    "validate_record_census", "validate_text_limits",
]
