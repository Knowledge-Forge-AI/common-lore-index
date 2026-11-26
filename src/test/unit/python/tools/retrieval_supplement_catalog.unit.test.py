"""Unit tests for strict supplement JSON, task and catalog parsing (synthetic namespace only)."""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest

from common_lore_index_test_support.retrieval_supplement_fixtures import (
    gold,
    make_test_bindings,
    make_test_catalog_payload,
    make_test_identity,
)
from retrieval_supplement_catalog import load_registered_catalog, parse_catalog
from retrieval_supplement_tasks import parse_task, task_digests
from retrieval_supplement_values import (
    ARMS,
    PHASE,
    SupplementError,
    SupplementIdentity,
    canonical_bytes,
    digest_bytes,
    safe_relative_path,
    strict_json,
)

BINDINGS = tuple(make_test_bindings().values())


@pytest.mark.parametrize("location,field", [
    ("task", "requested_mode"), ("task", "intended_match_surface"),
    ("task", "comparability_stratum"), ("task", "grading_rule"),
    ("plan", "lexical_query_style"), ("expectation", "execution"),
    ("expectation", "reason"), ("expectation", "actual_surface"),
    ("expectation", "fallback_policy"), ("case", "case_kind"),
])
@pytest.mark.parametrize("bad", [[], {}])
def test_enum_containers_are_validation_errors(location: str, field: str, bad: object) -> None:
    payload = _payload()
    case = payload["cases"][0]
    target = (case["task"] if location == "task" else case["task"]["permitted_query_plan"]
              if location == "plan" else case["expectations"][ARMS[0]] if location == "expectation" else case)
    target[field] = bad
    with pytest.raises(SupplementError):
        _parse(payload)


def _parse(payload: dict[str, Any]):
    return parse_catalog(canonical_bytes(payload), identity=make_test_identity(), binding_ids=BINDINGS)


def _rejects(payload: dict[str, Any], code: str) -> None:
    with pytest.raises(SupplementError, match=re.escape(code)):
        _parse(payload)


def _payload() -> dict[str, Any]:
    return copy.deepcopy(make_test_catalog_payload())


def _task(payload: dict[str, Any], index: int = 0) -> dict[str, Any]:
    return payload["cases"][index]["task"]


def test_valid_synthetic_catalog_round_trips_exactly() -> None:
    payload = _payload()
    raw = canonical_bytes(payload)
    catalog = parse_catalog(raw, identity=make_test_identity(), binding_ids=BINDINGS)
    assert catalog.raw == raw
    assert catalog.digest == digest_bytes(raw)
    assert catalog.to_dict() == payload
    assert catalog.registered is False
    assert [c.kind for c in catalog.cases].count("positive") == 8
    assert {c.kind for c in catalog.cases} == {"positive", "absent", "ambiguous", "privacy", "unsupported"}
    for case, source in zip(catalog.cases, payload["cases"]):
        assert case.task.to_dict() == source["task"]
        assert case.expectations == source["expectations"]
        assert case.intent_id == source["intent_id"]


def test_catalog_hands_out_fresh_values_and_is_immutable() -> None:
    catalog = _parse(_payload())
    case = catalog.cases[0]
    case.expectations[ARMS[0]]["execution"] = "refused"
    plan = case.task.permitted_query_plan
    assert isinstance(plan, dict)
    plan["limit"] = 99
    catalog.to_dict()["cases"].clear()
    assert case.expectations[ARMS[0]]["execution"] == "completed"
    assert case.task.permitted_query_plan["limit"] == 10
    assert len(catalog.to_dict()["cases"]) == 16
    with pytest.raises(AttributeError):
        setattr(catalog, "raw", b"{}")
    with pytest.raises(AttributeError):
        setattr(case, "kind", "absent")


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b'{"a":1,"a":2}', "json_duplicate_key"),
        (b'{"a":NaN}', "json_nonfinite_number"),
        (b'{"a":-Infinity}', "json_nonfinite_number"),
        (b'{"a":1e999}', "json_nonfinite_number"),
        (b"[" * 33 + b"]" * 33, "json_too_deep"),
        (b"\xef\xbb\xbf{}", "json_bom_forbidden"),
        (b'{"a":"\xff"}', "json_invalid_utf8"),
        (b'{"a":', "json_malformed"),
        (b" " * (512 * 1024 + 1), "json_too_large"),
    ],
)
def test_strict_json_rejections(raw: bytes, code: str) -> None:
    with pytest.raises(SupplementError, match=code):
        strict_json(raw)


def test_strict_json_accepts_bytes_only_and_bounded_depth() -> None:
    assert strict_json(b"[" * 32 + b"]" * 32) is not None
    assert strict_json(b'{"k":"[[[[ not depth ]]]]"}') == {"k": "[[[[ not depth ]]]]"}
    invalid_inputs: tuple[Any, ...] = ("{}", bytearray(b"{}"), memoryview(b"{}"))
    for value in invalid_inputs:
        with pytest.raises(SupplementError, match="json_bytes_required"):
            strict_json(value)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("generation", True, "generation:integer_required"),
        ("generation", 2, "generation:out_of_range"),
        ("generation", "1", "generation:integer_required"),
        ("generation", 1.0, "generation:integer_required"),
        ("split", "held_out", "split:calibration_required"),
        ("source_path_prefix", "lantern", "source_path_prefix:must_be_null"),
        ("selected_bindings", "unit-supplement-binding-lanterncode", "selected_bindings:list_required"),
        ("selected_bindings", [], "selected_bindings:bad_length"),
        ("selected_bindings", ["unit-supplement-binding-unknown"], "selected_bindings:unknown_binding"),
        ("requested_mode", "semantic_discovery", "requested_mode:unsupported"),
        ("intended_match_surface", "graph_topology", "intended_match_surface:unsupported"),
        ("comparability_stratum", "graph_topology", "comparability_stratum:unsupported"),
        ("family", "punctuation_phrase_and_distractors", "family:unknown"),
        ("graph_id", "another-graph", "graph_id:identity_mismatch"),
        ("comparator_exposure", [], "comparator_exposure:object_required"),
        ("task_id", "sup1-01", "task_id:namespace_mismatch"),
        ("task_id", "unit-supplement-33", "task_id:number_out_of_range"),
        ("task_id", "unit-supplement-1", "task_id:namespace_mismatch"),
    ],
)
def test_task_field_strictness(field: str, value: Any, code: str) -> None:
    payload = _payload()
    _task(payload)[field] = value
    _rejects(payload, code)


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("limit", True, "limit:integer_required"),
        ("limit", 20, "limit:out_of_range"),
        ("fixed_strings", 1, "fixed_strings:must_be_true"),
        ("ignore_case", False, "ignore_case:must_be_true"),
        ("lexical_query_style", "normalized_phrase", "lexical_query_style:unsupported"),
        ("patterns", ["a"] * 17, "patterns:bad_length"),
        ("patterns", ["dup", "dup"], "patterns:duplicate"),
        ("query", "   ", "query:empty"),
        ("projection_query_mode", "lexical", "permitted_query_plan:unknown_fields"),
    ],
)
def test_query_plan_strictness(key: str, value: Any, code: str) -> None:
    payload = _payload()
    _task(payload)["permitted_query_plan"][key] = value
    _rejects(payload, code)


def test_optional_graph_query_is_accepted_and_missing_plan_key_rejected() -> None:
    payload = _payload()
    _task(payload)["permitted_query_plan"]["graph_query"] = "register_lantern"
    assert _parse(payload).cases[0].task.permitted_query_plan["graph_query"] == "register_lantern"
    del _task(payload)["permitted_query_plan"]["limit"]
    _rejects(payload, "permitted_query_plan:missing_fields")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda g: g["locator"].update(start_line=1), "gold.locator.start_line:must_be_null"),
        (lambda g: g["locator"].update(source_relative_path="../escape.py"), "path:traversal"),
        (lambda g: g["locator"].update(source_relative_path="/abs.py"), "path:absolute"),
        (lambda g: g["locator"].update(extra=None), "gold.locator:unknown_fields"),
        (lambda g: g.update(canonical_key="node"), "gold.canonical_key:must_be_null"),
        (lambda g: g.update(relevance_grade=True), "gold.relevance_grade:integer_required"),
        (lambda g: g.update(relevance_grade=4), "gold.relevance_grade:out_of_range"),
        (lambda g: g.update(rationale=""), "gold.rationale:empty"),
        (lambda g: g["locator"].update(binding_id=BINDINGS[1]), "binding_id:not_selected"),
    ],
)
def test_gold_locator_strictness(mutate: Any, code: str) -> None:
    payload = _payload()
    mutate(_task(payload)["gold_targets"][0])
    _rejects(payload, code)


@pytest.mark.parametrize("where", ["root", "case", "task", "expectation"])
def test_unknown_fields_rejected_everywhere(where: str) -> None:
    payload = _payload()
    target = {
        "root": payload, "case": payload["cases"][0], "task": _task(payload),
        "expectation": payload["cases"][0]["expectations"][ARMS[2]],
    }[where]
    target["unexpected"] = None
    with pytest.raises(SupplementError, match="unknown_fields"):
        _parse(payload)


def test_root_identity_and_arm_contract() -> None:
    for key, value, code in (
        ("schema", "cloe-calibration-supplement-catalog-v2", "catalog.schema:unsupported"),
        ("repetitions", True, "catalog.repetitions:integer_required"),
        ("repetitions", 2, "catalog.repetitions:out_of_range"),
        ("arms", list(reversed(ARMS)), "catalog.arms:mismatch"),
        ("supplement_id", "unit-supplement-other", "catalog.supplement_id:mismatch"),
        ("purpose", "held_out", "catalog.purpose:unsupported"),
    ):
        payload = _payload()
        payload[key] = value
        _rejects(payload, code)
    payload = _payload()
    del payload["family_rationale"]["relevant_tests_for_behavior"]
    _rejects(payload, "catalog.family_rationale:missing_fields")


def test_population_floors_templates_and_uniqueness() -> None:
    payload = _payload()
    del payload["cases"][0]
    _rejects(payload, "catalog.cases:positive_floor")
    payload = _payload()
    payload["cases"] = [c for c in payload["cases"] if c["case_kind"] != "privacy"]
    _rejects(payload, "catalog.cases:privacy_template_missing")
    payload = _payload()
    _task(payload, 1)["question"] = _task(payload, 0)["question"]
    _rejects(payload, "duplicate_question")
    payload = _payload()
    payload["cases"][1]["intent_id"] = payload["cases"][0]["intent_id"]
    _rejects(payload, "duplicate_intent_id")
    payload = _payload()
    _task(payload, 1)["gold_targets"] = copy.deepcopy(_task(payload, 0)["gold_targets"])
    _rejects(payload, "positive_gold_sets_not_distinct")


def test_authored_cap_is_thirty_two_cases() -> None:
    payload = _payload()
    base = payload["cases"][0]
    for number in range(17, 34):
        extra = copy.deepcopy(base)
        extra["intent_id"] = f"unit-extra-{number}"
        extra["task"]["task_id"] = f"unit-supplement-{number:02d}"
        extra["task"]["question"] = f"Which synthetic module {number} exists?"
        extra["task"]["gold_targets"] = [gold("lanterncode", f"lantern/extra_{number}.py")]
        payload["cases"].append(extra)
    _rejects(payload, "catalog.cases:cap_exceeded")


def test_case_kind_shapes() -> None:
    kinds = [c["case_kind"] for c in make_test_catalog_payload()["cases"]]
    privacy, ambiguous = kinds.index("privacy"), kinds.index("ambiguous")
    payload = _payload()
    _task(payload, ambiguous)["gold_targets"][1]["relevance_grade"] = 1
    _rejects(payload, "case.ambiguous:equal_positive_grades")
    payload = _payload()
    _task(payload, privacy)["expected_refusal_code"] = "graph_privacy_not_authorized"
    _rejects(payload, "grading_rule:refusal_mismatch")
    payload = _payload()
    payload["cases"][0]["case_kind"] = "absent"
    _rejects(payload, "case.absent:other_family_required")
    payload = _payload()
    payload["cases"][0]["metadata_basis"] = []
    _rejects(payload, "case.metadata_basis:bad_length")


@pytest.mark.parametrize(
    ("row", "code"),
    [
        ({"execution": "completed", "reason": "unsupported_semantic_query_mode",
          "actual_surface": "captured_text", "fallback_policy": "none"}, "completed_shape"),
        ({"execution": "refused", "reason": None, "actual_surface": "none",
          "fallback_policy": "none"}, "noncompleted_shape"),
        ({"execution": "unavailable", "reason": "backend_unsupported", "actual_surface": "none",
          "fallback_policy": "none"}, "reason:unsupported"),
        ({"execution": "completed", "reason": None, "actual_surface": "graph_topology",
          "fallback_policy": "none"}, "actual_surface:unsupported"),
        ({"execution": "failed", "reason": None, "actual_surface": "none",
          "fallback_policy": "none"}, "execution:unsupported"),
    ],
)
def test_expectation_row_shapes(row: dict[str, Any], code: str) -> None:
    payload = _payload()
    payload["cases"][0]["expectations"][ARMS[3]] = row
    _rejects(payload, code)


def test_unicode_digest_schemes_differ_only_for_non_ascii() -> None:
    catalog = _parse(_payload())
    ascii_digests = task_digests(catalog.tasks[0])
    assert ascii_digests["retained_task_sha256"] == ascii_digests["task_sha256"]
    payload = _payload()
    _task(payload)["question"] = "Which module defines register_lantern for the phare d'été?"
    digests = task_digests(_parse(payload).tasks[0])
    assert digests["retained_task_sha256"] != digests["task_sha256"]
    raw_utf8 = canonical_bytes(_parse(payload).tasks[0].to_dict())
    assert digests["task_sha256"] == digest_bytes(raw_utf8)
    assert digests["retained_task_sha256"] == digest_bytes(canonical_bytes(_task(payload), ascii_only=True))


def test_parse_task_requires_exact_identity_bindings() -> None:
    task = _task(_payload())
    assert parse_task(task, make_test_identity(), BINDINGS).task_id == "unit-supplement-01"
    with pytest.raises(SupplementError, match="binding_ids:invalid"):
        parse_task(task, make_test_identity(), ())


def test_registered_class_cannot_be_reached_by_relabelling_test_data() -> None:
    with pytest.raises(SupplementError, match="phase:registered_mismatch"):
        SupplementIdentity("sup1-catalog", "sup1-corpus", "CLOE-W5-SUPPLEMENT1-UNIT", "g", "sup1-")
    with pytest.raises(SupplementError, match="task_prefix:reserved"):
        SupplementIdentity("sup1-x-cat", "sup1-x-corpus", "UNIT", "g", "sup1-x-")
    registered = SupplementIdentity("sup1-catalog", "sup1-corpus", PHASE, "unit-supplement-graph", "sup1-")
    payload = _payload()
    payload.update(supplement_id="sup1-catalog", corpus_id="sup1-corpus", phase=PHASE)
    for index, case in enumerate(payload["cases"], start=1):
        case["task"]["task_id"] = f"sup1-{index:02d}"
    raw = canonical_bytes(payload)
    with pytest.raises(SupplementError, match="registered_requires_pinned_loader"):
        parse_catalog(raw, identity=registered, binding_ids=BINDINGS, expected_sha256=digest_bytes(raw))
    forged_pin = canonical_bytes({"input_files": {"catalog-v1.json": {"sha256": digest_bytes(raw)}}})
    with pytest.raises(SupplementError, match="registered_pin_mismatch"):
        load_registered_catalog(raw, identity=registered, binding_ids=BINDINGS, pin_raw=forged_pin)
    with pytest.raises(SupplementError, match="registered_identity_required"):
        load_registered_catalog(raw, identity=make_test_identity(), binding_ids=BINDINGS, pin_raw=forged_pin)


def test_digest_mismatch_and_safe_paths() -> None:
    raw = canonical_bytes(_payload())
    with pytest.raises(SupplementError, match="catalog:digest_mismatch"):
        parse_catalog(raw, identity=make_test_identity(), binding_ids=BINDINGS,
                      expected_sha256="sha256:" + "0" * 64)
    assert safe_relative_path("docs/été.md") == "docs/été.md"
    for bad in ("", "a//b", "./a", "a/./b", "a\\b", "c:x", "a\x00b", "a/..", "/x"):
        with pytest.raises(SupplementError):
            safe_relative_path(bad)
