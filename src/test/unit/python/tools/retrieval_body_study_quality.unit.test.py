"""Pure body-study quality, outcome and recovery boundaries (not executed here)."""
from __future__ import annotations

import copy
from fractions import Fraction
from typing import Any

import pytest

from common_lore_index_test_support.retrieval_body_study_fixtures import quality_inputs
import retrieval_body_study_quality as quality_module
from retrieval_body_study_quality import (
    classify_successor,
    dedupe_ranked_files,
    evaluate_body_study,
    expected_random_baseline,
    file_metrics_at_10,
    select_primary_attempt,
)
from retrieval_body_study_values import ARMS, BODY_FAMILIES, FTS_ARM, NATIVE_ARM, BodyStudyError


def _inputs(**kwargs):
    meta, golds, universes, common, slots = quality_inputs(**kwargs)
    return copy.deepcopy(meta), copy.deepcopy(golds), dict(universes), common, [dict(s) for s in slots]


def _evaluate(slots=None, *, meta=None, golds=None, universes=None, common=None, **kwargs):
    default_meta, default_golds, default_universes, default_common, default_slots = _inputs()
    return evaluate_body_study(
        task_meta=default_meta if meta is None else meta,
        golds=default_golds if golds is None else golds,
        universes=default_universes if universes is None else universes,
        common_task_ids=default_common if common is None else common,
        floor_passed=True,
        slots=default_slots if slots is None else slots,
        **kwargs,
    )


def test_same_path_cross_binding_files_are_distinct():
    ranked = [["bind-a", "src/x.py"], ["bind-b", "src/x.py"], ["bind-a", "src/x.py"]]
    assert dedupe_ranked_files(ranked) == (("bind-a", "src/x.py"), ("bind-b", "src/x.py"))
    metrics = file_metrics_at_10(ranked, [["bind-b", "src/x.py", 3]])
    assert metrics["file_mrr@10"] == 0.5 and metrics["file_recall@10"] == 1.0


def test_random_context_refuses_zero_and_multiple_gold_analytic_claims():
    assert expected_random_baseline(22)["expected_ndcg@10"] == 0.206525
    for universe in (None, 0):
        baseline = expected_random_baseline(universe)
        assert baseline["status"] == "unavailable"
        assert baseline["reason"] == "universe_unavailable"
    for count in (0, 2):
        baseline = expected_random_baseline(22, count)
        assert baseline["status"] == "unavailable"
        assert baseline["expected_recall@10"] is None
        assert baseline["expected_ndcg@10"] is None


def test_common_floor_rejects_unknown_or_empty_candidate_universe():
    meta, golds, universes, common, slots = _inputs()
    universes[common.task_ids[0]] = None
    with pytest.raises(BodyStudyError, match="common_universe_unavailable"):
        _evaluate(slots, meta=meta, golds=golds, universes=universes, common=common)


def test_privacy_refusal_does_not_claim_an_observed_candidate_universe():
    meta, golds, universes, common, slots = _inputs()
    universes["privacy-1"] = 22
    with pytest.raises(BodyStudyError, match="privacy_universe_available"):
        _evaluate(slots, meta=meta, golds=golds, universes=universes, common=common)


def test_complete_report_retains_all_cases_slots_arms_repetitions_and_separates_controls():
    result = _evaluate()
    assert result["outcome"] == "calibration_signal_present"
    assert result["case_count"] == 24 and result["slot_count"] == 432
    assert len(result["observations"]) == 432
    assert {case["case_kind"] for case in result["cases"].values()} == {
        "body_positive", "metadata_anchor", "absent", "ambiguous", "privacy", "unsupported",
    }
    assert all(summary["slot_count"] == 72 for summary in result["arm_summaries"].values())
    assert all(len(case["arms"][ARMS[0]]["repetitions"]) == 3 for case in result["cases"].values())
    assert result["anchors_controls_pooled"] is False
    assert all("ranked_files" not in row for row in result["observations"])


def test_missing_slot_is_incomplete_never_zero_scored():
    _, _, _, _, slots = _inputs()
    slots[0] = dict(slots[0], execution_state="not_run", ranked_files=[])
    result = _evaluate(slots)
    assert result["outcome"] == "incomplete_technical"
    assert result["unstarted_scored_as_zero"] is False
    assert result["observations"][0]["metrics"] is None


def test_integrity_precedes_quality_and_low_floor_keeps_all_unstarted():
    assert _evaluate(integrity_broken=True)["outcome"] == "inadmissible"
    meta, golds, universes, common, slots = _inputs(common_count=7)
    unstarted = [dict(s, execution_state="unstarted", ranked_files=[], refusal_code=None) for s in slots]
    result = evaluate_body_study(
        task_meta=meta, golds=golds, universes=universes,
            common_task_ids=common, floor_passed=False, slots=unstarted,
    )
    assert result["outcome"] == "inconclusive_floor"
    forbidden = evaluate_body_study(
        task_meta=meta, golds=golds, universes=universes,
        common_task_ids=common, floor_passed=False, slots=slots,
    )
    assert forbidden["outcome"] == "inadmissible"
    assert len(forbidden["observations"]) == 432


def test_actual_common_floor_overrides_caller_true_claim():
    meta, golds, universes, low_common, slots = _inputs(common_count=3)
    unstarted = [dict(s, execution_state="unstarted", ranked_files=[], refusal_code=None) for s in slots]
    result = evaluate_body_study(
        task_meta=meta, golds=golds, universes=universes,
        common_task_ids=low_common, floor_passed=True, slots=unstarted,
    )
    assert result["outcome"] == "inconclusive_floor"
    assert result["common_floor"]["caller_claim"] is True
    assert result["common_floor"]["actual_passed"] is False


def test_unsupported_control_can_have_mixed_capability_without_case_global_refusal():
    meta, golds, universes, common, slots = _inputs()
    unsupported = [task_id for task_id, row in meta.items() if row["case_kind"] == "unsupported"]
    expected = {
        arm: {"state": "completed" if arm in {ARMS[0], ARMS[2]} else "unavailable",
              "refusal_code": None}
        for arm in ARMS
    }
    for task_id in unsupported:
        meta[task_id]["expected_arm_outcomes"] = expected
    for slot in slots:
        if slot["task_id"] in unsupported:
            if slot["arm"] in {ARMS[0], ARMS[2]}:
                slot.update(execution_state="completed", refusal_code=None,
                            ranked_files=[["bind-a", "src/gold.py"]])
            else:
                slot.update(execution_state="unavailable", refusal_code=None, ranked_files=[])
    result = _evaluate(slots, meta=meta, golds=golds, universes=universes, common=common)
    assert result["controls"]["unsupported"]["expectation_mismatches"] == 0
    assert result["controls"]["unsupported"]["state_counts"]["unavailable"] == 24


def test_absence_and_refusal_negatives_are_reported_without_zero_metrics():
    meta, golds, universes, common, slots = _inputs()
    absent_slot = next(s for s in slots if s["task_id"] == "absent-1")
    absent_slot["ranked_files"] = [["bind-a", "src/unexpected.py"]]
    privacy_slot = next(s for s in slots if s["task_id"] == "privacy-1")
    privacy_slot["refusal_code"] = "wrong_code"
    result = _evaluate(slots, meta=meta, golds=golds, universes=universes, common=common)
    assert result["outcome"] == "signal_not_demonstrated"
    assert result["controls"]["absent"]["absence_answer"]["incorrect"] == 1
    assert result["controls"]["privacy"]["expectation_mismatches"] == 1
    assert result["base_rates"]["absent-1"]["status"] == "unavailable"


def test_missing_expected_control_matrix_refuses_instead_of_inference():
    meta, golds, universes, common, slots = _inputs()
    del meta["privacy-1"]["expected_arm_outcomes"]
    with pytest.raises(BodyStudyError, match="expected_arm_outcomes_required"):
        _evaluate(slots, meta=meta, golds=golds, universes=universes, common=common)


def test_primacy_and_exposed_recovery_policy_includes_valid_negative_and_invalidation():
    attempts = [{"outcome": "incomplete_technical"}, {"outcome": "signal_not_demonstrated"},
                {"outcome": "calibration_signal_present"}]
    assert select_primary_attempt(attempts) == 1
    base: dict[str, Any] = dict(outcome="incomplete_technical", exposed_to_scores=True,
                defect_class="transport", scientific_fingerprints_identical=True,
                integrity_restored=True, rank_blind_review_done=False,
                operator_disposition=False, ownership_restored=True,
                cleanup_proven=True)
    assert classify_successor(**base) == "rank_blind_review_required"
    assert classify_successor(**{**base, "rank_blind_review_done": True}) == \
        "routine_neutral_successor_permitted"
    assert classify_successor(**{**base, "rank_blind_review_done": True,
                                 "defect_class": "scoring"}) == "operator_disposition_required"
    assert classify_successor(**{**base, "outcome": "signal_not_demonstrated"}) == \
        "no_successor_valid_result"
    invalidation = dict(base, outcome="signal_not_demonstrated", rank_blind_review_done=True,
                        operator_disposition=True, invalidates_complete_primary=True)
    assert classify_successor(**invalidation) == "successor_may_replace_after_operator_disposition"
    assert classify_successor(**{**invalidation, "cleanup_proven": None}) == \
        "refused_until_integrity_ownership_cleanup_proven"
    unknown = dict(base)
    unknown.pop("ownership_restored")
    assert classify_successor(**unknown) == \
        "refused_until_integrity_ownership_cleanup_proven"
    with pytest.raises(BodyStudyError, match="recovery_integrity_restored_type"):
        classify_successor(**{**base, "integrity_restored": "true"})


def test_realistic_low_floor_retains_expected_preflight_states():
    meta, golds, universes, common, slots = _inputs(common_count=3)
    preflight = []
    for slot in slots:
        expected = meta[slot["task_id"]]["expected_arm_outcomes"][slot["arm"]]
        state = expected["state"] if expected["state"] in {"refused", "unavailable"} else "unstarted"
        preflight.append(dict(slot, execution_state=state,
                              refusal_code=expected["refusal_code"] if state == "refused" else None,
                              ranked_files=[]))
    result = evaluate_body_study(
        task_meta=meta, golds=golds, universes=universes,
        common_task_ids=common, floor_passed=False, slots=preflight,
    )
    assert result["outcome"] == "inconclusive_floor"
    assert result["preflight_slots_retained"] > 0


def test_common_missing_comparison_slot_is_incomplete_not_metric_exception():
    meta, golds, universes, common, slots = _inputs()
    target = next(slot for slot in slots
                  if slot["task_id"] == common.task_ids[0] and slot["arm"] == ARMS[0])
    target.update(execution_state="unavailable", ranked_files=[])
    result = _evaluate(slots, meta=meta, golds=golds, universes=universes, common=common)
    assert result["outcome"] == "incomplete_technical"
    assert [common.task_ids[0], ARMS[0], target["repetition"]] in result["missing_slots"]


@pytest.mark.parametrize("task_id,arm,expected_state", [
    ("privacy-1", ARMS[0], "refused"),
    ("unsupported-1", ARMS[4], "refused"),
    ("unsupported-1", ARMS[4], "unavailable"),
])
def test_forbidden_privacy_or_unsupported_execution_is_inadmissible(task_id, arm, expected_state):
    meta, golds, universes, common, slots = _inputs()
    if expected_state == "unavailable":
        meta[task_id]["expected_arm_outcomes"][arm] = {"state": "unavailable", "refusal_code": None}
    target = next(slot for slot in slots if slot["task_id"] == task_id and slot["arm"] == arm)
    target.update(execution_state="completed", ranked_files=[], refusal_code=None)
    result = _evaluate(slots, meta=meta, golds=golds, universes=universes, common=common)
    assert result["outcome"] == "inadmissible"
    assert result["forbidden_work_observed"] is True


def test_computed_empty_metric_and_unsupported_refusal_are_exposure():
    meta, golds, universes, common, slots = _inputs()
    body_slot = next(slot for slot in slots
                     if slot["task_id"] == "body-01" and slot["arm"] == ARMS[0])
    body_slot["ranked_files"] = []
    result = _evaluate(slots, meta=meta, golds=golds, universes=universes, common=common)
    body_observation = next(row for row in result["observations"]
                           if row["task_id"] == "body-01" and row["arm"] == ARMS[0]
                           and row["repetition"] == body_slot["repetition"])
    unsupported_observation = next(row for row in result["observations"]
                                   if row["task_id"] == "unsupported-1"
                                   and row["arm"] == ARMS[0]
                                   and row["repetition"] == 1)
    assert body_observation["metrics"] is not None
    assert body_observation["quality_exposed"] is True
    assert unsupported_observation["quality_exposed"] is True


def test_signal_threshold_accepts_exact_decimal_boundary(monkeypatch):
    def boundary_quality(observations, meta, common):
        values = {"ndcg_gain": Fraction("0.05"), "mrr_gain": Fraction(0),
                  "recall_gain": Fraction(0)}
        return ({NATIVE_ARM: values, FTS_ARM: values},
                {family: Fraction(0) for family in BODY_FAMILIES}, {})

    monkeypatch.setattr(quality_module, "paired_quality", boundary_quality)
    result = _evaluate()
    assert result["outcome"] == "calibration_signal_present"


def test_privacy_inadmissibility_does_not_take_routine_successor():
    base: dict[str, Any] = dict(outcome="inadmissible", exposed_to_scores=True,
                defect_class="transport", scientific_fingerprints_identical=True,
                integrity_restored=True, ownership_restored=True, cleanup_proven=True,
                rank_blind_review_done=True, operator_disposition=False,
                inadmissibility_cause="privacy_disclosure")
    assert classify_successor(**base) == "operator_disposition_required"
    assert classify_successor(**{**base, "operator_disposition": True}) == \
        "successor_permitted_after_operator_disposition"


def test_unexposed_nonroutine_incomplete_allows_diagnosed_one_use_recovery():
    base: dict[str, Any] = dict(outcome="incomplete_technical", exposed_to_scores=False,
                defect_class="scoring", scientific_fingerprints_identical=False,
                integrity_restored=True, ownership_restored=True, cleanup_proven=True,
                rank_blind_review_done=False, operator_disposition=False)
    assert classify_successor(**base) == "diagnosed_correction_may_use_fresh_attempt"


def test_valid_low_floor_is_primary_and_cannot_be_rescued_by_later_signal():
    attempts = [{"outcome": "inconclusive_floor"}, {"outcome": "calibration_signal_present"}]
    assert select_primary_attempt(attempts) == 0
