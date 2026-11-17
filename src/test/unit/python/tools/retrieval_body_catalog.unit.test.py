"""Observable closed-class, mutation and legacy separation contracts."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from repomap_test_support.retrieval_body_fixtures import make_body_bindings, make_body_catalog_payload, make_body_identity
from retrieval_body_catalog import BodyCase, BodyCatalog, parse_functional_catalog
from retrieval_body_values import REGISTERED_IDENTITY, BodyError, BodyIdentity, canonical_bytes
from retrieval_public_text_admission import public_text_applicability


def _parse(payload: dict[str, Any]) -> BodyCatalog:
    return parse_functional_catalog(canonical_bytes(payload), identity=make_body_identity(), binding_ids=tuple(make_body_bindings().values()))


def test_valid_catalog_preserves_tasks_and_capability_partitions() -> None:
    payload = make_body_catalog_payload()
    result = _parse(payload)
    assert result.to_dict() == payload and result.raw == canonical_bytes(payload)
    assert not result.registered and result.is_factory_issued(result)
    assert len(result.cases) == 24
    body = result.cases[0]
    assert body.task.comparability_stratum == "body"
    assert public_text_applicability(body.task) == "metadata_representation_not_comparable"
    assert body.to_dict()["expected_capabilities"]["exact_vector:canonical-node-public-text-v2"]["preparation"] == "ready"


def test_catalog_and_case_are_immutable_and_return_defensive_values() -> None:
    catalog = _parse(make_body_catalog_payload())
    first = catalog.cases[0]
    first.to_dict()["witnesses"].clear()
    plan = first.task.permitted_query_plan
    assert isinstance(plan, dict)
    plan["query"] = "changed"
    catalog.to_dict()["cases"].clear()
    assert len(catalog.cases) == 24 and len(first.to_dict()["witnesses"]) == 1
    assert first.task.permitted_query_plan["query"] != "changed"
    with pytest.raises(AttributeError):
        catalog._raw = b"{}"
    with pytest.raises(AttributeError):
        first._raw = b"{}"


@pytest.mark.parametrize("field,value", [
    ("generation", True), ("generation", "1"), ("generation", 2), ("split", "held_out"),
    ("requested_mode", []), ("requested_mode", "invented"), ("source_path_prefix", "canopy"),
    ("selected_bindings", "oak"), ("selected_bindings", []), ("selected_bindings", ["foreign"]),
    ("intended_match_surface", "stored_metadata"), ("comparability_stratum", "metadata"),
    ("family", "exact_symbol_and_partial_name"), ("expected_refusal_code", "forged"),
    ("expected_capability_state", "unavailable"), ("comparator_exposure", []),
    ("task_id", "body1-01"), ("task_id", "unit-body-25"), ("task_id", "unit-body-1"),
])
def test_task_fields_refuse_coercion_and_cross_class_drift(field: str, value: Any) -> None:
    payload = make_body_catalog_payload()
    payload["cases"][0]["task"][field] = value
    with pytest.raises(BodyError):
        _parse(payload)


@pytest.mark.parametrize("owner", ["root", "case", "task", "plan", "gold", "locator", "witness", "distractor"])
def test_unknown_fields_refuse_at_each_boundary(owner: str) -> None:
    payload = make_body_catalog_payload()
    case = payload["cases"][0]
    task = case["task"]
    target = {"root": payload, "case": case, "task": task, "plan": task["permitted_query_plan"],
        "gold": task["gold_targets"][0], "locator": task["gold_targets"][0]["locator"],
        "witness": case["witnesses"][0], "distractor": case["distractors"][0]}[owner]
    target["unrecognized"] = True
    with pytest.raises(BodyError):
        _parse(payload)


@pytest.mark.parametrize("field,value", [("limit", True), ("limit", 11), ("fixed_strings", 1),
    ("ignore_case", False), ("patterns", []), ("patterns", ["dup", "dup"]),
    ("lexical_query_style", "invented"), ("query", " ")])
def test_query_plan_is_bounded_and_explicit(field: str, value: Any) -> None:
    payload = make_body_catalog_payload()
    payload["cases"][0]["task"]["permitted_query_plan"][field] = value
    with pytest.raises(BodyError):
        _parse(payload)


@pytest.mark.parametrize("field,value", [("start_line", 0), ("end_line", True),
    ("fact_start_line", 1), ("fact_end_line", None), ("kind", "doc.section"),
    ("source_sha256", "forged"), ("fact_model_bytes", 0), ("fact_model_start_byte", 65536),
    ("tokenizer_count", 500), ("truncation_survival", "passed"), ("source_relative_path", "../escape.py")])
def test_witnesses_cannot_claim_foreign_spans_or_observed_tokenization(field: str, value: Any) -> None:
    payload = make_body_catalog_payload()
    witness = payload["cases"][0]["witnesses"][0]
    witness[field] = witness["end_line"] + 1 if field == "fact_end_line" else value
    with pytest.raises(BodyError):
        _parse(payload)


def test_distractor_requires_distinct_file_matching_metadata_and_contrast() -> None:
    payload = make_body_catalog_payload()
    case = payload["cases"][0]
    case["distractors"][0]["witness"] = copy.deepcopy(case["witnesses"][0])
    with pytest.raises(BodyError, match="distractor_is_gold"):
        _parse(payload)
    payload = make_body_catalog_payload()
    payload["cases"][0]["distractors"][0]["witness"]["display_name"] = "obvious_different_symbol"
    with pytest.raises(BodyError, match="distractor_metadata_or_fact"):
        _parse(payload)
    payload = make_body_catalog_payload()
    payload["cases"][0]["witnesses"].clear()
    with pytest.raises(BodyError, match="witness_required"):
        _parse(payload)


def test_population_and_wording_do_not_silently_shrink() -> None:
    payload = make_body_catalog_payload()
    payload["cases"].pop()
    with pytest.raises(BodyError, match="24_case_matrix"):
        _parse(payload)
    payload = make_body_catalog_payload()
    payload["cases"][0]["wording_stratum"] = "paraphrased_intent"
    with pytest.raises(BodyError, match="family_wording_matrix"):
        _parse(payload)
    payload = make_body_catalog_payload()
    payload["cases"][1]["intent_id"] = payload["cases"][0]["intent_id"]
    with pytest.raises(BodyError, match="duplicate_intent_id"):
        _parse(payload)


def test_caller_labels_cannot_promote_incompatible_arms() -> None:
    payload = make_body_catalog_payload()
    payload["cases"][0]["expected_capabilities"]["exact_vector:canonical-node-v1"] = {
        "preparation": "ready", "reason": None, "actual_surface": "captured_text", "fallback_policy": "none"}
    with pytest.raises(BodyError, match="capability_matrix"):
        _parse(payload)


def test_registered_identity_requires_exact_loader_and_factory() -> None:
    raw = canonical_bytes(make_body_catalog_payload())
    with pytest.raises(BodyError, match="registered_loader_required"):
        parse_functional_catalog(raw, identity=REGISTERED_IDENTITY, binding_ids=tuple(make_body_bindings().values()))
    with pytest.raises(BodyError, match="registered_mismatch"):
        BodyIdentity("body1-other", "body1-other", "UNIT", "g", "body1-")
    with pytest.raises(BodyError, match="factory_required"):
        BodyCatalog(raw, make_body_identity(), ())
    with pytest.raises(BodyError, match="factory_required"):
        BodyCase({})
    assert not BodyCatalog.is_factory_issued(object())
