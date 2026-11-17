"""Strict body-class tasks over maintained leaves, without metadata relabelling."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from retrieval_evaluation_schema import EvaluationTask
from retrieval_body_values import (
    ANCHOR_FAMILY, BODY_FAMILIES, CONTROL_FAMILY, MODES, BodyIdentity,
    canonical_bytes, check_keys, integer, require, safe_relative_path, string_list, text,
)
from retrieval_supplement_tasks import GOLD_FIELDS, LOCATOR_FIELDS, TASK_FIELDS

PLAN_FIELDS = ("query", "patterns", "fixed_strings", "ignore_case", "limit", "lexical_query_style")


def _plan(value: object) -> None:
    plan = check_keys(value, PLAN_FIELDS, "body.plan")
    text(plan["query"], "body.plan.query", 1024)
    string_list(plan["patterns"], "body.plan.patterns", minimum=1, maximum=16, item_max=256)
    require(plan["fixed_strings"] is True and plan["ignore_case"] is True, "body.plan:literal_flags")
    integer(plan["limit"], "body.plan.limit", 10, 10)
    require(plan["lexical_query_style"] in ("ranked_terms", "literal_captured_text"), "body.plan:style")


def _golds(value: object, selected: list[str]) -> list[tuple[str, str, int]]:
    require(type(value) is list and len(value) <= 16, "body.gold:list")
    assert isinstance(value, list)
    result: list[tuple[str, str, int]] = []
    for item in value:
        gold = check_keys(item, GOLD_FIELDS, "body.gold")
        locator = check_keys(gold["locator"], LOCATOR_FIELDS, "body.gold.locator")
        binding = text(locator["binding_id"], "body.gold.binding", 256)
        require(binding in selected, "body.gold:unselected_binding")
        path = safe_relative_path(locator["source_relative_path"])
        require(all(locator[k] is None for k in LOCATOR_FIELDS[2:]), "body.gold:file_grading_required")
        require(gold["canonical_key"] is None, "body.gold:file_grading_required")
        grade = integer(gold["relevance_grade"], "body.gold.grade", 1, 3)
        text(gold["rationale"], "body.gold.rationale", 1024)
        require(all((binding, path) != (b, p) for b, p, _ in result), "body.gold:duplicate_file")
        result.append((binding, path, grade))
    return result


def _role(kind: str, task: dict[str, Any], golds: list[tuple[str, str, int]]) -> None:
    family, mode, stratum = task["family"], task["requested_mode"], task["comparability_stratum"]
    rule = task["grading_rule"]
    if kind == "body_positive":
        require(family in BODY_FAMILIES and stratum == "body", "body.task:body_family")
        require(mode == "ranked_discovery" and rule == "ranked_positive", "body.task:body_query")
        require(bool(golds), "body.task:positive_gold")
    elif kind == "metadata_anchor":
        require(family == ANCHOR_FAMILY and stratum == "metadata", "body.task:anchor_family")
        require(mode == "ranked_discovery" and rule == "ranked_positive" and bool(golds), "body.task:anchor_query")
    else:
        require(family == CONTROL_FAMILY, "body.task:control_family")
        if kind == "absent":
            require(stratum == "body" and mode == "ranked_discovery" and rule == "absent_answer" and not golds, "body.task:absent")
        elif kind == "ambiguous":
            require(stratum == "body" and mode == "ranked_discovery" and rule == "ranked_positive", "body.task:ambiguous")
            require(len(golds) >= 2 and len({g[2] for g in golds}) == 1, "body.task:ambiguous_golds")
        elif kind == "privacy":
            require(stratum == "body" and mode == "ranked_discovery" and rule == "safety_refusal" and not golds, "body.task:privacy")
        else:
            require(kind == "unsupported" and rule == "ranked_positive" and bool(golds), "body.task:unsupported")
            require((stratum, mode) in {("body", "literal_captured_text"), ("metadata", "current_literal_enumeration")}, "body.task:unsupported_mode")
    refused = kind == "privacy"
    require(task["expected_capability_state"] == ("refused" if refused else "available"), "body.task:state")
    require(task["expected_refusal_code"] == ("binding_privacy_not_authorized" if refused else None), "body.task:refusal")


def parse_body_task(
    value: object, *, kind: str, identity: BodyIdentity, binding_ids: Collection[str],
) -> EvaluationTask:
    """Validate exact field types first; the leaf constructor cannot coerce inputs."""
    require(type(identity) is BodyIdentity, "body.task:identity")
    require(type(kind) is str, "body.task:kind")
    task = dict(check_keys(value, TASK_FIELDS, "body.task"))
    identity.check_task_id(task["task_id"])
    family = text(task["family"], "body.task.family", 128)
    require(family in (*BODY_FAMILIES, ANCHOR_FAMILY, CONTROL_FAMILY), "body.task:family")
    require(task["split"] == "calibration", "body.task:calibration_only")
    text(task["question"], "body.task.question", 2048)
    _plan(task["permitted_query_plan"])
    require(task["graph_id"] == identity.graph_id, "body.task:graph")
    integer(task["generation"], "body.task.generation", 1, 1)
    selected = string_list(task["selected_bindings"], "body.task.bindings", minimum=1, maximum=16, item_max=256)
    require(bool(binding_ids) and all(type(b) is str for b in binding_ids), "body.task:binding_ids")
    require(all(b in binding_ids for b in selected), "body.task:unknown_binding")
    require(task["source_path_prefix"] is None, "body.task:no_path_restriction")
    require(type(task["requested_mode"]) is str and task["requested_mode"] in MODES, "body.task:mode")
    require(task["comparability_stratum"] in ("body", "metadata"), "body.task:stratum")
    surface = "captured_text" if task["comparability_stratum"] == "body" else "stored_metadata"
    require(task["intended_match_surface"] == surface, "body.task:surface")
    exposure = task["comparator_exposure"]
    require(type(exposure) is dict and len(exposure) <= 16, "body.task:exposure")
    assert isinstance(exposure, dict)
    for key, item in exposure.items():
        text(key, "body.exposure.key", 128)
        text(item, "body.exposure.value", 256)
    golds = _golds(task["gold_targets"], selected)
    _role(kind, task, golds)
    result = EvaluationTask.from_mapping(task)
    require(canonical_bytes(result.to_dict()) == canonical_bytes(task), "body.task:roundtrip")
    return result
