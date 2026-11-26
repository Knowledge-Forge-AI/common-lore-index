"""Unit tests for supplement per-arm applicability and static gold reachability."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest

from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy, ResolvedBindingPolicy
from common_lore_index_test_support.retrieval_supplement_fixtures import (
    expected_enumeration,
    expected_literal_body,
    expected_privacy,
    expected_ranked,
    make_test_bindings,
    make_test_catalog_payload,
    make_test_exposures,
    make_test_identity,
    make_test_policy,
)
from retrieval_public_text_admission import public_text_applicability
from retrieval_supplement_applicability import (
    CandidateExposure,
    check_catalog_applicability,
    compare_expectations,
    expected_applicability,
    expected_matrix,
    reachability_report,
    native_branch,
)
from retrieval_supplement_catalog import parse_catalog
from retrieval_supplement_values import (
    ARMS,
    FTS_ARM,
    FUSION_ARM,
    GRAPH_ARM,
    NATIVE_ARM,
    V1_ARM,
    V2_ARM,
    SupplementError,
    canonical_bytes,
)

BINDINGS = make_test_bindings()
VAULT_ID = BINDINGS["lanternvault"]


def _catalog(payload: dict[str, Any] | None = None):
    return parse_catalog(
        canonical_bytes(payload or make_test_catalog_payload()), identity=make_test_identity(),
        binding_ids=tuple(BINDINGS.values()),
    )


def _cases(kind: str):
    return [case for case in _catalog().cases if case.kind == kind]


def _policy_with_vault(**fields: str) -> BodyProjectionPolicy:
    base = make_test_policy(vault_privacy="public-dev")
    bindings = dict(base.bindings)
    bindings[VAULT_ID] = replace(bindings[VAULT_ID], **fields)
    return replace(base, bindings=bindings)


@pytest.mark.parametrize("bad", [[], {}])
@pytest.mark.parametrize("field", ["requested_mode", "comparability_stratum", "intended_match_surface", "grading_rule", "lexical_query_style"])
def test_direct_task_container_vocabulary_is_a_validation_error(field: str, bad: Any) -> None:
    task = _catalog().tasks[0]
    changes = {"permitted_query_plan": dict(task.permitted_query_plan, lexical_query_style=bad)} if field == "lexical_query_style" else {field: bad}
    task = replace(task, **changes)
    with pytest.raises(SupplementError):
        expected_applicability(task, FTS_ARM, make_test_policy())


@pytest.mark.parametrize("surfaces,expected", [
    (("stored_metadata",), "stored_metadata"), (("captured_text",), "captured_text"),
    (("stored_metadata", "captured_text"), "captured_text"), ((), "captured_text"),
])
def test_native_branch_uses_scoped_path_surfaces(surfaces: tuple[str, ...], expected: str) -> None:
    task = _catalog().tasks[0]
    branch = native_branch(task, surfaces)
    assert branch["surface"] == expected and branch["fallback"] == "required"
    assert branch["effective_mode"] == ("current_literal_enumeration" if expected == "stored_metadata" else "literal_captured_text")


def test_ranked_metadata_cases_follow_contract_table() -> None:
    policy = make_test_policy()
    for kind in ("positive", "absent", "ambiguous"):
        for case in _cases(kind):
            assert expected_matrix(case.task, policy) == expected_ranked()


def test_privacy_cases_refuse_all_six_arms_from_actual_policy() -> None:
    policy = make_test_policy()
    privacy = _cases("privacy")
    assert len(privacy) == 2 and len(privacy[1].task.selected_bindings) == 2
    for case in privacy:
        assert expected_matrix(case.task, policy) == expected_privacy()
    public = make_test_policy(vault_privacy="public-dev")
    native = expected_applicability(privacy[0].task, NATIVE_ARM, public)
    assert native["execution"] == "completed"
    with pytest.raises(SupplementError, match="applicability:unsupported_task_rule"):
        expected_applicability(privacy[0].task, V2_ARM, public)


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"resolved_graph_privacy": "private-dev"}, "graph_privacy_not_authorized"),
        ({"resolved_evidence_retention": "metadata-only"}, "retention_not_authorized"),
        ({"resolved_binding_privacy": "inherit"}, "unresolved_inherit_policy"),
        ({"role": "mailbox"}, "email_body_forbidden"),
        ({"source_kind": "rss"}, "feed_body_forbidden"),
    ],
)
def test_every_policy_denial_is_recomputed_first(fields: dict[str, str], reason: str) -> None:
    policy = _policy_with_vault(**fields)
    for arm in ARMS:
        assert expected_applicability(_cases("privacy")[1].task, arm, policy) == {
            "execution": "refused", "reason": reason, "actual_surface": "none", "fallback_policy": "none",
        }


def test_unselected_unknown_binding_and_graph_mismatch() -> None:
    task = _cases("privacy")[0].task
    base = make_test_policy(vault_privacy="public-dev")
    unselected = replace(base, selected_binding_ids=frozenset(set(BINDINGS.values()) - {VAULT_ID}))
    assert expected_applicability(task, GRAPH_ARM, unselected)["reason"] == "binding_not_selected"
    missing = dict(base.bindings)
    del missing[VAULT_ID]
    with pytest.raises(SupplementError, match="applicability:unknown_binding"):
        expected_applicability(task, NATIVE_ARM, replace(base, bindings=missing))
    with pytest.raises(SupplementError, match="applicability:graph_mismatch"):
        expected_applicability(task, NATIVE_ARM, replace(base, graph_id="another-graph"))
    with pytest.raises(SupplementError, match="applicability:unknown_arm"):
        expected_applicability(task, "exact_vector:hnsw", base)


def test_unsupported_mode_cases_follow_contract_table() -> None:
    literal, listing = _cases("unsupported")
    policy = make_test_policy()
    assert expected_matrix(literal.task, policy) == expected_literal_body()
    assert expected_matrix(listing.task, policy) == expected_enumeration()


def test_vector_branch_reuses_maintained_predicate_order() -> None:
    policy = make_test_policy()
    listing = _cases("unsupported")[1].task
    for case in _catalog().cases:
        if case.kind == "privacy":
            continue
        expected_reason = public_text_applicability(case.task)
        for arm in (V1_ARM, V2_ARM, FUSION_ARM):
            assert expected_applicability(case.task, arm, policy)["reason"] == expected_reason
    mixed = replace(listing, intended_match_surface="captured_text")
    assert expected_applicability(mixed, V2_ARM, policy)["reason"] == "metadata_representation_not_comparable"
    assert expected_applicability(mixed, GRAPH_ARM, policy)["reason"] == (
        "captured_text_surface_unsupported_by_metadata_graph"
    )


def test_fts_fallback_follows_style_and_native_follows_mode() -> None:
    policy = make_test_policy()
    positive = _cases("positive")[0].task
    literal_plan = dict(positive.permitted_query_plan, lexical_query_style="literal_captured_text")
    assert expected_applicability(replace(positive, permitted_query_plan=literal_plan), FTS_ARM, policy)[
        "fallback_policy"] == "required"
    literal = _cases("unsupported")[0].task
    ranked_plan = dict(literal.permitted_query_plan, lexical_query_style="ranked_terms")
    assert expected_applicability(replace(literal, permitted_query_plan=ranked_plan), FTS_ARM, policy)[
        "fallback_policy"] == "required"
    assert expected_applicability(literal, NATIVE_ARM, policy)["fallback_policy"] == "none"
    bad_plan = dict(positive.permitted_query_plan, lexical_query_style="normalized_phrase")
    with pytest.raises(SupplementError, match="unsupported_lexical_style"):
        expected_applicability(replace(positive, permitted_query_plan=bad_plan), FTS_ARM, policy)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("requested_mode", "semantic_discovery", "applicability:unsupported_mode"),
        ("intended_match_surface", "graph_topology", "applicability:unsupported_surface"),
        ("comparability_stratum", "graph_topology", "applicability:unsupported_stratum"),
        ("grading_rule", "direct_handoff", "applicability:unsupported_rule"),
    ],
)
def test_unsupported_strings_are_rejected(field: str, value: str, code: str) -> None:
    task = replace(_cases("positive")[0].task, **{field: value})
    with pytest.raises(SupplementError, match=code):
        expected_applicability(task, V1_ARM, make_test_policy())


def test_catalog_comparison_reports_field_mismatches() -> None:
    policy = make_test_policy()
    assert check_catalog_applicability(_catalog(), policy)["mismatch_count"] == 0
    payload = copy.deepcopy(make_test_catalog_payload())
    payload["cases"][0]["expectations"][FTS_ARM]["fallback_policy"] = "required"
    report = check_catalog_applicability(_catalog(payload), policy)
    assert report["mismatch_count"] == 1
    assert report["cases"][0]["mismatches"] == [f"{FTS_ARM}:fallback_policy"]
    expected = expected_ranked()
    expected[NATIVE_ARM]["fallback_policy"] = "record_actual"
    assert compare_expectations(expected, expected_ranked()) == ()
    expected[V2_ARM]["fallback_policy"] = "record_actual"
    assert compare_expectations(expected, expected_ranked()) == (f"{V2_ARM}:fallback_policy",)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"arm": "exact_vector:hnsw"},
        {"start_line": 3, "end_line": None},
        {"start_line": 4, "end_line": 2},
        {"start_line": True, "end_line": True},
        {"kind": "Doc.Page"},
        {"path": "../escape.py"},
        {"canonical_key": " "},
    ],
)
def test_candidate_exposure_validation(kwargs: dict[str, Any]) -> None:
    fields: dict[str, Any] = {
        "arm": V1_ARM, "binding_id": BINDINGS["lanterncode"], "path": "lantern/registry.py",
        "canonical_key": "k", "kind": "function", "start_line": 1, "end_line": 2,
    }
    fields.update(kwargs)
    with pytest.raises(SupplementError):
        CandidateExposure(**fields)


def test_reachability_floor_on_complete_inventory() -> None:
    report = reachability_report(_catalog(), make_test_exposures(), make_test_policy())
    assert report["gap_count"] == 0 and report["arms_without_exposures"] == []
    assert report["common_positive_count"] == 8 and report["floor_met"] is True
    assert set(report["family_counts"].values()) == {2}
    assert report["denied_exposure_count"] == 2
    assert report["query_hits_inferred"] is False and report["native_qualified"] is False
    assert report["token_counts"] is None


def _without(predicate: Any) -> tuple[CandidateExposure, ...]:
    return tuple(e for e in make_test_exposures() if not predicate(e))


def test_reachability_reports_missing_test_family_gold() -> None:
    exposures = _without(lambda e: e.arm == FTS_ARM and e.path == "tests/test_glass_cleaning.py")
    report = reachability_report(_catalog(), exposures, make_test_policy())
    assert report["gaps"] == [{
        "task_id": "unit-supplement-07", "arm": FTS_ARM, "binding_id": BINDINGS["lanterncode"],
        "source_relative_path": "tests/test_glass_cleaning.py", "code": "gold_not_exposed",
    }]
    assert report["family_counts"]["relevant_tests_for_behavior"] == 1
    assert report["common_positive_count"] == 7 and report["floor_met"] is False


def test_reachability_requires_doc_sections_and_observed_spans() -> None:
    page = [
        replace(e, kind="doc.page", start_line=None, end_line=None) if e.path.endswith(".md") and e.arm == GRAPH_ARM
        else e
        for e in make_test_exposures()
    ]
    report = reachability_report(_catalog(), page, make_test_policy())
    assert {gap["code"] for gap in report["gaps"]} == {"doc_section_span_required"}
    assert {gap["arm"] for gap in report["gaps"]} == {GRAPH_ARM}
    assert report["family_counts"]["code_plus_design_or_message_context"] == 0
    spanless = [
        replace(e, start_line=None, end_line=None) if e.path == "lantern/registry.py" and e.arm in {GRAPH_ARM, V2_ARM}
        else e
        for e in make_test_exposures()
    ]
    report = reachability_report(_catalog(), spanless, make_test_policy())
    assert [(gap["arm"], gap["code"]) for gap in report["gaps"]] == [(V2_ARM, "observed_span_required")]


def test_reachability_voids_floor_when_an_exact_arm_is_unavailable() -> None:
    report = reachability_report(_catalog(), _without(lambda e: e.arm == FUSION_ARM), make_test_policy())
    assert report["arms_without_exposures"] == [FUSION_ARM]
    assert report["common_positive_count"] == 0 and report["floor_met"] is False
    unknown = (*make_test_exposures(), CandidateExposure(
        V1_ARM, "unit-supplement-binding-ghost", "ghost.py", "ghost", "function", 1, 1))
    with pytest.raises(SupplementError, match="applicability:unknown_binding"):
        reachability_report(_catalog(), unknown, make_test_policy())
    denied_gold = [
        replace(e, binding_id=VAULT_ID) if e.path == "lantern/registry.py" else e
        for e in make_test_exposures()
    ]
    report = reachability_report(_catalog(), denied_gold, make_test_policy())
    assert {gap["task_id"] for gap in report["gaps"]} == {"unit-supplement-01"}
    assert len(report["gaps"]) == len(ARMS)


def test_bindings_are_full_ids_not_aliases() -> None:
    task = _cases("positive")[0].task
    assert task.selected_bindings == (BINDINGS["lanterncode"],)
    aliased = replace(task, selected_bindings=("lanterncode",))
    with pytest.raises(SupplementError, match="applicability:unknown_binding"):
        expected_applicability(aliased, NATIVE_ARM, make_test_policy())
    assert isinstance(make_test_policy().bindings[VAULT_ID], ResolvedBindingPolicy)
