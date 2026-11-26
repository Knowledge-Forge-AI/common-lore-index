"""Preparation/report joins use unrelated unit-body functional catalog data."""
from __future__ import annotations

from copy import deepcopy

import pytest

from common_lore_index_test_support.retrieval_body_fixtures import (
    make_body_bindings,
    make_body_catalog_payload,
    make_body_identity,
)
from retrieval_body_capabilities import expected_capabilities
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_report import (
    BodyReportError,
    PLANNED_SLOT_COUNT,
    PREPARATION_ROW_COUNT,
    build_body_report,
    build_body_report_rows,
)
from retrieval_body_values import ARMS, canonical_bytes, digest_bytes
from retrieval_supplement_tasks import task_digests


PIN = digest_bytes(b"unit-body-report-pin")
PUBLICATION = {"graph_id": "unit-body-canopy", "generation": 1, "run_id": 1}


def _catalog():
    return parse_functional_catalog(
        canonical_bytes(make_body_catalog_payload()),
        identity=make_body_identity(),
        binding_ids=tuple(make_body_bindings().values()),
    )


def _decisions(catalog):
    rows = []
    for case in catalog.cases:
        task = case.task
        digests = task_digests(task)
        for arm in ARMS:
            expected = expected_capabilities(task, case.kind)[arm]
            rows.append({
                "class_id": catalog.identity.class_id,
                "catalog_sha256": catalog.digest,
                "case_sha256": case.digest,
                "input_pin_sha256": PIN,
                "task_id": task.task_id,
                "task_sha256": digests["task_sha256"],
                "retained_task_sha256": digests["retained_task_sha256"],
                "generation": task.generation,
                "arm": arm,
                "preparation": expected["preparation"],
                "reason": expected["reason"],
                "actual_surface": expected["actual_surface"],
                "fallback_policy": expected["fallback_policy"],
                "admission_identity": f"functional:{task.task_id}:{arm}",
                "publication_identity": None if expected["preparation"] == "refused" else PUBLICATION,
                "representation_identity": {
                    ARMS[0]: "native-tools-baseline",
                    ARMS[1]: "current-graph-baseline:postgresql",
                    ARMS[2]: "postgres-native-fts:postgresql",
                    ARMS[3]: "canonical-node-v1",
                    ARMS[4]: "canonical-node-public-text-v2",
                    ARMS[5]: "canonical-node-public-text-v2",
                }[arm],
            })
    return rows


def test_report_requires_complete_matrix_and_emits_all_planned_slots_without_text():
    catalog = _catalog()
    report = build_body_report(catalog, _decisions(catalog))
    assert len(report["preparation_rows"]) == PREPARATION_ROW_COUNT == 144
    assert len(report["planned_slots"]) == PLANNED_SLOT_COUNT == 432
    assert all(row["execution_state"] == "not_executed" for row in report["planned_slots"])
    assert all("question" not in row and "text" not in row for row in report["preparation_rows"])
    assert report["body"]["task_count"] == 12 and report["body"]["slot_count"] == 216
    assert report["metadata_anchors"]["task_count"] == 4 and report["metadata_anchors"]["slot_count"] == 72
    assert report["controls"]["task_count"] == 8 and report["controls"]["slot_count"] == 144
    assert report["embedding_count"]["status"] == "PLANNED"
    assert report["embedding_count"]["value"] is None
    assert report["body_anchor_pooling"] is False and report["repetition_pooling"] is False


def test_report_keeps_acceptance_and_tokenization_flags_false():
    report = build_body_report(_catalog(), _decisions(_catalog()))
    for row in report["planned_slots"]:
        assert row["model_accepted"] is False
        assert row["study_accepted"] is False
        assert row["tokenizer_observed"] is False
        assert row["scientific_acceptance"] is False
        assert row["heldout_acceptance"] is False
        assert row["production_acceptance"] is False
        assert row["refused_preparation_is_execution_evidence"] is False
    assert report["origin_proven"] is False
    assert report["controller_admission_join_required"] is True


def test_report_rejects_omitted_duplicate_and_fabricated_execution_rows():
    catalog = _catalog()
    decisions = _decisions(catalog)
    with pytest.raises(BodyReportError, match="preparation_row_count"):
        build_body_report_rows(catalog, decisions[:-1])
    duplicate = list(decisions)
    duplicate[-1] = deepcopy(duplicate[0])
    with pytest.raises(BodyReportError, match="duplicate_preparation_row"):
        build_body_report_rows(catalog, duplicate)
    fabricated = list(decisions)
    fabricated[0] = {**fabricated[0], "execution_state": "completed"}
    with pytest.raises(BodyReportError, match="execution_state"):
        build_body_report_rows(catalog, fabricated)


def test_report_rejects_inconsistent_preparation_and_caller_acceptance_claims():
    catalog = _catalog()
    decisions = _decisions(catalog)
    bad_state = list(decisions)
    bad_state[0] = {**bad_state[0], "preparation": "ready", "reason": "fabricated"}
    with pytest.raises(BodyReportError, match="ready_fields"):
        build_body_report_rows(catalog, bad_state)
    bad_flag = list(decisions)
    bad_flag[0] = {**bad_flag[0], "scientific_acceptance": True}
    with pytest.raises(BodyReportError, match="execution_claim"):
        build_body_report_rows(catalog, bad_flag)


def test_report_does_not_mutate_supplied_decision_identities():
    catalog = _catalog()
    decisions = _decisions(catalog)
    publication = decisions[0]["publication_identity"]
    report = build_body_report(catalog, decisions)
    publication["run_id"] = 99
    assert report["publication_identity"]["run_id"] == 1
