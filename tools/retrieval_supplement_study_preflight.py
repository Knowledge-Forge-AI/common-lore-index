"""Pre-query store/gold/token joins and 432-slot paired criteria analysis.

Before any query slot is graded, accepted-store node/span/publication identities
must join the frozen supplement and every positive task's required gold targets
must be reachable. Tokenizer counts and truncation flags come exclusively from
the counted model workers' verified responses — never from an uncounted
product-model preflight. A truncated required query or target record cannot
contribute to the eight-positive floor. All 432 raw slots (including refused,
unavailable, failed and not_run) are retained alongside paired task rows,
cost summary references, and prospective criteria eligibility; nothing here
claims scientific, held-out, full-W5 or production acceptance.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from retrieval_semantic_generator_kernel import SemanticGeneration
from retrieval_semantic_generator_output_receipt import VerifiedWorkerResponse
from retrieval_supplement_study_contracts import (
    ARM_COUNT,
    FAMILY_FLOOR,
    MODEL_MAX_TOKENS,
    POSITIVE_COUNT,
    POSITIVE_FLOOR,
    REPETITIONS,
    SLOT_COUNT,
    TASK_COUNT,
    TOTAL_RECORDS,
    refuse,
)
from retrieval_supplement_primitives import ARMS, POSITIVE_FAMILIES

SLOT_STATES = frozenset({"completed", "refused", "unavailable", "failed", "not_run"})
VECTOR_AND_FUSION_ARMS = frozenset(ARMS[3:])


@dataclass(frozen=True)
class TokenEvidence:
    record_id: str
    token_count: int
    truncated: bool
    worker_representation: str
    token_count_before_limit: int | None = None
    overflow_chunks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "token_count": self.token_count,
            "token_count_before_limit": (
                self.token_count if self.token_count_before_limit is None
                else self.token_count_before_limit
            ),
            "overflow_chunks": self.overflow_chunks,
            "truncated": self.truncated,
            "worker_representation": self.worker_representation,
        }


def extract_worker_token_evidence(
    representation: str, response: VerifiedWorkerResponse, *, id_prefix: str = "",
) -> dict[str, Any]:
    """Extract counted-worker token rows from a verified worker response."""
    refuse(type(representation) is str and bool(representation),
           "preflight:worker_representation_required")
    refuse(type(response) is VerifiedWorkerResponse and response.status == "completed",
           "preflight:verified_worker_response_required")
    generation = response.generation
    refuse(type(generation) is SemanticGeneration, "preflight:worker_generation_required")
    assert isinstance(generation, SemanticGeneration)
    rows: list[dict[str, Any]] = []
    for emb in generation.embeddings:
        rows.append({
            "record_id": f"{id_prefix}{emb.record_id}",
            "token_count": int(emb.token_count_used),
            "token_count_before_limit": int(emb.token_count_before_limit),
            "overflow_chunks": int(emb.overflow_chunks),
            "truncated": bool(emb.truncated),
        })
    return {
        "representation": representation,
        "attempt_id": response.attempt_id,
        "record_count": len(rows),
        "token_evidence": rows,
    }


def parse_token_evidence(
    worker_outputs: Sequence[Mapping[str, Any]], *, expected_total: int | None = None,
) -> dict[str, TokenEvidence]:
    """Index counted-worker token evidence; duplicates, missing or out-of-range rows refuse."""
    evidence: dict[str, TokenEvidence] = {}
    for output in worker_outputs:
        rows = output.get("token_evidence", ())
        refuse(type(rows) is list, "preflight:token_evidence_list_required")
        representation = str(output.get("representation", ""))
        refuse(bool(representation), "preflight:worker_representation_required")
        for row in rows:
            refuse(isinstance(row, Mapping), "preflight:token_row_object_required")
            record_id = str(row.get("record_id", ""))
            refuse(bool(record_id) and record_id not in evidence, "preflight:duplicate_token_row")
            count = row.get("token_count")
            refuse(type(count) is int and 0 <= int(count) <= MODEL_MAX_TOKENS,
                   "preflight:token_count_range")
            refuse(type(row.get("truncated")) is bool, "preflight:truncation_flag_required")
            before_raw = row.get("token_count_before_limit")
            overflow_raw = row.get("overflow_chunks", 0)
            refuse(before_raw is None or (type(before_raw) is int and int(before_raw) >= int(count)),
                   "preflight:token_before_limit")
            refuse(type(overflow_raw) is int and int(overflow_raw) >= 0,
                   "preflight:token_overflow_chunks")
            evidence[record_id] = TokenEvidence(
                record_id, int(count), bool(row["truncated"]), representation,
                None if before_raw is None else int(before_raw), int(overflow_raw),
            )
    if expected_total is not None:
        refuse(len(evidence) == expected_total, "preflight:token_record_total_mismatch")
    return evidence


def require_store_joins(
    *, ordered_task_ids: Sequence[str],
    gold_targets: Mapping[str, Sequence[str]],
    accepted_nodes: Mapping[str, Mapping[str, Any]],
    publication_identity: str,
    expected_publication_identity: str,
    positive_task_ids: Sequence[str] | None = None,
    expected_task_count: int = TASK_COUNT,
    expected_positive_count: int = POSITIVE_COUNT,
    expected_token_record_ids: Sequence[str] | None = None,
    token_evidence: Mapping[str, TokenEvidence] | None = None,
) -> None:
    """Every positive task must have non-empty gold targets joining accepted store rows."""
    refuse(len(ordered_task_ids) == expected_task_count
           and len(set(ordered_task_ids)) == expected_task_count,
           "preflight:frozen_task_order")
    refuse(publication_identity == expected_publication_identity
           and bool(publication_identity), "preflight:publication_mismatch")
    task_set = set(ordered_task_ids)
    if positive_task_ids is not None:
        refuse(len(positive_task_ids) == expected_positive_count
               and len(set(positive_task_ids)) == expected_positive_count
               and set(positive_task_ids) <= task_set,
               "preflight:positive_task_set")
        for task_id in positive_task_ids:
            targets = gold_targets.get(task_id, ())
            refuse(bool(targets), "preflight:positive_task_missing_gold")
    refuse(bool(gold_targets), "preflight:gold_targets_empty")
    for task_id, targets in gold_targets.items():
        refuse(task_id in task_set, "preflight:unknown_gold_task")
        refuse(bool(targets), "preflight:empty_gold_target_list")
        for target in targets:
            node = accepted_nodes.get(target)
            refuse(node is not None, "preflight:gold_target_unreachable")
            assert node is not None
            refuse(node.get("publication_identity") == publication_identity,
                   "preflight:node_publication_mismatch")
            refuse(type(node.get("span_start")) is int and type(node.get("span_end")) is int
                   and 0 <= int(node["span_start"]) <= int(node["span_end"]),
                   "preflight:node_span_invalid")
    if expected_token_record_ids is not None or token_evidence is not None:
        refuse(expected_token_record_ids is not None and token_evidence is not None,
               "preflight:token_join_incomplete")
        assert expected_token_record_ids is not None and token_evidence is not None
        expected_ids = list(expected_token_record_ids)
        refuse(len(set(expected_ids)) == len(expected_ids), "preflight:duplicate_expected_token_id")
        refuse(set(token_evidence) == set(expected_ids), "preflight:token_identity_join_mismatch")


@dataclass(frozen=True)
class SlotOutcome:
    task_id: str
    family: str
    kind: str  # "positive" | control kinds ("privacy", "absent", "ambiguous", etc.)
    arm: str
    repetition: int
    state: str
    query_record_id: str | None = None
    target_record_ids: tuple[str, ...] = ()
    reason: str | None = None
    metrics: Mapping[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "family": self.family,
            "kind": self.kind,
            "arm": self.arm,
            "repetition": self.repetition,
            "state": self.state,
            "reason": self.reason,
            "query_record_id": self.query_record_id,
            "target_record_ids": list(self.target_record_ids),
            "metrics": None if self.metrics is None else dict(self.metrics),
        }


def analyze_slots(
    outcomes: Sequence[SlotOutcome], *,
    evidence: Mapping[str, TokenEvidence],
    ordered_task_ids: Sequence[str] | None = None,
    cost_summary: Mapping[str, Any] | None = None,
    expected_task_count: int = TASK_COUNT,
    expected_slot_count: int = SLOT_COUNT,
) -> dict[str, Any]:
    """Validate all 432 slots, task consistency and token joins; retain raw & paired rows."""
    refuse(len(outcomes) == expected_slot_count, "analysis:all_432_slots_required")
    keys = [(item.task_id, item.arm, item.repetition) for item in outcomes]
    refuse(len(set(keys)) == expected_slot_count, "analysis:duplicate_slot")
    refuse(all(item.arm in ARMS for item in outcomes), "analysis:unknown_arm")
    refuse(all(1 <= item.repetition <= REPETITIONS for item in outcomes), "analysis:repetition")
    refuse(all(item.state in SLOT_STATES for item in outcomes), "analysis:slot_state")

    by_task: dict[str, list[SlotOutcome]] = {}
    task_order_observed: list[str] = []
    for item in outcomes:
        if item.task_id not in by_task:
            by_task[item.task_id] = []
            task_order_observed.append(item.task_id)
        by_task[item.task_id].append(item)
    refuse(len(by_task) == expected_task_count, "analysis:task_count_mismatch")
    if ordered_task_ids is not None:
        refuse(list(ordered_task_ids) == task_order_observed, "analysis:task_order_mismatch")

    counts: dict[str, int] = {state: 0 for state in sorted(SLOT_STATES)}
    for item in outcomes:
        counts[item.state] += 1

    comparable: list[str] = []
    family_counts: dict[str, int] = {family: 0 for family in POSITIVE_FAMILIES}
    control_breakdown: dict[str, dict[str, int]] = {}
    paired_tasks: list[dict[str, Any]] = []

    for task_id in task_order_observed:
        rows = by_task[task_id]
        refuse(len(rows) == ARM_COUNT * REPETITIONS, "analysis:task_slot_grid_incomplete")
        kinds = {row.kind for row in rows}
        families = {row.family for row in rows}
        refuse(len(kinds) == 1 and len(families) == 1, "analysis:task_metadata_inconsistent")
        kind = rows[0].kind
        family = rows[0].family
        if kind == "positive":
            refuse(family in POSITIVE_FAMILIES, "analysis:unknown_positive_family")
        else:
            bucket = control_breakdown.setdefault(kind, {s: 0 for s in sorted(SLOT_STATES)})
            for row in rows:
                bucket[row.state] += 1

        arm_states: dict[str, list[str]] = {arm: [] for arm in ARMS}
        for row in rows:
            arm_states[row.arm].append(row.state)
            if row.arm in VECTOR_AND_FUSION_ARMS and row.state == "completed":
                refuse(type(row.query_record_id) is str and bool(row.query_record_id),
                       "analysis:completed_vector_slot_missing_query_record_id")
                if kind == "positive":
                    refuse(bool(row.target_record_ids),
                           "analysis:completed_positive_slot_missing_target_record_ids")

        completed_every_slot = all(row.state == "completed" for row in rows)
        truncated = False
        checked_records: set[str] = set()
        if kind == "positive" and completed_every_slot:
            for row in rows:
                if row.arm not in VECTOR_AND_FUSION_ARMS:
                    continue
                for record_id in (row.query_record_id, *row.target_record_ids):
                    refuse(type(record_id) is str and bool(record_id),
                           "analysis:missing_positive_record_id")
                    assert isinstance(record_id, str)
                    token = evidence.get(record_id)
                    refuse(token is not None, "analysis:missing_counted_token_evidence")
                    assert token is not None
                    checked_records.add(record_id)
                    truncated = truncated or token.truncated
            if not truncated:
                comparable.append(task_id)
                family_counts[family] += 1

        paired_tasks.append({
            "task_id": task_id,
            "kind": kind,
            "family": family,
            "arm_states": arm_states,
            "completed_all_arms": completed_every_slot,
            "token_truncated": truncated,
            "comparable_untruncated_positive": task_id in comparable,
            "checked_token_records": sorted(checked_records),
        })

    floor_met = (
        len(comparable) >= POSITIVE_FLOOR
        and all(count >= FAMILY_FLOOR for count in family_counts.values())
    )
    incomplete = counts["completed"] != expected_slot_count and counts["not_run"] + counts["failed"] > 0
    return {
        "schema": "cloe-supplement-study-paired-analysis-v1",
        "slot_count": expected_slot_count,
        "task_count": expected_task_count,
        "arms": ARM_COUNT,
        "repetitions": REPETITIONS,
        "state_counts": counts,
        "control_breakdown": control_breakdown,
        "comparable_positive_tasks": list(comparable),
        "comparable_positive_count": len(comparable),
        "family_counts": family_counts,
        "positive_floor": POSITIVE_FLOOR,
        "family_floor": FAMILY_FLOOR,
        "floor_met": floor_met,
        "criteria_proposal_allowed": floor_met,
        "prospective_criteria": {
            "status": "eligible_for_operator_review" if floor_met else "blocked_below_floor",
            "comparable_positive_count": len(comparable),
            "family_counts": dict(family_counts),
            "operator_review_required": True,
        },
        "raw_slots": [item.to_dict() for item in outcomes],
        "paired_tasks": paired_tasks,
        "token_evidence_count": len(evidence),
        "expected_full_study_token_records": TOTAL_RECORDS,
        "cost_summary": (
            {"status": "unprovided_in_synthetic_call", "physical_os_pg_costs": "unknown"}
            if cost_summary is None else dict(cost_summary)
        ),
        "scientific_acceptance_claimed": False,
        "heldout_acceptance_claimed": False,
        "full_w5_acceptance_claimed": False,
        "production_acceptance_claimed": False,
        "incomplete_attempt_retained": counts["completed"] != expected_slot_count,
        "technical_incomplete_or_failed": incomplete,
    }


__all__ = [
    "SLOT_STATES", "SlotOutcome", "TokenEvidence", "VECTOR_AND_FUSION_ARMS",
    "analyze_slots", "extract_worker_token_evidence", "parse_token_evidence",
    "require_store_joins",
]
