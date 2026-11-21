"""Closed grid validation and safe observation helpers for the body study."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import math
from typing import Any, cast

from retrieval_body_study_metrics import dedupe_ranked_files
from retrieval_body_study_values import (
    ARMS,
    BODY_FAMILIES,
    CASE_KINDS,
    REPETITIONS,
    SLOT_COUNT,
    SLOT_STATES,
    TASK_COUNT,
    require,
)

EXPECTED_CASE_COUNTS = {
    "body_positive": 12,
    "metadata_anchor": 4,
    "absent": 2,
    "ambiguous": 2,
    "privacy": 2,
    "unsupported": 2,
}
EXPECTED_ARM_OUTCOME_FIELDS = frozenset({"state", "refusal_code"})
SLOT_TRANSIENT_STATES = frozenset({"failed", "not_run", "unstarted", "missing", "timed_out"})
_SAFE_CODE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_:-.")
_SLOT_FIELDS = frozenset(
    {
        "task_id",
        "arm",
        "repetition",
        "execution_state",
        "ranked_files",
        "refusal_code",
        "absence_answer_correct",
        "absent_answer_correct",
        "safety_refusal_correct",
        "forbidden_work",
        "source_ownership_violation",
        "ownership_violation",
        "source_violation",
        "privacy_disclosed",
        "integrity_broken",
        "cleanup_unknown",
        "metrics",
        "quality_metrics",
        "rank",
        "score",
        "quality_score",
        "capability_state",
        "grading_state",
        "unavailable_reason",
        "returned_hit_count",
        "scope_complete",
    }
)
_BOOL_SLOT_FIELDS = frozenset(
    {
        "absence_answer_correct",
        "absent_answer_correct",
        "safety_refusal_correct",
        "forbidden_work",
        "source_ownership_violation",
        "ownership_violation",
        "source_violation",
        "privacy_disclosed",
        "integrity_broken",
        "cleanup_unknown",
        "scope_complete",
    }
)
_EXPOSURE_FIELDS = frozenset(
    {
        "metrics",
        "quality_metrics",
        "rank",
        "score",
        "quality_score",
        "absence_answer_correct",
        "absent_answer_correct",
        "safety_refusal_correct",
    }
)


def _safe_code(value: object, code: str) -> None:
    require(
        value is None
        or (
            type(value) is str
            and 1 <= len(value) <= 128
            and all(char in _SAFE_CODE_CHARS for char in value)
        ),
        code,
    )


def _validate_expected_arm_outcomes(
    value: object, *, task_id: str, case_kind: str
) -> dict[str, dict[str, Any]]:
    require(value is not None, "body_study:expected_arm_outcomes_required")
    require(isinstance(value, Mapping), "body_study:expected_arm_outcomes_mapping")
    assert isinstance(value, Mapping)
    require(set(value) == set(ARMS), "body_study:expected_arm_outcomes_arms")
    result: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        row = value[arm]
        require(
            isinstance(row, Mapping) and set(row) == EXPECTED_ARM_OUTCOME_FIELDS,
            "body_study:expected_arm_outcome_fields",
        )
        state = row.get("state")
        require(
            type(state) is str and state in {"completed", "refused", "unavailable"},
            "body_study:expected_arm_outcome_state",
        )
        if case_kind == "privacy":
            require(state in {"refused", "unavailable"}, "body_study:privacy_capability")
        if case_kind in {"body_positive", "absent", "ambiguous"} and arm in {ARMS[1], ARMS[3]}:
            require(state != "completed", "body_study:body_metadata_capability")
        if case_kind == "unsupported" and arm in {ARMS[3], ARMS[4], ARMS[5]}:
            require(state != "completed", "body_study:unsupported_semantic_capability")
        _safe_code(row.get("refusal_code"), "body_study:expected_refusal_code")
        if state != "refused":
            require(row.get("refusal_code") is None, "body_study:unexpected_expected_refusal_code")
        result[arm] = {"state": state, "refusal_code": row.get("refusal_code")}
    return result


def validate_task_meta(
    task_meta: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[str, ...], dict[str, dict[str, Any]], dict[str, int]]:
    """Validate the closed 24-case metadata set and per-arm expectations."""
    require(isinstance(task_meta, Mapping), "body_study:task_meta_mapping")
    require(len(task_meta) == TASK_COUNT, "body_study:task_meta_count")
    normalized: dict[str, dict[str, Any]] = {}
    counts = {kind: 0 for kind in CASE_KINDS}
    for task_id, raw in task_meta.items():
        require(type(task_id) is str and bool(task_id), "body_study:task_id_type")
        _safe_code(task_id, "body_study:task_id_safe")
        require(isinstance(raw, Mapping), "body_study:task_meta_row")
        kind = raw.get("case_kind")
        family = raw.get("family")
        intent_id = raw.get("intent_id")
        wording = raw.get("wording_stratum")
        require(type(kind) is str and kind in CASE_KINDS, "body_study:case_kind")
        require(type(family) is str and bool(family), "body_study:family_type")
        require(type(intent_id) is str and bool(intent_id), "body_study:intent_id_type")
        require(type(wording) is str and bool(wording), "body_study:wording_type")
        _safe_code(family, "body_study:family_safe")
        _safe_code(intent_id, "body_study:intent_id_safe")
        _safe_code(wording, "body_study:wording_safe")
        if kind == "body_positive":
            require(family in BODY_FAMILIES, "body_study:body_family")
            require(wording in {"literal_clue", "paraphrased_intent"}, "body_study:body_wording")
        else:
            require(wording == "not_applicable", "body_study:control_wording")
        expected = _validate_expected_arm_outcomes(
            raw.get("expected_arm_outcomes"), task_id=task_id, case_kind=cast(str, kind)
        )
        normalized[task_id] = {
            "task_id": task_id,
            "case_kind": kind,
            "family": family,
            "intent_id": intent_id,
            "wording_stratum": wording,
            "expected_arm_outcomes": expected,
        }
        counts[cast(str, kind)] += 1
    require(counts == EXPECTED_CASE_COUNTS, "body_study:case_role_matrix")
    require(len({row["intent_id"] for row in normalized.values()}) == TASK_COUNT,
            "body_study:duplicate_intent_id")
    return tuple(normalized), normalized, counts


def validate_common_task_ids(
    task_meta: Mapping[str, Mapping[str, Any]], common_task_ids: Sequence[str]
) -> dict[str, Any]:
    """Derive the common-positive floor from actual metadata, not a caller flag."""
    require(
        isinstance(common_task_ids, Sequence)
        and not isinstance(common_task_ids, (str, bytes, bytearray)),
        "body_study:common_sequence",
    )
    common = tuple(common_task_ids)
    require(all(type(task_id) is str and bool(task_id) for task_id in common),
            "body_study:common_task_id_type")
    require(len(set(common)) == len(common), "body_study:common_duplicate")
    require(all(task_id in task_meta for task_id in common), "body_study:common_membership")
    require(all(task_meta[task_id]["case_kind"] == "body_positive" for task_id in common),
            "body_study:common_body_only")
    family_counts = {family: 0 for family in BODY_FAMILIES}
    intents: set[str] = set()
    for task_id in common:
        row = task_meta[task_id]
        intents.add(row["intent_id"])
        family_counts[row["family"]] += 1
    floor_passed = len(intents) >= 8 and all(
        family_counts[family] >= 2 for family in BODY_FAMILIES
    )
    return {
        "task_ids": common,
        "distinct_intents": len(intents),
        "family_counts": family_counts,
        "floor_passed": floor_passed,
        "minimum_distinct_intents": 8,
        "minimum_per_family": 2,
    }


def _validate_optional_slot_fields(slot: Mapping[str, Any]) -> None:
    require(set(slot).issubset(_SLOT_FIELDS), "body_study:slot_fields")
    for name in _BOOL_SLOT_FIELDS:
        if name in slot:
            require(type(slot[name]) is bool, "body_study:slot_boolean_type")
    for name in ("capability_state", "grading_state", "unavailable_reason"):
        if name in slot:
            require(type(slot[name]) is str and bool(slot[name]), "body_study:slot_label_type")
    if "returned_hit_count" in slot:
        require(
            type(slot["returned_hit_count"]) is int and slot["returned_hit_count"] >= 0,
            "body_study:slot_hit_count",
        )
    for name in ("rank", "score", "quality_score"):
        if name in slot:
            require(
                type(slot[name]) in {int, float}
                and not isinstance(slot[name], bool)
                and math.isfinite(float(slot[name])),
                "body_study:slot_numeric_type",
            )
    for name in ("metrics", "quality_metrics"):
        if name in slot:
            metrics = slot[name]
            require(isinstance(metrics, Mapping), "body_study:slot_metrics_mapping")
            for metric_name, metric_value in metrics.items():
                require(type(metric_name) is str, "body_study:slot_metric_name")
                require(
                    type(metric_value) in {int, float}
                    and not isinstance(metric_value, bool)
                    and math.isfinite(float(metric_value)),
                    "body_study:slot_metric_value",
                )


def validate_slot_grid(
    slots: Sequence[Mapping[str, Any]], task_meta: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[tuple[str, str, int], Mapping[str, Any]], dict[str, int]]:
    """Require exactly one typed observation for every 24x6x3 slot."""
    require(
        isinstance(slots, Sequence) and not isinstance(slots, (str, bytes, bytearray)),
        "body_study:slots_sequence",
    )
    require(len(slots) == SLOT_COUNT, "body_study:slot_count")
    expected = {
        (task_id, arm, repetition)
        for task_id in task_meta
        for arm in ARMS
        for repetition in range(1, REPETITIONS + 1)
    }
    indexed: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    counts = {state: 0 for state in SLOT_STATES}
    for slot in slots:
        require(isinstance(slot, Mapping), "body_study:slot_mapping")
        _validate_optional_slot_fields(slot)
        task_id, arm, repetition = slot.get("task_id"), slot.get("arm"), slot.get("repetition")
        require(
            type(task_id) is str
            and task_id in task_meta
            and type(arm) is str
            and arm in ARMS
            and type(repetition) is int
            and not isinstance(repetition, bool)
            and 1 <= repetition <= REPETITIONS,
            "body_study:slot_identity",
        )
        state = slot.get("execution_state")
        require(type(state) is str and state in SLOT_STATES, "body_study:slot_state")
        key = (cast(str, task_id), cast(str, arm), cast(int, repetition))
        require(key not in indexed, "body_study:slot_duplicate")
        ranked = slot.get("ranked_files", ())
        require(
            isinstance(ranked, Sequence) and not isinstance(ranked, (str, bytes, bytearray)),
            "body_study:ranked_sequence",
        )
        dedupe_ranked_files(ranked)
        _safe_code(slot.get("refusal_code"), "body_study:refusal_code")
        if state != "refused":
            require(slot.get("refusal_code") is None, "body_study:unexpected_refusal_code")
        indexed[key] = slot
        counts[cast(str, state)] += 1
    require(set(indexed) == expected, "body_study:slot_set")
    return indexed, counts


def slot_has_integrity_violation(slot: Mapping[str, Any]) -> bool:
    """Recognize only explicit, typed source/ownership/cleanup violations."""
    return any(
        slot.get(name) is True
        for name in (
            "forbidden_work",
            "source_ownership_violation",
            "ownership_violation",
            "source_violation",
            "privacy_disclosed",
            "integrity_broken",
        )
    )


def slot_exposes_quality(
    slot: Mapping[str, Any], case_kind: str, metrics: Mapping[str, Any] | None = None
) -> bool:
    """Detect score/rank/quality/refusal exposure without exporting payloads."""
    if metrics is not None or slot.get("execution_state") == "completed" or slot.get("returned_hit_count", 0):
        return True
    if bool(slot.get("ranked_files")):
        return True
    if any(name in slot and slot[name] is not None for name in _EXPOSURE_FIELDS):
        return True
    return slot.get("execution_state") == "refused"


def slot_has_forbidden_execution(
    slot: Mapping[str, Any], *, case_kind: str, expected_state: str
) -> bool:
    """Identify work outside any declared unavailable/refused capability."""
    if expected_state not in {"refused", "unavailable"}:
        return False
    return (slot.get("execution_state") == "completed" or bool(slot.get("ranked_files"))
            or bool(slot.get("returned_hit_count", 0)))


def safe_state_counts(values: Sequence[str] | Mapping[str, Any]) -> dict[str, int]:
    """Return every closed state, including zero-count outcomes."""
    counter = Counter(values if not isinstance(values, Mapping) else values.keys())
    if isinstance(values, Mapping):
        counter = Counter({str(key): int(value) for key, value in values.items()})
    return {state: int(counter.get(state, 0)) for state in sorted(SLOT_STATES)}


__all__ = [
    "EXPECTED_CASE_COUNTS",
    "EXPECTED_ARM_OUTCOME_FIELDS",
    "SLOT_TRANSIENT_STATES",
    "safe_state_counts",
    "slot_exposes_quality",
    "slot_has_forbidden_execution",
    "slot_has_integrity_violation",
    "validate_common_task_ids",
    "validate_slot_grid",
    "validate_task_meta",
]
