"""Body-study selected-backend join and bound-preparation refusals."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest

from repomap_test_support.retrieval_body_study_fixtures import study_catalog, study_reports
from retrieval_body_report import build_body_report
from retrieval_body_study_join import prepare_bound_body_study, select_body_study_matrix
from retrieval_body_study_values import ARMS, FUSION_ARM, NATIVE_ARM, BodyStudyError
from repomap_test_support.retrieval_body_study_fixtures import study_decisions, PG_PUB
from repomap_test_support.retrieval_body_fixtures import make_body_policy


def test_selected_matrix_retains_both_reports_and_432_inert_slots():
    catalog, sqlite, postgres = study_reports()
    matrix = select_body_study_matrix(sqlite, postgres, catalog=catalog)
    assert matrix["selected_row_count"] == 144 and matrix["planned_slot_count"] == 432
    assert len(matrix["sqlite_report"]["preparation_rows"]) == 144
    assert len(matrix["postgres_report"]["preparation_rows"]) == 144
    assert matrix["combined_publication_digest"] is None
    assert matrix["origin_proven"] is False and matrix["execution_authority_granted"] is False
    assert matrix["functional_only"] is True
    assert matrix["decision_rows_verified"] is False
    assert matrix["source_class_id"] == catalog.identity.class_id
    native = [r for r in matrix["selected_rows"] if r["arm"] == NATIVE_ARM]
    assert native and all(r["selected_backend"] == "sqlite" for r in native)
    assert all(r["selected_backend"] == "postgresql"
               for r in matrix["selected_rows"] if r["arm"] != NATIVE_ARM)


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r.update(class_id="other"), "sqlite_class"),
    (lambda r: r.update(origin_proven=True), "sqlite_origin_claim"),
    (lambda r: r.update(execution_state="executed"), "sqlite_execution_claim"),
    (lambda r: r["preparation_rows"].pop(), "sqlite_row_count"),
    (lambda r: r["preparation_rows"].__setitem__(0, dict(r["preparation_rows"][1])), "duplicate"),
])
def test_malformed_reports_refuse(mutate, code):
    catalog, sqlite, postgres = study_reports()
    bad = deepcopy(sqlite)
    mutate(bad)
    with pytest.raises(BodyStudyError, match=code):
        select_body_study_matrix(bad, postgres, catalog=catalog)


def test_mixed_input_pin_and_wrong_representation_refuse():
    catalog, sqlite, postgres = study_reports()
    bad = deepcopy(postgres)
    bad["input_pin_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(BodyStudyError, match="mixed_input_pin_sha256"):
        select_body_study_matrix(sqlite, bad, catalog=catalog)
    bad2 = deepcopy(postgres)
    bad2["preparation_rows"][0]["representation_identity"] = "wrong"
    with pytest.raises(BodyStudyError, match="representation"):
        select_body_study_matrix(sqlite, bad2, catalog=catalog)


def test_ready_fusion_requires_same_pg_fts_and_v2_ready():
    catalog = study_catalog()
    decisions = study_decisions(catalog, PG_PUB)
    for row in decisions:
        if row["arm"] == ARMS[2] and row["preparation"] == "ready":
            row.update(preparation="unavailable", reason="fts_down",
                       actual_surface="none", fallback_policy="none")
            break
    _, sqlite, _ = study_reports(catalog)
    with pytest.raises(BodyStudyError, match="fusion_dependency_not_ready"):
        select_body_study_matrix(sqlite, build_body_report(catalog, decisions), catalog=catalog)


def test_bound_preparation_refuses_missing_pins_before_any_join():
    catalog, sqlite, postgres = study_reports()
    policy = make_body_policy()
    with pytest.raises(BodyStudyError, match="sqlite_pin_missing"):
        prepare_bound_body_study(
            catalog, sqlite_decisions=[], postgres_decisions=[],
            sqlite_pin=None, postgres_pin=None, policy=policy, frozen_policy=policy)
    with pytest.raises(BodyStudyError, match="sqlite_pin_type"):
        prepare_bound_body_study(
            catalog, sqlite_decisions=[], postgres_decisions=[],
            sqlite_pin=cast(Any, object()), postgres_pin=None, policy=policy, frozen_policy=policy)
    assert FUSION_ARM in ARMS and sqlite and postgres


@pytest.mark.parametrize("field,value", [
    ("case_sha256", "sha256:" + "1" * 64),
    ("task_sha256", "sha256:" + "2" * 64),
    ("family", "other_family"),
    ("intent_id", "other_intent"),
    ("input_pin_sha256", "sha256:" + "3" * 64),
])
def test_catalog_membership_cannot_be_forged_in_matching_report_pair(field, value):
    catalog, sqlite, postgres = study_reports()
    # The same forgery on both sides must not pass a mere pair-equality check.
    for report in (sqlite, postgres):
        report["preparation_rows"][0][field] = value
    with pytest.raises(BodyStudyError, match="row_" + field):
        select_body_study_matrix(sqlite, postgres, catalog=catalog)


def test_selected_row_publication_cannot_differ_from_its_report():
    catalog, sqlite, postgres = study_reports()
    sqlite["preparation_rows"][0]["publication_identity"] = PG_PUB
    with pytest.raises(BodyStudyError, match="mixed_row_publication"):
        select_body_study_matrix(sqlite, postgres, catalog=catalog)


def test_nested_report_and_row_cannot_smuggle_execution_authority():
    catalog, sqlite, postgres = study_reports()
    sqlite["model_executed"] = True
    with pytest.raises(BodyStudyError, match="sqlite_execution_claim"):
        select_body_study_matrix(sqlite, postgres, catalog=catalog)
    _, sqlite, postgres = study_reports()
    sqlite["preparation_rows"][0]["execution_authority_granted"] = True
    with pytest.raises(BodyStudyError, match="sqlite_row_fields"):
        select_body_study_matrix(sqlite, postgres, catalog=catalog)


@pytest.mark.parametrize("mutate,code", [
    (lambda report: report.update(rows=[]), "row_alias"),
    (lambda report: report.update(slots=[]), "planned_slots"),
    (lambda report: report.update(task_count=True), "declared_counts"),
])
def test_complete_source_reports_include_consistent_aliases_and_all_slots(mutate, code):
    catalog, sqlite, postgres = study_reports()
    mutate(sqlite)
    with pytest.raises(BodyStudyError, match=code):
        select_body_study_matrix(sqlite, postgres, catalog=catalog)


def test_formatter_rejects_ready_privacy_even_with_copied_publication_identity():
    catalog = study_catalog()
    decisions = study_decisions(catalog, PG_PUB)
    privacy = {case.task.task_id for case in catalog.cases if case.kind == "privacy"}
    next(row for row in decisions if row["task_id"] in privacy).update(
        preparation="ready", reason=None, actual_surface="captured_text", fallback_policy="none")
    _, sqlite, _ = study_reports(catalog)
    with pytest.raises(BodyStudyError, match="forbidden_ready"):
        select_body_study_matrix(sqlite, build_body_report(catalog, decisions), catalog=catalog)


@pytest.mark.parametrize("field,value", [("actual_surface", "stored_metadata"),
                                         ("fallback_policy", "none")])
def test_formatter_rejects_forged_ready_surface_or_fallback(field, value):
    catalog = study_catalog()
    decisions = study_decisions(catalog, PG_PUB)
    next(row for row in decisions if row["preparation"] == "ready")[field] = value
    _, sqlite, _ = study_reports(catalog)
    with pytest.raises(BodyStudyError, match="ready_surface"):
        select_body_study_matrix(sqlite, build_body_report(catalog, decisions), catalog=catalog)
