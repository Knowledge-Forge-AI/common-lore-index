"""Unit tests for inert supplement preparation and the baseline/FTS prepare-only seams."""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import replace
from typing import Any

import pytest

from common_lore_index_test_support.retrieval_supplement_fixtures import (
    gold,
    make_test_bindings,
    make_test_catalog_payload,
    make_test_corpus_payload,
    make_test_exposures,
    make_test_identity,
    make_test_policy,
)
from retrieval_supplement_applicability import CandidateExposure, expected_applicability
from retrieval_supplement_baselines import prepare_baseline_task, prepare_fts_request
from retrieval_supplement_catalog import parse_catalog
from retrieval_supplement_corpus import parse_corpus
from retrieval_supplement_preparation import combined_record_count, prepare_supplement
from retrieval_supplement_tasks import task_digests
from retrieval_supplement_values import (
    ARMS,
    FTS_ARM,
    FUSION_ARM,
    PREPARATION_TASK_CAP,
    V1_ARM,
    V2_ARM,
    SupplementError,
    canonical_bytes,
)

BINDINGS = make_test_bindings()
CODE_ID = BINDINGS["lanterncode"]


def _catalog(payload: dict[str, Any] | None = None):
    return parse_catalog(canonical_bytes(payload or make_test_catalog_payload()),
                         identity=make_test_identity(), binding_ids=tuple(BINDINGS.values()))


def _corpus():
    return parse_corpus(canonical_bytes(make_test_corpus_payload()), identity=make_test_identity(),
                        bindings=BINDINGS)


def _prepare(catalog: Any = None, exposures: Any = None, policy: Any = None) -> dict[str, Any]:
    return prepare_supplement(
        catalog or _catalog(), _corpus(), policy=policy or make_test_policy(),
        exposures=make_test_exposures() if exposures is None else exposures,
    )


def _extra(arm: str, count: int) -> list[CandidateExposure]:
    return [CandidateExposure(arm, CODE_ID, "lantern/registry.py", f"extra:{arm}:{n}", "function", 1, 2)
            for n in range(count)]


@pytest.mark.parametrize("bad", [[], {}])
def test_direct_baseline_task_container_mode_is_a_validation_error(bad: Any) -> None:
    task = replace(_catalog().tasks[0], requested_mode=bad)
    for prepare in (prepare_baseline_task, prepare_fts_request):
        with pytest.raises(SupplementError):
            prepare(task, policy=make_test_policy())


def test_preparation_is_inert_exact_and_counts_once_per_representation() -> None:
    catalog = _catalog()
    result = _prepare(catalog)
    canonical_bytes(result)
    assert result["queries_executed"] is False and result["model_workers_started"] == 0
    assert result["native_qualified"] is False and result["execution_authorized"] is False
    assert result["tokenization"] == "unknown" and result["encodings"]["token_counts"] is None
    assert PREPARATION_TASK_CAP == 24 and result["slot_count"] == 16 * len(ARMS) * 3
    exchanges = {e["representation"]: e for e in result["exchanges"]}
    assert set(exchanges) == {"canonical-node-v1", "canonical-node-public-text-v2"}
    tasks = {task.task_id: task for task in catalog.tasks}
    eligible = {f"unit-supplement-{n:02d}" for n in range(1, 13)}
    for exchange in exchanges.values():
        ids = [q["task_id"] for q in exchange["queries"]]
        assert len(ids) == len(set(ids)) and set(ids) == eligible
        assert exchange["document_count"] == 16 and exchange["query_count"] == 12
        assert exchange["record_count"] == exchange["embedding_count_expected"] == 28
        for query in exchange["queries"]:
            task = tasks[query["task_id"]]
            assert query["text"] == task.question
            assert {k: query[k] for k in ("retained_task_sha256", "task_sha256")} == task_digests(task)
    assert result["encodings"]["combined_records"] == 56


def test_three_repetitions_reference_one_prepared_exchange() -> None:
    result = _prepare()
    exchange_by_arm = {e["arm"]: e["exchange_id"] for e in result["exchanges"]}
    references = result["references"]
    assert len(references) == 12 * 3 * 3
    per_slot = Counter((r["arm"], r["task_id"]) for r in references)
    assert set(per_slot.values()) == {3}
    for ref in references:
        assert ref["repetition"] in {1, 2, 3}
        target = V2_ARM if ref["arm"] == FUSION_ARM else ref["arm"]
        assert ref["exchange_id"] == exchange_by_arm[target]
        assert ref["query_id"] == f"{exchange_by_arm[target]}:{ref['task_id']}"
    queries = [q["query_id"] for e in result["exchanges"] for q in e["queries"]]
    assert len(queries) == 24 and {r["query_id"] for r in references} == set(queries)


def test_old_twenty_four_task_preparation_cap_is_kept() -> None:
    payload = copy.deepcopy(make_test_catalog_payload())
    for number in range(17, 26):
        extra = copy.deepcopy(payload["cases"][0])
        extra["intent_id"] = f"unit-extra-{number}"
        extra["task"].update(task_id=f"unit-supplement-{number:02d}",
                             question=f"Which synthetic module {number} exists?",
                             gold_targets=[gold("lanterncode", f"lantern/extra_{number}.py")])
        payload["cases"].append(extra)
    catalog = _catalog(payload)
    assert len(catalog.tasks) == 25
    with pytest.raises(SupplementError, match="preparation:task_cap_exceeded"):
        _prepare(catalog)


def test_candidate_pair_cap_is_eighty_per_representation() -> None:
    at_cap = (*make_test_exposures(), *_extra(V1_ARM, 64))
    assert _prepare(exposures=at_cap)["exchanges"][0]["document_count"] == 80
    with pytest.raises(SupplementError, match="preparation:candidate_pair_cap_exceeded"):
        _prepare(exposures=(*make_test_exposures(), *_extra(V1_ARM, 65)))


def test_independent_record_budget_without_relaxing_any_class_limit() -> None:
    assert combined_record_count(200, 50, 200, 50) == 500
    with pytest.raises(SupplementError, match="preparation:combined_record_cap_exceeded"):
        combined_record_count(200, 50, 200, 51)
    with pytest.raises(SupplementError):
        combined_record_count(True, 0, 0, 0)
    within = (*make_test_exposures(), *_extra(V1_ARM, 64), *_extra(V2_ARM, 64))
    prepared = _prepare(exposures=within)
    assert all(exchange["document_count"] == 80 for exchange in prepared["exchanges"])
    assert prepared["encodings"]["combined_records"] <= 2 * (80 + 24) < 500


def test_exposure_identity_denial_and_substitution_refusals() -> None:
    base = make_test_exposures()
    cases: list[tuple[tuple[CandidateExposure, ...], str]] = [
        ((*base, base[0]), "preparation:duplicate_exposure"),
        ((*base, CandidateExposure(V1_ARM, CODE_ID, "lantern/ghost.py", "ghost", "function", 1, 1)),
         "preparation:unknown_exposure"),
        ((*base, CandidateExposure(V1_ARM, BINDINGS["lanternvault"], "vault/inert_marker_one.py",
                                   "vault", "function", 1, 1)), "preparation:denied_eligible_document"),
        ((*base, CandidateExposure(FUSION_ARM, CODE_ID, "lantern/registry.py", "hnsw:registry",
                                   "function", 1, 2)), "preparation:fusion_substitution"),
        (tuple(replace(e, end_line=1) if e.arm == FUSION_ARM and e.path == "lantern/registry.py" else e
               for e in base), "preparation:fusion_substitution"),
        (tuple(e for e in base if not (e.arm == FTS_ARM and e.path == "tests/test_flame_color.py")),
         "preparation:gold_reachability_missing"),
        (tuple(replace(e, start_line=None, end_line=None)
               if e.arm in {V2_ARM, FUSION_ARM} and e.path == "lantern/harbor_signal.py" else e for e in base),
         "preparation:gold_reachability_missing"),
    ]
    for exposures, code in cases:
        with pytest.raises(SupplementError, match=code):
            _prepare(exposures=exposures)
    ghost = (*base, CandidateExposure(V1_ARM, "unit-supplement-binding-ghost", "x.py", "x", "function", 1, 1))
    with pytest.raises(SupplementError, match="applicability:unknown_binding"):
        _prepare(exposures=ghost)


def test_scopes_are_rechecked_at_call_time() -> None:
    payload = copy.deepcopy(make_test_catalog_payload())
    payload["cases"][0]["expectations"][V2_ARM]["actual_surface"] = "stored_metadata"
    with pytest.raises(SupplementError, match="preparation:expectation_mismatch"):
        _prepare(_catalog(payload))
    with pytest.raises(SupplementError, match="preparation:policy_graph_mismatch"):
        _prepare(policy=replace(make_test_policy(), graph_id="another-graph"))
    # A policy that no longer denies the vault must not silently pass the privacy cases.
    with pytest.raises(SupplementError, match="applicability:unsupported_task_rule"):
        _prepare(policy=make_test_policy(vault_privacy="public-dev"))


def test_baseline_alias_view_keeps_original_task_and_explicit_mapping() -> None:
    task = _catalog().tasks[4]
    original = task.to_dict()
    views = prepare_baseline_task(task, policy=make_test_policy())
    assert task.to_dict() == original
    assert views.original_digests == task_digests(task)
    assert views.alias_task.selected_bindings == ("lanterncode", "lanterndocs")
    assert views.alias_to_binding == {"lanterncode": CODE_ID, "lanterndocs": BINDINGS["lanterndocs"]}
    assert views.alias_digests == task_digests(views.alias_task) != views.original_digests
    plan = views.alias_task.permitted_query_plan
    assert isinstance(plan, dict)
    plan["limit"] = 1
    assert views.alias_task.permitted_query_plan["limit"] == 10
    assert views.to_dict()["alias_task"]["gold_targets"] == original["gold_targets"]


def test_baseline_refuses_denied_unknown_and_ambiguous_scopes() -> None:
    catalog = _catalog()
    privacy = [c.task for c in catalog.cases if c.kind == "privacy"]
    for task in privacy:
        with pytest.raises(SupplementError, match="baseline:binding_privacy_not_authorized"):
            prepare_baseline_task(task, policy=make_test_policy())
        with pytest.raises(SupplementError, match="baseline:binding_privacy_not_authorized"):
            prepare_fts_request(task, policy=make_test_policy())
    aliased = replace(catalog.tasks[0], selected_bindings=("lanterncode",))
    with pytest.raises(SupplementError, match="applicability:unknown_binding"):
        prepare_baseline_task(aliased, policy=make_test_policy())
    policy = make_test_policy()
    bindings = dict(policy.bindings)
    vault = BINDINGS["lanternvault"]
    bindings[vault] = replace(bindings[vault], alias="lanterncode")
    with pytest.raises(SupplementError, match="baseline:ambiguous_alias"):
        prepare_baseline_task(catalog.tasks[0], policy=replace(policy, bindings=bindings))
    with pytest.raises(SupplementError, match="applicability:graph_mismatch"):
        prepare_fts_request(catalog.tasks[0], policy=replace(policy, graph_id="another-graph"))


def test_fts_requests_use_full_ids_and_maintained_fallbacks() -> None:
    catalog = _catalog()
    policy = make_test_policy()
    design = prepare_fts_request(catalog.tasks[4], policy=policy)
    assert design.selected_binding_ids == tuple(sorted((CODE_ID, BINDINGS["lanterndocs"])))
    assert design.query == catalog.tasks[4].permitted_query_plan["query"]
    assert (design.query_style, design.fallback, design.limit, design.offset) == ("ranked_terms", None, 10, 0)
    assert design.source_path_prefix is None
    literal, listing = [c.task for c in catalog.cases if c.kind == "unsupported"]
    literal_request = prepare_fts_request(literal, policy=policy)
    assert (literal_request.query_style, literal_request.fallback) == ("literal_captured_text", None)
    listing_request = prepare_fts_request(listing, policy=policy)
    assert listing_request.query_style == "literal_captured_text"
    assert listing_request.fallback is not None
    assert listing_request.fallback.from_mode.value == "current_literal_enumeration"
    assert listing_request.fallback.to_mode.value == "literal_captured_text"
    for case in catalog.cases:
        if case.kind == "privacy":
            continue
        request = prepare_fts_request(case.task, policy=policy)
        derived = expected_applicability(case.task, FTS_ARM, policy)["fallback_policy"]
        assert (request.fallback is None) == (derived == "none"), case.task.task_id
