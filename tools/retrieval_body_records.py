"""Inert body query joins over exact factory-issued preparation decisions."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from retrieval_body_admission import BodyTaskAdmission
from retrieval_body_estimate import (
    BodyRecordEstimate,
    PartitionEstimate,
    estimate_body_record_budget,
    estimate_body_records,
)
from retrieval_body_record_types import (
    BodyInputFrame,
    BodyPreparationDecision,
    BodyQueryLedger,
    BodyQueryRecord,
    BodyRecordError,
    FUSION_ARM,
    INPUT_POLICY,
    MAX_COMBINED_RECORDS,
    MAX_DOCUMENTS_PER_REPRESENTATION,
    MAX_QUERY_TASKS,
    MAX_QUERIES_PER_REPRESENTATION,
    PUBLIC_TEXT_V2_ARM,
    PUBLIC_TEXT_V2_REPRESENTATION,
    _digest,
    _digest_string,
    _publication,
    _reject_execution_claims,
    _require,
    _state,
    _task_digests,
)
from retrieval_evaluation_schema import EvaluationTask
from retrieval_semantic_generator_kernel_contracts import SemanticMode, SemanticText
from retrieval_semantic_generator_kernel_validation import validated_inputs
from retrieval_supplement_primitives import ARMS


def _decision_from_row(
    task: EvaluationTask,
    row: Mapping[str, Any],
    *,
    admission_identity: str,
    require_arm: bool,
) -> BodyPreparationDecision:
    _require(isinstance(row, Mapping), "decision_mapping")
    _reject_execution_claims(row)
    task_sha256, retained_task_sha256 = _task_digests(task)
    class_id = row.get("class_id")
    catalog_sha256 = row.get("catalog_sha256")
    case_sha256 = row.get("case_sha256")
    input_pin_sha256 = row.get("input_pin_sha256")
    task_id = row.get("task_id")
    _require(type(class_id) is str and bool(class_id), "decision_class")
    catalog_sha256 = _digest_string(catalog_sha256, "decision_catalog_digest")
    case_sha256 = _digest_string(case_sha256, "decision_case_digest")
    input_pin_sha256 = _digest_string(input_pin_sha256, "decision_input_pin")
    _require(type(task_id) is str and task_id == task.task_id, "decision_task_identity")
    _require(
        row.get("task_sha256") == task_sha256
        and row.get("retained_task_sha256") == retained_task_sha256,
        "decision_task_digest",
    )
    generation = row.get("generation", task.generation)
    arm = row.get("arm")
    _require(type(generation) is int and generation == task.generation, "decision_generation")
    _require(type(arm) is str and arm in ARMS, "decision_arm")
    if require_arm:
        _require(arm in {PUBLIC_TEXT_V2_ARM, FUSION_ARM}, "query_arm")
    state, reason, surface, fallback = _state(row)
    _require(type(admission_identity) is str and bool(admission_identity), "decision_admission_identity")
    publication = _publication(row.get("publication_identity"))
    _require(publication is not None or state == "refused", "decision_publication_identity")
    representation = row.get("representation_identity")
    if representation is None:
        representation = PUBLIC_TEXT_V2_REPRESENTATION
    _require(representation == PUBLIC_TEXT_V2_REPRESENTATION, "decision_representation_identity")
    assert isinstance(class_id, str) and isinstance(task_id, str) and isinstance(arm, str)
    return BodyPreparationDecision(
        class_id,
        catalog_sha256,
        case_sha256,
        input_pin_sha256,
        task_id,
        task_sha256,
        retained_task_sha256,
        generation,
        arm,
        state,
        reason,
        surface,
        fallback,
        admission_identity,
        publication,
        representation,
    )


def _query_record_from_task(
    task: EvaluationTask, decision: BodyPreparationDecision
) -> BodyQueryRecord:
    _require(
        decision.preparation == "ready" and decision.arm == PUBLIC_TEXT_V2_ARM,
        "query_not_ready",
    )
    _require(task.requested_mode == "ranked_discovery", "query_mode")
    body = task.comparability_stratum == "body" and task.intended_match_surface == "captured_text"
    anchor = task.comparability_stratum == "metadata" and task.intended_match_surface == "stored_metadata"
    _require(body or anchor, "query_surface")
    source = SemanticText("query:" + task.task_id, task.question, SemanticMode.QUERY)
    try:
        checked = validated_inputs((source,))[0]
    except (KeyError, TypeError, ValueError) as exc:
        raise BodyRecordError("query_semantic_input") from exc
    _require(len(checked) == 1 and checked[0].text == task.question, "query_question_rewritten")
    return BodyQueryRecord(
        checked[0],
        decision.class_id,
        decision.catalog_sha256,
        decision.case_sha256,
        decision.input_pin_sha256,
        task.task_id,
        decision.task_sha256,
        decision.retained_task_sha256,
        decision.generation,
        decision.admission_identity,
        decision.publication_identity,
        decision.representation_identity,
    )


def _frame(decisions: Sequence[BodyPreparationDecision]) -> BodyInputFrame:
    _require(bool(decisions), "empty_admissions")
    first = decisions[0]
    publications = [item.publication_identity for item in decisions if item.publication_identity is not None]
    publication = publications[0] if publications else None
    if publication is not None:
        identity = _digest(publication)
        _require(all(_digest(item) == identity for item in publications), "mixed_publication")
    frame_key = (
        first.class_id,
        first.catalog_sha256,
        first.input_pin_sha256,
        first.generation,
    )
    for item in decisions:
        _require(
            (item.class_id, item.catalog_sha256, item.input_pin_sha256, item.generation) == frame_key,
            "mixed_frame",
        )
    _require(first.representation_identity == PUBLIC_TEXT_V2_REPRESENTATION, "representation_identity")
    return BodyInputFrame(
        first.class_id,
        first.catalog_sha256,
        first.input_pin_sha256,
        first.generation,
        publication,
    )


def _finish_query_records(
    decisions: Sequence[BodyPreparationDecision],
    tasks: Mapping[str, EvaluationTask],
) -> BodyQueryLedger:
    _require(0 < len(decisions) <= MAX_QUERY_TASKS * 2, "admission_count")
    frame = _frame(decisions)
    exact = {item.task_id: item for item in decisions if item.arm == PUBLIC_TEXT_V2_ARM}
    _require(bool(exact), "missing_exact_vector_admission")
    records = [
        _query_record_from_task(tasks[item.task_id], item)
        for item in decisions
        if item.arm == PUBLIC_TEXT_V2_ARM and item.preparation == "ready"
    ]
    for item in decisions:
        if item.arm == FUSION_ARM and item.preparation == "ready":
            dependency = exact.get(item.task_id)
            _require(dependency is not None and dependency.preparation == "ready", "fusion_query_dependency")
            _require(any(record.task_id == item.task_id for record in records), "fusion_query_identity")
    records.sort(key=lambda record: record.task_id.encode("utf-8"))
    return BodyQueryLedger(frame, tuple(records), tuple(decisions))


def _build_query_records(entries: Sequence[tuple[Any, ...]]) -> BodyQueryLedger:
    """Pure core for validated functional task/decision leaves.

    Entries are ``(task, row)`` or ``(task, row, identity)`` tuples.  This
    helper never issues registered admission; the public facade requires it.
    """
    _require(
        isinstance(entries, Sequence) and not isinstance(entries, (str, bytes, bytearray)),
        "entries_sequence",
    )
    decisions: list[BodyPreparationDecision] = []
    seen_pairs: set[tuple[str, str]] = set()
    tasks: dict[str, EvaluationTask] = {}
    for entry in entries:
        _require(type(entry) is tuple and len(entry) in {2, 3}, "entry_shape")
        task, row = entry[:2]
        admission_identity = (
            entry[2]
            if len(entry) == 3
            else row.get("admission_identity", "functional-core")
            if isinstance(row, Mapping)
            else ""
        )
        _require(type(task) is EvaluationTask and isinstance(row, Mapping), "entry_types")
        decision = _decision_from_row(
            task,
            row,
            admission_identity=admission_identity,
            require_arm=True,
        )
        pair = (decision.task_id, decision.arm)
        _require(pair not in seen_pairs, "duplicate_admission")
        seen_pairs.add(pair)
        _require(
            decision.task_id not in tasks or tasks[decision.task_id].to_dict() == task.to_dict(),
            "task_drift",
        )
        tasks[decision.task_id] = task
        _require(len(tasks) <= MAX_QUERY_TASKS, "task_count")
        decisions.append(decision)
    return _finish_query_records(decisions, tasks)


def build_body_query_records(admissions: Sequence[BodyTaskAdmission]) -> BodyQueryLedger:
    """Build v2 queries from exact current admissions; fusion never makes one."""
    _require(
        isinstance(admissions, Sequence)
        and not isinstance(admissions, (str, bytes, bytearray)),
        "admissions_sequence",
    )
    _require(0 < len(admissions) <= MAX_QUERY_TASKS * 2, "admission_count")
    entries: list[tuple[EvaluationTask, Mapping[str, Any], str]] = []
    for admission in admissions:
        _require(type(admission) is BodyTaskAdmission, "admission_type")
        _require(BodyTaskAdmission.is_factory_issued(admission), "admission_issued")
        try:
            admission.require_current()
            task, row = admission.task, admission.to_dict()
        except Exception as exc:
            raise BodyRecordError("admission_current") from exc
        _require(row.get("arm") == admission.arm, "admission_arm_drift")
        entries.append((task, row, admission.identity))
    return _build_query_records(entries)


__all__ = [
    "BodyInputFrame",
    "BodyPreparationDecision",
    "BodyQueryLedger",
    "BodyQueryRecord",
    "BodyRecordError",
    "BodyRecordEstimate",
    "FUSION_ARM",
    "INPUT_POLICY",
    "MAX_COMBINED_RECORDS",
    "MAX_DOCUMENTS_PER_REPRESENTATION",
    "MAX_QUERY_TASKS",
    "MAX_QUERIES_PER_REPRESENTATION",
    "PartitionEstimate",
    "PUBLIC_TEXT_V2_ARM",
    "PUBLIC_TEXT_V2_REPRESENTATION",
    "build_body_query_records",
    "estimate_body_record_budget",
    "estimate_body_records",
]
