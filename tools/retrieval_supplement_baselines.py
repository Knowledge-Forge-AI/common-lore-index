"""Prepare/check-only seams for the old native/graph baselines and the PostgreSQL FTS arm.

Old native/graph primitives address bindings by alias, so a defensive *alias view*
of a supplement task is built from the actual policy's unique aliases; the original
full-ID task stays unchanged and both digest pairs are recorded with the explicit
mapping. The FTS request is built from the maintained style resolver and
``LexicalQueryRequest`` using the actual supplied policy and full selected IDs; the
old ``execute_postgres_native_fts_task`` (which hardcodes the mixed-v1 policy) is not
used. Denied or unknown scopes are refused before any request is constructed. No
adapter query runs here; W5 admission remains mandatory before any eventual callback.
"""

from __future__ import annotations

from typing import Any

from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from common_lore_index.retrieval.query_contracts import QueryMode
from retrieval_evaluation_schema import EvaluationTask
from retrieval_supplement_applicability import task_denial
from retrieval_supplement_tasks import task_digests
from retrieval_supplement_values import (
    REQUESTED_MODES,
    canonical_bytes,
    fail,
    frozen_json,
    require,
    safe_id,
    thaw_json,
)


class BaselineTaskViews:
    """Original full-ID digests plus a fresh aliased task view for old primitives."""

    __slots__ = ("_alias_digests", "_alias_task", "_aliases", "_original_digests")
    _alias_digests: tuple[tuple[str, str], ...]
    _alias_task: bytes
    _aliases: tuple[tuple[str, str], ...]
    _original_digests: tuple[tuple[str, str], ...]

    def __init__(
        self,
        original_digests: dict[str, str],
        alias_task: EvaluationTask,
        aliases: dict[str, str],
    ) -> None:
        object.__setattr__(self, "_original_digests", tuple(sorted(original_digests.items())))
        object.__setattr__(self, "_alias_task", frozen_json(alias_task.to_dict()))
        object.__setattr__(self, "_alias_digests", tuple(sorted(task_digests(alias_task).items())))
        object.__setattr__(self, "_aliases", tuple(sorted(aliases.items())))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("BaselineTaskViews is immutable")

    @property
    def original_digests(self) -> dict[str, str]:
        """``retained_task_sha256``/``task_sha256`` of the unchanged full-ID task."""
        return dict(self._original_digests)

    @property
    def alias_task(self) -> EvaluationTask:
        """Fresh aliased ``EvaluationTask`` for the old native/graph primitives."""
        return EvaluationTask.from_mapping(thaw_json(self._alias_task))

    @property
    def alias_digests(self) -> dict[str, str]:
        return dict(self._alias_digests)

    @property
    def alias_to_binding(self) -> dict[str, str]:
        """Explicit alias -> full binding ID mapping used for the view."""
        return dict(self._aliases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_task_digests": self.original_digests,
            "alias_task_digests": self.alias_digests,
            "alias_to_binding": self.alias_to_binding,
            "alias_task": thaw_json(self._alias_task),
        }


def _checked_scope(task: EvaluationTask, policy: BodyProjectionPolicy) -> None:
    require(isinstance(task, EvaluationTask), "baseline:task_required")
    require(isinstance(policy, BodyProjectionPolicy), "baseline:policy_required")
    require(type(task.requested_mode) is str and task.requested_mode in REQUESTED_MODES, "baseline:unsupported_mode")
    require(len(set(task.selected_bindings)) == len(task.selected_bindings), "baseline:duplicate_binding")
    denial = task_denial(task, policy)
    if denial is not None:
        fail(f"baseline:{denial}")


def policy_alias_map(policy: BodyProjectionPolicy) -> dict[str, str]:
    """Unique, safe alias -> binding ID map from the actual policy bindings."""
    aliases: dict[str, str] = {}
    for binding_id, resolved in policy.bindings.items():
        alias = safe_id(resolved.alias, "baseline.alias")
        require(alias not in aliases, "baseline:ambiguous_alias")
        aliases[alias] = binding_id
    return aliases


def prepare_baseline_task(task: EvaluationTask, *, policy: BodyProjectionPolicy) -> BaselineTaskViews:
    """Build the aliased view after graph, binding and denial checks; ``task`` is untouched."""
    _checked_scope(task, policy)
    original_bytes = canonical_bytes(task.to_dict())
    original = task_digests(task)
    by_binding = {binding_id: alias for alias, binding_id in policy_alias_map(policy).items()}
    selected_aliases = [by_binding[binding_id] for binding_id in task.selected_bindings]
    view = task.to_dict()
    view["selected_bindings"] = selected_aliases
    try:
        alias_task = EvaluationTask.from_mapping(view)
    except (KeyError, TypeError, ValueError):
        fail("baseline:alias_view_rejected")
    require(canonical_bytes(task.to_dict()) == original_bytes, "baseline:original_task_mutated")
    used = dict(zip(selected_aliases, task.selected_bindings))
    return BaselineTaskViews(original, alias_task, used)


def prepare_fts_request(task: EvaluationTask, *, policy: BodyProjectionPolicy) -> LexicalQueryRequest:
    """``LexicalQueryRequest`` over the actual full selected binding IDs; nothing is queried."""
    _checked_scope(task, policy)
    # Imported lazily: the maintained resolver's module also hosts the old adapter.
    from retrieval_evaluation_fts import _resolve_lexical_style_and_fallback

    plan = task.permitted_query_plan
    query, limit, style = plan.get("query"), plan.get("limit"), plan.get("lexical_query_style")
    require(type(query) is str and query.strip(), "baseline:plan_query_required")
    require(type(limit) is int and limit > 0, "baseline:plan_limit_required")
    require(type(style) is str, "baseline:plan_style_required")
    # These checks establish exact runtime types; retain that fact for mypy.
    if not isinstance(query, str) or not isinstance(limit, int) or not isinstance(style, str):
        raise ValueError("baseline:plan_types")
    query_style, fallback = _resolve_lexical_style_and_fallback(
        QueryMode(task.requested_mode), style, None
    )
    return LexicalQueryRequest(
        query=query,
        query_style=query_style,
        selected_binding_ids=tuple(sorted(task.selected_bindings)),
        source_path_prefix=task.source_path_prefix,
        limit=limit,
        offset=0,
        fallback=fallback,
    )


__all__ = ["BaselineTaskViews", "policy_alias_map", "prepare_baseline_task", "prepare_fts_request"]
