"""Strict supplement task parsing onto the maintained ``EvaluationTask`` leaf record.

Every primitive is type-checked exactly *before* ``EvaluationTask.from_mapping`` so the
leaf constructor never coerces (``bool`` is not ``int``, lists are not tuples, no
unknown fields). The parsed task must round-trip to byte-identical canonical JSON.
No predecessor ``TaskCatalog`` parser or task-value loader is used.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
import re
from typing import Any

from retrieval_evaluation_schema import EvaluationTask
from retrieval_supplement_values import (
    AUTHORED_TASK_CAP,
    GRADING_RULES,
    INTENDED_SURFACES,
    LEXICAL_STYLES,
    OTHER_FAMILY,
    POSITIVE_FAMILIES,
    PRIVACY_REASON,
    REQUESTED_MODES,
    STRATA,
    SupplementError,
    SupplementIdentity,
    canonical_bytes,
    check_keys,
    digest_json,
    fail,
    integer,
    require,
    safe_relative_path,
    string_list,
    text,
    thaw_json,
)

TASK_FIELDS: tuple[str, ...] = (
    "task_id", "family", "split", "question", "permitted_query_plan", "graph_id", "generation",
    "selected_bindings", "source_path_prefix", "requested_mode", "intended_match_surface",
    "comparability_stratum", "comparator_exposure", "gold_targets", "expected_capability_state",
    "expected_refusal_code", "grading_rule",
)
PLAN_REQUIRED = frozenset(
    {"query", "patterns", "fixed_strings", "ignore_case", "limit", "lexical_query_style"}
)
PLAN_OPTIONAL = frozenset({"graph_query"})
LOCATOR_FIELDS: tuple[str, ...] = (
    "binding_id", "source_relative_path", "start_line", "end_line", "canonical_key",
    "evidence_key", "link_kind",
)
GOLD_FIELDS: tuple[str, ...] = ("locator", "relevance_grade", "rationale", "canonical_key")
NON_REFUSED_STATES = frozenset({"available", "unavailable"})
PLAN_LIMIT = 10
MAX_LIST = 16
MAX_QUESTION_BYTES = 2048
MAX_QUERY_BYTES = 1024


def _task_id(value: object, prefix: str) -> str:
    task_id = text(value, "task_id", 64)
    match = re.fullmatch(re.escape(prefix) + r"([0-9]{2})", task_id)
    require(match is not None, "task_id:namespace_mismatch")
    assert match is not None
    require(1 <= int(match.group(1)) <= AUTHORED_TASK_CAP, "task_id:number_out_of_range")
    return task_id


def _plan(value: object) -> dict[str, Any]:
    require(isinstance(value, dict), "permitted_query_plan:object_required")
    assert isinstance(value, dict)
    keys = set(value)
    require(not (keys - PLAN_REQUIRED - PLAN_OPTIONAL), "permitted_query_plan:unknown_fields")
    require(PLAN_REQUIRED <= keys, "permitted_query_plan:missing_fields")
    text(value["query"], "permitted_query_plan.query", MAX_QUERY_BYTES)
    if "graph_query" in value:
        text(value["graph_query"], "permitted_query_plan.graph_query", MAX_QUERY_BYTES)
    string_list(
        value["patterns"], "permitted_query_plan.patterns", minimum=1, maximum=MAX_LIST, item_max=256
    )
    require(value["fixed_strings"] is True, "permitted_query_plan.fixed_strings:must_be_true")
    require(value["ignore_case"] is True, "permitted_query_plan.ignore_case:must_be_true")
    integer(value["limit"], "permitted_query_plan.limit", PLAN_LIMIT, PLAN_LIMIT)
    require(
        type(value["lexical_query_style"]) is str and value["lexical_query_style"] in LEXICAL_STYLES,
        "permitted_query_plan.lexical_query_style:unsupported",
    )
    return value


def _locator(value: object, selected: Collection[str]) -> tuple[str, str]:
    locator = check_keys(value, LOCATOR_FIELDS, "gold.locator")
    binding_id = text(locator["binding_id"], "gold.locator.binding_id", 256)
    require(binding_id in selected, "gold.locator.binding_id:not_selected")
    path = safe_relative_path(locator["source_relative_path"])
    for name in LOCATOR_FIELDS[2:]:
        require(locator[name] is None, f"gold.locator.{name}:must_be_null")
    return binding_id, path


def _golds(value: object, selected: Collection[str]) -> list[tuple[tuple[str, str], int]]:
    require(type(value) is list, "gold_targets:list_required")
    assert isinstance(value, list)
    require(len(value) <= MAX_LIST, "gold_targets:too_many")
    seen: list[tuple[tuple[str, str], int]] = []
    for item in value:
        gold = check_keys(item, GOLD_FIELDS, "gold")
        identity = _locator(gold["locator"], selected)
        grade = integer(gold["relevance_grade"], "gold.relevance_grade", 0, 3)
        text(gold["rationale"], "gold.rationale", 1024)
        require(gold["canonical_key"] is None, "gold.canonical_key:must_be_null")
        require(all(identity != prior for prior, _ in seen), "gold_targets:duplicate_file")
        seen.append((identity, grade))
    return seen


def _exposure(value: object) -> None:
    require(isinstance(value, dict), "comparator_exposure:object_required")
    assert isinstance(value, dict)
    require(len(value) <= MAX_LIST, "comparator_exposure:too_many")
    for key, item in value.items():
        text(key, "comparator_exposure.key", 128)
        text(item, "comparator_exposure.value", 256)


def _rules(task: Mapping[str, Any], golds: list[tuple[tuple[str, str], int]]) -> None:
    family, rule = task["family"], task["grading_rule"]
    state, refusal = task["expected_capability_state"], task["expected_refusal_code"]
    if family in POSITIVE_FAMILIES:
        require(rule == "ranked_positive", "family:positive_rule_required")
        require(task["requested_mode"] == "ranked_discovery", "family:positive_mode_required")
        require(task["comparability_stratum"] == "metadata", "family:positive_stratum_required")
        require(
            task["intended_match_surface"] == "stored_metadata", "family:positive_surface_required"
        )
    else:
        require(family == OTHER_FAMILY, "family:unknown")
    if rule == "safety_refusal":
        require(state == "refused" and refusal == PRIVACY_REASON, "grading_rule:refusal_mismatch")
    else:
        require(state in NON_REFUSED_STATES, "expected_capability_state:unsupported")
        require(refusal is None, "expected_refusal_code:must_be_null")
    if rule == "ranked_positive":
        require(any(grade > 0 for _, grade in golds), "gold_targets:positive_grade_required")
    else:
        require(not golds, "gold_targets:must_be_empty")


def parse_task(
    mapping: object, identity: SupplementIdentity, binding_ids: Collection[str]
) -> EvaluationTask:
    """Strictly parse one task mapping into a validated ``EvaluationTask``."""
    require(isinstance(identity, SupplementIdentity), "identity:required")
    allowed = tuple(binding_ids)
    require(allowed and all(type(b) is str and b.strip() for b in allowed), "binding_ids:invalid")
    task = check_keys(mapping, TASK_FIELDS, "task")
    _task_id(task["task_id"], identity.task_prefix)
    require(
        type(task["family"]) is str and (task["family"] in POSITIVE_FAMILIES or task["family"] == OTHER_FAMILY), "family:unknown"
    )
    require(task["split"] == "calibration", "split:calibration_required")
    text(task["question"], "question", MAX_QUESTION_BYTES)
    _plan(task["permitted_query_plan"])
    require(task["graph_id"] == identity.graph_id, "graph_id:identity_mismatch")
    integer(task["generation"], "generation", 1, 1)
    selected = string_list(
        task["selected_bindings"], "selected_bindings", minimum=1, maximum=MAX_LIST, item_max=256
    )
    require(all(b in allowed for b in selected), "selected_bindings:unknown_binding")
    require(task["source_path_prefix"] is None, "source_path_prefix:must_be_null")
    require(type(task["requested_mode"]) is str and task["requested_mode"] in REQUESTED_MODES, "requested_mode:unsupported")
    require(type(task["intended_match_surface"]) is str and task["intended_match_surface"] in INTENDED_SURFACES, "intended_match_surface:unsupported")
    require(type(task["comparability_stratum"]) is str and task["comparability_stratum"] in STRATA, "comparability_stratum:unsupported")
    _exposure(task["comparator_exposure"])
    golds = _golds(task["gold_targets"], selected)
    require(type(task["grading_rule"]) is str and task["grading_rule"] in GRADING_RULES, "grading_rule:unsupported")
    refusal = task["expected_refusal_code"]
    require(refusal is None or type(refusal) is str, "expected_refusal_code:invalid")
    require(type(task["expected_capability_state"]) is str, "expected_capability_state:invalid")
    _rules(task, golds)
    try:
        parsed = EvaluationTask.from_mapping(task)
    except SupplementError:
        raise
    except (KeyError, TypeError, ValueError):
        fail("task:leaf_rejected")
    require(
        canonical_bytes(parsed.to_dict()) == canonical_bytes(task), "task:round_trip_mismatch"
    )
    return parsed


def task_digests(task: EvaluationTask) -> dict[str, str]:
    """Both explicit digest schemes; they legitimately differ for non-ASCII text."""
    payload = task.to_dict()
    return {
        "retained_task_sha256": digest_json(payload, ascii_only=True),
        "task_sha256": digest_json(payload, ascii_only=False),
    }


def task_from_canonical(raw: bytes) -> EvaluationTask:
    """Rebuild a fresh task from canonical bytes stored after a strict parse."""
    return EvaluationTask.from_mapping(thaw_json(raw))


__all__ = ["GOLD_FIELDS", "LOCATOR_FIELDS", "TASK_FIELDS", "parse_task", "task_digests"]
