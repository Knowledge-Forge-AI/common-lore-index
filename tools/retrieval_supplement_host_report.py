"""Validate the SDK's retained study output without turning usefulness into PASS."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from retrieval_supplement_files import read_member
from retrieval_supplement_study_contracts import (
    INPUT_PIN_SHA256, MAX_REPORT_ARCHIVE_BYTES, SLOT_COUNT, TOTAL_RECORDS,
    WORKER_CLOSURE_SHA256, refuse,
)
from retrieval_supplement_study_inputs import (
    FrozenStudyInputs, QueryTargetJoin, require_study_manifest,
)
from retrieval_supplement_study_preflight import (
    POSITIVE_FAMILIES, SLOT_STATES, SlotOutcome, analyze_slots, parse_token_evidence,
)
from retrieval_supplement_primitives import ARMS, digest_bytes, strict_json

_RAW_FIELDS = frozenset(SlotOutcome(
    "", "", "", "", 1, "", None, (), None, None,
).to_dict())
_NESTED_FIELDS = frozenset({
    "slot", "task_digests", "delivery_kind", "execution_state", "reason",
    "expected_execution_state", "surface_observation", "expected_surface",
    "expected_fallback_policy", "surface_expectation_matches",
    "fallback_expectation_matches", "capability_expectation_matches", "grading",
    "capture_reference_sha256",
})


def _expected_query_id(join: QueryTargetJoin, arm: str) -> str:
    return join.v1_query_record_id if arm == ARMS[3] else join.v2_query_record_id


def _validate_frozen_joins(
    *,
    plan: Mapping[str, Any],
    frozen_inputs: FrozenStudyInputs,
    rows: list[dict[str, Any]],
    slots: list[dict[str, Any]],
) -> None:
    """Join plan, raw rows, nested slots and pinned task metadata before analysis."""
    refuse(isinstance(frozen_inputs, FrozenStudyInputs), "host_report:study_inputs")
    refuse(
        frozen_inputs.input_pin_sha256 == INPUT_PIN_SHA256
        and frozen_inputs.catalog_sha256 == frozen_inputs.catalog.digest,
        "host_report:study_inputs_identity",
    )
    expected = require_study_manifest(plan, frozen_inputs.catalog)
    expected_keys = [slot.key for slot in expected]
    task_ids = [task.task_id for task in frozen_inputs.catalog.tasks]
    refuse(len(expected_keys) == SLOT_COUNT and len(task_ids) == 24, "host_report:frozen_catalog_counts")
    raw_keys: list[tuple[str, str, int, int]] = []
    for row in rows:
        refuse(set(row) == _RAW_FIELDS, "host_report:raw_slot_shape")
        task_id, arm, repetition = row.get("task_id"), row.get("arm"), row.get("repetition")
        refuse(type(task_id) is str and type(arm) is str and type(repetition) is int,
               "host_report:raw_slot_types")
        assert isinstance(task_id, str) and isinstance(arm, str) and isinstance(repetition, int)
        metadata = frozen_inputs.task_metadata.get(task_id)
        refuse(metadata is not None, "host_report:raw_task_identity")
        assert metadata is not None
        refuse(row.get("family") == metadata.family and row.get("kind") == metadata.kind,
               "host_report:raw_task_metadata")
        raw_keys.append((task_id, arm, repetition, 1))
    refuse(raw_keys == expected_keys, "host_report:raw_slot_manifest")
    for row, nested, required in zip(rows, slots, expected, strict=True):
        refuse(set(nested) == _NESTED_FIELDS, "host_report:nested_slot_shape")
        refuse(nested.get("slot") == required.to_dict(), "host_report:nested_slot_identity")
        task_id = required.task_id
        metadata = frozen_inputs.task_metadata[task_id]
        task = next(task for task in frozen_inputs.catalog.tasks if task.task_id == task_id)
        refuse(
            nested.get("task_digests") == frozen_inputs.task_digests[task_id]
            and nested.get("execution_state") == row.get("state"),
            "host_report:nested_task_join",
        )
        grading = nested.get("grading")
        refuse(
            isinstance(grading, dict)
            and grading.get("task_id") == task_id
            and grading.get("family") == metadata.family
            and grading.get("grading_rule") == task.grading_rule,
            "host_report:nested_task_metadata",
        )
        if metadata.kind == "positive" and row.get("state") == "completed" and required.arm in ARMS[3:]:
            join = frozen_inputs.query_target_joins.get(task_id)
            refuse(isinstance(join, QueryTargetJoin), "host_report:query_target_join")
            assert isinstance(join, QueryTargetJoin)
            refuse(row.get("query_record_id") == _expected_query_id(join, required.arm),
                   "host_report:query_record_join")


def validate_exported_study(
    report_root: Path,
    *,
    plan: Mapping[str, Any],
    charge_bytes: Callable[[int], None],
    counted_worker_tokens: Sequence[Mapping[str, Any]],
    frozen_inputs: FrozenStudyInputs,
) -> dict[str, Any]:
    """Validate execution/identity completeness; a low scientific floor is data."""
    charge_bytes(MAX_REPORT_ARCHIVE_BYTES)
    raw = read_member(report_root, "supplement-study/supplement-study-report-v1.json",
                      max_bytes=MAX_REPORT_ARCHIVE_BYTES)
    value = strict_json(raw, maximum_bytes=MAX_REPORT_ARCHIVE_BYTES)
    refuse(isinstance(value, dict) and value.get("schema") == "cloe-supplement-study-bundle-v1",
           "host_report:schema")
    refuse(value.get("attempt_id") == plan["attempt_id"] and value.get("functional_only") is False
           and value.get("input_pin_sha256") == INPUT_PIN_SHA256
           and value.get("evaluator_closure_sha256") == plan["evaluator_closure_sha256"]
           and value.get("worker_closure_sha256") == WORKER_CLOSURE_SHA256,
           "host_report:identity_or_origin")
    refuse(value.get("scientific_acceptance_claimed") is False, "host_report:acceptance_claim")
    paired, supplement = value.get("paired_analysis"), value.get("supplement_report")
    refuse(isinstance(paired, dict) and isinstance(supplement, dict), "host_report:sections")
    assert isinstance(paired, dict) and isinstance(supplement, dict)
    refuse(supplement.get("slot_count") == SLOT_COUNT
           and supplement.get("input_pin_sha256") == INPUT_PIN_SHA256
           and paired.get("slot_count") == SLOT_COUNT
           and paired.get("task_count") == 24
           and paired.get("token_evidence_count") == TOTAL_RECORDS,
           "host_report:counts")
    slots, raw_rows = supplement.get("slots"), paired.get("raw_slots")
    refuse(isinstance(slots, list) and len(slots) == SLOT_COUNT
           and all(isinstance(row, dict) and row.get("delivery_kind") == "external_unqualified"
                   for row in slots), "host_report:actual_slots")
    refuse(isinstance(raw_rows, list) and len(raw_rows) == SLOT_COUNT, "host_report:raw_slots")
    assert isinstance(slots, list) and isinstance(raw_rows, list)
    rows = [row for row in raw_rows if isinstance(row, dict)]
    nested = [row for row in slots if isinstance(row, dict)]
    refuse(len(rows) == SLOT_COUNT and len(nested) == SLOT_COUNT, "host_report:slot_objects")
    _validate_frozen_joins(plan=plan, frozen_inputs=frozen_inputs, rows=rows, slots=nested)
    refuse(len(counted_worker_tokens) == 2
           and [item.get("record_count") for item in counted_worker_tokens] == [86, 60]
           and [item.get("representation") for item in counted_worker_tokens]
           == ["canonical-node-v1", "canonical-node-public-text-v2"], "host_report:counted_workers")
    tokens = parse_token_evidence(counted_worker_tokens, expected_total=TOTAL_RECORDS)
    recomputed = analyze_slots(
        [SlotOutcome(**row) for row in rows],
        evidence=tokens,
        ordered_task_ids=list(dict.fromkeys(row["task_id"] for row in rows)),
        cost_summary=paired.get("cost_summary"),
    )
    for key in ("paired_tasks", "state_counts", "control_breakdown", "comparable_positive_tasks",
                "comparable_positive_count", "family_counts", "floor_met", "criteria_proposal_allowed",
                "technical_incomplete_or_failed", "prospective_criteria"):
        refuse(paired.get(key) == recomputed[key], "host_report:counted_token_analysis_join")
    for name in ("scientific_acceptance_claimed", "heldout_acceptance_claimed",
                 "full_w5_acceptance_claimed", "production_acceptance_claimed"):
        refuse(paired.get(name) is False, "host_report:paired_acceptance_claim")
    for name in ("scientific_acceptance", "held_out_acceptance", "production_acceptance", "native_acceptance"):
        refuse(supplement.get(name) is False, "host_report:supplement_acceptance_claim")
    counts = paired.get("state_counts")
    refuse(isinstance(counts, dict) and set(counts) == set(SLOT_STATES)
           and all(type(count) is int and count >= 0 for count in counts.values())
           and sum(counts.values()) == SLOT_COUNT, "host_report:state_counts")
    assert isinstance(counts, dict)
    refuse(all(row.get("state") in SLOT_STATES for row in rows)
           and all(sum(row["state"] == state for row in rows) == count for state, count in counts.items()),
           "host_report:raw_state_join")
    positives, families = paired.get("comparable_positive_tasks"), paired.get("family_counts")
    refuse(isinstance(positives, list) and len(set(positives)) == len(positives)
           and paired.get("comparable_positive_count") == len(positives)
           and isinstance(families, dict) and set(families) == set(POSITIVE_FAMILIES)
           and all(type(count) is int and 0 <= count <= 4 for count in families.values())
           and sum(families.values()) == len(positives), "host_report:positive_family_counts")
    assert isinstance(positives, list) and isinstance(families, dict)
    expected_floor = len(positives) >= 8 and all(count >= 2 for count in families.values())
    refuse(type(paired.get("floor_met")) is bool
           and paired["floor_met"] == expected_floor
           and paired.get("criteria_proposal_allowed") is paired["floor_met"], "host_report:floor")
    return {"report_sha256": digest_bytes(raw), "slots": SLOT_COUNT, "token_records": TOTAL_RECORDS,
        "state_counts": dict(counts), "floor_met": paired["floor_met"],
        "comparable_positive_count": paired.get("comparable_positive_count"),
        "technical_incomplete_or_failed": recomputed["technical_incomplete_or_failed"],
        "scientific_acceptance_claimed": False}
