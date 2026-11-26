"""Layered costs and closed-allowlist output: PhaseRecorder elapsed, samples, no leaks."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from common_lore_index_test_support.retrieval_supplement_analysis_fixtures import (
    SYNTHETIC_ANALYZER,
    synthetic_bundle,
    synthetic_bytes,
    synthetic_cost_record,
    synthetic_provenance,
    synthetic_tasks,
)
from retrieval_supplement_analysis_admission import admit_bundle
from retrieval_supplement_analysis_costs import cost_analysis
from retrieval_supplement_analysis_output import analyze_retained, render_report
from retrieval_supplement_analysis_values import AnalysisRefusal
from retrieval_supplement_primitives import ARMS, digest_json


def _costs(**kwargs: Any) -> dict[str, Any]:
    tasks = kwargs.get("tasks") or synthetic_tasks()
    raw = synthetic_bytes(**kwargs)
    admitted = admit_bundle(raw, provenance=synthetic_provenance(raw), tasks=tasks)
    return cost_analysis(admitted)


def test_per_slot_phase_recorder_elapsed_ns_without_samples_populates_per_arm_query() -> None:
    result = _costs(emit_slot_query_costs=True)
    assert result["record_count"] == 8 * 6 * 3
    for arm_index, arm in enumerate(ARMS):
        arm_row = result["per_arm_query"][arm]
        assert arm_row["status"] == "available"
        assert arm_row["reason"] is None
        assert arm_row["observation_denominator"] == 24
        assert arm_row["sample_denominator"] == 24
        assert arm_row["elapsed_denominator"] == 24
        assert arm_row["unknown_elapsed_count"] == 0
        assert arm_row["minimum_elapsed_ns"] == 1000 + 50 * arm_index + 10
        assert arm_row["maximum_elapsed_ns"] == 1000 + 50 * arm_index + 30
        assert arm_row["mean_elapsed_ns"] == pytest.approx(1000 + 50 * arm_index + 20)
        assert arm_row["field_samples"] == {}
        assert arm_row["additive_with_field_samples"] is False


def test_shared_cost_retained_once_and_never_allocated_to_arms() -> None:
    records = (
        synthetic_cost_record(
            shared_owner="synthetic-worker",
            phase="inference",
            sample_state="shared",
            sample_name="worker_elapsed",
            operation_id="synthetic-op-shared",
        ),
        synthetic_cost_record(
            arm=ARMS[0],
            samples=(
                {
                    "name": "rows_returned",
                    "units": "rows",
                    "value": 4,
                    "state": "observed",
                    "layer": "database",
                    "shared_owner": None,
                    "additive": False,
                },
                {
                    "name": "observed_statements",
                    "units": "statements",
                    "value": 1,
                    "state": "observed",
                    "layer": "database",
                    "shared_owner": None,
                    "additive": False,
                },
            ),
            operation_id="synthetic-op-arm",
        ),
    )
    result = _costs(cost_records=records)
    assert result["shared_observations_retained_once"] is True
    assert result["shared_cost_allocated_to_arms"] is False
    assert result["cross_layer_sum"] is None
    assert result["non_additive_across_layers"] is True
    shared = [row for row in result["observations"] if row["scope"] == "shared"]
    assert len(shared) == 1
    arm0 = result["per_arm_query"][ARMS[0]]
    assert arm0["sample_denominator"] == 1
    assert "rows_returned:database:rows" in arm0["field_samples"]
    assert "observed_statements:database:statements" in arm0["field_samples"]
    for arm in ARMS[1:]:
        assert result["per_arm_query"][arm]["sample_denominator"] == 0
        assert result["per_arm_query"][arm]["status"] == "unavailable"


def test_unknown_elapsed_and_unknown_samples_stay_unknown_never_zero() -> None:
    records = (
        synthetic_cost_record(
            arm=ARMS[1],
            elapsed_ns=None,
            errors={"body": None, "clock": "ValueError", "capture": None},
            sample_state="unknown",
            sample_name="wire_bytes",
        ),
    )
    result = _costs(cost_records=records)
    arm1 = result["per_arm_query"][ARMS[1]]
    assert arm1["observation_denominator"] == 1
    assert arm1["elapsed_denominator"] == 0
    assert arm1["unknown_elapsed_count"] == 1
    assert arm1["status"] == "unavailable"
    assert arm1["mean_elapsed_ns"] is None
    assert result["unknown_sample_count"] == 1
    assert result["unknown_elapsed_count"] == 1
    assert result["physical_postgresql_cost"] == "unknown"
    assert result["physical_rss_cost"] == "unknown"
    assert result["cost_completeness_upgraded"] is False


@pytest.mark.parametrize("mutation", [
    "cross_sum",
    "additive_sample",
    "foreign_attempt",
    "foreign_slot",
    "duplicate_scope",
    "inference_per_arm",
    "bad_layer",
    "completeness",
    "cost_summary_divergence",
    "rehashed_arbitrary_state",
    "rehashed_bool_elapsed",
    "rehashed_none_elapsed_without_clock_error",
    "rehashed_incomplete_without_error",
    "rehashed_capture_before_stop",
])
def test_forged_cost_claims_and_inconsistent_records_refuse(mutation: str) -> None:
    record = synthetic_cost_record(arm=ARMS[0], sample_state="observed")
    if mutation == "duplicate_scope":
        raw = synthetic_bytes(cost_records=(record, copy.deepcopy(record)))
        with pytest.raises(AnalysisRefusal):
            admit_bundle(raw, provenance=synthetic_provenance(raw), tasks=synthetic_tasks())
        return

    bundle = synthetic_bundle(cost_records=(record,))
    phase_costs = bundle["supplement_report"]["phase_costs"]
    stored = phase_costs["groups"][ARMS[0]][0]
    if mutation == "cross_sum":
        phase_costs["cross_layer_sum"] = 123
        bundle["paired_analysis"]["cost_summary"] = copy.deepcopy(phase_costs)
    elif mutation == "additive_sample":
        stored["samples"][0]["additive"] = True
        stored["record_sha256"] = digest_json({k: v for k, v in stored.items() if k != "record_sha256"})
        bundle["paired_analysis"]["cost_summary"] = copy.deepcopy(phase_costs)
    elif mutation == "foreign_attempt":
        stored["identity"]["attempt_id"] = "foreign-attempt"
        stored["record_sha256"] = digest_json({k: v for k, v in stored.items() if k != "record_sha256"})
        bundle["paired_analysis"]["cost_summary"] = copy.deepcopy(phase_costs)
    elif mutation == "foreign_slot":
        stored["identity"]["task_id"] = "syn-an-99"
        stored["record_sha256"] = digest_json({k: v for k, v in stored.items() if k != "record_sha256"})
        bundle["paired_analysis"]["cost_summary"] = copy.deepcopy(phase_costs)
    elif mutation == "inference_per_arm":
        stored["identity"]["phase"] = "inference"
        stored["record_sha256"] = digest_json({k: v for k, v in stored.items() if k != "record_sha256"})
        bundle["paired_analysis"]["cost_summary"] = copy.deepcopy(phase_costs)
    elif mutation == "bad_layer":
        stored["samples"][0]["layer"] = "private_runtime_layer"
        stored["record_sha256"] = digest_json({k: v for k, v in stored.items() if k != "record_sha256"})
        bundle["paired_analysis"]["cost_summary"] = copy.deepcopy(phase_costs)
    elif mutation == "completeness":
        bundle["supplement_report"]["cost_completeness"] = "complete"
    elif mutation == "cost_summary_divergence":
        bundle["paired_analysis"]["cost_summary"]["record_count"] = 0
        bundle["paired_analysis"]["cost_summary"]["groups"] = {}
    elif mutation == "rehashed_arbitrary_state":
        stored["state"] = "SELECT_secret_from_pg"
        stored["record_sha256"] = digest_json({k: v for k, v in stored.items() if k != "record_sha256"})
        bundle["paired_analysis"]["cost_summary"] = copy.deepcopy(phase_costs)
    elif mutation == "rehashed_bool_elapsed":
        stored["elapsed_ns"] = True
        stored["record_sha256"] = digest_json({k: v for k, v in stored.items() if k != "record_sha256"})
        bundle["paired_analysis"]["cost_summary"] = copy.deepcopy(phase_costs)
    elif mutation == "rehashed_none_elapsed_without_clock_error":
        stored["elapsed_ns"] = None
        stored["elapsed_observation"] = "unknown"
        stored["record_sha256"] = digest_json({k: v for k, v in stored.items() if k != "record_sha256"})
        bundle["paired_analysis"]["cost_summary"] = copy.deepcopy(phase_costs)
    elif mutation == "rehashed_incomplete_without_error":
        stored["observation_complete"] = False
        stored["record_sha256"] = digest_json({k: v for k, v in stored.items() if k != "record_sha256"})
        bundle["paired_analysis"]["cost_summary"] = copy.deepcopy(phase_costs)
    else:
        stored["capture_after_clock_stop"] = False
        stored["record_sha256"] = digest_json({k: v for k, v in stored.items() if k != "record_sha256"})
        bundle["paired_analysis"]["cost_summary"] = copy.deepcopy(phase_costs)

    raw = synthetic_bytes(bundle)
    with pytest.raises(AnalysisRefusal):
        admit_bundle(raw, provenance=synthetic_provenance(raw), tasks=synthetic_tasks())


def test_open_sample_names_shared_owners_and_reasons_never_leak_to_output() -> None:
    records = (
        synthetic_cost_record(
            shared_owner="private.host.container-99",
            phase="inference",
            sample_state="shared",
            sample_name="private.endpoint.secret_metric",
            operation_id="synthetic-op-private-token",
        ),
    )
    bundle = synthetic_bundle(cost_records=records)
    bundle["paired_analysis"]["raw_slots"][0]["reason"] = "SELECT secret FROM /private/path"
    bundle["supplement_report"]["slots"][0]["reason"] = "SELECT secret FROM /private/path"
    raw = synthetic_bytes(bundle)
    result = analyze_retained(
        raw,
        provenance=synthetic_provenance(raw),
        tasks=synthetic_tasks(),
        analyzer_source_sha256=SYNTHETIC_ANALYZER,
    )
    encoded = json.dumps(result, sort_keys=True)
    rendered = render_report(result)
    for forbidden in (
        "SELECT",
        "secret",
        "/private/path",
        "private.endpoint",
        "secret_metric",
        "container-99",
        "private.host",
    ):
        assert forbidden not in encoded
        assert forbidden not in rendered
    obs = result["costs"]["observations"][0]
    assert obs["shared_owner_category"] == "shared_owner"
    assert obs["samples"][0]["name"] == "other_registered_sample"
