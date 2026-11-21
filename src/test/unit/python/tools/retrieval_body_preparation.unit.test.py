"""Inert body query records use only unrelated unit-body functional inputs."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from repomap_test_support.retrieval_body_fixtures import (
    make_body_bindings,
    make_body_catalog_payload,
    make_body_identity,
)
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_records import (
    BodyRecordError,
    FUSION_ARM,
    PUBLIC_TEXT_V2_ARM,
    _build_query_records,
    build_body_query_records,
    estimate_body_records,
)
from retrieval_body_values import canonical_bytes, digest_bytes
from retrieval_semantic_generator_kernel_contracts import SemanticMode, SemanticText
from retrieval_supplement_tasks import task_digests


PIN = digest_bytes(b"unit-body-input-pin")
PUBLICATION = {"graph_id": "unit-body-canopy", "generation": 1, "run_id": 1}


def _catalog():
    identity = make_body_identity()
    return parse_functional_catalog(
        canonical_bytes(make_body_catalog_payload()),
        identity=identity,
        binding_ids=tuple(make_body_bindings().values()),
    )


def _row(case, *, arm=PUBLIC_TEXT_V2_ARM, publication=None, **changes):
    task = case.task
    digests = task_digests(task)
    row = {
        "class_id": make_body_identity().class_id,
        "catalog_sha256": _catalog().digest,
        "case_sha256": case.digest,
        "input_pin_sha256": PIN,
        "task_id": task.task_id,
        "task_sha256": digests["task_sha256"],
        "retained_task_sha256": digests["retained_task_sha256"],
        "generation": task.generation,
        "arm": arm,
        "preparation": "ready",
        "reason": None,
        "actual_surface": "captured_text",
        "fallback_policy": "none",
        "publication_identity": PUBLICATION if publication is None else publication,
        "representation_identity": "canonical-node-public-text-v2",
    }
    row.update(changes)
    return row


def _entry(case, *, arm=PUBLIC_TEXT_V2_ARM, identity="functional-admission", **changes):
    return case.task, _row(case, arm=arm, **changes), identity


def test_public_builder_rejects_functional_objects_and_pretenders():
    with pytest.raises(BodyRecordError, match="admission_type"):
        build_body_query_records([cast(Any, SimpleNamespace(task=object()))])


def test_private_core_preserves_exact_utf8_query_and_omits_text_from_diagnostics():
    case = _catalog().cases[0]
    ledger = _build_query_records([_entry(case)])
    record = ledger.records[0]
    assert record.semantic_text.text == case.task.question
    assert record.semantic_text.mode is SemanticMode.QUERY
    assert record.semantic_text.record_id == "query:" + case.task.task_id
    assert record.to_dict()["input_policy"] == "cloe-w5-public-generation-input-v1"
    assert "question" not in record.to_dict() and "text" not in record.to_dict()
    assert record.question_sha256 == digest_bytes(case.task.question.encode("utf-8"))


def test_fusion_reuses_v2_identity_without_creating_a_second_query():
    case = _catalog().cases[0]
    ledger = _build_query_records([_entry(case), _entry(case, arm=FUSION_ARM)])
    assert len(ledger.records) == 1
    assert [item.arm for item in ledger.decisions] == [PUBLIC_TEXT_V2_ARM, FUSION_ARM]


def test_exact_v2_metadata_anchor_uses_stored_metadata_without_rewriting_question():
    case = _catalog().cases[12]
    row = _row(case, actual_surface="stored_metadata")
    ledger = _build_query_records([(case.task, row, "functional-anchor")])
    assert ledger.records[0].semantic_text.text == case.task.question
    assert ledger.records[0].semantic_text.mode is SemanticMode.QUERY


@pytest.mark.parametrize("change,code", [
    ({"class_id": "other-class"}, "mixed_frame"),
    ({"catalog_sha256": digest_bytes(b"other-catalog")}, "mixed_frame"),
    ({"input_pin_sha256": digest_bytes(b"other-pin")}, "mixed_frame"),
    ({"generation": 2}, "decision_generation"),
])
def test_private_core_rejects_drift(change, code):
    catalog = _catalog()
    with pytest.raises(BodyRecordError, match=code):
        if code in {"mixed_frame", "mixed_frame", "mixed_frame"}:
            _build_query_records([_entry(catalog.cases[0]), _entry(catalog.cases[1], **change)])
        else:
            _build_query_records([_entry(catalog.cases[0], **change)])


def test_private_core_rejects_mixed_publications():
    catalog = _catalog()
    other = dict(PUBLICATION, run_id=2)
    with pytest.raises(BodyRecordError, match="mixed_publication"):
        _build_query_records([_entry(catalog.cases[0]), _entry(catalog.cases[1], publication=other)])


def test_private_core_rejects_more_than_24_unique_tasks():
    base = _catalog().cases[0].task
    entries = []
    for number in range(1, 26):
        task = replace(base, task_id=f"unit-body-{number:02d}", question=f"Canopy intent {number}?")
        case = SimpleNamespace(task=task, digest=_catalog().cases[0].digest)
        entries.append((task, _row(case), f"functional-{number}"))
    with pytest.raises(BodyRecordError, match="task_count"):
        _build_query_records(entries)


def test_records_are_defensive_against_publication_mapping_mutation():
    case = _catalog().cases[0]
    publication = dict(PUBLICATION)
    ledger = _build_query_records([_entry(case, publication=publication)])
    publication["generation"] = 99
    assert ledger.records[0].publication_identity["generation"] == 1
    serialized = ledger.to_dict()
    serialized["records"][0]["publication_identity"]["generation"] = 88
    assert ledger.records[0].publication_identity["generation"] == 1


def test_estimator_counts_actual_utf8_partitions_and_marks_embeddings_planned():
    documents = (SemanticText("document:1", "café", SemanticMode.DOCUMENT),)
    queries = (SemanticText("query:1", "¿qué?", SemanticMode.QUERY),)
    estimate = estimate_body_records(documents, queries, documents, queries)
    assert estimate.v1.documents == estimate.v2.documents == 1
    assert estimate.v1.queries == estimate.v2.queries == 1
    assert estimate.combined_records == 4
    assert estimate.embedding_count_status == "PLANNED"
    assert estimate.to_dict()["executed_embeddings"] is False
    assert estimate.to_dict()["token_counts"] == "unobserved"


def test_estimator_rejects_wrong_modes_duplicate_ids_and_document_cap():
    with pytest.raises(BodyRecordError, match="partition_mode"):
        estimate_body_records((SemanticText("query:1", "q", SemanticMode.QUERY),), (), (), ())
    duplicate = SemanticText("document:1", "other", SemanticMode.QUERY)
    with pytest.raises(BodyRecordError, match="partition_duplicate_record_id"):
        estimate_body_records((SemanticText("document:1", "one", SemanticMode.DOCUMENT),), (duplicate,), (), ())
    docs = tuple(SemanticText(f"document:{i}", "x", SemanticMode.DOCUMENT) for i in range(81))
    with pytest.raises(BodyRecordError, match="document_cap"):
        estimate_body_records(docs, (), (), ())


def test_estimator_preserves_kernel_input_and_combined_byte_guards():
    from retrieval_semantic_generator_kernel_contracts import MAX_RECORD_TEXT_BYTES
    oversized = SemanticText("document:large", "x" * (MAX_RECORD_TEXT_BYTES + 1), SemanticMode.DOCUMENT)
    with pytest.raises(BodyRecordError, match="partition_semantic_input"):
        estimate_body_records((oversized,), (), (), ())
    blob = "x" * MAX_RECORD_TEXT_BYTES
    first = tuple(SemanticText(f"document:a-{i}", blob, SemanticMode.DOCUMENT) for i in range(5))
    second = tuple(SemanticText(f"document:b-{i}", blob, SemanticMode.DOCUMENT) for i in range(5))
    with pytest.raises(BodyRecordError, match="combined_text_byte_cap"):
        estimate_body_records(first, (), second, ())
