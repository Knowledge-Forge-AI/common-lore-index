"""One-node registry and pytest collection hook for the study actual owner (ADR 0203).

Ordinary populations visibly *deselect* this node (never run/skip/xfail).
It runs only inside the authenticated canonical study SDK when
``CLOE_PGVECTOR_OFFLINE_PLAN`` points to the projected ``0600`` inner study
plan (``/sandbox-scratch/pgvector/plan.json``) carrying a verified
``StudyAdmission`` and the collected population is exactly this one node.
Arbitrary host environment strings never satisfy admission.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
import os
from pathlib import Path
from typing import Any

from retrieval_supplement_study_contracts import (
    ENV_STUDY_ADMISSION,
    STUDY_MARKER,
    STUDY_NODE_ID,
    STUDY_INNER_PLAN_SCHEMA,
    STUDY_SELECTOR,
    StudyRefusal,
)
from retrieval_supplement_study_plan import (
    StudyAdmission,
    admission_from_inner_plan,
)
from runner_pgvector_budget import private_file
from runner_pgvector_prebuilt import (
    ENV_OFFLINE_PLAN,
    INNER_PLAN,
    read_regular,
    strict_object,
)

MARKER = STUDY_MARKER
NODE_FILE = STUDY_SELECTOR
NODE_ID = STUDY_NODE_ID
OPTIONAL_NODE_IDS = (NODE_ID,)
ENV_ADMISSION = ENV_STUDY_ADMISSION
POPULATION_ATTR = "_supplement_study_population"
DESELECTION_REASON = (
    "actual supplement study runs only inside the canonical study SDK with verified inner StudyAdmission"
)
_MAX_INNER_PLAN = 2 * 1024**2


class SupplementStudyProfileError(RuntimeError):
    """Study-owner population or admission mismatch."""


def _verified_inner_admission(
    environment: Mapping[str, str], *, expected_inner_path: Path,
) -> StudyAdmission | None:
    if ENV_ADMISSION in environment:
        raise SupplementStudyProfileError(
            "unauthenticated CLOE_SUPPLEMENT_STUDY_ADMISSION env cannot bypass inner SDK plan"
        )
    raw_target = environment.get(ENV_OFFLINE_PLAN)
    if raw_target is None:
        return None
    if raw_target != str(expected_inner_path):
        raise SupplementStudyProfileError("offline plan path mismatch inside sandbox")
    try:
        private_file(expected_inner_path)
        plan = strict_object(read_regular(expected_inner_path, _MAX_INNER_PLAN))
    except (OSError, RuntimeError, ValueError) as exc:
        raise SupplementStudyProfileError("offline inner plan unreadable or malformed") from exc
    if plan.get("schema") != STUDY_INNER_PLAN_SCHEMA:
        if plan.get("role") == "study":
            raise SupplementStudyProfileError("study role requires closed study plan schema")
        return None
    from test_sandbox import active_sandbox

    if not active_sandbox(environment):
        raise SupplementStudyProfileError("study inner plan requires authenticated active sandbox")
    try:
        return admission_from_inner_plan(plan)
    except (StudyRefusal, ValueError) as exc:
        raise SupplementStudyProfileError(f"study inner admission refused: {exc}") from exc


def study_admission_active(
    environment: Mapping[str, str] | None = None, *, inner_plan_path: Path | None = None,
) -> bool:
    env = os.environ if environment is None else environment
    target = Path(INNER_PLAN) if inner_plan_path is None else inner_plan_path
    return _verified_inner_admission(env, expected_inner_path=target) is not None


def require_study_admission(
    environment: Mapping[str, str] | None = None, *, inner_plan_path: Path | None = None,
) -> StudyAdmission:
    env = os.environ if environment is None else environment
    target = Path(INNER_PLAN) if inner_plan_path is None else inner_plan_path
    admission = _verified_inner_admission(env, expected_inner_path=target)
    if admission is None:
        raise SupplementStudyProfileError(DESELECTION_REASON)
    return admission


def covers_study_owner(config: Any) -> bool:
    """Whether a path argument (not a node selection) contains the study owner file."""
    root = Path(getattr(config, "rootpath", Path.cwd()))
    base = Path(getattr(getattr(config, "invocation_params", None), "dir", root))
    node = (root / NODE_FILE).resolve()
    paths = [(base / arg).resolve() for arg in getattr(config, "args", ()) if "::" not in str(arg)]
    return any(path == node or path in node.parents for path in paths)


def reconcile_supplement_study(
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
        raise SupplementStudyProfileError("study marker and one-node registry disagree")
    if optional & set(deferred_build_node_ids) or any(
        item.get_closest_marker("requires_build_profile") is not None for item in marked
    ):
        raise SupplementStudyProfileError("the study owner must not enter deferred build debt")
    raw = [item.nodeid for item in items]
    if study_admission_active():
        if raw != [NODE_ID]:
            raise SupplementStudyProfileError(
                "admitted study population must be exactly the one named node"
            )
        setattr(config, POPULATION_ATTR,
                {"excluded": [], "count": 0, "raw_count": len(raw), "reason": DESELECTION_REASON})
        return
    if covers_study_owner(config) and collected != optional:
        raise SupplementStudyProfileError(
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
    "OPTIONAL_NODE_IDS", "POPULATION_ATTR", "SupplementStudyProfileError",
    "covers_study_owner", "reconcile_supplement_study",
    "require_study_admission", "study_admission_active",
]
