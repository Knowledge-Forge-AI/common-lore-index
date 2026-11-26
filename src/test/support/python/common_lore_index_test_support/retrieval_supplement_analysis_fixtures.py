"""Distinct synthetic retained-bundle grid builders for supplement analysis proof.

Every question digest, gold join and metric value here is invented for tests.
Default fixtures emit the native-shaped 12-metric ``file_*`` namespace and copy
``phase_costs`` into ``paired_analysis.cost_summary`` as the study driver does;
``include_span_metrics=True`` opts into the 24-metric dual-namespace shape.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from common_lore_index_test_support.retrieval_supplement_analysis_cost_fixtures import (
    ATTEMPT as ATTEMPT,
    NATIVE_SYNTHETIC_ATTEMPT as NATIVE_SYNTHETIC_ATTEMPT,
    SYNTHETIC_ANALYZER as SYNTHETIC_ANALYZER,
    SYNTHETIC_DECLARATION as SYNTHETIC_DECLARATION,
    SYNTHETIC_EVALUATOR as SYNTHETIC_EVALUATOR,
    SYNTHETIC_FREEZE as SYNTHETIC_FREEZE,
    SYNTHETIC_PIN as SYNTHETIC_PIN,
    SYNTHETIC_WORKER as SYNTHETIC_WORKER,
    digest_seed as digest_seed,
    native_shaped_provenance as native_shaped_provenance,
    synthetic_cost_record as synthetic_cost_record,
    synthetic_counted_tokens as synthetic_counted_tokens,
    synthetic_provenance as synthetic_provenance,
)
from retrieval_supplement_analysis_values import (
    COST_SUMMARY_SCHEMA,
    FILE_METRICS,
    NATIVE_SCHEMA,
    PAIRED_ANALYSIS_SCHEMA,
    SPAN_METRICS,
    SUPPLEMENT_REPORT_SCHEMA,
    SYNTHETIC_SCHEMA,
    TaskDescriptor,
)
from retrieval_supplement_primitives import (
    ARMS,
    OTHER_FAMILY,
    POSITIVE_FAMILIES,
    REGISTERED_INPUT_PIN_SHA256,
)
from retrieval_supplement_study_contracts import WORKER_CLOSURE_SHA256

CONTROL_KINDS: tuple[str, ...] = ("absent", "ambiguous", "privacy", "unsupported")


def _control_rule(kind: str) -> str:
    if kind == "absent":
        return "absent_answer"
    return "safety_refusal" if kind == "privacy" else "ranked_positive"


def _default_expectations(kind: str, task_index: int) -> dict[str, dict[str, Any]]:
    expectations: dict[str, dict[str, Any]] = {}
    for arm_index, arm in enumerate(ARMS):
        if kind == "privacy":
            expectations[arm] = {"execution": "refused", "reason": "binding_privacy_not_authorized",
                                 "actual_surface": "none", "fallback_policy": "none"}
        elif kind == "unsupported":
            completed_arms = 3 if task_index % 2 == 1 else 2
            if arm_index < completed_arms:
                surface = "captured_text" if arm_index in (0, 2) else "stored_metadata"
                expectations[arm] = {"execution": "completed", "reason": None,
                                     "actual_surface": surface, "fallback_policy": "none"}
            else:
                expectations[arm] = {"execution": "unavailable",
                                     "reason": "unsupported_semantic_query_mode",
                                     "actual_surface": "none", "fallback_policy": "none"}
        else:
            surface = "captured_text" if arm in (ARMS[4], ARMS[5]) else "stored_metadata"
            expectations[arm] = {"execution": "completed", "reason": None,
                                 "actual_surface": surface, "fallback_policy": "none"}
    return expectations


def synthetic_tasks(*, full: bool = False, prefix: str = "syn-an-") -> tuple[TaskDescriptor, ...]:
    """Ordered synthetic descriptors: positives first, then all four control kinds."""
    tasks: list[TaskDescriptor] = []
    positives_per_family = 4 if full else 1
    index = 0
    for family in POSITIVE_FAMILIES:
        for _ in range(positives_per_family):
            index += 1
            task_id = f"{prefix}{index:02d}"
            tasks.append(TaskDescriptor(
                task_id=task_id, family=family, kind="positive", grading_rule="ranked_positive",
                task_digests={"task_sha256": digest_seed(f"{task_id}:utf8"),
                              "retained_task_sha256": digest_seed(f"{task_id}:ascii")},
                arm_expectations=_default_expectations("positive", index),
            ))
    controls: tuple[str, ...] = (
        ("absent", "absent", "ambiguous", "ambiguous",
         "privacy", "privacy", "unsupported", "unsupported")
        if full else CONTROL_KINDS
    )
    for control_ordinal, kind in enumerate(controls, start=1):
        index += 1
        task_id = f"{prefix}{index:02d}"
        tasks.append(TaskDescriptor(
            task_id=task_id, family=OTHER_FAMILY, kind=kind, grading_rule=_control_rule(kind),
            task_digests={"task_sha256": digest_seed(f"{task_id}:utf8"),
                          "retained_task_sha256": digest_seed(f"{task_id}:ascii")},
            arm_expectations=_default_expectations(kind, control_ordinal),
        ))
    return tuple(tasks)


def _slot_metrics(
    task_index: int, arm_index: int, repetition: int, state: str, *, include_span: bool
) -> dict[str, float]:
    names = FILE_METRICS + SPAN_METRICS if include_span else FILE_METRICS
    if state != "completed":
        return {name: 0.0 for name in names}
    base = 0.12 * ((task_index + arm_index) % 5) + 0.01 * repetition
    out: dict[str, float] = {}
    for k in (1, 3, 5, 10):
        file_val = round(min(1.0, base + 0.03 * k / 10), 6)
        out[f"file_recall@{k}"] = file_val
        out[f"file_mrr@{k}"] = round(min(1.0, file_val * 0.9), 6)
        out[f"file_ndcg@{k}"] = round(min(1.0, file_val * 0.95), 6)
        if include_span:
            span_val = round(max(0.0, file_val - 0.05), 6)
            out[f"recall@{k}"] = span_val
            out[f"mrr@{k}"] = round(span_val * 0.9, 6)
            out[f"ndcg@{k}"] = round(span_val * 0.95, 6)
    return out


def _record_ids(
    task: TaskDescriptor, task_index: int, arm: str, state: str, *, native: bool
) -> tuple[str | None, list[str]]:
    if arm in ARMS[:3] or state != "completed":
        return None, []
    if arm == ARMS[3]:
        q_id = f"v1.query:{task.task_id}" if native else f"syn-v1-q:{task.task_id}"
        doc_hash = hashlib.sha256(f"v1-doc-{task_index}".encode("utf-8")).hexdigest()
        doc = f"v1.document:{doc_hash}" if native else f"syn-v1-d:{task_index:02d}"
        return q_id, [doc] if task.kind in {"positive", "ambiguous"} else []
    q_id = f"ptx.q.{task.task_id}" if native else f"syn-ptx-q:{task.task_id}"
    doc = f"ptx.d.{task_index + 1:04d}" if native else f"syn-ptx-d:{task_index:02d}"
    return q_id, [doc] if task.kind in {"positive", "ambiguous"} else []


def synthetic_bundle(
    tasks: tuple[TaskDescriptor, ...] | None = None,
    *,
    native: bool = False,
    include_span_metrics: bool = False,
    refused: frozenset[tuple[str, str, int]] = frozenset(),
    unavailable: frozenset[tuple[str, str, int]] = frozenset(),
    truncated_tasks: frozenset[str] = frozenset(),
    cost_records: tuple[dict[str, Any], ...] | None = None,
    emit_slot_query_costs: bool = False,
) -> dict[str, Any]:
    """Build a complete synthetic grid bundle matching the native slot/cost contracts."""
    descriptors = (
        synthetic_tasks(full=native, prefix="sup1-" if native else "syn-an-")
        if tasks is None else tasks
    )
    pin = REGISTERED_INPUT_PIN_SHA256 if native else SYNTHETIC_PIN
    worker = WORKER_CLOSURE_SHA256 if native else SYNTHETIC_WORKER
    attempt = NATIVE_SYNTHETIC_ATTEMPT if native else ATTEMPT
    supplement_id = "sup1-calibration-v1" if native else "syn-an-supplement"
    delivery_kind = "external_unqualified" if native else "functional_synthetic"

    raw_slots: list[dict[str, Any]] = []
    nested_slots: list[dict[str, Any]] = []
    paired_tasks: list[dict[str, Any]] = []
    auto_costs: list[dict[str, Any]] = []
    state_counts = {"completed": 0, "failed": 0, "not_run": 0, "refused": 0, "unavailable": 0}
    control_breakdown: dict[str, dict[str, int]] = {}
    comparable: list[str] = []
    family_counts = {family: 0 for family in POSITIVE_FAMILIES}

    for task_index, task in enumerate(descriptors):
        arm_states: dict[str, list[str]] = {arm: [] for arm in ARMS}
        all_completed = True
        checked_records: set[str] = set()
        expectations = (
            task.arm_expectations if task.arm_expectations is not None
            else _default_expectations(task.kind, task_index + 1)
        )
        for arm_index, arm in enumerate(ARMS):
            exp = expectations[arm]
            for repetition in (1, 2, 3):
                key = (task.task_id, arm, repetition)
                reason: str | None
                if key in refused:
                    state, reason = "refused", "synthetic_refusal"
                elif key in unavailable:
                    state, reason = "unavailable", "unsupported_semantic_query_mode"
                else:
                    state, reason = str(exp["execution"]), exp["reason"]
                state_counts[state] += 1
                if task.kind != "positive":
                    control_breakdown.setdefault(
                        task.kind, {s: 0 for s in sorted(state_counts)}
                    )[state] += 1
                arm_states[arm].append(state)
                all_completed = all_completed and state == "completed"
                metrics = _slot_metrics(
                    task_index, arm_index, repetition, state, include_span=include_span_metrics
                )
                q_id, t_ids = _record_ids(task, task_index, arm, state, native=native)
                if task.kind == "positive" and arm in ARMS[3:] and q_id is not None:
                    checked_records.update([q_id, *t_ids])
                raw_slots.append({
                    "task_id": task.task_id, "family": task.family, "kind": task.kind,
                    "arm": arm, "repetition": repetition, "state": state, "reason": reason,
                    "query_record_id": q_id, "target_record_ids": t_ids, "metrics": dict(metrics),
                })
                obs_surface = str(exp["actual_surface"]) if state == "completed" else "none"
                obs_fallback = (
                    "required"
                    if state == "completed" and exp["fallback_policy"] == "required"
                    else "none"
                )
                surf_ok = obs_surface == exp["actual_surface"]
                fb_ok = (
                    exp["fallback_policy"] == "record_actual"
                    or obs_fallback == exp["fallback_policy"]
                )
                cap_ok = (state == exp["execution"]
                          and (exp["reason"] is None or reason == exp["reason"])
                          and surf_ok and fb_ok)
                g_state = (
                    "not_applicable" if task.grading_rule == "safety_refusal"
                    else ("ungraded" if state != "completed"
                          else ("correct" if task.grading_rule == "absent_answer" else "partial"))
                )
                nested_slots.append({
                    "slot": {"task_id": task.task_id, "arm": arm,
                             "repetition": repetition, "generation": 1},
                    "task_digests": dict(task.task_digests),
                    "delivery_kind": delivery_kind, "execution_state": state, "reason": reason,
                    "expected_execution_state": exp["execution"],
                    "surface_observation": {
                        "surface": obs_surface, "fallback": obs_fallback,
                        "requested_mode": "ranked_discovery" if arm in ARMS[:3] else None,
                        "effective_mode": "ranked_discovery" if arm in ARMS[:3] else None,
                        "fallback_disclosure": None, "basis": "synthetic_fixture",
                    },
                    "expected_surface": exp["actual_surface"],
                    "expected_fallback_policy": exp["fallback_policy"],
                    "surface_expectation_matches": surf_ok,
                    "fallback_expectation_matches": fb_ok,
                    "capability_expectation_matches": cap_ok,
                    "grading": {
                        "task_id": task.task_id, "family": task.family,
                        "comparability_stratum": "metadata", "grading_rule": task.grading_rule,
                        "execution_state": state, "grading_state": g_state,
                        "capability_state": "available" if state == "completed" else state,
                        "safety_refusal_correct": (
                            state == "refused" if task.grading_rule == "safety_refusal" else None
                        ),
                        "absent_answer_correct": (
                            True if task.grading_rule == "absent_answer" and state == "completed"
                            else None
                        ),
                        "metrics": dict(metrics),
                        "returned_hit_count": (
                            0 if task.grading_rule == "absent_answer" or state != "completed" else 1
                        ),
                        "timing": None, "byte_accounting": None,
                    },
                    "capture_reference_sha256": digest_seed(f"{task.task_id}:{arm}:{repetition}"),
                })
                if cost_records is None and (native or emit_slot_query_costs):
                    auto_costs.append(synthetic_cost_record(
                        arm=arm, task_id=task.task_id, repetition=repetition, phase="query",
                        elapsed_ns=1000 + 50 * arm_index + 10 * repetition,
                        operation_id=f"slot-{task.task_id}-{arm}-{repetition}",
                        supplement_id=supplement_id, input_pin_sha256=pin,
                        freeze_sha256=SYNTHETIC_FREEZE,
                        source_closure_sha256=SYNTHETIC_DECLARATION, attempt_id=attempt,
                    ))

        positive_ready = task.kind == "positive" and all_completed
        truncated = positive_ready and (task.task_id in truncated_tasks)
        is_comparable = positive_ready and not truncated
        if is_comparable:
            comparable.append(task.task_id)
            family_counts[task.family] += 1
        paired_tasks.append({
            "task_id": task.task_id, "kind": task.kind, "family": task.family,
            "arm_states": arm_states, "completed_all_arms": all_completed,
            "token_truncated": truncated, "comparable_untruncated_positive": is_comparable,
            "checked_token_records": sorted(checked_records) if positive_ready else [],
        })

    selected_costs = tuple(auto_costs) if cost_records is None else cost_records
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in selected_costs:
        owner = record["identity"].get("shared_owner")
        group_key = f"shared:{owner}" if owner else str(record["identity"]["arm"])
        groups.setdefault(group_key, []).append(copy.deepcopy(record))
    phase_costs: dict[str, Any] = {
        "schema": COST_SUMMARY_SCHEMA, "record_count": len(selected_costs),
        "groups": dict(sorted(groups.items())), "cross_layer_sum": None,
        "shared_cost_allocation": "retained_once_not_divided",
        "unknown_defaults": "remain_unknown", "native_or_scientific_acceptance": False,
    }
    floor = len(comparable) >= 8 and all(v >= 2 for v in family_counts.values())
    slot_count = len(raw_slots)
    incomplete = state_counts["completed"] != slot_count
    tech_failed = incomplete and (state_counts["failed"] + state_counts["not_run"] > 0)
    workers = [
        {"representation": rep, "request_attempt_id": f"{attempt}-v{idx}",
         "request_sha256": digest_seed(f"req-{idx}"), "admission_sha256": digest_seed(f"adm-{idx}"),
         "response_members": {"report.json": digest_seed(f"rep-{idx}")},
         "raw_paths": [f"workers/v{idx}/request.json"], "response_received": True}
        for idx, rep in enumerate(("canonical-node-v1", "canonical-node-public-text-v2"), start=1)
    ] if native else []
    return {
        "schema": NATIVE_SCHEMA if native else SYNTHETIC_SCHEMA,
        "attempt_id": attempt, "functional_only": not native, "input_pin_sha256": pin,
        "declaration_source_closure_sha256": SYNTHETIC_DECLARATION,
        "evaluator_closure_sha256": SYNTHETIC_EVALUATOR, "worker_closure_sha256": worker,
        "worker_identities": workers, "observed_slot_count": slot_count,
        "token_record_count": 146, "candidate_bytes": 1024,
        "byte_forecast": {"mode": "synthetic"},
        "inner_evidence": {"schema": "cloe-supplement-study-inner-evidence-v1"},
        "cleanup": {"relations_clean": True, "container_absent": True, "receipt": None},
        "supplement_report": {
            "schema": SUPPLEMENT_REPORT_SCHEMA, "supplement_id": supplement_id,
            "freeze_sha256": SYNTHETIC_FREEZE, "source_closure_sha256": SYNTHETIC_DECLARATION,
            "slot_count": slot_count, "input_pin_sha256": pin, "slots": nested_slots,
            "phase_costs": phase_costs,
            "cost_completeness": "reported_scopes_only" if selected_costs else "unobserved",
            "phase_presence_is_not_per_slot_cost_coverage": True,
            "repetitions_are_independent_samples": False, "predecessor_pooling": False,
            "native_acceptance": False, "scientific_acceptance": False,
            "held_out_acceptance": False, "production_acceptance": False,
        },
        "paired_analysis": {
            "schema": PAIRED_ANALYSIS_SCHEMA, "slot_count": slot_count,
            "task_count": len(descriptors), "arms": 6, "repetitions": 3,
            "token_evidence_count": 146, "expected_full_study_token_records": 146,
            "raw_slots": raw_slots, "paired_tasks": paired_tasks,
            "state_counts": state_counts, "control_breakdown": control_breakdown,
            "comparable_positive_tasks": comparable, "comparable_positive_count": len(comparable),
            "family_counts": family_counts, "positive_floor": 8, "family_floor": 2,
            "floor_met": floor, "criteria_proposal_allowed": floor,
            "prospective_criteria": {
                "status": "eligible_for_operator_review" if floor else "blocked_below_floor",
                "comparable_positive_count": len(comparable),
                "family_counts": dict(family_counts), "operator_review_required": True,
            },
            "cost_summary": copy.deepcopy(phase_costs),
            "scientific_acceptance_claimed": False, "heldout_acceptance_claimed": False,
            "full_w5_acceptance_claimed": False, "production_acceptance_claimed": False,
            "incomplete_attempt_retained": incomplete, "technical_incomplete_or_failed": tech_failed,
        },
        "scientific_acceptance_claimed": False,
        "held_out_acceptance_claimed": False, "production_acceptance_claimed": False,
    }


def synthetic_bytes(bundle: dict[str, Any] | None = None, **kwargs: Any) -> bytes:
    value = synthetic_bundle(**kwargs) if bundle is None else bundle
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


__all__ = [
    "ATTEMPT", "CONTROL_KINDS", "NATIVE_SYNTHETIC_ATTEMPT", "SYNTHETIC_ANALYZER",
    "SYNTHETIC_DECLARATION", "SYNTHETIC_EVALUATOR", "SYNTHETIC_FREEZE", "SYNTHETIC_PIN",
    "SYNTHETIC_WORKER", "digest_seed", "native_shaped_provenance", "synthetic_bundle",
    "synthetic_bytes", "synthetic_cost_record", "synthetic_counted_tokens",
    "synthetic_provenance", "synthetic_tasks",
]
