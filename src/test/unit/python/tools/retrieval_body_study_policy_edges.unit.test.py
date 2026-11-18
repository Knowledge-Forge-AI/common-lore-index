"""Closed capability, low-floor, evidence and primary-invalidation boundaries."""
from __future__ import annotations

from typing import Any, cast

import pytest

from repomap_test_support.retrieval_body_study_fixtures import quality_inputs, study_catalog
from retrieval_body_capabilities import expected_capabilities
from retrieval_body_records import BodyRecordError, build_body_query_records
from retrieval_body_study_common import select_common_positives
from retrieval_body_study_outcomes import validate_task_meta
from retrieval_body_study_quality import evaluate_body_study
from retrieval_body_study_recovery import classify_successor, select_primary_attempt
from retrieval_body_study_values import (
    ARMS, ARM_REPRESENTATIONS, COMPARISON_ARMS, BodyStudyError, digest_value,
)


def _evaluate(inputs):
    meta, golds, universes, common, slots = inputs
    return evaluate_body_study(task_meta=meta, golds=golds, universes=universes,
                               common_task_ids=common, floor_passed=common.floor_passed, slots=slots)


def _preflight():
    inputs = quality_inputs(common_count=3)
    meta, _golds, _universes, _common, slots = inputs
    for slot in slots:
        expected = meta[slot["task_id"]]["expected_arm_outcomes"][slot["arm"]]
        slot.update(execution_state=expected["state"] if expected["state"] != "completed" else "unstarted",
                    refusal_code=expected["refusal_code"], ranked_files=[])
    return inputs


@pytest.mark.parametrize("task_id,arm", [("body-01", ARMS[3]), ("absent-1", ARMS[3]),
                                         ("ambiguous-1", ARMS[3]), ("body-01", ARMS[1])])
def test_any_declared_unavailable_arm_cannot_execute_as_a_valid_negative(task_id, arm):
    inputs = quality_inputs()
    slot = next(row for row in inputs[-1] if row["task_id"] == task_id and row["arm"] == arm)
    slot.update(execution_state="completed", ranked_files=[["bind-a", "src/gold.py"]])
    report = _evaluate(inputs)
    assert report["outcome"] == "inadmissible"
    assert report["forbidden_work_observed"] is True
    assert report["exposed_to_scores"] is True


@pytest.mark.parametrize("task_id,arm,code", [
    ("body-01", ARMS[3], "body_metadata_capability"),
    ("absent-1", ARMS[3], "body_metadata_capability"),
    ("unsupported-1", ARMS[4], "unsupported_semantic_capability"),
])
def test_expected_outcome_cannot_enable_an_incompatible_semantic_arm(task_id, arm, code):
    inputs = quality_inputs()
    inputs[0][task_id]["expected_arm_outcomes"][arm] = {"state": "completed", "refusal_code": None}
    with pytest.raises(BodyStudyError, match=code):
        _evaluate(inputs)


def test_closed_masks_accept_factory_catalog_capabilities():
    catalog = study_catalog()
    meta = {}
    for case in catalog.cases:
        row = case.to_dict()
        capabilities = expected_capabilities(case.task, case.kind)
        meta[case.task.task_id] = {
            "case_kind": case.kind, "family": case.task.family,
            "intent_id": row["intent_id"], "wording_stratum": row["wording_stratum"],
            "expected_arm_outcomes": {
                arm: {"state": "completed" if cap["preparation"] == "ready" else cap["preparation"],
                      "refusal_code": cap["reason"] if cap["preparation"] == "refused" else None}
                for arm, cap in capabilities.items()
            },
        }
    assert len(validate_task_meta(meta)[0]) == 24


def test_global_arm_summary_contains_availability_counts_without_pooled_quality():
    report = _evaluate(quality_inputs())
    assert report["anchors_controls_pooled"] is False
    for arm in report["arm_summaries"].values():
        assert arm["slot_count"] == 72
        assert "metric_means" not in arm
        assert arm["quality_metrics_pooled"] is False
    assert all(key.startswith("body-") for key in report["per_task"])


def test_wrong_refusal_at_low_floor_is_reported_without_losing_observations():
    inputs = _preflight()
    next(row for row in inputs[-1] if row["task_id"] == "privacy-1")["refusal_code"] = "wrong_code"
    report = _evaluate(inputs)
    assert report["outcome"] == "inconclusive_floor"
    assert report["controls"]["privacy"]["expectation_mismatches"] == 1
    assert len(report["observations"]) == 432


def test_retrieval_after_low_floor_is_retained_as_inadmissible_even_when_empty():
    inputs = _preflight()
    slot = next(row for row in inputs[-1] if row["task_id"] == "body-01" and row["arm"] == ARMS[0])
    slot.update(execution_state="completed", ranked_files=[])
    report = _evaluate(inputs)
    assert report["outcome"] == "inadmissible"
    assert report["low_floor_retrieval_violations"] == [["body-01", ARMS[0], 1]]
    assert report["exposed_to_scores"] is True
    assert len(report["observations"]) == 432


def _common_arguments():
    meta = quality_inputs()[0]
    planned = [{"task_id": task_id, **{key: row[key] for key in
                ("case_kind", "family", "intent_id", "wording_stratum")}}
               for task_id, row in meta.items() if row["case_kind"] == "body_positive"]
    receipts = [{"task_id": row["task_id"], "arm": arm, "admitted": True,
                 "captured_text_reachable": True, "truncated": False, "token_count": 10,
                 "representation_identity": ARM_REPRESENTATIONS[arm]}
                for row in planned for arm in COMPARISON_ARMS]
    return planned, receipts


def test_missing_required_tokenizer_evidence_is_technical_not_a_floor_exclusion():
    planned, receipts = _common_arguments()
    receipts[0]["token_count"] = None
    with pytest.raises(BodyStudyError, match="tokenizer_receipt_missing"):
        select_common_positives(planned, receipts)


def test_structurally_unadmitted_input_requires_no_forbidden_tokenizer_work():
    planned, receipts = _common_arguments()
    receipts[0].update(admitted=False, token_count=None)
    common = select_common_positives(planned, receipts)
    assert "body-01" not in common.task_ids
    assert common.floor_passed is True


def _invalidation(attempts):
    return {"attempt_index": 0, "attempt_identity": digest_value(attempts[0]),
            "objective_defect_code": "unit_contract_defect", "rank_blind_review_done": True,
            "operator_disposition": True, "rank_blind_review_sha256": digest_value({"unit": "review"}),
            "operator_decision_sha256": digest_value({"unit": "decision"})}


def test_explicit_objective_invalidation_can_replace_a_low_floor_without_score_selection():
    attempts = [{"outcome": "inconclusive_floor"}, {"outcome": "signal_not_demonstrated"},
                {"outcome": "calibration_signal_present"}]
    assert select_primary_attempt(attempts) == 0
    assert select_primary_attempt(attempts, invalidations=[_invalidation(attempts)]) == 1
    base: dict[str, Any] = dict(outcome="inconclusive_floor", exposed_to_scores=True,
        defect_class="contract", scientific_fingerprints_identical=False, integrity_restored=True,
        ownership_restored=True, cleanup_proven=True, rank_blind_review_done=True,
        invalidates_complete_primary=True, operator_disposition=False)
    assert classify_successor(**base) == "operator_disposition_required"
    assert classify_successor(**{**base, "operator_disposition": True}) == \
        "successor_may_replace_after_operator_disposition"


@pytest.mark.parametrize("field,value", [("operator_disposition", False),
    ("rank_blind_review_done", False), ("attempt_identity", "sha256:" + "0" * 64)])
def test_unbound_or_unapproved_invalidation_cannot_rescue_a_result(field, value):
    attempts = [{"outcome": "inconclusive_floor"}, {"outcome": "calibration_signal_present"}]
    row = _invalidation(attempts)
    row[field] = value
    with pytest.raises(BodyStudyError, match="invalidation_binding|invalidation_disposition"):
        select_primary_attempt(attempts, invalidations=[row])


def test_partial_empty_absence_preserves_the_real_grader_negative():
    inputs = quality_inputs()
    slot = next(row for row in inputs[-1] if row["task_id"] == "absent-1" and row["arm"] == ARMS[0])
    slot.update(capability_state="available", scope_complete=False, absent_answer_correct=False)
    report = _evaluate(inputs)
    observation = next(row for row in report["observations"] if row["task_id"] == "absent-1"
                       and row["arm"] == ARMS[0] and row["repetition"] == 1)
    assert observation["absence_answer_correct"] is False


def test_supplied_refusal_safety_failure_prevents_a_positive_signal():
    inputs = quality_inputs()
    slot = next(row for row in inputs[-1] if row["task_id"] == "privacy-1")
    slot.update(capability_state="refused", safety_refusal_correct=False)
    report = _evaluate(inputs)
    assert report["outcome"] == "signal_not_demonstrated"
    assert report["controls"]["privacy"]["expectation_mismatches"] == 1


@pytest.mark.parametrize("state", ["failed", "timed_out"])
def test_started_but_unfinished_retrieval_after_low_floor_is_inadmissible(state):
    inputs = _preflight()
    target = next(row for row in inputs[-1] if row["task_id"] == "body-01" and row["arm"] == ARMS[0])
    target["execution_state"] = state
    report = _evaluate(inputs)
    assert report["outcome"] == "inadmissible"
    assert report["low_floor_retrieval_violations"] == [["body-01", ARMS[0], 1]]


@pytest.mark.parametrize("state", ["missing", "not_run"])
def test_missing_prequery_observation_cannot_become_a_valid_floor(state):
    inputs = _preflight()
    target = next(row for row in inputs[-1] if row["task_id"] == "body-01" and row["arm"] == ARMS[0])
    target["execution_state"] = state
    report = _evaluate(inputs)
    assert report["outcome"] == "incomplete_technical"
    assert report["missing_slots"] == [["body-01", ARMS[0], 1]]


@pytest.mark.parametrize("task_id,arm", [("privacy-1", ARMS[0]), ("body-01", ARMS[3])])
@pytest.mark.parametrize("state", ["failed", "timed_out", "missing"])
def test_failed_or_lost_protected_guard_cannot_take_a_routine_successor(task_id, arm, state):
    inputs = quality_inputs()
    target = next(row for row in inputs[-1] if row["task_id"] == task_id and row["arm"] == arm)
    target.update(execution_state=state, refusal_code=None, ranked_files=[])
    report = _evaluate(inputs)
    assert report["outcome"] == "inadmissible"
    assert report["unknown_forbidden_execution"]
    assert classify_successor(outcome=report["outcome"], exposed_to_scores=True,
        defect_class="transport", scientific_fingerprints_identical=True,
        integrity_restored=True, ownership_restored=True, cleanup_proven=True,
        rank_blind_review_done=True, operator_disposition=False,
        inadmissibility_cause=report["inadmissibility_cause"]) == "operator_disposition_required"


def test_empty_absence_without_envelope_context_stays_unavailable():
    inputs = quality_inputs()
    target = next(row for row in inputs[-1] if row["task_id"] == "absent-1" and row["arm"] == ARMS[0])
    target.pop("scope_complete")
    target.pop("capability_state")
    report = _evaluate(inputs)
    observation = next(row for row in report["observations"] if row["task_id"] == "absent-1"
                       and row["arm"] == ARMS[0] and row["repetition"] == 1)
    assert observation["absence_answer_correct"] is None


def test_low_floor_lost_guard_reports_unknown_work_without_claiming_observed_execution():
    inputs = _preflight()
    target = next(row for row in inputs[-1] if row["task_id"] == "privacy-1")
    target.update(execution_state="failed", refusal_code=None)
    report = _evaluate(inputs)
    assert report["outcome"] == "inadmissible"
    assert report["unknown_forbidden_execution"]
    assert report["forbidden_work_observed"] is False
    assert report["low_floor_retrieval_violations"] == []


@pytest.mark.parametrize("kind", ["privacy", "unsupported"])
def test_maintained_v2_facade_refuses_caller_created_ready_admissions(kind):
    case = next(case for case in study_catalog().cases if case.kind == kind)
    forged = {"task_id": case.task.task_id, "preparation": "ready", "arm": ARMS[4],
              "actual_surface": "captured_text", "task": case.task.to_dict()}
    with pytest.raises(BodyRecordError, match="admission_type"):
        build_body_query_records(cast(Any, [forged]))


@pytest.mark.parametrize("arm", COMPARISON_ARMS)
def test_fully_observed_wrong_query_refusal_is_a_valid_negative_not_retry_permission(arm):
    inputs = quality_inputs()
    target = next(row for row in inputs[-1] if row["task_id"] == "body-01" and row["arm"] == arm)
    target.update(execution_state="refused", ranked_files=[], returned_hit_count=0,
                  capability_state="refused", refusal_code="unsupported_semantic_query_mode")
    report = _evaluate(inputs)
    assert report["outcome"] == "signal_not_demonstrated"
    assert report["contrasts"] is None
    assert report["missing_slots"] == []
    assert report["refused_common_slots"] == [["body-01", arm, 1]]
    observation = next(row for row in report["observations"] if row["task_id"] == "body-01"
                       and row["arm"] == arm and row["repetition"] == 1)
    assert observation["metrics"] is None and observation["quality_exposed"] is True
    assert select_primary_attempt([report]) == 0
    assert classify_successor(outcome=report["outcome"], exposed_to_scores=True,
        defect_class="transport", scientific_fingerprints_identical=True,
        integrity_restored=True, ownership_restored=True, cleanup_proven=True,
        rank_blind_review_done=True, operator_disposition=False) == "no_successor_valid_result"
