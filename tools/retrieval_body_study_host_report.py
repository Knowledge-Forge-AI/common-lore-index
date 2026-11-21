"""Validate a genuine retained BODY actual envelope, including low floors."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from retrieval_body_study_contracts import (
    BODY_ACTUAL_REPORT_SCHEMA, BODY_INPUT_PIN_SHA256, BODY_OUTCOMES, BODY_PHASE,
    BODY_ROLE, BODY_REPORT_SCHEMA, BODY_REPORT_BYTES, BODY_REPORT_MEMBER,
    SLOT_COUNT, TOTAL_RECORDS, refuse, strict_json,
)
from retrieval_body_study_files import read_member
from retrieval_body_study_plan import plan_digest, validate_body_plan

_SLOT_STATES = frozenset({"completed", "refused", "unavailable", "failed", "not_run",
                           "unstarted", "missing", "timed_out"})


def _validate_slots(slots: object) -> dict[str, int]:
    refuse(isinstance(slots, list) and len(slots) == SLOT_COUNT, "body actual slots")
    slot_rows = cast(list[object], slots)
    keys: set[tuple[str, str, int, int]] = set()
    counts = {state: 0 for state in _SLOT_STATES}
    for row in slot_rows:
        refuse(isinstance(row, Mapping), "body actual slot object")
        row_map = cast(Mapping[str, object], row)
        task_value, arm_value, repetition_value, generation_value = (
            row_map.get("task_id"), row_map.get("arm"), row_map.get("repetition"), row_map.get("generation"))
        refuse(type(task_value) is str and type(arm_value) is str and type(repetition_value) is int
               and type(generation_value) is int, "body actual slot identity")
        task, arm = cast(str, task_value), cast(str, arm_value)
        repetition, generation = cast(int, repetition_value), cast(int, generation_value)
        key = (task, arm, repetition, generation)
        refuse(key not in keys, "body actual slot duplicate")
        keys.add(key)
        state = row_map.get("state", row_map.get("execution_state"))
        refuse(state in _SLOT_STATES, "body actual slot state")
        counts[cast(str, state)] += 1
    refuse(sum(counts.values()) == SLOT_COUNT, "body actual slot counts")
    return counts


def _validate_workers(workers: object) -> None:
    refuse(isinstance(workers, list) and len(workers) == 2, "body actual worker count")
    worker_rows = cast(list[object], workers)
    expected = [("canonical-node-v1", 70), ("canonical-node-public-text-v2", 64)]
    actual = []
    for worker in worker_rows:
        refuse(isinstance(worker, Mapping), "body actual worker record")
        worker_map = cast(Mapping[str, object], worker)
        actual.append((cast(str, worker_map.get("representation")), cast(int, worker_map.get("record_count"))))
    refuse(actual == expected and sum(count for _name, count in actual) == TOTAL_RECORDS,
           "body actual worker census")


def validate_actual_body_envelope(report: object, *, plan: Mapping[str, Any],
                                  cleanup: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Require actual joins without demanding a scientific positive result."""
    checked = validate_body_plan(plan)
    refuse(isinstance(report, Mapping), "body actual report object")
    value = dict(cast(Mapping[str, Any], report))
    refuse(value.get("schema") == BODY_ACTUAL_REPORT_SCHEMA
           and value.get("phase") == BODY_PHASE and value.get("role") == BODY_ROLE,
           "body actual report identity")
    plan_join = value.get("plan_sha256")
    refuse(value.get("attempt_id") == checked["attempt_id"]
           and plan_join == plan_digest(checked)
           and value.get("input_pin_sha256") == BODY_INPUT_PIN_SHA256
           and value.get("source_closure_sha256") == checked["source_closure_sha256"]
           and value.get("treatment_sha256") == checked["treatment_sha256"]
           and value.get("worker_closure_sha256") == checked["worker_closure_sha256"]
           and value.get("evaluator_closure_sha256") == checked["evaluator_closure_sha256"]
           and value.get("protocol_sha256") == checked["protocol_sha256"]
           and value.get("operator_decision_sha256") == checked["operator_decision_sha256"],
           "body actual source join")
    outcome = value.get("quality_outcome", value.get("outcome"))
    refuse(outcome in BODY_OUTCOMES, "body actual quality outcome")
    refuse(value.get("origin_proven") is True
           and value.get("execution_state") in {"completed", "retained", "partial", "failed"}
           and value.get("scientific_acceptance_claimed") is False,
           "body actual origin/acceptance")
    counts = _validate_slots(value.get("slots"))
    expected_slots = [(row["task_id"], row["arm"], row["repetition"], row["generation"])
                      for row in checked["declaration"]["slot_manifest"]]
    actual_slots = [(row["task_id"], row["arm"], row["repetition"], row["generation"])
                    for row in value["slots"]]
    refuse(actual_slots == expected_slots, "body actual slot declaration")
    refuse(value.get("slot_count") == SLOT_COUNT
           and value.get("slot_state_counts") == counts, "body actual slot summary")
    _validate_workers(value.get("workers"))
    owned = value.get("owned_cleanup")
    refuse(isinstance(owned, Mapping) and owned.get("cleanup") in {"proven", "unknown", "failed"}
           and owned.get("sdk_container") is not None
           and owned.get("pg_lifecycle") is not None, "body actual cleanup")
    owned_map = cast(Mapping[str, Any], owned)
    if cleanup is not None:
        refuse(dict(cleanup) == dict(owned_map), "body actual cleanup join")
    return value


def validate_body_report(report: object) -> dict[str, Any]:
    """Retain the source-1 inert report API while refusing actual aliases."""
    refuse(isinstance(report, Mapping), "body report object")
    value = dict(cast(Mapping[str, Any], report))
    if value.get("schema") == BODY_REPORT_SCHEMA and "attempt_id" not in value:
        required = {"schema", "phase", "outcome", "slot_count", "origin_proven",
                    "slot_state_counts", "evaluation_sha256"}
        refuse(set(value) == required and value["phase"] == BODY_PHASE
               and value["origin_proven"] is False and value["slot_count"] == SLOT_COUNT
               and value["outcome"] in BODY_OUTCOMES, "body inert report")
        counts = value["slot_state_counts"]
        refuse(isinstance(counts, Mapping) and all(type(item) is int and item >= 0
               for item in counts.values()) and sum(counts.values()) == SLOT_COUNT,
               "body inert report counts")
        return value
    raise ValueError("body actual envelope requires plan-bound validation")


def read_actual_body_report(report_root: Path, *, plan: Mapping[str, Any],
                            charge_bytes: Callable[[int], None],
                            cleanup: Mapping[str, Any] | None = None) -> dict[str, Any]:
    charge_bytes(BODY_REPORT_BYTES + 1)
    raw = read_member(report_root, BODY_REPORT_MEMBER, max_bytes=BODY_REPORT_BYTES)
    value = strict_json(raw, maximum_bytes=BODY_REPORT_BYTES)
    if cleanup is not None:
        refuse(isinstance(value, Mapping), "body actual report object")
        inner_cleanup = value.get("owned_cleanup")
        value = {**value, "inner_cleanup": inner_cleanup,
                 "cleanup": dict(cleanup), "owned_cleanup": dict(cleanup)}
    return validate_actual_body_envelope(value, plan=plan, cleanup=cleanup)


def report_journal_matches(report: Mapping[str, Any], journal: Mapping[str, Any]) -> bool:
    """Join the separately validated report and terminal without rewriting either."""
    common = report.get("common_freeze")
    evaluation = report.get("evaluation")
    outcome = journal["terminal"].get("outcome")
    return (
        isinstance(common, Mapping) and type(common.get("common_identity")) is str
        and common["common_identity"] == journal["common_identity"]
        and report.get("report_identity") == journal["terminal_report_sha256"]
        and isinstance(evaluation, Mapping)
        and report.get("quality_outcome") == report.get("outcome") == evaluation.get("outcome") == outcome
    )


validate_exported_body_study = read_actual_body_report
validate_exported_study = read_actual_body_report

__all__ = [
    "read_actual_body_report", "validate_actual_body_envelope", "validate_body_report",
    "validate_exported_body_study", "validate_exported_study",
]
