"""Prospective arm capabilities for this class, separate from execution state."""

from __future__ import annotations

from typing import Any

from retrieval_evaluation_schema import EvaluationTask
from retrieval_body_values import ARMS, require


def _row(state: str, reason: str | None, surface: str, fallback: str = "none") -> dict[str, Any]:
    return {"preparation": state, "reason": reason, "actual_surface": surface, "fallback_policy": fallback}


def expected_capabilities(task: EvaluationTask, kind: str) -> dict[str, dict[str, Any]]:
    """Derive the authored matrix from task semantics, never a query or rank."""
    require(type(task) is EvaluationTask, "body.capability:task")
    if kind == "privacy":
        return {arm: _row("refused", "binding_privacy_not_authorized", "none") for arm in ARMS}
    body = task.comparability_stratum == "body"
    values = [
        _row("ready", None, "captured_text", "record_actual"),
        _row("unavailable", "captured_text_surface_unsupported_by_metadata_graph", "none") if body else _row("ready", None, "stored_metadata"),
        _row("ready", None, "captured_text"),
        _row("unavailable", "metadata_representation_not_comparable", "none") if body else _row("ready", None, "stored_metadata"),
        _row("ready", None, "captured_text"),
        _row("ready", None, "captured_text"),
    ]
    mode = task.requested_mode
    style = task.permitted_query_plan["lexical_query_style"]
    if mode != "ranked_discovery":
        for index in (4, 5):
            values[index] = _row("unavailable", "unsupported_semantic_query_mode", "none")
        if not body:
            values[3] = _row("unavailable", "unsupported_semantic_query_mode", "none")
    fallback = mode == "current_literal_enumeration" or (
        mode == "literal_captured_text" and style == "ranked_terms"
    ) or (mode == "ranked_discovery" and style == "literal_captured_text")
    if fallback:
        values[2] = _row("ready", None, "captured_text", "required")
    return dict(zip(ARMS, values))
