"""Synthetic complete-grid evidence checks; no relevance or execution qualification."""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Any

import pytest

from common_lore_index.retrieval.query_contracts import CapabilityState, FallbackDisclosure, MatchSurface, QueryMode
from common_lore_index_test_support.retrieval_supplement_report_fixture import (
    make_baseline_slot_result, make_functional_report_fixture,
)
from retrieval_evaluation_grading import ExecutionState
from retrieval_semantic_grading import grade_w5_task_slot
from retrieval_semantic_fusion import FileFusionResult
from retrieval_supplement_capture import bind_operation_capture
from retrieval_supplement_report import report_supplement, result_artifact_bytes
from retrieval_supplement_operation import PhaseRecorder
from retrieval_supplement_operation_values import PhaseIdentity
from retrieval_supplement_values import ARMS, canonical_bytes


@pytest.mark.parametrize("branch", ["native_metadata", "fts_fallback"])
def test_actual_surface_and_fallback_are_retained_and_compared(branch: str) -> None:
    fixture = make_functional_report_fixture()
    index = 0 if branch == "native_metadata" else 6
    first = fixture.results[index]
    envelope = first.baseline_envelope
    assert envelope is not None
    if branch == "native_metadata":
        target, surface = QueryMode.CURRENT_LITERAL_ENUMERATION, MatchSurface.STORED_METADATA
    else:
        target, surface = QueryMode.LITERAL_CAPTURED_TEXT, MatchSurface.CAPTURED_TEXT
    changed = replace(envelope, effective_mode=target, match_surface=surface,
        hits=tuple(replace(hit, match_surface=surface) for hit in envelope.hits),
        fallback=FallbackDisclosure(True, envelope.requested_mode, target, "functional_branch"))
    result = make_baseline_slot_result(fixture.catalog.tasks[0], first.slot, fixture.declaration,
        first.publication_pin, ExecutionState.COMPLETED, changed,
        native_path_surfaces=("stored_metadata",) if branch == "native_metadata" else None)
    rows = list(fixture.results)
    rows[index] = result
    row = report_supplement(fixture.catalog, fixture.declaration, rows)["slots"][index]
    assert row["execution_state"] == "completed" and row["capability_expectation_matches"] is False
    assert row["surface_observation"]["surface"] == surface.value
    assert row["surface_observation"]["effective_mode"] == target.value
    assert row["surface_observation"]["fallback_disclosure"]["reason_code"] == "functional_branch"
    assert row["surface_expectation_matches"] is (branch != "native_metadata")
    assert row["fallback_expectation_matches"] is (branch == "native_metadata")


def test_maintained_grader_preserves_representation_and_rejects_unknown_versions() -> None:
    fixture = make_functional_report_fixture()
    pin = fixture.results[3 * 3].publication_pin
    task = replace(fixture.catalog.tasks[0], comparability_stratum="body", intended_match_surface="captured_text")
    kwargs: dict[str, Any] = dict(arm_id="exact_vector", verified_pin=pin, execution_state=ExecutionState.COMPLETED)
    assert grade_w5_task_slot(task, **kwargs).execution_state is ExecutionState.UNAVAILABLE
    graded = grade_w5_task_slot(task, **kwargs, representation_version="canonical-node-public-text-v2")
    assert graded.execution_state is ExecutionState.COMPLETED
    assert graded.metrics["file_recall@10"] == 0.0
    with pytest.raises(ValueError, match="invalid_representation_version"):
        grade_w5_task_slot(task, **kwargs, representation_version="unapproved-representation")


@pytest.mark.parametrize("surfaces", [None, ("stored_metadata",)])
def test_rehashed_missing_or_inconsistent_native_scope_is_refused(surfaces: tuple[str, ...] | None) -> None:
    fixture = make_functional_report_fixture()
    first = fixture.results[0]
    assert first.baseline_envelope is not None
    artifact = canonical_bytes({"envelope": first.baseline_envelope.to_dict(), "native_path_surfaces": surfaces})
    reference = bind_operation_capture(first.capture_record_raw, slot=first.slot, declaration=fixture.declaration,
        result_artifact_raw=artifact)
    changed = replace(first, native_path_surfaces=surfaces, capture_reference_raw=reference.raw)
    with pytest.raises(ValueError, match="native_branch"):
        report_supplement(fixture.catalog, fixture.declaration, (changed, *fixture.results[1:]))


def test_vector_artifact_has_explicit_representation_and_refuses_foreign_arm() -> None:
    fixture = make_functional_report_fixture()
    row = fixture.results[12]
    assert row.slot.arm == ARMS[4]
    fusion = FileFusionResult(execution_state=ExecutionState.COMPLETED, refusal_code=None,
        unavailable_reason=None, failure_reason=None, provenance=row.publication_pin, rrf_k=60,
        hits=(), collapsed_by_arm={ARMS[4]: ()}, per_arm_raw_hit_counts={ARMS[4]: 0},
        per_arm_collapsed_file_counts={ARMS[4]: 0}, pre_cap_fused_file_count=0,
        work_candidates_inspected=0, truncated=False, partial_coverage=False)
    raw = result_artifact_bytes(replace(row, fusion_result=fusion))
    assert raw is not None
    value = json.loads(raw)
    assert value["arms"] == [{"arm_id": ARMS[4], "representation_version": "canonical-node-public-text-v2"}]
    assert "representation_version" not in value and "schema_version" not in value
    foreign = replace(fusion, collapsed_by_arm={ARMS[3]: ()}, per_arm_raw_hit_counts={ARMS[3]: 0})
    with pytest.raises(ValueError, match="serializer_arms"):
        result_artifact_bytes(replace(row, fusion_result=foreign))


def test_phase_costs_bind_actual_declared_slots() -> None:
    fixture = make_functional_report_fixture()
    declared = fixture.declaration.to_dict()
    slot = fixture.results[0].slot
    identity = PhaseIdentity(supplement_id=declared["supplement_id"], input_pin_sha256=declared["input_pin_sha256"],
        freeze_sha256=declared["freeze_sha256"], source_closure_sha256=declared["source_closure_sha256"], attempt_id=declared["attempt_id"],
        operation_id="unit-query", phase="query", task_id=slot.task_id, arm=slot.arm, repetition=slot.repetition)
    valid = PhaseRecorder()
    with valid.scope(identity):
        pass
    assert report_supplement(fixture.catalog, fixture.declaration, fixture.results, phase_records=valid.records)["cost_completeness"] == "reported_scopes_only"
    foreign = PhaseRecorder()
    with foreign.scope(replace(identity, task_id="unit-supplement-foreign")):
        pass
    with pytest.raises(ValueError, match="foreign_phase_slot"):
        report_supplement(fixture.catalog, fixture.declaration, fixture.results, phase_records=foreign.records)


def test_complete_grid_preserves_nonexecution_and_fixed_denominators() -> None:
    fixture = make_functional_report_fixture()
    report = report_supplement(fixture.catalog, fixture.declaration, fixture.results)
    assert report["slot_count"] == len(fixture.catalog.tasks) * 6 * 3
    assert report["slot_set_sha256"] == fixture.declaration.to_dict()["slot_set_sha256"]
    for arm in ARMS:
        summary = report["arm_summaries"][arm]
        assert summary["whole_declared_denominator"] == len(fixture.catalog.tasks) * 3
        assert sum(summary["states"].values()) == summary["whole_declared_denominator"]
    for arm in ARMS[3:]:
        assert report["arm_summaries"][arm]["states"]["not_run"] == len(fixture.catalog.tasks) * 3
        assert report["arm_summaries"][arm]["capability_mismatches"] > 0
    assert report["phase_costs"]["cross_layer_sum"] is None
    assert report["cost_completeness"] == "unobserved"
    assert report["repetitions_are_independent_samples"] is False
    assert not any(report[key] for key in ("native_acceptance", "scientific_acceptance", "production_acceptance", "held_out_acceptance"))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "reordered", "foreign_slot", "native_claim", "changed_reference"])
def test_invalid_cell_sets_are_refused_without_denominator_shrink(mutation: str) -> None:
    fixture = make_functional_report_fixture()
    rows = list(fixture.results)
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[1] = rows[0]
    elif mutation == "reordered":
        rows[0], rows[1] = rows[1], rows[0]
    elif mutation == "foreign_slot":
        rows[0] = replace(rows[0], slot=replace(rows[0].slot, task_id="unit-supplement-foreign"))
    elif mutation == "native_claim":
        rows[0] = replace(rows[0], delivery_kind="native_qualified")
    else:
        reference = json.loads(rows[0].capture_reference_raw)
        reference["outer_attempt_id"] = "foreign"
        rows[0] = replace(rows[0], capture_reference_raw=canonical_bytes(reference))
    with pytest.raises(ValueError):
        report_supplement(fixture.catalog, fixture.declaration, rows)


def test_result_artifact_hash_prevents_silent_hit_or_scope_changes() -> None:
    fixture = make_functional_report_fixture()
    first = fixture.results[0]
    envelope = first.baseline_envelope
    assert envelope is not None and envelope.hits
    changed = replace(first, baseline_envelope=replace(envelope, hits=()))
    with pytest.raises(ValueError, match="reference_changed"):
        report_supplement(fixture.catalog, fixture.declaration, (changed, *fixture.results[1:]))


def test_same_bare_exact_record_hash_has_distinct_qualified_references() -> None:
    fixture = make_functional_report_fixture()
    exact = [row for row in fixture.results if row.slot.task_id == fixture.catalog.tasks[0].task_id
             and row.slot.repetition == 1 and row.slot.arm in (ARMS[3], ARMS[4])]
    assert len(exact) == 2 and exact[0].capture_record_raw == exact[1].capture_record_raw
    assert exact[0].capture_reference_raw != exact[1].capture_reference_raw
    left, right = (json.loads(row.capture_reference_raw) for row in exact)
    assert left["capture_record_sha256"] == right["capture_record_sha256"]
    assert left["journal"]["representation"] != right["journal"]["representation"]
    assert left["journal"]["journal_id"] != right["journal"]["journal_id"]
    assert left["native_origin_or_execution_qualification"] is False


def test_failed_execution_remains_a_failed_slot() -> None:
    fixture = make_functional_report_fixture()
    first = fixture.results[0]
    assert first.baseline_envelope is not None
    envelope = replace(first.baseline_envelope, capability_state=CapabilityState.FAILED, hits=(),
                       scope_complete=False, unavailable_reason="functional_failure")
    failed = make_baseline_slot_result(fixture.catalog.tasks[0], first.slot, fixture.declaration,
                                      first.publication_pin, ExecutionState.FAILED, envelope)
    report = report_supplement(fixture.catalog, fixture.declaration, (failed, *fixture.results[1:]))
    assert report["slots"][0]["execution_state"] == "failed"
    assert report["slots"][0]["grading"]["grading_state"] == "ungraded"
    assert report["arm_summaries"][ARMS[0]]["states"]["failed"] == 1


def test_explicit_not_run_baseline_remains_ungraded() -> None:
    fixture = make_functional_report_fixture()
    first = fixture.results[0]
    assert first.baseline_envelope is not None
    envelope = replace(first.baseline_envelope, capability_state=CapabilityState.NOT_RUN, hits=(),
                       scope_complete=False, unavailable_reason="functional_not_run")
    pending = make_baseline_slot_result(fixture.catalog.tasks[0], first.slot, fixture.declaration,
                                       first.publication_pin, ExecutionState.NOT_RUN, envelope)
    report = report_supplement(fixture.catalog, fixture.declaration, (pending, *fixture.results[1:]))
    assert report["slots"][0]["execution_state"] == "not_run"
    assert report["slots"][0]["grading"]["grading_state"] == "ungraded"
    assert report["arm_summaries"][ARMS[0]]["states"]["not_run"] == 1


def test_store_advance_invalidates_even_self_consistent_capture_references() -> None:
    fixture = make_functional_report_fixture()
    fixture.cells["sqlite"].advance()
    with pytest.raises(ValueError):
        report_supplement(fixture.catalog, fixture.declaration, fixture.results)


def test_capture_binds_result_bytes_separately_from_old_schema() -> None:
    fixture = make_functional_report_fixture()
    first = fixture.results[0]
    assert first.baseline_envelope is not None
    record = json.loads(first.capture_record_raw)
    assert record["work"]["per_binding"] is None
    assert set(record["identity"]) == {"generation", "task_id", "arm_id", "repetition"}
    reference = bind_operation_capture(first.capture_record_raw, slot=first.slot, declaration=fixture.declaration,
        result_artifact_raw=canonical_bytes({"envelope": first.baseline_envelope.to_dict(),
                                            "native_path_surfaces": first.native_path_surfaces}))
    assert reference.raw == first.capture_reference_raw
    assert "representation" not in record["identity"]
