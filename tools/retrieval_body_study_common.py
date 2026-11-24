"""Frozen common-positive selection for the body study (pure data only).

A raw mapping cannot prove trusted tokenizer origin: tokenizer receipts are
accepted here only as validated data fields, and the result explicitly keeps
``trusted_controller_admission`` false.  No similarity, score or rank field
may influence inclusion; unknown fields carrying such data are refused.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from retrieval_body_study_values import (
    ARM_REPRESENTATIONS, BODY_FAMILIES, BODY_POSITIVE_COUNT, COMMON_SET_SCHEMA, COMPARISON_ARMS,
    FLOOR_INTENTS, FLOOR_PER_FAMILY, INERT_FLAGS, MAX_TOKENS, digest_value, require,
)
from retrieval_body_record_types import _freeze, _thaw

_RECEIPT_FIELDS = frozenset({
    "task_id", "arm", "admitted", "captured_text_reachable", "token_count",
    "truncated", "representation_identity",
})
_META_FIELDS = frozenset({"task_id", "family", "intent_id", "wording_stratum", "case_kind"})
_EXCLUDED_FIELDS = frozenset({"task_id", "family", "intent_id", "wording_stratum", "reasons"})
_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class CommonPositiveSet:
    """Validated prospective common set; not trusted controller admission."""

    task_ids: tuple[str, ...]
    planned: tuple[Mapping[str, Any], ...]
    excluded: tuple[Mapping[str, Any], ...]
    floor_passed: bool
    _issued: object

    def __init__(
        self,
        task_ids: tuple[str, ...],
        planned: tuple[Mapping[str, Any], ...],
        excluded: tuple[Mapping[str, Any], ...],
        floor_passed: bool,
        *,
        _token: object = None,
    ) -> None:
        require(_token is _TOKEN, "body_study:common_factory_required")
        object.__setattr__(self, "task_ids", tuple(task_ids))
        object.__setattr__(self, "planned", tuple(_freeze(item) for item in planned))
        object.__setattr__(self, "excluded", tuple(_freeze(item) for item in excluded))
        object.__setattr__(self, "floor_passed", floor_passed)
        object.__setattr__(self, "_issued", _TOKEN)

    @staticmethod
    def is_factory_issued(value: object) -> bool:
        return type(value) is CommonPositiveSet and getattr(value, "_issued", None) is _TOKEN

    def to_dict(self) -> dict[str, Any]:
        families = Counter(item["family"] for item in self.planned
                             if item["task_id"] in self.task_ids)
        return {
            "schema": COMMON_SET_SCHEMA, "task_ids": list(self.task_ids),
            "distinct_intents": len({item["intent_id"] for item in self.planned
                                     if item["task_id"] in self.task_ids}),
            "family_counts": dict(families),
            "planned_positives": [dict(item) for item in self.planned],
            "excluded": [_thaw(item) for item in self.excluded],
            "planned_positive_count": len(self.planned),
            "floor": {"minimum_distinct_intents": FLOOR_INTENTS,
                      "minimum_per_family": FLOOR_PER_FAMILY,
                      "families": list(BODY_FAMILIES), "passed": self.floor_passed},
            "frozen_before_ranking": False, "rank_based_inclusion": False,
            "freeze_requirement": "before_similarity_scoring_or_rank",
            "tokenizer_origin_proven": False, "trusted_controller_admission": False,
            "similarity_computed": False, **INERT_FLAGS,
        }

    @property
    def identity(self) -> str:
        return digest_value(self.to_dict())


def select_common_positives(
    task_meta: Sequence[Mapping[str, Any]],
    eligibility: Sequence[Mapping[str, Any]],
) -> CommonPositiveSet:
    """Intersect admitted/untruncated eligibility across the four comparators.

    ``task_meta`` must list exactly the 12 planned body positives.
    ``eligibility`` supplies one closed receipt per (task, comparison arm).
    """
    planned: list[dict[str, Any]] = []
    seen_tasks: set[str] = set()
    seen_intents: set[str] = set()
    for item in task_meta:
        require(isinstance(item, Mapping) and set(item) == _META_FIELDS,
                "body_study:meta_fields")
        require(item["case_kind"] == "body_positive", "body_study:meta_kind")
        require(item["family"] in BODY_FAMILIES, "body_study:meta_family")
        require(item["wording_stratum"] in {"literal_clue", "paraphrased_intent"},
                "body_study:meta_wording")
        task_id = item["task_id"]
        require(type(task_id) is str and bool(task_id) and task_id not in seen_tasks,
                "body_study:meta_task")
        intent = item["intent_id"]
        require(type(intent) is str and bool(intent) and intent not in seen_intents,
                "body_study:meta_intent")
        seen_intents.add(intent)
        seen_tasks.add(task_id)
        planned.append(dict(item))
    require(len(planned) == BODY_POSITIVE_COUNT, "body_study:planned_positive_count")

    receipts: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in eligibility:
        require(isinstance(row, Mapping) and set(row) == _RECEIPT_FIELDS,
                "body_study:receipt_fields")
        require(type(row["task_id"]) is str and type(row["arm"]) is str,
                "body_study:receipt_identity_type")
        key = (row["task_id"], row["arm"])
        require(key[0] in seen_tasks and key[1] in COMPARISON_ARMS,
                "body_study:receipt_membership")
        require(key not in receipts, "body_study:receipt_duplicate")
        require(row["representation_identity"] == ARM_REPRESENTATIONS[str(key[1])],
                "body_study:receipt_representation")
        require(type(row["admitted"]) is bool
                and type(row["captured_text_reachable"]) is bool
                and type(row["truncated"]) is bool, "body_study:receipt_flags")
        tokens = row["token_count"]
        require(tokens is None or (type(tokens) is int and 0 <= tokens <= MAX_TOKENS),
                "body_study:receipt_tokens")
        require(not (row["admitted"] and row["captured_text_reachable"] and tokens is None),
                "body_study:tokenizer_receipt_missing")
        receipts[key] = row
    require(len(receipts) == BODY_POSITIVE_COUNT * len(COMPARISON_ARMS),
            "body_study:receipt_completeness")

    included: list[str] = []
    excluded: list[dict[str, Any]] = []
    for item in planned:
        task_id = item["task_id"]
        reasons: list[str] = []
        for arm in COMPARISON_ARMS:
            row = receipts[(task_id, arm)]
            if not row["admitted"]:
                reasons.append(f"{arm}:not_admitted")
            if not row["captured_text_reachable"]:
                reasons.append(f"{arm}:captured_text_unreachable")
            if row["truncated"]:
                reasons.append(f"{arm}:truncated")
        if reasons:
            excluded.append({"task_id": task_id, "family": item["family"],
                             "intent_id": item["intent_id"],
                             "wording_stratum": item["wording_stratum"],
                             "reasons": reasons})
        else:
            included.append(task_id)
    by_id = {item["task_id"]: item for item in planned}
    intents = {by_id[task_id]["intent_id"] for task_id in included}
    families = Counter(by_id[task_id]["family"] for task_id in included)
    floor_passed = (len(intents) >= FLOOR_INTENTS
                    and all(families.get(family, 0) >= FLOOR_PER_FAMILY
                            for family in BODY_FAMILIES))
    return CommonPositiveSet(tuple(sorted(included)), tuple(planned),
                             tuple(excluded), floor_passed, _token=_TOKEN)


def validate_common_positive_set(
    value: object, task_meta: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Bind a factory-issued planned/excluded matrix to the evaluator metadata."""
    require(CommonPositiveSet.is_factory_issued(value), "body_study:common_factory_required")
    assert isinstance(value, CommonPositiveSet)
    body = {task_id: row for task_id, row in task_meta.items()
            if row["case_kind"] == "body_positive"}
    planned: dict[str, Mapping[str, Any]] = {}
    for row in value.planned:
        require(isinstance(row, Mapping) and set(row) == _META_FIELDS,
                "body_study:common_planned_fields")
        task_id = row["task_id"]
        require(type(task_id) is str and task_id not in planned,
                "body_study:common_planned_identity")
        planned[task_id] = row
    require(set(planned) == set(body), "body_study:common_planned_task_set")
    for task_id, row in planned.items():
        require(all(row[field] == body[task_id][field] for field in _META_FIELDS),
                "body_study:common_planned_binding")
    common = tuple(value.task_ids)
    require(len(set(common)) == len(common), "body_study:common_duplicate")
    require(set(common).issubset(set(planned)), "body_study:common_membership")
    excluded: dict[str, Mapping[str, Any]] = {}
    for row in value.excluded:
        require(isinstance(row, Mapping) and set(row) == _EXCLUDED_FIELDS,
                "body_study:common_excluded_fields")
        task_id = row["task_id"]
        reasons = row["reasons"]
        require(type(task_id) is str and task_id not in excluded,
                "body_study:common_excluded_identity")
        require(isinstance(reasons, Sequence) and not isinstance(reasons, (str, bytes, bytearray))
                and bool(reasons) and all(type(reason) is str and bool(reason) for reason in reasons),
                "body_study:common_excluded_reasons")
        excluded[task_id] = row
    require(set(excluded) == set(planned) - set(common),
            "body_study:common_excluded_task_set")
    for task_id, row in excluded.items():
        require(all(row[field] == planned[task_id][field]
                    for field in ("task_id", "family", "intent_id", "wording_stratum")),
                "body_study:common_excluded_binding")
    family_counts = {family: 0 for family in BODY_FAMILIES}
    intents = {body[task_id]["intent_id"] for task_id in common}
    for task_id in common:
        family_counts[body[task_id]["family"]] += 1
    floor_passed = len(intents) >= FLOOR_INTENTS and all(
        family_counts[family] >= FLOOR_PER_FAMILY for family in BODY_FAMILIES
    )
    require(type(value.floor_passed) is bool and value.floor_passed == floor_passed,
            "body_study:common_floor_binding")
    return {
        "identity": value.identity,
        "task_ids": common,
        "planned": tuple(planned),
        "excluded": tuple(excluded),
        "distinct_intents": len(intents),
        "family_counts": family_counts,
        "floor_passed": floor_passed,
        "minimum_distinct_intents": FLOOR_INTENTS,
        "minimum_per_family": FLOOR_PER_FAMILY,
    }


__all__ = ["CommonPositiveSet", "select_common_positives", "validate_common_positive_set"]
