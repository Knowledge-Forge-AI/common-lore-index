"""Slot-grid, metric-namespace, expectation and paired-task admission joins.

Pure leaf called by ``retrieval_supplement_analysis_admission``. Requires the
12-metric ``file_*`` namespace on every slot, allows the 12-metric ``span``
namespace only when uniformly present across every slot, joins raw+nested
reasons/states/metrics, recomputes surface/fallback/capability expectation
matches, and joins ``paired_tasks`` against raw record IDs and counted tokens.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from retrieval_supplement_analysis_joins import (
    SlotRecord,
    TokenRecordEvidence,
    validate_slot_record_ids,
)
from retrieval_supplement_analysis_values import (
    FILE_METRICS,
    GRADING_STATES,
    MATCH_SURFACES,
    SLOT_STATES,
    SPAN_METRICS,
    TaskDescriptor,
    as_bool,
    as_digest,
    as_int,
    as_metric,
    as_str,
    as_token,
    capability_category,
    exact_keys,
    fail,
    optional_bool,
    reason_category,
    refuse,
)
from retrieval_supplement_primitives import (
    ACTUAL_SURFACES,
    ARMS,
    EXECUTIONS,
    FALLBACK_POLICIES,
    POSITIVE_FAMILIES,
    STRATA,
)

_RAW = frozenset(
    "task_id family kind arm repetition state reason query_record_id target_record_ids metrics".split()
)
_NESTED = frozenset(
    "slot task_digests delivery_kind execution_state reason expected_execution_state "
    "surface_observation expected_surface expected_fallback_policy surface_expectation_matches "
    "fallback_expectation_matches capability_expectation_matches grading capture_reference_sha256".split()
)
_SURFACE_OBS = frozenset(
    "surface fallback requested_mode effective_mode fallback_disclosure basis".split()
)
_GRADING = frozenset(
    "task_id family comparability_stratum grading_rule execution_state grading_state capability_state "
    "safety_refusal_correct absent_answer_correct metrics returned_hit_count timing byte_accounting".split()
)
_PAIRED_TASK = frozenset(
    "task_id kind family arm_states completed_all_arms token_truncated "
    "comparable_untruncated_positive checked_token_records".split()
)
_FILE_SET = frozenset(FILE_METRICS)
_DUAL_SET = frozenset(FILE_METRICS + SPAN_METRICS)


@dataclass(frozen=True)
class VerifiedSlotGrid:
    slots: tuple[SlotRecord, ...]
    declared_slot_keys: frozenset[tuple[str, str, int, int]]
    observed_namespaces: tuple[str, ...]
    state_counts: dict[str, int]
    task_truncated: dict[str, bool]
    task_completed_all_arms: dict[str, bool]
    comparable_positive_tasks: tuple[str, ...]
    family_counts: dict[str, int]
    floor_met: bool


def _slot_metrics(value: object, label: str) -> tuple[dict[str, float], tuple[str, ...]]:
    if not isinstance(value, dict):
        fail(f"analysis:{label}:object_required")
    keys = frozenset(value)
    if keys == _FILE_SET:
        namespaces: tuple[str, ...] = ("file",)
    elif keys == _DUAL_SET:
        namespaces = ("file", "span")
    else:
        fail(f"analysis:{label}:metric_namespace")
    return {name: as_metric(value[name], label) for name in sorted(keys)}, namespaces


def _check_expectations(
    cell: dict[str, Any], task: TaskDescriptor, arm: str, state: str
) -> tuple[bool, bool, bool]:
    exp_exec = as_str(cell["expected_execution_state"], "expected_execution_state")
    exp_surface = as_str(cell["expected_surface"], "expected_surface")
    exp_fallback = as_str(cell["expected_fallback_policy"], "expected_fallback_policy")
    refuse(
        exp_exec in EXECUTIONS and exp_surface in ACTUAL_SURFACES
        and exp_fallback in FALLBACK_POLICIES,
        "analysis:expected_surface_or_fallback_vocab",
    )
    obs = exact_keys(cell["surface_observation"], _SURFACE_OBS, "surface_observation")
    actual_surface = as_str(obs["surface"], "observed_surface")
    actual_fallback = as_str(obs["fallback"], "observed_fallback")
    refuse(
        actual_surface in MATCH_SURFACES and actual_fallback in {"required", "none"},
        "analysis:observed_surface_or_fallback_vocab",
    )
    as_str(obs["basis"], "surface_basis")
    for mode_key in ("requested_mode", "effective_mode"):
        refuse(obs[mode_key] is None or type(obs[mode_key]) is str, "analysis:surface_mode_shape")
    refuse(
        obs["fallback_disclosure"] is None or isinstance(obs["fallback_disclosure"], dict),
        "analysis:fallback_disclosure_shape",
    )
    surf_ok = as_bool(cell["surface_expectation_matches"], "surface_match")
    fb_ok = as_bool(cell["fallback_expectation_matches"], "fallback_match")
    cap_ok = as_bool(cell["capability_expectation_matches"], "capability_match")
    refuse(surf_ok is (actual_surface == exp_surface), "analysis:surface_match_join")
    expected_fb_ok = exp_fallback == "record_actual" or actual_fallback == exp_fallback
    refuse(fb_ok is expected_fb_ok, "analysis:fallback_match_join")
    if task.arm_expectations is not None:
        pinned = task.arm_expectations[arm]
        refuse(
            exp_exec == pinned["execution"]
            and exp_surface == pinned["actual_surface"]
            and exp_fallback == pinned["fallback_policy"],
            "analysis:pinned_expectation_join",
        )
        expected_cap_ok = (
            state == pinned["execution"]
            and (pinned["reason"] is None or cell["reason"] == pinned["reason"])
            and surf_ok
            and fb_ok
        )
        refuse(cap_ok is expected_cap_ok, "analysis:capability_match_join")
    elif state != exp_exec or not surf_ok or not fb_ok:
        refuse(cap_ok is False, "analysis:capability_match_join")
    return surf_ok, fb_ok, cap_ok


def validate_slot_grid(
    *,
    descriptors: Sequence[TaskDescriptor],
    report: Mapping[str, Any],
    paired: Mapping[str, Any],
    native: bool,
    token_map: Mapping[str, TokenRecordEvidence] | None,
) -> VerifiedSlotGrid:
    """Validate ordered raw+nested slots, state counts and paired task joins."""
    slot_total = len(descriptors) * len(ARMS) * 3
    raw_rows, nested_rows = paired["raw_slots"], report["slots"]
    refuse(
        type(raw_rows) is list and type(nested_rows) is list
        and len(raw_rows) == slot_total and len(nested_rows) == slot_total,
        "analysis:slot_lists",
    )
    expected_grid = [(t, arm, rep) for t in descriptors for arm in ARMS for rep in (1, 2, 3)]
    declared_slot_keys = frozenset((t.task_id, arm, rep, 1) for t, arm, rep in expected_grid)
    slots: list[SlotRecord] = []
    counts = {state: 0 for state in SLOT_STATES}
    control_counts: dict[str, dict[str, int]] = {}
    observed_namespaces: tuple[str, ...] | None = None

    for (task, arm, rep), raw_row, nested in zip(expected_grid, raw_rows, nested_rows, strict=True):
        row = exact_keys(raw_row, _RAW, "raw_slot")
        cell = exact_keys(nested, _NESTED, "nested_slot")
        refuse(
            row["task_id"] == task.task_id and row["family"] == task.family
            and row["kind"] == task.kind and row["arm"] == arm
            and type(row["repetition"]) is int and row["repetition"] == rep,
            "analysis:raw_slot_order",
        )
        refuse(
            cell["slot"] == {"task_id": task.task_id, "arm": arm, "repetition": rep, "generation": 1},
            "analysis:nested_slot_identity",
        )
        refuse(cell["task_digests"] == dict(task.task_digests), "analysis:task_digest_join")
        refuse(
            cell["delivery_kind"] == ("external_unqualified" if native else "functional_synthetic"),
            "analysis:delivery_kind_origin",
        )
        state = as_str(row["state"], "slot_state")
        refuse(state in SLOT_STATES and cell["execution_state"] == state, "analysis:slot_state_join")
        refuse(
            (row["reason"] is None or type(row["reason"]) is str) and row["reason"] == cell["reason"],
            "analysis:slot_reason_join",
        )
        counts[state] += 1
        if task.kind != "positive":
            control_counts.setdefault(task.kind, {s: 0 for s in SLOT_STATES})[state] += 1
        surf_ok, fb_ok, cap_ok = _check_expectations(cell, task, arm, state)
        grading = exact_keys(cell["grading"], _GRADING, "grading")
        refuse(
            grading["task_id"] == task.task_id and grading["family"] == task.family
            and grading["grading_rule"] == task.grading_rule and grading["execution_state"] == state,
            "analysis:grading_join",
        )
        refuse(grading["grading_state"] in GRADING_STATES, "analysis:grading_state")
        refuse(grading["comparability_stratum"] in STRATA, "analysis:stratum")
        cap_tok = as_token(grading["capability_state"], "capability_state")
        as_digest(cell["capture_reference_sha256"], "capture_reference")
        for opt in ("timing", "byte_accounting"):
            refuse(grading[opt] is None or isinstance(grading[opt], dict), f"analysis:{opt}_shape")
        metrics, slot_ns = _slot_metrics(grading["metrics"], "grading_metrics")
        raw_metrics, raw_ns = _slot_metrics(row["metrics"], "raw_metrics")
        refuse(raw_metrics == metrics and raw_ns == slot_ns, "analysis:metric_join")
        if observed_namespaces is None:
            observed_namespaces = slot_ns
        else:
            refuse(slot_ns == observed_namespaces, "analysis:inconsistent_slot_metric_namespaces")
        q_id, t_ids = validate_slot_record_ids(
            task=task, arm=arm, state=state,
            query_raw=row["query_record_id"], targets_raw=row["target_record_ids"], native=native,
        )
        slots.append(SlotRecord(
            task_id=task.task_id, family=task.family, kind=task.kind, arm=arm,
            repetition=rep, state=state, reason_category=reason_category(row["reason"]),
            grading_rule=task.grading_rule, grading_state=str(grading["grading_state"]),
            capability_category=capability_category(cap_tok), metrics=metrics,
            returned_hit_count=as_int(grading["returned_hit_count"], "hit_count", 0, 100000),
            safety_refusal_correct=optional_bool(grading["safety_refusal_correct"], "safety_correct"),
            absent_answer_correct=optional_bool(grading["absent_answer_correct"], "absent_correct"),
            surface_expectation_matches=surf_ok, fallback_expectation_matches=fb_ok,
            capability_expectation_matches=cap_ok, query_record_id=q_id, target_record_ids=t_ids,
        ))

    assert observed_namespaces is not None
    refuse(
        exact_keys(paired["state_counts"], frozenset(SLOT_STATES), "state_counts") == counts,
        "analysis:state_counts_join",
    )
    if "control_breakdown" in paired:
        refuse(paired["control_breakdown"] == control_counts, "analysis:control_breakdown_join")
    incomplete = counts["completed"] != slot_total
    tech_failed = incomplete and (counts["failed"] + counts["not_run"] > 0)
    if "incomplete_attempt_retained" in paired:
        refuse(paired["incomplete_attempt_retained"] is incomplete, "analysis:incomplete_flag_join")
    if "technical_incomplete_or_failed" in paired:
        refuse(paired["technical_incomplete_or_failed"] is tech_failed, "analysis:technical_flag")
    if native:
        refuse(
            counts["failed"] == 0 and counts["not_run"] == 0
            and paired.get("technical_incomplete_or_failed") is False,
            "analysis:technically_incomplete",
        )

    paired_rows = paired["paired_tasks"]
    refuse(
        type(paired_rows) is list and len(paired_rows) == len(descriptors),
        "analysis:paired_tasks",
    )
    comparable: list[str] = []
    families = {family: 0 for family in POSITIVE_FAMILIES}
    task_truncated: dict[str, bool] = {}
    task_completed_all: dict[str, bool] = {}
    by_task: dict[str, list[SlotRecord]] = {}
    for slot in slots:
        by_task.setdefault(slot.task_id, []).append(slot)

    for task, raw_paired in zip(descriptors, paired_rows, strict=True):
        entry = exact_keys(raw_paired, _PAIRED_TASK, "paired_task")
        rows = by_task[task.task_id]
        arm_states = {arm: [s.state for s in rows if s.arm == arm] for arm in ARMS}
        refuse(
            entry["task_id"] == task.task_id and entry["kind"] == task.kind
            and entry["family"] == task.family and entry["arm_states"] == arm_states,
            "analysis:paired_task_join",
        )
        all_completed = all(s.state == "completed" for s in rows)
        refuse(entry["completed_all_arms"] is all_completed, "analysis:completed_all_join")
        expected_checked = sorted({
            rec_id
            for s in rows
            if task.kind == "positive" and all_completed and s.arm in ARMS[3:]
            for rec_id in ([s.query_record_id, *s.target_record_ids] if s.query_record_id else [])
        })
        refuse(entry["checked_token_records"] == expected_checked, "analysis:checked_tokens_join")
        truncated = as_bool(entry["token_truncated"], "token_truncated")
        if not expected_checked:
            refuse(truncated is False, "analysis:unexpected_truncation_flag")
        elif token_map is not None:
            recomputed_trunc = False
            for rec_id in expected_checked:
                tok_ev = token_map.get(rec_id)
                if tok_ev is None:
                    fail("analysis:missing_counted_token_evidence")
                recomputed_trunc = recomputed_trunc or tok_ev.truncated
            refuse(truncated is recomputed_trunc, "analysis:token_truncation_join")
        is_comparable = (
            task.kind == "positive" and all_completed and not truncated and bool(expected_checked)
        )
        refuse(entry["comparable_untruncated_positive"] is is_comparable, "analysis:comparable_join")
        task_truncated[task.task_id] = truncated
        task_completed_all[task.task_id] = all_completed
        if is_comparable:
            comparable.append(task.task_id)
            families[task.family] += 1

    refuse(
        paired["comparable_positive_tasks"] == comparable
        and paired["comparable_positive_count"] == len(comparable)
        and exact_keys(paired["family_counts"], frozenset(POSITIVE_FAMILIES), "family_counts") == families,
        "analysis:comparable_counts_join",
    )
    floor = len(comparable) >= 8 and all(count >= 2 for count in families.values())
    refuse(
        paired["floor_met"] is floor and paired["criteria_proposal_allowed"] is floor,
        "analysis:floor_join",
    )
    if "prospective_criteria" in paired:
        expected_criteria = {
            "status": "eligible_for_operator_review" if floor else "blocked_below_floor",
            "comparable_positive_count": len(comparable),
            "family_counts": families,
            "operator_review_required": True,
        }
        refuse(paired["prospective_criteria"] == expected_criteria, "analysis:prospective_criteria_join")

    return VerifiedSlotGrid(
        slots=tuple(slots),
        declared_slot_keys=declared_slot_keys,
        observed_namespaces=observed_namespaces,
        state_counts=counts,
        task_truncated=task_truncated,
        task_completed_all_arms=task_completed_all,
        comparable_positive_tasks=tuple(comparable),
        family_counts=families,
        floor_met=floor,
    )


__all__ = ["VerifiedSlotGrid", "validate_slot_grid"]
