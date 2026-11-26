"""One-node registry and pytest collection hook for the study actual owner (ADR 0206).

Ordinary populations visibly *deselect* this node (never run/skip/xfail).
It runs only inside the authenticated canonical study SDK when
``CLOE_PGVECTOR_OFFLINE_PLAN`` points to the projected ``0600`` inner study
plan (``/sandbox-scratch/pgvector/plan.json``) carrying a verified
``BodyStudyAdmission`` and the collected population is exactly this one node.
Arbitrary host environment strings never satisfy admission.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
import os
from pathlib import Path
from typing import Any

from retrieval_body_study_contracts import (
    ENV_BODY_ADMISSION,
    BODY_MARKER,
    BODY_NODE_ID,
    BODY_INNER_PLAN_SCHEMA,
    BODY_SELECTOR,
    BodyStudyRefusal,
)
from retrieval_body_study_plan import (
    BodyStudyAdmission,
    admission_from_inner_plan,
)
from runner_pgvector_budget import private_file
from runner_pgvector_prebuilt import (
    ENV_OFFLINE_PLAN,
    INNER_PLAN,
    read_regular,
    strict_object,
)

MARKER = BODY_MARKER
NODE_FILE = BODY_SELECTOR
NODE_ID = BODY_NODE_ID
OPTIONAL_NODE_IDS = (NODE_ID,)
ENV_ADMISSION = ENV_BODY_ADMISSION
POPULATION_ATTR = "_body_study_population"
DESELECTION_REASON = (
    "actual body study runs only inside the canonical study SDK with verified inner BodyStudyAdmission"
)
_MAX_INNER_PLAN = 2 * 1024**2


class BodyStudyProfileError(RuntimeError):
    """Study-owner population or admission mismatch."""


def _verified_inner_admission(
    environment: Mapping[str, str], *, expected_inner_path: Path,
) -> BodyStudyAdmission | None:
    if ENV_ADMISSION in environment:
        raise BodyStudyProfileError(
            "unauthenticated CLOE_BODY_STUDY_ADMISSION env cannot bypass inner SDK plan"
        )
    raw_target = environment.get(ENV_OFFLINE_PLAN)
    if raw_target is None:
        return None
    if raw_target != str(expected_inner_path):
        raise BodyStudyProfileError("offline plan path mismatch inside sandbox")
    try:
        private_file(expected_inner_path)
        plan = strict_object(read_regular(expected_inner_path, _MAX_INNER_PLAN))
    except (OSError, RuntimeError, ValueError) as exc:
        raise BodyStudyProfileError("offline inner plan unreadable or malformed") from exc
    if plan.get("schema") != BODY_INNER_PLAN_SCHEMA:
        if plan.get("role") == "body-study":
            raise BodyStudyProfileError("study role requires closed study plan schema")
        return None
    from test_sandbox import active_sandbox

    if not active_sandbox(environment):
        raise BodyStudyProfileError("study inner plan requires authenticated active sandbox")
    try:
        return admission_from_inner_plan(plan)
    except (BodyStudyRefusal, ValueError) as exc:
        raise BodyStudyProfileError(f"study inner admission refused: {exc}") from exc


def body_admission_active(
    environment: Mapping[str, str] | None = None, *, inner_plan_path: Path | None = None,
) -> bool:
    env = os.environ if environment is None else environment
    target = Path(INNER_PLAN) if inner_plan_path is None else inner_plan_path
    return _verified_inner_admission(env, expected_inner_path=target) is not None


def require_body_admission(
    environment: Mapping[str, str] | None = None, *, inner_plan_path: Path | None = None,
) -> BodyStudyAdmission:
    env = os.environ if environment is None else environment
    target = Path(INNER_PLAN) if inner_plan_path is None else inner_plan_path
    admission = _verified_inner_admission(env, expected_inner_path=target)
    if admission is None:
        raise BodyStudyProfileError(DESELECTION_REASON)
    return admission


def covers_body_owner(config: Any) -> bool:
    """Whether a path argument (not a node selection) contains the study owner file."""
    root = Path(getattr(config, "rootpath", Path.cwd()))
    base = Path(getattr(getattr(config, "invocation_params", None), "dir", root))
    node = (root / NODE_FILE).resolve()
    paths = [(base / arg).resolve() for arg in getattr(config, "args", ()) if "::" not in str(arg)]
    return any(path == node or path in node.parents for path in paths)


def reconcile_body_study(
    config: Any,
    items: list[Any],
    *,
    deferred_build_node_ids: Collection[str] = (),
) -> None:
    """Visibly deselect the actual-study owner unless authenticated alone inside the study SDK."""
    optional = set(OPTIONAL_NODE_IDS)
    marked = [item for item in items if item.get_closest_marker(MARKER) is not None]
    marked_ids = {item.nodeid for item in marked}
    collected = {item.nodeid for item in items} & optional
    if marked_ids - optional or collected - marked_ids:
        raise BodyStudyProfileError("study marker and one-node registry disagree")
    if optional & set(deferred_build_node_ids) or any(
        item.get_closest_marker("requires_build_profile") is not None for item in marked
    ):
        raise BodyStudyProfileError("the study owner must not enter deferred build debt")
    raw = [item.nodeid for item in items]
    if body_admission_active():
        if raw != [NODE_ID]:
            raise BodyStudyProfileError(
                "admitted study population must be exactly the one named node"
            )
        setattr(config, POPULATION_ATTR,
                {"excluded": [], "count": 0, "raw_count": len(raw), "reason": DESELECTION_REASON})
        return
    if covers_body_owner(config) and collected != optional:
        raise BodyStudyProfileError(
            "a population covering the study owner must collect exactly its one named node"
        )
    if marked:
        items[:] = [item for item in items if item.nodeid not in marked_ids]
        config.hook.pytest_deselected(items=marked)
    excluded = [{"nodeid": nodeid, "raw_index": index}
                for index, nodeid in enumerate(raw) if nodeid in marked_ids]
    setattr(config, POPULATION_ATTR,
            {"excluded": excluded, "count": len(excluded), "raw_count": len(raw),
             "reason": DESELECTION_REASON})


__all__ = [
    "DESELECTION_REASON", "ENV_ADMISSION", "MARKER", "NODE_FILE", "NODE_ID",
    "OPTIONAL_NODE_IDS", "POPULATION_ATTR", "BodyStudyProfileError",
    "covers_body_owner", "reconcile_body_study",
    "require_body_admission", "body_admission_active",
]
