"""Prospective per-arm applicability and static gold reachability for the supplement.

Expectations are derived from the actual ``BodyProjectionPolicy`` and task fields:
binding denial is recomputed first (all six arms refuse before any primitive), then
the per-arm capability rules. The vector/fusion branch reuses the maintained v1
predicate ``public_text_applicability``. Nothing here executes a query, infers hits,
counts tokens or qualifies a native runtime.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from common_lore_index.retrieval.projection_policy import (
    ALLOWED_BINDING_PRIVACY,
    ALLOWED_EVIDENCE_RETENTION,
    ALLOWED_GRAPH_PRIVACY,
    BodyProjectionPolicy,
    ExclusionReason,
    _is_email_role_or_path,
    _is_feed_role_or_path,
)
from retrieval_evaluation_schema import EvaluationTask
from retrieval_public_text_admission import public_text_applicability
from retrieval_supplement_catalog import SupplementCatalog
from retrieval_supplement_values import (
    ARMS,
    EXPECTATION_REASONS,
    FAMILY_FLOOR,
    FTS_ARM,
    FUSION_ARM,
    GRADING_RULES,
    GRAPH_ARM,
    GRAPH_BODY_REASON,
    INTENDED_SURFACES,
    LEXICAL_STYLES,
    NATIVE_ARM,
    POSITIVE_FAMILIES,
    POSITIVE_FLOOR,
    REQUESTED_MODES,
    STRATA,
    V1_ARM,
    V2_ARM,
    require,
    safe_relative_path,
    text,
)

SPAN_REQUIRED_ARMS = frozenset({V2_ARM, FUSION_ARM})
_KIND = re.compile(r"\A[a-z][a-z0-9_.]{0,63}\Z")
MAX_LINE = 10_000_000


def binding_denial(policy: BodyProjectionPolicy, binding_id: str) -> str | None:
    """Public-dev graph/binding and full-authorized body check; unknown bindings reject."""
    resolved = policy.bindings.get(binding_id)
    require(resolved is not None, "applicability:unknown_binding")
    assert resolved is not None
    if "inherit" in {
        resolved.resolved_graph_privacy,
        resolved.resolved_binding_privacy,
        resolved.resolved_evidence_retention,
    }:
        return ExclusionReason.UNRESOLVED_INHERIT_POLICY.value
    if resolved.resolved_graph_privacy != ALLOWED_GRAPH_PRIVACY:
        return ExclusionReason.GRAPH_PRIVACY_NOT_AUTHORIZED.value
    if resolved.resolved_binding_privacy != ALLOWED_BINDING_PRIVACY:
        return ExclusionReason.BINDING_PRIVACY_NOT_AUTHORIZED.value
    if resolved.resolved_evidence_retention != ALLOWED_EVIDENCE_RETENTION:
        return ExclusionReason.RETENTION_NOT_AUTHORIZED.value
    if _is_email_role_or_path(resolved.role, resolved.source_kind, resolved.alias, None):
        return ExclusionReason.EMAIL_BODY_FORBIDDEN.value
    if _is_feed_role_or_path(resolved.role, resolved.source_kind, resolved.alias, None):
        return ExclusionReason.FEED_BODY_FORBIDDEN.value
    if binding_id not in policy.selected_binding_ids:
        return ExclusionReason.BINDING_NOT_SELECTED.value
    return None


def task_denial(task: EvaluationTask, policy: BodyProjectionPolicy) -> str | None:
    """First denial over the task's full selected binding IDs, after scope checks."""
    policy.validate()
    require(task.graph_id == policy.graph_id, "applicability:graph_mismatch")
    require(task.selected_bindings, "applicability:empty_selection")
    for binding_id in task.selected_bindings:
        reason = binding_denial(policy, binding_id)
        if reason is not None:
            return reason
    return None


def _record(execution: str, reason: str | None, surface: str, fallback: str) -> dict[str, Any]:
    return {
        "execution": execution, "reason": reason, "actual_surface": surface,
        "fallback_policy": fallback,
    }


def native_branch(task: EvaluationTask, scoped_path_surfaces: Sequence[str]) -> dict[str, str]:
    """Resolve the maintained native branch from supplied scoped-path surfaces.

    These are caller observations, not an origin receipt. A body/mixed/empty
    scope uses captured text; a nonempty metadata-only scope uses metadata.
    Supplement tasks exclude the legacy extracted_metadata empty-scope mode.
    """
    require(type(task.requested_mode) is str and task.requested_mode in REQUESTED_MODES, "native_branch:mode")
    require(isinstance(scoped_path_surfaces, (list, tuple)) and len(scoped_path_surfaces) <= 4096, "native_branch:scope")
    require(all(type(value) is str and value in {"stored_metadata", "captured_text"}
                for value in scoped_path_surfaces), "native_branch:surface")
    metadata = bool(scoped_path_surfaces) and all(value == "stored_metadata" for value in scoped_path_surfaces)
    surface = "stored_metadata" if metadata else "captured_text"
    mode = "current_literal_enumeration" if metadata else "literal_captured_text"
    return {"surface": surface, "effective_mode": mode,
        "fallback": "none" if task.requested_mode == mode else "required"}


def _fts_fallback(mode: str, style: object) -> str:
    """Disclosed FTS fallback, matching the maintained lexical style resolver."""
    require(type(style) is str and style in LEXICAL_STYLES, "applicability:unsupported_lexical_style")
    if mode == "current_literal_enumeration":
        return "required"
    if mode == "literal_captured_text":
        return "required" if style == "ranked_terms" else "none"
    return "required" if style == "literal_captured_text" else "none"


def expected_applicability(
    task: EvaluationTask, arm: str, policy: BodyProjectionPolicy
) -> dict[str, Any]:
    """Closed per-arm expectation ``{execution, reason, actual_surface, fallback_policy}``."""
    require(arm in ARMS, "applicability:unknown_arm")
    mode, stratum = task.requested_mode, task.comparability_stratum
    require(type(mode) is str and mode in REQUESTED_MODES, "applicability:unsupported_mode")
    require(type(stratum) is str and stratum in STRATA, "applicability:unsupported_stratum")
    require(type(task.intended_match_surface) is str and task.intended_match_surface in INTENDED_SURFACES, "applicability:unsupported_surface")
    require(type(task.grading_rule) is str and task.grading_rule in GRADING_RULES, "applicability:unsupported_rule")
    denial = task_denial(task, policy)
    if denial is not None:
        return _record("refused", denial, "none", "none")
    if arm == NATIVE_ARM:
        # Prospective corpus class exposes authorized bodies. Actual delivered
        # path surfaces are independently checked at reporting, not inferred.
        forecast = native_branch(task, ("captured_text",))
        return _record("completed", None, forecast["surface"], forecast["fallback"])
    if arm == FTS_ARM:
        style = task.permitted_query_plan.get("lexical_query_style")
        return _record("completed", None, "captured_text", _fts_fallback(mode, style))
    if arm == GRAPH_ARM:
        body = (
            stratum == "body" or task.intended_match_surface == "captured_text"
            or mode == "literal_captured_text"
        )
        if body:
            return _record("unavailable", GRAPH_BODY_REASON, "none", "none")
        return _record("completed", None, "stored_metadata", "none")
    reason = public_text_applicability(task)
    if reason is not None:
        require(reason in EXPECTATION_REASONS, "applicability:unsupported_task_rule")
        return _record("unavailable", reason, "none", "none")
    surface = "stored_metadata" if arm == V1_ARM else "captured_text"
    return _record("completed", None, surface, "none")


def expected_matrix(task: EvaluationTask, policy: BodyProjectionPolicy) -> dict[str, dict[str, Any]]:
    return {arm: expected_applicability(task, arm, policy) for arm in ARMS}


def compare_expectations(
    expected: Mapping[str, Mapping[str, Any]], derived: Mapping[str, Mapping[str, Any]]
) -> tuple[str, ...]:
    """Field-level mismatches; ``record_actual`` is accepted only for the native arm."""
    require(set(expected) == set(ARMS) and set(derived) == set(ARMS), "applicability:arm_set")
    mismatches: list[str] = []
    for arm in ARMS:
        for field in ("execution", "reason", "actual_surface", "fallback_policy"):
            want, got = expected[arm].get(field), derived[arm].get(field)
            if field == "fallback_policy" and arm == NATIVE_ARM and want == "record_actual":
                continue
            if want != got:
                mismatches.append(f"{arm}:{field}")
    return tuple(mismatches)


def check_catalog_applicability(
    catalog: SupplementCatalog, policy: BodyProjectionPolicy
) -> dict[str, Any]:
    """Compare every authored expectation with the policy/task-derived matrix."""
    require(policy.graph_id == catalog.identity.graph_id, "applicability:graph_mismatch")
    results = []
    for case in catalog.cases:
        task = case.task
        mismatches = compare_expectations(case.expectations, expected_matrix(task, policy))
        results.append({"task_id": task.task_id, "mismatches": list(mismatches)})
    return {
        "cases": results,
        "mismatch_count": sum(len(r["mismatches"]) for r in results),
        "queries_executed": False,
    }


@dataclass(frozen=True)
class CandidateExposure:
    """One statically captured node/binding candidate for one arm's inventory."""

    arm: str
    binding_id: str
    path: str
    canonical_key: str
    kind: str
    start_line: int | None
    end_line: int | None

    def __post_init__(self) -> None:
        require(self.arm in ARMS, "exposure.arm:unknown")
        text(self.binding_id, "exposure.binding_id", 256)
        safe_relative_path(self.path)
        text(self.canonical_key, "exposure.canonical_key", 512)
        require(type(self.kind) is str and _KIND.match(self.kind), "exposure.kind:invalid")
        start, end = self.start_line, self.end_line
        if start is None or end is None:
            require(start is None and end is None, "exposure.span:partial")
        else:
            require(type(start) is int and type(end) is int, "exposure.span:integer_required")
            require(1 <= start <= end <= MAX_LINE, "exposure.span:out_of_range")

    @property
    def identity(self) -> tuple[str, str, str]:
        """Node/binding pair identity within one arm."""
        return (self.arm, self.binding_id, self.canonical_key)

    @property
    def spanned(self) -> bool:
        return self.start_line is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm, "binding_id": self.binding_id, "source_relative_path": self.path,
            "canonical_key": self.canonical_key, "kind": self.kind,
            "start_line": self.start_line, "end_line": self.end_line,
        }


def exposure_gap(exposure: CandidateExposure) -> str | None:
    """Why an exposure cannot reach a gold file in its arm, if it cannot."""
    if exposure.path.endswith(".md") and not (exposure.kind == "doc.section" and exposure.spanned):
        return "doc_section_span_required"
    if exposure.arm in SPAN_REQUIRED_ARMS and not exposure.spanned:
        return "observed_span_required"
    return None


def reachability_report(
    catalog: SupplementCatalog,
    exposures: Iterable[CandidateExposure],
    policy: BodyProjectionPolicy,
) -> dict[str, Any]:
    """Report every gold-file gap per arm plus the common-positive and family floors."""
    items = tuple(exposures)
    require(all(isinstance(e, CandidateExposure) for e in items), "exposure:type")
    require(policy.graph_id == catalog.identity.graph_id, "applicability:graph_mismatch")
    denied = {e.binding_id for e in items if binding_denial(policy, e.binding_id) is not None}
    by_file: dict[tuple[str, str, str], list[CandidateExposure]] = {}
    for exposure in items:
        if exposure.binding_id not in denied:
            by_file.setdefault((exposure.arm, exposure.binding_id, exposure.path), []).append(exposure)
    gaps: list[dict[str, Any]] = []
    common: list[str] = []
    families = {family: 0 for family in POSITIVE_FAMILIES}
    for case in catalog.cases:
        if case.kind not in {"positive", "ambiguous"}:
            continue
        task = case.task
        case_gaps = 0
        for gold in task.gold_targets:
            if gold.relevance_grade <= 0:
                continue
            binding_id, path = gold.locator.file_identity_key()
            for arm in ARMS:
                found = by_file.get((arm, binding_id, path), [])
                reasons = [exposure_gap(e) for e in found]
                if found and any(reason is None for reason in reasons):
                    continue
                code = "gold_not_exposed" if not found else str(reasons[0])
                case_gaps += 1
                gaps.append({
                    "task_id": task.task_id, "arm": arm, "binding_id": binding_id,
                    "source_relative_path": path, "code": code,
                })
        if case.kind != "positive" or case_gaps:
            continue
        matrix = expected_matrix(task, policy)
        if all(record["execution"] == "completed" for record in matrix.values()):
            common.append(task.task_id)
            families[task.family] += 1
    present = {e.arm for e in items}
    return {
        "gaps": gaps,
        "gap_count": len(gaps),
        "arms_without_exposures": [arm for arm in ARMS if arm not in present],
        "denied_exposure_count": sum(1 for e in items if e.binding_id in denied),
        "common_positive_count": len(common),
        "common_positive_task_ids": common,
        "family_counts": families,
        "floor_met": len(common) >= POSITIVE_FLOOR
        and all(count >= FAMILY_FLOOR for count in families.values()),
        "query_hits_inferred": False,
        "token_counts": None,
        "native_qualified": False,
    }


__all__ = [
    "CandidateExposure", "binding_denial", "check_catalog_applicability", "compare_expectations",
    "expected_applicability", "expected_matrix", "reachability_report", "task_denial",
]
