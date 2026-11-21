"""Strict retained-input admission: forged provenance, joins, tokens and counts refuse."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from repomap_test_support.retrieval_supplement_analysis_fixtures import (
    SYNTHETIC_ANALYZER,
    SYNTHETIC_EVALUATOR,
    digest_seed,
    native_shaped_provenance,
    synthetic_bundle,
    synthetic_bytes,
    synthetic_counted_tokens,
    synthetic_provenance,
    synthetic_tasks,
)
from retrieval_supplement_analysis_admission import admit_bundle, parse_bounded_json
from retrieval_supplement_analysis_output import analyze_retained
from retrieval_supplement_analysis_values import (
    FAILURE_RECEIPT_SCHEMA,
    PUBLIC_COST_CENSUS_SCHEMA,
    PUBLIC_OBSERVATION_SCHEMA,
    AnalysisRefusal,
    TaskDescriptor,
)
from retrieval_supplement_primitives import ARMS, OTHER_FAMILY, POSITIVE_FAMILIES


def _admit(raw: bytes, **overrides: Any) -> Any:
    return admit_bundle(
        raw,
        provenance=synthetic_provenance(raw, **overrides),
        tasks=synthetic_tasks(),
    )


def test_file_only_default_synthetic_bundle_admits_without_span_metrics() -> None:
    raw = synthetic_bytes()
    admitted = _admit(raw)
    assert admitted.origin == "synthetic"
    assert admitted.native_qualified is False
    assert admitted.observed_namespaces == ("file",)
    assert len(admitted.slots) == 8 * 6 * 3
    assert admitted.state_counts["completed"] > 0
    assert len(admitted.comparable_positive_tasks) == 4


def test_dual_file_and_span_namespaces_admit_when_uniform_across_all_slots() -> None:
    raw = synthetic_bytes(include_span_metrics=True)
    admitted = _admit(raw)
    assert admitted.observed_namespaces == ("file", "span")


def test_native_shaped_bundle_with_432_slots_and_432_duplicated_costs_admits() -> None:
    tasks = synthetic_tasks(full=True, prefix="sup1-")
    bundle = synthetic_bundle(tasks=tasks, native=True)
    tokens = synthetic_counted_tokens(bundle, native=True)
    raw = synthetic_bytes(bundle)
    admitted = admit_bundle(
        raw,
        provenance=native_shaped_provenance(raw),
        tasks=tasks,
        counted_worker_tokens=tokens,
    )
    assert admitted.origin == "native"
    assert admitted.native_qualified is True
    assert admitted.observed_namespaces == ("file",)
    assert len(admitted.slots) == 432
    assert admitted.cost_record_count == 432
    assert admitted.state_counts == {
        "completed": 375, "failed": 0, "not_run": 0, "refused": 36, "unavailable": 21,
    }
    assert len(admitted.comparable_positive_tasks) == 16
    assert admitted.floor_met is True
    assert admitted.counted_worker_tokens_verified is True


def test_public_projections_failure_receipts_and_synthetic_refuse_inside_admit_as_native() -> None:
    native_tasks = synthetic_tasks(full=True, prefix="sup1-")

    # 1. Public observation projection reaches admit_bundle and refuses by schema, not constructor.
    projection = json.dumps({"schema": PUBLIC_OBSERVATION_SCHEMA, "slots": 432}).encode("utf-8")
    with pytest.raises(AnalysisRefusal) as obs_exc:
        admit_bundle(projection, provenance=native_shaped_provenance(projection), tasks=native_tasks)
    assert obs_exc.value.code == "analysis:public_projection_not_native_bundle"

    # 2. Bundle carrying sanitized public cost census refuses as public projection.
    census_bundle = synthetic_bundle(tasks=native_tasks, native=True)
    census_bundle["paired_analysis"]["cost_summary"] = {"schema": PUBLIC_COST_CENSUS_SCHEMA}
    census_raw = synthetic_bytes(census_bundle)
    with pytest.raises(AnalysisRefusal) as census_exc:
        admit_bundle(census_raw, provenance=native_shaped_provenance(census_raw), tasks=native_tasks)
    assert census_exc.value.code == "analysis:public_projection_not_native_bundle"

    # 3. Failure receipt refuses explicitly inside admit_bundle.
    receipt = json.dumps({"schema": FAILURE_RECEIPT_SCHEMA}).encode("utf-8")
    with pytest.raises(AnalysisRefusal) as fail_exc:
        admit_bundle(receipt, provenance=native_shaped_provenance(receipt), tasks=native_tasks)
    assert fail_exc.value.code == "analysis:failure_receipt_not_completed_bundle"

    # 4. Synthetic schema with valid native provenance refuses on bundle schema/origin.
    syn_bundle = synthetic_bundle(tasks=native_tasks, native=False)
    syn_raw = synthetic_bytes(syn_bundle)
    with pytest.raises(AnalysisRefusal) as syn_exc:
        admit_bundle(syn_raw, provenance=native_shaped_provenance(syn_raw), tasks=native_tasks)
    assert syn_exc.value.code == "analysis:bundle_schema_or_origin"


def test_task_descriptor_raises_analysis_refusal_for_every_contract_violation() -> None:
    digests = {"task_sha256": digest_seed("a"), "retained_task_sha256": digest_seed("b")}
    with pytest.raises(AnalysisRefusal):
        TaskDescriptor(task_id="../bad", family=POSITIVE_FAMILIES[0], kind="positive",
                       grading_rule="ranked_positive", task_digests=digests)
    with pytest.raises(AnalysisRefusal):
        TaskDescriptor(task_id="syn-an-90", family=OTHER_FAMILY, kind="ambiguous",
                       grading_rule="absent_answer", task_digests=digests)
    with pytest.raises(AnalysisRefusal):
        TaskDescriptor(task_id="syn-an-91", family=OTHER_FAMILY, kind="unsupported",
                       grading_rule="safety_refusal", task_digests=digests)
    with pytest.raises(AnalysisRefusal):
        TaskDescriptor(task_id="syn-an-92", family=OTHER_FAMILY, kind="privacy",
                       grading_rule="ranked_positive", task_digests=digests)


def test_expected_hash_and_identity_forgery_refuse() -> None:
    raw = synthetic_bytes()
    with pytest.raises(AnalysisRefusal):
        _admit(raw, expected_raw_sha256="sha256:" + "f" * 64)
    for field in (
        "input_pin_sha256",
        "attempt_id",
        "evaluator_closure_sha256",
        "worker_closure_sha256",
        "declaration_source_closure_sha256",
    ):
        forged = "sha256:" + "e" * 64 if field.endswith("sha256") else "foreign-attempt"
        with pytest.raises(AnalysisRefusal):
            _admit(raw, **{field: forged})


def test_analyzer_identity_is_separate_from_original_study() -> None:
    raw = synthetic_bytes()
    result = analyze_retained(
        raw,
        provenance=synthetic_provenance(raw),
        tasks=synthetic_tasks(),
        analyzer_source_sha256=SYNTHETIC_ANALYZER,
    )
    assert result["bindings"]["analyzer_source_sha256"] == SYNTHETIC_ANALYZER
    assert result["bindings"]["evaluator_closure_sha256"] == SYNTHETIC_EVALUATOR
    with pytest.raises(AnalysisRefusal):
        analyze_retained(
            raw,
            provenance=synthetic_provenance(raw),
            tasks=synthetic_tasks(),
            analyzer_source_sha256=SYNTHETIC_EVALUATOR,
        )


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_key", "nonfinite", "bom", "bool_count", "oversize_string", "deep", "oversize_list"],
)
def test_bounded_strict_json_refuses(mutation: str) -> None:
    if mutation == "duplicate_key":
        raw = b'{"schema": 1, "schema": 2}'
    elif mutation == "nonfinite":
        raw = b'{"schema": NaN}'
    elif mutation == "bom":
        raw = "\ufeff{}".encode("utf-8")
    elif mutation == "bool_count":
        bundle = synthetic_bundle()
        bundle["paired_analysis"]["slot_count"] = True
        with pytest.raises(AnalysisRefusal):
            _admit(synthetic_bytes(bundle))
        return
    elif mutation == "oversize_string":
        raw = b'{"schema": "' + b"x" * 17000 + b'"}'
    elif mutation == "oversize_list":
        raw = json.dumps({"items": list(range(4100))}).encode("utf-8")
    else:
        raw = b'{"a":' * 40 + b"1" + b"}" * 40
    with pytest.raises(AnalysisRefusal):
        parse_bounded_json(raw)


@pytest.mark.parametrize("mutation", [
    "reorder_raw",
    "missing_slot",
    "duplicate_slot",
    "raw_nested_state",
    "raw_nested_reason",
    "task_digest",
    "family",
    "grading_rule",
    "report_schema",
    "paired_schema",
    "metric_join",
    "partial_span_metric",
    "mixed_slot_namespaces",
    "unknown_metric",
    "metric_bool",
    "forged_surface_match",
    "forged_capability_match",
    "state_count",
    "control_breakdown",
    "comparable_list",
    "false_floor",
    "checked_tokens_mismatch",
    "missing_vector_query_id",
    "acceptance",
])
def test_slot_joins_schemas_tokens_and_metrics_are_exact(mutation: str) -> None:
    bundle = copy.deepcopy(synthetic_bundle())
    paired, report = bundle["paired_analysis"], bundle["supplement_report"]
    if mutation == "reorder_raw":
        paired["raw_slots"][0], paired["raw_slots"][1] = (
            paired["raw_slots"][1], paired["raw_slots"][0]
        )
    elif mutation == "missing_slot":
        paired["raw_slots"].pop()
    elif mutation == "duplicate_slot":
        paired["raw_slots"][-1] = copy.deepcopy(paired["raw_slots"][0])
    elif mutation == "raw_nested_state":
        report["slots"][0]["execution_state"] = "refused"
    elif mutation == "raw_nested_reason":
        paired["raw_slots"][0]["reason"] = "synthetic_refusal"
    elif mutation == "task_digest":
        report["slots"][0]["task_digests"]["task_sha256"] = "sha256:" + "b" * 64
    elif mutation == "family":
        paired["raw_slots"][0]["family"] = "foreign_family"
    elif mutation == "grading_rule":
        report["slots"][0]["grading"]["grading_rule"] = "absent_answer"
    elif mutation == "report_schema":
        report["schema"] = "cloe-foreign-report-v1"
    elif mutation == "paired_schema":
        paired["schema"] = "cloe-foreign-paired-v1"
    elif mutation == "metric_join":
        paired["raw_slots"][0]["metrics"]["file_recall@5"] = 0.999999
    elif mutation == "partial_span_metric":
        report["slots"][0]["grading"]["metrics"]["recall@5"] = 0.5
        paired["raw_slots"][0]["metrics"]["recall@5"] = 0.5
    elif mutation == "mixed_slot_namespaces":
        dual = synthetic_bundle(include_span_metrics=True)
        report["slots"][0]["grading"]["metrics"] = dual["supplement_report"]["slots"][0]["grading"]["metrics"]
        paired["raw_slots"][0]["metrics"] = dual["paired_analysis"]["raw_slots"][0]["metrics"]
    elif mutation == "unknown_metric":
        report["slots"][0]["grading"]["metrics"]["secret_score@5"] = 0.5
        paired["raw_slots"][0]["metrics"]["secret_score@5"] = 0.5
    elif mutation == "metric_bool":
        report["slots"][0]["grading"]["metrics"]["file_recall@5"] = True
        paired["raw_slots"][0]["metrics"]["file_recall@5"] = True
    elif mutation == "forged_surface_match":
        report["slots"][0]["surface_expectation_matches"] = False
    elif mutation == "forged_capability_match":
        report["slots"][0]["capability_expectation_matches"] = False
    elif mutation == "state_count":
        paired["state_counts"]["completed"] -= 1
    elif mutation == "control_breakdown":
        paired["control_breakdown"]["privacy"]["refused"] -= 1
    elif mutation == "comparable_list":
        paired["comparable_positive_tasks"] = []
        paired["comparable_positive_count"] = 0
    elif mutation == "false_floor":
        paired["floor_met"] = True
        paired["criteria_proposal_allowed"] = True
    elif mutation == "checked_tokens_mismatch":
        paired["paired_tasks"][0]["checked_token_records"].append("syn-v1-forged")
    elif mutation == "missing_vector_query_id":
        paired["raw_slots"][9]["query_record_id"] = None
    else:
        bundle["scientific_acceptance_claimed"] = True
    with pytest.raises(AnalysisRefusal):
        _admit(synthetic_bytes(bundle))


def test_counted_worker_tokens_detect_forged_untruncated_claim() -> None:
    bundle = synthetic_bundle()
    # Mark task 1's v1 query record as truncated in counted worker evidence while
    # the bundle falsely claims token_truncated=False and comparable=True.
    tokens = synthetic_counted_tokens(bundle, truncated_records=frozenset({"syn-v1-q:syn-an-01"}))
    raw = synthetic_bytes(bundle)
    with pytest.raises(AnalysisRefusal) as exc_info:
        admit_bundle(
            raw,
            provenance=synthetic_provenance(raw),
            tasks=synthetic_tasks(),
            counted_worker_tokens=tokens,
        )
    assert exc_info.value.code == "analysis:token_truncation_join", exc_info.value.code

    # When the bundle honestly records truncation on syn-an-01, admission succeeds.
    honest = synthetic_bundle(truncated_tasks=frozenset({"syn-an-01"}))
    honest_tokens = synthetic_counted_tokens(
        honest, truncated_records=frozenset({"syn-v1-q:syn-an-01"})
    )
    honest_raw = synthetic_bytes(honest)
    admitted = admit_bundle(
        honest_raw,
        provenance=synthetic_provenance(honest_raw),
        tasks=synthetic_tasks(),
        counted_worker_tokens=honest_tokens,
    )
    assert "syn-an-01" not in admitted.comparable_positive_tasks
    assert admitted.counted_worker_tokens_verified is True


def test_unknown_fields_and_foreign_arms_refuse() -> None:
    bundle = copy.deepcopy(synthetic_bundle())
    bundle["unexpected_private_field"] = "must not flow through"
    with pytest.raises(AnalysisRefusal):
        _admit(synthetic_bytes(bundle))
    bundle = copy.deepcopy(synthetic_bundle())
    bundle["paired_analysis"]["raw_slots"][0]["arm"] = "foreign_arm"
    with pytest.raises(AnalysisRefusal):
        _admit(synthetic_bytes(bundle))


def test_refused_and_unavailable_slots_are_retained_not_dropped() -> None:
    key = ("syn-an-01", ARMS[3], 2)
    raw = synthetic_bytes(unavailable=frozenset({key}))
    admitted = _admit(raw)
    assert admitted.state_counts["unavailable"] == 13  #12 unsupported-control slots +1 positive
    assert len(admitted.slots) == 144
    assert "syn-an-01" not in admitted.comparable_positive_tasks
