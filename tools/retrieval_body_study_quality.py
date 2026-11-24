"""Inert prospective quality, outcome and recovery policy for the W5 body study."""
from __future__ import annotations
from collections import defaultdict
from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any
from retrieval_body_study_metrics import (
    collapse_gold_files,
    dedupe_ranked_files,
    expected_random_baseline,
    file_metrics_at_10,
)
from retrieval_body_study_common import CommonPositiveSet, validate_common_positive_set
from retrieval_body_study_outcomes import (
    SLOT_TRANSIENT_STATES,
    safe_state_counts,
    slot_exposes_quality,
    slot_has_integrity_violation,
    validate_slot_grid,
    validate_task_meta,
)
from retrieval_body_study_quality_boundaries import (
    absence_value as _absence_value,
    common_completion_outcome,
    derive_forbidden_work,
    low_floor_violations,
    paired_quality,
    public_metric_values,
    refusal_match as _refusal_match,
    refusal_verdict,
    split_expectation_failures,
    unknown_protected_execution,
)
from retrieval_body_study_recovery import classify_successor, select_primary_attempt
from retrieval_body_study_values import (
    ARMS,
    INERT_FLAGS,
    NDCG_GAIN_MIN,
    FAMILY_LOSS_MAX,
    QUALITY_SCHEMA,
    REPETITIONS,
    SLOT_COUNT,
    require,
)
def _validate_inputs(
    task_meta: Mapping[str, Mapping[str, Any]],
    golds: Mapping[str, Sequence[Sequence[Any]]],
    universes: Mapping[str, int | None],
    common_task_ids: CommonPositiveSet,
    floor_passed: bool,
    slots: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[tuple[str, str, int], Mapping[str, Any]],
    dict[str, int], dict[str, int],
    dict[str, Any],
]:
    _, meta, _ = validate_task_meta(task_meta)
    require(isinstance(golds, Mapping), "body_study:golds_mapping")
    require(isinstance(universes, Mapping), "body_study:universes_mapping")
    require(set(golds) == set(meta), "body_study:gold_task_set")
    require(set(universes) == set(meta), "body_study:universe_task_set")
    for task_id in meta:
        require(isinstance(golds[task_id], Sequence) and not isinstance(
            golds[task_id], (str, bytes, bytearray)), "body_study:gold_sequence")
        collapse_gold_files(golds[task_id])
        universe = universes[task_id]
        require(universe is None or (type(universe) is int and universe >= 0), "body_study:universe_size")
        if meta[task_id]["case_kind"] == "privacy":
            require(universe is None or universe == 0, "body_study:privacy_universe_available")
    require(type(floor_passed) is bool, "body_study:floor_claim_type")
    indexed, state_counts = validate_slot_grid(slots, meta)
    common = validate_common_positive_set(common_task_ids, meta)
    if common["floor_passed"]:
        require(
            all(
                isinstance(universe := universes[task_id], int) and universe > 0
                for task_id in common["task_ids"]
            ),
            "body_study:common_universe_unavailable",
        )
    return meta, indexed, state_counts, common["family_counts"], common
def _expected(meta: Mapping[str, Any], arm: str) -> tuple[str, str | None]:
    row = meta["expected_arm_outcomes"][arm]
    return str(row["state"]), row["refusal_code"]
def _slot_metrics(
    slot: Mapping[str, Any], golds: Sequence[Sequence[Any]], *, suppress: bool
) -> dict[str, float] | None:
    if suppress or slot["execution_state"] != "completed":
        return None
    if not collapse_gold_files(golds):
        return None
    return file_metrics_at_10(slot.get("ranked_files", ()), golds)
def _build_observations(
    meta: Mapping[str, Mapping[str, Any]],
    indexed: Mapping[tuple[str, str, int], Mapping[str, Any]],
    golds: Mapping[str, Sequence[Sequence[Any]]],
    *,
    suppress_metrics: bool,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    observations: list[dict[str, Any]] = []
    cases: dict[str, dict[str, Any]] = {}
    for task_id, task in meta.items():
        arm_rows: dict[str, dict[str, Any]] = {}
        case_states: list[str] = []
        for arm in ARMS:
            repetitions: list[dict[str, Any]] = []
            arm_states: list[str] = []
            for repetition in range(1, REPETITIONS + 1):
                slot = indexed[(task_id, arm, repetition)]
                state, expected_code = _expected(task, arm)
                metrics = _slot_metrics(slot, golds[task_id], suppress=suppress_metrics)
                absence = _absence_value(slot) if task["case_kind"] == "absent" else None
                refusal_correct = refusal_verdict(slot, task["case_kind"], state, expected_code)
                observation = {
                    "task_id": task_id,
                    "case_kind": task["case_kind"],
                    "family": task["family"],
                    "intent_id": task["intent_id"],
                    "wording_stratum": task["wording_stratum"],
                    "arm": arm,
                    "repetition": repetition,
                    "execution_state": slot["execution_state"],
                    "expected_state": state,
                    "expected_refusal_code": expected_code,
                    "refusal_code": slot.get("refusal_code"),
                    "expectation_matches": _refusal_match(slot, state, expected_code),
                    "ranked_file_count": len(dedupe_ranked_files(slot.get("ranked_files", ()))),
                    "quality_exposed": slot_exposes_quality(
                        slot, task["case_kind"], metrics
                    ) or refusal_correct is not None,
                    "metrics": metrics,
                    "absence_answer_correct": absence,
                    "refusal_correct": refusal_correct,
                }
                observations.append(observation)
                repetitions.append({
                    "repetition": repetition,
                    "execution_state": slot["execution_state"],
                    "expected_state": state,
                    "refusal_code": slot.get("refusal_code"),
                    "expectation_matches": observation["expectation_matches"],
                    "ranked_file_count": observation["ranked_file_count"],
                    "quality_exposed": observation["quality_exposed"],
                    "metrics": metrics,
                    "absence_answer_correct": absence,
                    "refusal_correct": refusal_correct,
                })
                arm_states.append(slot["execution_state"])
                case_states.append(slot["execution_state"])
            metric_rows = [row["metrics"] for row in repetitions if row["metrics"] is not None]
            means = {
                name: round(sum(row[name] for row in metric_rows) / len(metric_rows), 6)
                for name in ("file_recall@10", "file_mrr@10", "file_ndcg@10")
                if metric_rows
            }
            arm_rows[arm] = {
                "slot_count": REPETITIONS,
                "state_counts": safe_state_counts(arm_states),
                "completed_metric_count": len(metric_rows),
                "metric_means": means,
                "repetitions": repetitions,
            }
        cases[task_id] = {
            "task_id": task_id,
            "case_kind": task["case_kind"],
            "family": task["family"],
            "intent_id": task["intent_id"],
            "wording_stratum": task["wording_stratum"],
            "slot_count": len(ARMS) * REPETITIONS,
            "state_counts": safe_state_counts(case_states),
            "arms": arm_rows,
        }
    arm_summaries: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        rows = [row for row in observations if row["arm"] == arm]
        arm_summaries[arm] = {
            "slot_count": len(rows),
            "state_counts": safe_state_counts([row["execution_state"] for row in rows]),
            "expected_state_counts": safe_state_counts([row["expected_state"] for row in rows]),
            "quality_metrics_pooled": False,
        }
    return observations, cases, arm_summaries
def _control_summaries(
    observations: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    by_kind: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in observations:
        if row["case_kind"] in {"absent", "ambiguous", "privacy", "unsupported"}:
            by_kind[row["case_kind"]].append(row)
    failures: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    for kind in ("absent", "ambiguous", "privacy", "unsupported"):
        rows = by_kind[kind]
        absence = [row["absence_answer_correct"] for row in rows if row["absence_answer_correct"] is not None]
        refusal = [row["refusal_correct"] for row in rows if row["refusal_correct"] is not None]
        for row in rows:
            if not row["expectation_matches"]:
                failures.append({
                    "task_id": row["task_id"], "arm": row["arm"],
                    "repetition": row["repetition"],
                    "expected_state": row["expected_state"],
                    "observed_state": row["execution_state"],
                    "expected_refusal_code": row["expected_refusal_code"],
                    "observed_refusal_code": row["refusal_code"],
                })
            if kind == "absent" and row["absence_answer_correct"] is False:
                failures.append({
                    "task_id": row["task_id"], "arm": row["arm"],
                    "repetition": row["repetition"], "failure": "absence_incorrect",
                })
        summaries[kind] = {
            "task_count": len({row["task_id"] for row in rows}),
            "slot_count": len(rows),
            "state_counts": safe_state_counts([row["execution_state"] for row in rows]),
            "expectation_matches": sum(row["expectation_matches"] for row in rows),
            "expectation_mismatches": sum(not row["expectation_matches"] for row in rows),
            "absence_answer": {
                "correct": sum(value is True for value in absence),
                "incorrect": sum(value is False for value in absence),
                "unavailable": len(rows) - len(absence),
            },
            "refusal": {
                "correct": sum(value is True for value in refusal),
                "incorrect": sum(value is False for value in refusal),
                "unavailable": len(rows) - len(refusal),
            },
            "per_slot_metrics": {
                f"{row['task_id']}|{row['arm']}|{row['repetition']}": row["metrics"]
                for row in rows if row["metrics"] is not None
            },
        }
    return summaries, failures, {
        "absence_correct": sum(row["absence_answer_correct"] is True for row in observations),
        "absence_incorrect": sum(row["absence_answer_correct"] is False for row in observations),
    }
def evaluate_body_study(
    *,
    task_meta: Mapping[str, Mapping[str, Any]],
    golds: Mapping[str, Sequence[Sequence[Any]]],
    universes: Mapping[str, int | None],
    common_task_ids: CommonPositiveSet,
    floor_passed: bool,
    slots: Sequence[Mapping[str, Any]],
    integrity_broken: bool = False,
    privacy_disclosed: bool = False,
    forbidden_work_executed: bool = False,
    preparation_complete: bool = True,
    telemetry_cleanup_proven: bool = True,
) -> dict[str, Any]:
    for name, value in (
        ("integrity_broken", integrity_broken),
        ("privacy_disclosed", privacy_disclosed),
        ("forbidden_work_executed", forbidden_work_executed),
        ("preparation_complete", preparation_complete),
        ("telemetry_cleanup_proven", telemetry_cleanup_proven),
    ):
        require(type(value) is bool, f"body_study:{name}_type")
    meta, indexed, state_counts, family_counts, common = _validate_inputs(
        task_meta, golds, universes, common_task_ids, floor_passed, slots)
    slot_violation = any(slot_has_integrity_violation(slot) for slot in indexed.values())
    forbidden_observations = derive_forbidden_work(meta, indexed)
    protected_unknown = unknown_protected_execution(meta, indexed)
    floor_violations = low_floor_violations(meta, indexed) if not common["floor_passed"] else []
    forbidden_observations.extend({
        "task_id": task_id, "arm": arm, "repetition": repetition,
        "reason": "retrieval_after_failed_common_floor",
    } for task_id, arm, repetition in floor_violations)
    cleanup_unknown = any(slot.get("cleanup_unknown") is True for slot in indexed.values())
    suppress_metrics = (integrity_broken or privacy_disclosed or forbidden_work_executed
                        or slot_violation or bool(forbidden_observations) or bool(protected_unknown))
    observations, cases, arm_summaries = _build_observations(
        meta, indexed, golds, suppress_metrics=suppress_metrics)
    controls, control_failures, absence_totals = _control_summaries(observations)
    matrix_failures = [{key: row[key] for key in (
        "task_id", "arm", "repetition", "expected_state", "execution_state",
        "expected_refusal_code", "refusal_code")}
        for row in observations if not row["expectation_matches"]]
    failure_groups = split_expectation_failures(observations, common["task_ids"])
    base: dict[str, Any] = {
        "schema": QUALITY_SCHEMA,
        "case_count": len(meta), "arm_count": len(ARMS),
        "repetition_count": REPETITIONS, "slot_count": SLOT_COUNT,
        "slot_states": safe_state_counts(state_counts),
        "all_cases_reported": len(cases) == 24,
        "all_slots_reported": len(observations) == SLOT_COUNT,
        "common_set_identity": common["identity"],
        "common_task_ids": list(common["task_ids"]),
        "common_floor": {
            "distinct_intents": common["distinct_intents"],
            "family_counts": dict(family_counts),
            "minimum_distinct_intents": common["minimum_distinct_intents"],
            "minimum_per_family": common["minimum_per_family"],
            "actual_passed": common["floor_passed"], "caller_claim": floor_passed,
        },
        "anchors_controls_pooled": False,
        "observations": observations, "cases": cases,
        "arm_summaries": arm_summaries,
        "anchors": {task_id: case for task_id, case in cases.items() if case["case_kind"] == "metadata_anchor"},
        "controls": controls,
        "absence": absence_totals,
        "base_rates": {
            task_id: {
                "universe_size": universes[task_id],
                "gold_count": len(collapse_gold_files(golds[task_id])),
                **expected_random_baseline(
                    universes[task_id], len(collapse_gold_files(golds[task_id]))
                ),
            } for task_id in meta
        },
        "exposed_to_scores": any(row["quality_exposed"] for row in observations),
        "unstarted_scored_as_zero": False,
        "threshold_comparisons_unrounded": True,
        "refusal_failures": control_failures, "expectation_failures": matrix_failures,
        "signal_gate_failures": {
            "common": [dict(row) for row in failure_groups["common"]],
            "semantic_controls": [dict(row) for row in failure_groups["semantic_controls"]],
            "other": [dict(row) for row in failure_groups["other"]],
        },
        "forbidden_work_observed": bool(forbidden_observations) or forbidden_work_executed,
        "forbidden_work_failures": forbidden_observations,
        "unknown_forbidden_execution": protected_unknown,
        "inadmissibility_cause": "other" if protected_unknown else None,
        "low_floor_retrieval_violations": floor_violations,
        "v2_secondary_rescue": False,
        "cost": {"observed": "unknown", "shared_intervals_summed": False},
        **INERT_FLAGS,
    }
    if (
        integrity_broken
        or privacy_disclosed
        or forbidden_work_executed
        or slot_violation
        or bool(forbidden_observations)
        or bool(protected_unknown)
    ):
        return {**base, "outcome": "inadmissible", "signal": None}
    if not common["floor_passed"] and (cleanup_unknown or not preparation_complete or not telemetry_cleanup_proven):
        return {**base, "outcome": "incomplete_technical", "signal": None}
    if not common["floor_passed"]:
        missing = [[task, arm, rep] for (task, arm, rep), slot in indexed.items()
                   if slot["execution_state"] in {"missing", "not_run"}]
        if missing:
            return {**base, "outcome": "incomplete_technical", "signal": None, "missing_slots": missing}
        return {
            **base, "outcome": "inconclusive_floor", "signal": None,
            "intentionally_unstarted_slots": sum(
                slot["execution_state"] == "unstarted" for slot in indexed.values()
            ), "preflight_slots_retained": SLOT_COUNT - sum(
                slot["execution_state"] == "unstarted" for slot in indexed.values()
            ),
        }
    if cleanup_unknown or not preparation_complete or not telemetry_cleanup_proven:
        return {**base, "outcome": "incomplete_technical", "signal": None}
    transient = [[task_id, arm, repetition] for (task_id, arm, repetition), slot in indexed.items()
                 if slot["execution_state"] in SLOT_TRANSIENT_STATES]
    if transient:
        return {**base, "outcome": "incomplete_technical", "signal": None,
                "missing_slots": transient}
    completion = common_completion_outcome(observations, indexed, common["task_ids"])
    if completion is not None:
        return {**base, **completion}
    contrasts, families, per_task = paired_quality(observations, meta, common["task_ids"])
    ndcg_gain_min = Fraction(str(NDCG_GAIN_MIN))
    family_loss_max = Fraction(str(FAMILY_LOSS_MAX))
    signal = (
        all(
            values["ndcg_gain"] >= ndcg_gain_min
            and values["mrr_gain"] >= 0.0
            and values["recall_gain"] >= 0.0
            for values in contrasts.values()
        )
        and all(value >= -family_loss_max for value in families.values())
        and not failure_groups["common"]
        and not failure_groups["semantic_controls"]
    )
    public_contrasts = {comparator: public_metric_values(values)
                        for comparator, values in contrasts.items()}
    public_families = {family: float(value) for family, value in families.items()}
    public_per_task = {key: public_metric_values(values)
                       for key, values in per_task.items()}
    return {
        **base,
        "outcome": "calibration_signal_present" if signal else "signal_not_demonstrated",
        "signal": signal, "contrasts": public_contrasts,
        "family_ndcg_gains": public_families, "per_task": public_per_task,
    }
__all__ = [
    "classify_successor", "collapse_gold_files", "dedupe_ranked_files",
    "evaluate_body_study", "expected_random_baseline", "file_metrics_at_10",
    "select_primary_attempt",
]
