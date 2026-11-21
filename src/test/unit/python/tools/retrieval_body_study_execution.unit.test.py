"""Closed body cohort and finite execution policy, without actual admission."""
from __future__ import annotations

import pytest

from retrieval_body_study_contracts import BODY_SELECTOR, BodyStudyRefusal
from retrieval_body_study_model_contract import ModelHostRefusal, validate_body_model_request
from retrieval_body_study_operation import exposed_successor_allowed
from retrieval_body_study_schedule import build_schedule, validate_schedule
from runner_body_study import validate_body_cohort


def test_schedule_fits_nested_limits():
    schedule = build_schedule()
    assert schedule["sdk_total_seconds"] == 4800
    assert schedule["controller_total_seconds"] <= 5400
    assert schedule["builds"] == schedule["pulls"] == schedule["materializations"] == 0
    assert schedule["worker_count"] == 2
    assert schedule["slot_count"] == 432 and schedule["prospective_records"] == 134
    assert schedule["source_memoization"] is False


def test_schedule_digest_does_not_accept_an_unrelated_plan():
    with pytest.raises(BodyStudyRefusal):
        validate_schedule({"schedule_sha256": "sha256:" + "a" * 64})


def test_supplement_phase_refused():
    with pytest.raises(ModelHostRefusal):
        validate_body_model_request({"phase": "CLOE-W5-SUPPLEMENT-STUDY1",
            "representation": "canonical-node-v1", "document_count": 66, "query_count": 4})


def test_right_census_alone_cannot_mint_a_model_descriptor():
    with pytest.raises(ModelHostRefusal):
        validate_body_model_request({"phase": "CLOE-W5-BODY-STUDY1",
            "representation": "canonical-node-public-text-v2", "document_count": 44, "query_count": 20})


def test_exposed_recovery_only_neutral_classes():
    flags = {"rank_blind_review": True, "treatment_unchanged": True,
             "integrity_ok": True, "ownership_ok": True, "cleanup_ok": True}
    assert exposed_successor_allowed(defect_class="transport", **flags) == "routine_neutral_successor_permitted"
    assert exposed_successor_allowed(defect_class="scoring", **flags) == "operator_disposition_required"


def test_unknown_cleanup_or_ownership_cannot_admit_a_neutral_successor():
    assert exposed_successor_allowed(defect_class="transport", rank_blind_review=True,
        treatment_unchanged=True, integrity_ok=True) == "refused_until_integrity_ownership_cleanup_proven"


def test_body_cohort_exact_selector():
    validate_body_cohort(["--suite", "int", "--no-coverage", "--pg-test-profile", "pgvector",
        "--report-dir", "r", "--", "-s",
        BODY_SELECTOR])
    with pytest.raises(RuntimeError, match="pgvector prebuilt refused"):
        validate_body_cohort(["--suite", "int", "--", "-s", "other.py"])


def test_another_test_or_an_extra_owner_does_not_gain_body_lifetime():
    prefix = ["--suite", "int", "--no-coverage", "--pg-test-profile", "pgvector",
              "--report-dir", "r", "--", "-s"]
    for tail in ([BODY_SELECTOR + "::other"], [BODY_SELECTOR, "other.py"]):
        with pytest.raises(RuntimeError, match="pgvector prebuilt refused"):
            validate_body_cohort(prefix + tail)
