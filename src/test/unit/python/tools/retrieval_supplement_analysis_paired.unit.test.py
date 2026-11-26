"""Paired descriptive analysis: repetitions reduce first, comparable positives govern pairs."""

from __future__ import annotations

from typing import Any

import pytest

from common_lore_index_test_support.retrieval_supplement_analysis_fixtures import (
    SYNTHETIC_ANALYZER,
    synthetic_bytes,
    synthetic_provenance,
    synthetic_tasks,
)
from retrieval_supplement_analysis_admission import admit_bundle
from retrieval_supplement_analysis_output import analyze_retained
from retrieval_supplement_analysis_paired import paired_analysis
from retrieval_supplement_analysis_values import SPAN_METRICS
from retrieval_supplement_primitives import ARMS


def _paired(**kwargs: Any) -> dict[str, Any]:
    tasks = kwargs.get("tasks") or synthetic_tasks()
    raw = synthetic_bytes(**kwargs)
    admitted = admit_bundle(raw, provenance=synthetic_provenance(raw), tasks=tasks)
    return paired_analysis(admitted)


def test_repetitions_reduce_within_task_arm_before_task_weighting() -> None:
    result = _paired()
    metric = result["metrics"]["file_recall@5"]
    for _task_id, by_arm in metric["task_arm"].items():
        for _arm, cell in by_arm.items():
            assert cell["repetitions_observed"] + cell["repetitions_missing"] == 3
            assert cell["mean"] is None or cell["minimum"] <= cell["mean"] <= cell["maximum"]
            assert cell["comparable_untruncated_positive"] is True
    for arm in ARMS:
        summary = metric["arm_summaries"][arm]
        # One weight per distinct positive task, never per repetition.
        assert summary["task_denominator"] == 4
        assert summary["comparable_task_denominator"] == 4
        assert summary["independent_samples_claimed"] is False


def test_task_weighting_weights_distinct_tasks_not_family_averages() -> None:
    tasks = synthetic_tasks(full=True)
    # Make 3 of the 4 tasks in family 0 unavailable across an arm so family sizes
    # are unequal (1 vs 4 vs 4 vs 4 = 13 completed tasks).
    unavail = frozenset({
        (tid, ARMS[0], rep)
        for tid in ("syn-an-01", "syn-an-02", "syn-an-03")
        for rep in (1, 2, 3)
    })
    result = _paired(tasks=tasks, unavailable=unavail)
    metric = result["metrics"]["file_recall@5"]
    observed_means = [
        by_arm[ARMS[0]]["mean"]
        for by_arm in metric["task_arm"].values()
        if by_arm[ARMS[0]]["mean"] is not None
    ]
    assert len(observed_means) == 13
    expected_task_weighted = round(sum(observed_means) / 13, 6)
    summary = metric["arm_summaries"][ARMS[0]]
    assert summary["task_denominator"] == 13
    assert summary["comparable_task_denominator"] == 13
    assert summary["missing_positive_task_count"] == 3
    assert summary["task_weighted_mean"] == pytest.approx(expected_task_weighted)


def test_absent_span_namespace_is_explicit_unavailable_with_reason() -> None:
    result = _paired(include_span_metrics=False)
    assert result["observed_namespaces"] == ["file"]
    assert result["namespace_status"]["file"]["status"] == "available"
    assert result["namespace_status"]["span"]["status"] == "unavailable"
    assert result["namespace_status"]["span"]["reason"] == "span_namespace_not_recorded_in_bundle"
    for span_name in SPAN_METRICS:
        entry = result["metrics"][span_name]
        assert entry["namespace"] == "span"
        assert entry["status"] == "unavailable"
        assert entry["reason"] == "span_namespace_not_recorded_in_bundle"
        for arm in ARMS:
            assert entry["arm_summaries"][arm]["status"] == "unavailable"
            assert entry["arm_summaries"][arm]["task_weighted_mean"] is None
            assert entry["arm_summaries"][arm]["task_denominator"] == 0
        assert len(entry["comparisons"]) == 15
        for comp in entry["comparisons"]:
            assert comp["status"] == "unavailable"
            assert comp["reason"] == "span_namespace_not_recorded_in_bundle"
            assert comp["paired_task_denominator"] == 0


def test_dual_metric_namespaces_stay_separate_and_never_combine() -> None:
    result = _paired(include_span_metrics=True)
    assert result["observed_namespaces"] == ["file", "span"]
    assert result["namespace_status"]["span"]["status"] == "available"
    file_mean = result["metrics"]["file_recall@5"]["arm_summaries"][ARMS[0]]["task_weighted_mean"]
    span_mean = result["metrics"]["recall@5"]["arm_summaries"][ARMS[0]]["task_weighted_mean"]
    assert file_mean is not None and span_mean is not None
    assert file_mean != span_mean


def test_truncated_or_partially_refused_positives_are_marked_and_excluded_from_comparable_pairs() -> None:
    # syn-an-01 is token-truncated; syn-an-02 has one refused repetition on ARMS[3].
    refused_slot = frozenset({("syn-an-02", ARMS[3], 1)})
    result = _paired(truncated_tasks=frozenset({"syn-an-01"}), refused=refused_slot)
    assert result["comparable_positive_tasks"] == ["syn-an-03", "syn-an-04"]
    metric = result["metrics"]["file_ndcg@10"]

    cell_trunc = metric["task_arm"]["syn-an-01"][ARMS[0]]
    assert cell_trunc["comparable_untruncated_positive"] is False
    assert cell_trunc["token_truncated"] is True
    assert cell_trunc["mean"] is not None  # task remains visible, never dropped

    cell_partial = metric["task_arm"]["syn-an-02"][ARMS[3]]
    assert cell_partial["comparable_untruncated_positive"] is False
    assert cell_partial["completed_all_arms"] is False
    assert cell_partial["repetitions_observed"] == 2
    assert cell_partial["repetitions_missing"] == 1

    arm0 = metric["arm_summaries"][ARMS[0]]
    assert arm0["comparable_task_denominator"] == 2
    assert arm0["non_comparable_task_count"] == 2
    assert arm0["task_denominator"] == 4

    for comp in metric["comparisons"]:
        assert comp["population"] == "common_comparable_untruncated_positives"
        assert comp["paired_task_denominator"] == 2
        assert comp["positive_task_total"] == 4
        excluded = {item["task_id"]: item["reason"] for item in comp["excluded_positive_tasks"]}
        assert excluded == {
            "syn-an-01": "token_truncated",
            "syn-an-02": "incomplete_across_arms",
        }
        assert comp["win"] + comp["tie"] + comp["loss"] == 2


def test_all_arm_comparisons_have_paired_denominators_and_wtl() -> None:
    result = _paired()
    comparisons = result["metrics"]["file_ndcg@10"]["comparisons"]
    assert len(comparisons) == 15
    for row in comparisons:
        total = row["win"] + row["tie"] + row["loss"]
        assert total == row["paired_task_denominator"]
        assert len(row["signed_task_differences"]) == row["paired_task_denominator"]
        assert set(row["family_breakdown"])
        assert row["status"] == "available"


def test_zero_common_comparable_tasks_yields_unavailable_comparison() -> None:
    keys = frozenset({
        (tid, ARMS[0], 1) for tid in ("syn-an-01", "syn-an-02", "syn-an-03", "syn-an-04")
    })
    result = _paired(unavailable=keys)
    for row in result["metrics"]["file_recall@5"]["comparisons"]:
        assert row["paired_task_denominator"] == 0
        assert row["status"] == "unavailable"
        assert row["reason"] == "no_common_comparable_tasks"
        assert row["mean_signed_difference"] is None


def test_controls_stay_outside_positive_means_and_preserve_all_dimensions() -> None:
    result = _paired()
    assert result["positive_task_ids"] == ["syn-an-01", "syn-an-02", "syn-an-03", "syn-an-04"]
    controls = result["controls"]
    assert set(controls) == {"absent", "ambiguous", "privacy", "unsupported"}
    assert controls["privacy"]["safety_refusal"]["true"] == 18
    assert controls["absent"]["absent_answer"]["true"] == 18
    assert controls["unsupported"]["states"] == {"completed": 6, "unavailable": 12}
    for row in controls.values():
        assert "surface_expectation_matches" in row
        assert "fallback_expectation_matches" in row
        assert "capability_expectation_matches" in row
        assert "reason_categories" in row
        assert row["ranking_success_claimed"] is False
        assert "task_weighted_mean" not in row


def test_no_significance_or_acceptance_claims_and_predecessors_unpooled() -> None:
    raw = synthetic_bytes()
    result = analyze_retained(
        raw,
        provenance=synthetic_provenance(raw),
        tasks=synthetic_tasks(),
        analyzer_source_sha256=SYNTHETIC_ANALYZER,
    )
    text = str(result).lower()
    assert "p_value" not in text and "p-value" not in text
    assert "significan" not in text.replace("no_significance", "")
    for flag in (
        "scientific_acceptance",
        "heldout_acceptance",
        "full_w5_acceptance",
        "production_acceptance",
        "native_platform_acceptance",
    ):
        assert result["acceptance"][flag] is False
    assert result["decision"]["operator_criteria_decision"] == "pending"
    assert result["decision"]["preferred_arm_selected"] is False
    assert result["decision"]["threshold_selected"] is False
    assert len(result["predecessor_failure_sha256"]) == 1
    assert result["predecessor_failure_sha256"][0].startswith("sha256:")
    assert "synthetic_predecessor_transport_failure" not in text
    assert result["predecessors_pooled_into_results"] is False
