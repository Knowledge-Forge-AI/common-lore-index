"""Deterministic descriptive paired relevance and control analysis.

Stored maintained grades are consumed as-is; nothing is regraded. Repetitions
reduce within each task/arm first (they share embeddings and are never
independent samples), then each distinct task receives one weight. Absent metric
namespaces emit explicit unavailable-with-reason entries; non-comparable
positives remain visible with membership flags and are never silently dropped or
pooled into common-comparable arm comparisons. Controls stay outside positive
means with distinct safety, absence, surface, fallback and capability counts.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations
from typing import Any

from retrieval_supplement_analysis_admission import AdmittedBundle, SlotRecord
from retrieval_supplement_analysis_values import (
    DISPLAY_DIGITS,
    FILE_METRICS,
    METRIC_ORDER,
    SPAN_METRICS,
)
from retrieval_supplement_primitives import ARMS, POSITIVE_FAMILIES

_SPAN_ABSENT_REASON = "span_namespace_not_recorded_in_bundle"


def _round(value: float) -> float:
    return round(value, DISPLAY_DIGITS)


def _cell(
    rows: list[SlotRecord],
    metric: str,
    *,
    namespace_present: bool,
    is_comparable: bool,
    token_truncated: bool,
    completed_all_arms: bool,
) -> dict[str, Any]:
    if not namespace_present:
        return {
            "status": "unavailable",
            "reason": _SPAN_ABSENT_REASON,
            "mean": None,
            "minimum": None,
            "maximum": None,
            "range": None,
            "repetitions_observed": 0,
            "repetitions_missing": len(rows),
            "values": [],
            "comparable_untruncated_positive": is_comparable,
            "token_truncated": token_truncated,
            "completed_all_arms": completed_all_arms,
        }
    observed = [_round(row.metrics[metric]) for row in rows if row.state == "completed"]
    missing = len(rows) - len(observed)
    if not observed:
        return {
            "status": "unavailable",
            "reason": "no_completed_repetition",
            "mean": None,
            "minimum": None,
            "maximum": None,
            "range": None,
            "repetitions_observed": 0,
            "repetitions_missing": missing,
            "values": [],
            "comparable_untruncated_positive": is_comparable,
            "token_truncated": token_truncated,
            "completed_all_arms": completed_all_arms,
        }
    return {
        "status": "available",
        "reason": None,
        "mean": _round(sum(observed) / len(observed)),
        "minimum": min(observed),
        "maximum": max(observed),
        "range": _round(max(observed) - min(observed)),
        "repetitions_observed": len(observed),
        "repetitions_missing": missing,
        "values": observed,
        "comparable_untruncated_positive": is_comparable,
        "token_truncated": token_truncated,
        "completed_all_arms": completed_all_arms,
    }


def _exclusion_reason(admitted: AdmittedBundle, task_id: str) -> str:
    if admitted.task_truncated.get(task_id, False):
        return "token_truncated"
    return "incomplete_across_arms"


def _comparisons(
    cells: dict[str, dict[str, dict[str, Any]]],
    families: dict[str, str],
    admitted: AdmittedBundle,
    *,
    namespace_present: bool,
) -> list[dict[str, Any]]:
    comparable_set = frozenset(admitted.comparable_positive_tasks)
    ordered_tasks = sorted(cells)
    excluded = [
        {"task_id": tid, "reason": _exclusion_reason(admitted, tid)}
        for tid in ordered_tasks
        if tid not in comparable_set
    ]
    out: list[dict[str, Any]] = []
    for left, right in combinations(ARMS, 2):
        family_rows: dict[str, dict[str, int]] = {
            family: {"win": 0, "tie": 0, "loss": 0, "paired_tasks": 0}
            for family in POSITIVE_FAMILIES
        }
        if not namespace_present:
            out.append({
                "left_arm": left,
                "right_arm": right,
                "population": "common_comparable_untruncated_positives",
                "status": "unavailable",
                "reason": _SPAN_ABSENT_REASON,
                "paired_task_denominator": 0,
                "positive_task_total": len(ordered_tasks),
                "excluded_positive_tasks": excluded,
                "all_pairwise_completed_denominator": 0,
                "signed_task_differences": [],
                "mean_signed_difference": None,
                "win": 0,
                "tie": 0,
                "loss": 0,
                "family_breakdown": family_rows,
            })
            continue

        differences: list[dict[str, Any]] = []
        win = tie = loss = 0
        all_pairwise_completed = 0
        for task_id in ordered_tasks:
            a = cells[task_id][left]["mean"]
            b = cells[task_id][right]["mean"]
            if a is not None and b is not None:
                all_pairwise_completed += 1
            if task_id not in comparable_set or a is None or b is None:
                continue
            diff = _round(float(a) - float(b))
            differences.append({"task_id": task_id, "difference": diff})
            bucket = family_rows[families[task_id]]
            bucket["paired_tasks"] += 1
            if diff > 0:
                win += 1
                bucket["win"] += 1
            elif diff < 0:
                loss += 1
                bucket["loss"] += 1
            else:
                tie += 1
                bucket["tie"] += 1
        denominator = len(differences)
        out.append({
            "left_arm": left,
            "right_arm": right,
            "population": "common_comparable_untruncated_positives",
            "status": "available" if denominator else "unavailable",
            "reason": None if denominator else "no_common_comparable_tasks",
            "paired_task_denominator": denominator,
            "positive_task_total": len(ordered_tasks),
            "excluded_positive_tasks": excluded,
            "all_pairwise_completed_denominator": all_pairwise_completed,
            "signed_task_differences": differences,
            "mean_signed_difference": (
                _round(sum(d["difference"] for d in differences) / denominator)
                if denominator else None
            ),
            "win": win,
            "tie": tie,
            "loss": loss,
            "family_breakdown": family_rows,
        })
    return out


def _tally(values: Sequence[bool | None]) -> dict[str, int]:
    return {
        "true": sum(v is True for v in values),
        "false": sum(v is False for v in values),
        "unknown": sum(v is None for v in values),
        "total": len(values),
    }


def _controls(admitted: AdmittedBundle) -> dict[str, Any]:
    controls: dict[str, Any] = {}
    kinds = sorted({task.kind for task in admitted.tasks if task.kind != "positive"})
    for kind in kinds:
        rows = [slot for slot in admitted.slots if slot.kind == kind]
        task_ids = sorted({slot.task_id for slot in rows})
        controls[kind] = {
            "task_ids": task_ids,
            "task_count": len(task_ids),
            "slot_count": len(rows),
            "states": {
                state: sum(r.state == state for r in rows)
                for state in sorted({r.state for r in rows})
            },
            "grading_states": {
                state: sum(r.grading_state == state for r in rows)
                for state in sorted({r.grading_state for r in rows})
            },
            "capability_categories": {
                cat: sum(r.capability_category == cat for r in rows)
                for cat in sorted({r.capability_category for r in rows})
            },
            "reason_categories": {
                cat: sum(r.reason_category == cat for r in rows)
                for cat in sorted({r.reason_category for r in rows})
            },
            "safety_refusal": _tally([r.safety_refusal_correct for r in rows]),
            "absent_answer": _tally([r.absent_answer_correct for r in rows]),
            "surface_expectation_matches": _tally([r.surface_expectation_matches for r in rows]),
            "fallback_expectation_matches": _tally([r.fallback_expectation_matches for r in rows]),
            "capability_expectation_matches": _tally([r.capability_expectation_matches for r in rows]),
            "mismatched_slot_count": sum(not r.capability_expectation_matches for r in rows),
            "ranking_success_claimed": False,
        }
    return controls


def paired_analysis(admitted: AdmittedBundle) -> dict[str, Any]:
    """Task-weighted paired summaries over positive tasks only; controls separate."""
    positives = [task for task in admitted.tasks if task.kind == "positive"]
    families = {task.task_id: task.family for task in positives}
    comparable_set = frozenset(admitted.comparable_positive_tasks)
    observed_ns = frozenset(admitted.observed_namespaces)
    by_cell: dict[tuple[str, str], list[SlotRecord]] = {}
    for slot in admitted.slots:
        by_cell.setdefault((slot.task_id, slot.arm), []).append(slot)

    metrics: dict[str, Any] = {}
    for metric in METRIC_ORDER:
        namespace = "file" if metric.startswith("file_") else "span"
        ns_present = namespace in observed_ns
        cells = {
            task.task_id: {
                arm: _cell(
                    by_cell[(task.task_id, arm)],
                    metric,
                    namespace_present=ns_present,
                    is_comparable=task.task_id in comparable_set,
                    token_truncated=admitted.task_truncated.get(task.task_id, False),
                    completed_all_arms=admitted.task_completed_all_arms.get(task.task_id, False),
                )
                for arm in ARMS
            }
            for task in positives
        }
        summaries: dict[str, Any] = {}
        for arm in ARMS:
            if not ns_present:
                summaries[arm] = {
                    "status": "unavailable",
                    "reason": _SPAN_ABSENT_REASON,
                    "task_weighted_mean": None,
                    "task_denominator": 0,
                    "positive_task_total": len(positives),
                    "comparable_task_weighted_mean": None,
                    "comparable_task_denominator": 0,
                    "comparable_positive_total": len(comparable_set),
                    "non_comparable_task_count": 0,
                    "missing_positive_task_count": len(positives),
                    "family_means": {family: None for family in POSITIVE_FAMILIES},
                    "comparable_family_means": {family: None for family in POSITIVE_FAMILIES},
                    "family_denominators": {family: 0 for family in POSITIVE_FAMILIES},
                    "independent_samples_claimed": False,
                }
                continue

            all_means = [
                float(cells[t.task_id][arm]["mean"])
                for t in positives
                if cells[t.task_id][arm]["mean"] is not None
            ]
            comp_means = [
                float(cells[t.task_id][arm]["mean"])
                for t in positives
                if t.task_id in comparable_set and cells[t.task_id][arm]["mean"] is not None
            ]
            fam_means: dict[str, float | None] = {}
            comp_fam_means: dict[str, float | None] = {}
            fam_denoms: dict[str, int] = {}
            for family in POSITIVE_FAMILIES:
                f_vals = [
                    float(cells[t.task_id][arm]["mean"])
                    for t in positives
                    if t.family == family and cells[t.task_id][arm]["mean"] is not None
                ]
                cf_vals = [
                    float(cells[t.task_id][arm]["mean"])
                    for t in positives
                    if t.family == family and t.task_id in comparable_set
                    and cells[t.task_id][arm]["mean"] is not None
                ]
                fam_denoms[family] = len(f_vals)
                fam_means[family] = _round(sum(f_vals) / len(f_vals)) if f_vals else None
                comp_fam_means[family] = _round(sum(cf_vals) / len(cf_vals)) if cf_vals else None

            summaries[arm] = {
                "status": "available" if all_means else "unavailable",
                "reason": None if all_means else "no_completed_positive_tasks",
                "task_weighted_mean": _round(sum(all_means) / len(all_means)) if all_means else None,
                "task_denominator": len(all_means),
                "positive_task_total": len(positives),
                "comparable_task_weighted_mean": (
                    _round(sum(comp_means) / len(comp_means)) if comp_means else None
                ),
                "comparable_task_denominator": len(comp_means),
                "comparable_positive_total": len(comparable_set),
                "non_comparable_task_count": len(all_means) - len(comp_means),
                "missing_positive_task_count": len(positives) - len(all_means),
                "family_means": fam_means,
                "comparable_family_means": comp_fam_means,
                "family_denominators": fam_denoms,
                "independent_samples_claimed": False,
            }
        metrics[metric] = {
            "namespace": namespace,
            "status": "available" if ns_present else "unavailable",
            "reason": None if ns_present else _SPAN_ABSENT_REASON,
            "task_arm": cells,
            "arm_summaries": summaries,
            "comparisons": _comparisons(
                cells, families, admitted, namespace_present=ns_present
            ),
        }

    return {
        "namespaces": ["file", "span"],
        "observed_namespaces": list(admitted.observed_namespaces),
        "namespace_status": {
            "file": {"status": "available", "reason": None, "metrics": list(FILE_METRICS)},
            "span": (
                {"status": "available", "reason": None, "metrics": list(SPAN_METRICS)}
                if "span" in observed_ns
                else {
                    "status": "unavailable",
                    "reason": _SPAN_ABSENT_REASON,
                    "metrics": list(SPAN_METRICS),
                }
            ),
        },
        "positive_task_ids": [task.task_id for task in positives],
        "comparable_positive_tasks": list(admitted.comparable_positive_tasks),
        "comparable_positive_count": len(admitted.comparable_positive_tasks),
        "family_counts": dict(admitted.family_counts),
        "floor_met": admitted.floor_met,
        "metrics": metrics,
        "controls": _controls(admitted),
        "repetitions_are_independent_samples": False,
        "preferred_attempt_selected": False,
    }


__all__ = ["paired_analysis"]
