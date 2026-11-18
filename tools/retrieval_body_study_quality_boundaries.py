"""Bounded quality classifications for the inert body-study matrix."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any

from retrieval_body_study_outcomes import slot_has_forbidden_execution
from retrieval_body_study_values import BODY_FAMILIES, COMPARISON_ARMS, FTS_ARM, FUSION_ARM, REPETITIONS, require

METRIC_NAMES = ("file_recall@10", "file_mrr@10", "file_ndcg@10")


def _expected(task: Mapping[str, Any], arm: str) -> tuple[str, str | None]:
    row = task["expected_arm_outcomes"][arm]
    return str(row["state"]), row["refusal_code"]


def refusal_match(slot: Mapping[str, Any], state: str, code: str | None) -> bool:
    """Respect state, capability, no-hit refusal and supplied grader safety."""
    if slot["execution_state"] != state or slot.get("safety_refusal_correct") is False:
        return False
    capability = slot.get("capability_state")
    if capability is not None and capability != ("available" if state == "completed" else state):
        return False
    return state != "refused" or (not slot.get("ranked_files")
        and not slot.get("returned_hit_count", 0) and slot.get("refusal_code") == code)


def absence_value(slot: Mapping[str, Any]) -> bool | None:
    """An explicit envelope context must establish complete available absence."""
    if slot["execution_state"] != "completed":
        return None
    empty = not bool(slot.get("ranked_files", ())) and not bool(slot.get("returned_hit_count", 0))
    contextual = "scope_complete" in slot or "capability_state" in slot
    if not contextual:
        return None
    computed = empty and (not contextual or (
        slot.get("scope_complete") is True and slot.get("capability_state") == "available"))
    value = slot.get("absence_answer_correct", slot.get("absent_answer_correct"))
    if value is not None:
        require(type(value) is bool and value is computed, "body_study:absence_correct_mismatch")
    return computed


def refusal_verdict(slot: Mapping[str, Any], kind: str, state: str, code: str | None) -> bool | None:
    observed = slot["execution_state"]
    if observed not in {"completed", "refused", "unavailable"}:
        return None
    if kind == "privacy" or (kind == "unsupported" and (
        state in {"refused", "unavailable"} or observed in {"refused", "unavailable"})):
        return refusal_match(slot, state, code)
    return None


def derive_forbidden_work(
    meta: Mapping[str, Mapping[str, Any]],
    indexed: Mapping[tuple[str, str, int], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return safe observations of semantic/privacy work outside capability."""
    failures: list[dict[str, Any]] = []
    for (task_id, arm, repetition), slot in indexed.items():
        task = meta[task_id]
        state, _ = _expected(task, arm)
        if slot_has_forbidden_execution(
            slot, case_kind=task["case_kind"], expected_state=state
        ):
            failures.append({
                "task_id": task_id,
                "arm": arm,
                "repetition": repetition,
                "expected_state": state,
                "observed_state": slot["execution_state"],
                "reason": "forbidden_semantic_or_private_execution",
            })
    return failures


def low_floor_violations(
    meta: Mapping[str, Mapping[str, Any]],
    indexed: Mapping[tuple[str, str, int], Mapping[str, Any]],
) -> list[list[Any]]:
    """Find actual retrieval/score work after a failed floor, preserving preflight."""
    violations: list[list[Any]] = []
    for (task_id, arm, repetition), slot in indexed.items():
        ranked = any(name in slot for name in ("rank", "score", "quality_score", "metrics", "quality_metrics"))
        expected, _ = _expected(meta[task_id], arm)
        started = slot["execution_state"] == "completed" or (
            expected == "completed" and slot["execution_state"] in {"failed", "timed_out"})
        if started or slot.get("ranked_files") or slot.get("returned_hit_count", 0) or ranked:
            violations.append([task_id, arm, repetition])
    return violations


def unknown_protected_execution(
    meta: Mapping[str, Mapping[str, Any]],
    indexed: Mapping[tuple[str, str, int], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """A failed/lost protected guard does not prove refusal before model input."""
    return [{"task_id": task, "arm": arm, "repetition": repetition,
             "reason": "protected_preflight_execution_unknown"}
            for (task, arm, repetition), slot in indexed.items()
            if _expected(meta[task], arm)[0] in {"refused", "unavailable"}
            and slot["execution_state"] in {"failed", "timed_out", "missing"}]


def common_missing_slots(
    observations: Sequence[Mapping[str, Any]], common_task_ids: Sequence[str]
) -> list[list[Any]]:
    """List every common comparison slot without a completed metric."""
    common = set(common_task_ids)
    missing: list[list[Any]] = []
    for row in observations:
        if row["task_id"] not in common or row["arm"] not in COMPARISON_ARMS:
            continue
        if row["execution_state"] != "completed" or row["metrics"] is None:
            missing.append([row["task_id"], row["arm"], row["repetition"]])
    return missing


def common_completion_outcome(
    observations: Sequence[Mapping[str, Any]],
    indexed: Mapping[tuple[str, str, int], Mapping[str, Any]],
    common_task_ids: Sequence[str],
) -> dict[str, Any] | None:
    """A complete, well-formed query refusal is a negative, never retry debt."""
    missing = common_missing_slots(observations, common_task_ids)
    if not missing:
        return None
    refused = set()
    for key, slot in indexed.items():
        if (slot["execution_state"] == "refused" and slot.get("capability_state") == "refused"
                and isinstance(slot.get("refusal_code"), str) and slot["refusal_code"]
                and not slot.get("ranked_files") and not slot.get("returned_hit_count", 0)):
            refused.add(key)
    if any(tuple(row) not in refused for row in missing):
        return {"outcome": "incomplete_technical", "signal": None, "missing_slots": missing}
    return {"outcome": "signal_not_demonstrated", "signal": False,
            "refused_common_slots": missing, "missing_slots": [], "contrasts": None,
            "quality_contrasts_status": "unavailable_due_to_observed_refusal"}


def split_expectation_failures(
    observations: Sequence[Mapping[str, Any]], common_task_ids: Sequence[str]
) -> dict[str, list[Mapping[str, Any]]]:
    """Partition safe mismatches by signal gate versus separately reported cases."""
    common = set(common_task_ids)
    result: dict[str, list[Mapping[str, Any]]] = {"common": [], "semantic_controls": [], "other": []}
    for row in observations:
        if row["expectation_matches"]:
            continue
        if row["case_kind"] == "body_positive" and row["task_id"] in common:
            result["common"].append(row)
        elif row["case_kind"] in {"privacy", "unsupported"}:
            result["semantic_controls"].append(row)
        else:
            result["other"].append(row)
    return result


def exact_metric_average(rows: Sequence[Mapping[str, Any]]) -> dict[str, Fraction]:
    """Aggregate canonical six-decimal observations without a second rounding."""
    return {
        name: sum((Fraction(str(row[name])) for row in rows), Fraction(0)) / len(rows)
        for name in METRIC_NAMES
    }


def public_metric_values(values: Mapping[str, Any]) -> dict[str, float]:
    """Serialize exact threshold intermediates without exporting Fraction objects."""
    return {name: float(value) for name, value in values.items()}


def paired_quality(
    observations: Sequence[Mapping[str, Any]],
    meta: Mapping[str, Mapping[str, Any]],
    common: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    """Aggregate comparison metrics exactly; presentation rounding is deferred."""
    by_key = {(row["task_id"], row["arm"], row["repetition"]): row for row in observations}
    metrics: dict[tuple[str, str], dict[str, Any]] = {}
    for task_id in common:
        for arm in COMPARISON_ARMS:
            reps = [by_key[(task_id, arm, repetition)]["metrics"]
                    for repetition in range(1, REPETITIONS + 1)]
            require(all(isinstance(value, Mapping) for value in reps),
                    "body_study:common_metric_missing")
            metrics[(task_id, arm)] = exact_metric_average(reps)
    contrasts: dict[str, dict[str, Any]] = {}
    for comparator in ("native_tools_baseline", FTS_ARM):
        contrasts[comparator] = {
            f"{short}_gain": sum(
                metrics[(task, FUSION_ARM)][name] - metrics[(task, comparator)][name]
                for task in common
            ) / len(common)
            for short, name in (("ndcg", "file_ndcg@10"),
                                ("mrr", "file_mrr@10"),
                                ("recall", "file_recall@10"))
        }
    families: dict[str, Any] = {}
    for family in BODY_FAMILIES:
        tasks = [task for task in common if meta[task]["family"] == family]
        require(bool(tasks), "body_study:family_common_empty")
        families[family] = min(
            sum(metrics[(task, FUSION_ARM)]["file_ndcg@10"]
                - metrics[(task, comparator)]["file_ndcg@10"] for task in tasks) / len(tasks)
            for comparator in ("native_tools_baseline", FTS_ARM)
        )
    return contrasts, families, {f"{task}|{arm}": value for (task, arm), value in metrics.items()}


__all__ = [
    "absence_value",
    "common_missing_slots",
    "common_completion_outcome",
    "derive_forbidden_work",
    "exact_metric_average",
    "low_floor_violations",
    "paired_quality",
    "public_metric_values",
    "refusal_match",
    "refusal_verdict",
    "split_expectation_failures",
    "unknown_protected_execution",
]
