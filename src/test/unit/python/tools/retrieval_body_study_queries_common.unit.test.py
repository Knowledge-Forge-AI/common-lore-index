"""Anchor v1 queries, census limits and common-positive selection."""
from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, cast

import pytest

from common_lore_index_test_support.retrieval_body_study_fixtures import study_reports
from retrieval_body_study_common import CommonPositiveSet, select_common_positives
from retrieval_body_study_join import select_body_study_matrix
from retrieval_body_study_queries import (
    prepare_anchor_v1_queries, require_query_admission_row, validate_record_census, validate_text_limits,
)
from retrieval_body_study_values import (
    ARM_REPRESENTATIONS, BODY_FAMILIES, COMPARISON_ARMS, V1_ARM, BodyStudyError, digest_value,
)


def _selected():
    catalog, sqlite, postgres = study_reports()
    return catalog, select_body_study_matrix(sqlite, postgres, catalog=catalog)["selected_rows"]


def test_anchor_v1_queries_are_anchor_only_and_inert():
    catalog, rows = _selected()
    records = prepare_anchor_v1_queries(catalog, rows)
    assert len(records) == 4
    kinds = {case.task.task_id: case.kind for case in catalog.cases}
    assert all(kinds[r.task_id] == "metadata_anchor" for r in records)
    assert all(r.to_dict()["tokenizer_observed"] is False for r in records)


def test_ready_v1_body_or_privacy_row_refuses_not_skips():
    catalog, rows = _selected()
    bad = [dict(r) for r in rows]
    target = next(r for r in bad if r["arm"] == V1_ARM and r["case_kind"] == "body_positive")
    target.update(preparation="ready", reason=None, actual_surface="stored_metadata")
    with pytest.raises(BodyStudyError, match="v1_anchor_only"):
        prepare_anchor_v1_queries(catalog, bad)


def test_census_caps_and_prospective_134():
    result = validate_record_census(v1_documents=66, v1_queries=4, v2_documents=44, v2_queries=20)
    assert result["combined_records"] == 134 and result["matches_prospective_census"] is True
    with pytest.raises(BodyStudyError, match="document_cap"):
        validate_record_census(v1_documents=81, v1_queries=0, v2_documents=0, v2_queries=0)
    with pytest.raises(BodyStudyError, match="query_cap"):
        validate_record_census(v1_documents=0, v1_queries=25, v2_documents=0, v2_queries=0)


def test_text_limits_never_claim_unobserved_tokenizer_fit():
    assert validate_text_limits(token_count=None, excerpt_bytes=1, stream_bytes=2)["truncated"] == "unobserved"
    with pytest.raises(BodyStudyError, match="token_cap"):
        validate_text_limits(token_count=513, excerpt_bytes=1, stream_bytes=2)
    with pytest.raises(BodyStudyError, match="excerpt_cap"):
        validate_text_limits(token_count=1, excerpt_bytes=4097, stream_bytes=2)


def test_query_admission_checks_full_decision_identity_and_projected_values():
    live = {"task_id": "t1", "preparation": "ready", "actual_surface": "captured_text",
            "policy_digest": "frozen-policy", "candidate_access_performed": False}
    selected = {key: live[key] for key in ("task_id", "preparation", "actual_surface")}
    selected["admission_identity"] = "mapping:" + digest_value(live)
    require_query_admission_row(selected, live)

    with pytest.raises(BodyStudyError, match="query_admission_matrix_join"):
        require_query_admission_row(selected, {**live, "policy_digest": "other-policy"})
    with pytest.raises(BodyStudyError, match="query_admission_matrix_join"):
        require_query_admission_row({**selected, "actual_surface": "none"}, live)
    selected.pop("preparation")
    with pytest.raises(BodyStudyError, match="query_admission_matrix_join"):
        require_query_admission_row(selected, live)


def _meta():
    return [{"task_id": f"t{i}", "family": BODY_FAMILIES[(i - 1) // 4],
             "intent_id": f"i{i}", "wording_stratum": "literal_clue",
             "case_kind": "body_positive"} for i in range(1, 13)]


def _receipts(changes=None):
    rows = []
    for item in _meta():
        for arm in COMPARISON_ARMS:
            row = {"task_id": item["task_id"], "arm": arm, "admitted": True,
                   "captured_text_reachable": True, "token_count": 10, "truncated": False,
                   "representation_identity": ARM_REPRESENTATIONS[arm]}
            row.update((changes or {}).get((item["task_id"], arm), {}))
            rows.append(row)
    return rows


def test_common_set_floor_and_attrition_retained():
    result = select_common_positives(_meta(), _receipts())
    assert result.floor_passed and len(result.task_ids) == 12
    assert result.to_dict()["tokenizer_origin_proven"] is False
    low = select_common_positives(_meta(), _receipts({
        (f"t{i}", COMPARISON_ARMS[0]): {"truncated": True} for i in range(1, 7)}))
    assert not low.floor_passed and len(low.excluded) == 6 and len(low.planned) == 12


def test_common_set_refuses_rank_data_and_incomplete_receipts():
    receipts = _receipts()
    receipts[0]["rank"] = 1
    with pytest.raises(BodyStudyError, match="receipt_fields"):
        select_common_positives(_meta(), receipts)
    with pytest.raises(BodyStudyError, match="receipt_completeness"):
        select_common_positives(_meta(), _receipts()[:-1])


def test_reported_token_count_does_not_prove_tokenizer_observation_or_nontruncation():
    record = validate_text_limits(token_count=10, excerpt_bytes=20, stream_bytes=50)
    assert record["supplied_count_present"] is True
    assert record["tokenizer_observed"] is False
    assert record["tokenizer_origin_proven"] is False
    assert record["truncated"] == "unobserved"


def test_common_set_is_immutable_and_does_not_claim_execution_order():
    original = _meta()
    result = select_common_positives(original, _receipts())
    identity = result.identity
    original[0]["intent_id"] = "changed"
    assert result.identity == identity
    with pytest.raises(TypeError):
        cast(MutableMapping[str, Any], result.planned[0])["intent_id"] = "changed"
    assert result.to_dict()["frozen_before_ranking"] is False


def test_common_set_constructor_is_not_an_evidence_factory():
    with pytest.raises(BodyStudyError, match="common_factory_required"):
        CommonPositiveSet((), (), (), False)


def test_common_set_rejects_wrong_representation_or_duplicate_intent():
    receipts = _receipts()
    receipts[0]["representation_identity"] = "unrelated"
    with pytest.raises(BodyStudyError, match="receipt_representation"):
        select_common_positives(_meta(), receipts)
    meta = _meta()
    meta[1]["intent_id"] = meta[0]["intent_id"]
    with pytest.raises(BodyStudyError, match="meta_intent"):
        select_common_positives(meta, _receipts())


def test_anchor_query_requires_all_v1_rows_and_current_task_identity():
    catalog, rows = _selected()
    reduced = [row for row in rows if not (row["arm"] == V1_ARM and row["case_kind"] == "privacy")]
    with pytest.raises(BodyStudyError, match="v1_row_completeness"):
        prepare_anchor_v1_queries(catalog, reduced)
    changed = [dict(row) for row in rows]
    anchor = next(row for row in changed if row["arm"] == V1_ARM and row["case_kind"] == "metadata_anchor")
    anchor["case_sha256"] = "sha256:" + "4" * 64
    with pytest.raises(BodyStudyError, match="v1_case_identity"):
        prepare_anchor_v1_queries(catalog, changed)
