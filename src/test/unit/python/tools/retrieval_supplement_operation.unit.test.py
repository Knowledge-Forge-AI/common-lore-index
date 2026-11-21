"""Synthetic phase-accounting proof, with no calibration query or model."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from retrieval_supplement_operation import PhaseCaptureError, PhaseRecorder, summarize_phase_costs
from retrieval_supplement_operation_values import FieldSample, PhaseIdentity
from retrieval_supplement_values import ARMS, digest_json


def _identity(**changes: Any) -> PhaseIdentity:
    value = PhaseIdentity(
        "unit-supplement-cost", "sha256:" + "1" * 64, "sha256:" + "2" * 64,
        "sha256:" + "3" * 64, "unit-attempt", "unit-operation", "query",
        task_id="unit-supplement-01", arm=ARMS[0], repetition=1,
    )
    return replace(value, **changes)


def _clock(*values: int) -> Any:
    iterator = iter(values)
    return lambda: next(iterator)


def test_elapsed_is_one_phase_boundary_and_samples_are_not_summed() -> None:
    recorder = PhaseRecorder(clock=_clock(100, 160))
    with recorder.scope(_identity()) as scope:
        scope.observe(FieldSample("query_sample", "ns", 30, "observed", "adapter"))
        scope.observe(FieldSample("rows_returned", "rows", 2, "observed", "database"))
        scope.observe(FieldSample("wire_bytes", "bytes", None, "unknown", "transport"))
    row = recorder.records[0]
    assert row["elapsed_ns"] == 60
    assert row["elapsed_observation"] == "caller_phase_boundary"
    assert row["observation_complete"] is True
    summary = summarize_phase_costs(recorder.records)
    assert summary["cross_layer_sum"] is None
    assert summary["groups"][ARMS[0]][0]["elapsed_ns"] == 60
    assert summary["native_or_scientific_acceptance"] is False


def test_capture_runs_after_stop_and_has_no_assumed_zero_fields() -> None:
    steps: list[str] = []
    def clock() -> int:
        steps.append("clock")
        return len(steps) * 10
    def capture() -> list[FieldSample]:
        steps.append("capture")
        return []
    recorder = PhaseRecorder(clock=clock)
    with recorder.scope(_identity(), capture=capture):
        steps.append("body")
    assert steps == ["clock", "body", "clock", "capture"]
    assert recorder.records[0]["samples"] == []
    assert recorder.records[0]["generation_ledger_defaults_imported"] is False


def test_explicit_observed_zero_and_unknown_are_distinct() -> None:
    recorder = PhaseRecorder(clock=_clock(0, 0))
    with recorder.scope(_identity()) as scope:
        scope.observe(FieldSample("observed_statements", "statements", 0, "observed", "database"))
        scope.observe(FieldSample("unseen_storage", "bytes", None, "unknown", "container"))
    values = {row["name"]: row for row in recorder.records[0]["samples"]}
    assert values["observed_statements"]["value"] == 0
    assert values["observed_statements"]["state"] == "observed"
    assert values["unseen_storage"]["value"] is None


def test_shared_worker_is_retained_once_without_per_arm_allocation() -> None:
    identity = _identity(phase="inference", task_id=None, arm=None, repetition=None, shared_owner="unit-worker")
    recorder = PhaseRecorder(clock=_clock(1, 101))
    with recorder.scope(identity) as scope:
        scope.observe(FieldSample("worker_elapsed", "ns", 100, "shared", "model_worker", "unit-worker"))
    summary = summarize_phase_costs(recorder.records)
    assert list(summary["groups"]) == ["shared:unit-worker"]
    assert summary["record_count"] == 1
    assert summary["shared_cost_allocation"] == "retained_once_not_divided"
    with pytest.raises(ValueError, match="shared_owner"):
        _identity(phase="inference").validate()


def test_original_body_exception_survives_secondary_capture_failure() -> None:
    original = RuntimeError("synthetic-private-message-not-retained")
    def capture() -> list[FieldSample]:
        raise OSError("synthetic-private-capture-message")
    recorder = PhaseRecorder(clock=_clock(1, 7))
    with pytest.raises(RuntimeError) as caught:
        with recorder.scope(_identity(), capture=capture):
            raise original
    assert caught.value is original
    row = recorder.records[0]
    assert row["errors"] == {"body": "RuntimeError", "clock": None, "capture": "OSError"}
    assert row["state"] == "failed" and row["observation_complete"] is False
    assert "synthetic-private" not in str(row)
    summarize_phase_costs(recorder.records)


def test_capture_failure_is_sanitized_and_partial_capture_is_not_installed() -> None:
    recorder = PhaseRecorder(clock=_clock(1, 2, 3, 4))
    first = FieldSample("duplicate", "count", 1, "observed", "caller")
    with pytest.raises(PhaseCaptureError, match="supplement_phase_capture_failed"):
        with recorder.scope(_identity(), capture=lambda: [first, first]):
            pass
    assert recorder.records[0]["samples"] == []
    with recorder.scope(_identity(operation_id="unit-next")):
        pass
    assert len(recorder.records) == 2


@pytest.mark.parametrize("times", [(5, 4), (1, True)])
def test_invalid_stop_clock_is_unknown_and_original_failure_wins(times: tuple[int, int]) -> None:
    recorder = PhaseRecorder(clock=_clock(*times))
    original = LookupError("unretained")
    with pytest.raises(LookupError) as caught:
        with recorder.scope(_identity()):
            raise original
    assert caught.value is original
    row = recorder.records[0]
    assert row["elapsed_ns"] is None
    assert row["errors"]["clock"] == "ValueError"
    summarize_phase_costs(recorder.records)


def test_clock_start_failure_never_runs_body_and_leaves_recorder_available() -> None:
    recorder = PhaseRecorder(clock=_clock(-1, 1, 2))
    entered = False
    with pytest.raises(PhaseCaptureError, match="clock_failed"):
        with recorder.scope(_identity()):
            entered = True
    assert entered is False
    assert recorder.records[0]["elapsed_ns"] is None
    with recorder.scope(_identity(operation_id="unit-next")):
        pass
    assert len(recorder.records) == 2


def test_nested_scopes_and_duplicate_scope_reuse_are_refused() -> None:
    recorder = PhaseRecorder(clock=_clock(1, 2))
    with recorder.scope(_identity()):
        with pytest.raises(ValueError, match="nested"):
            with recorder.scope(_identity(operation_id="nested")):
                pytest.fail("nested body must not execute")
    with pytest.raises(ValueError, match="duplicate_phase_scope"):
        with recorder.scope(_identity()):
            pytest.fail("duplicate body must not execute")


@pytest.mark.parametrize("changes", [
    {"value": True}, {"value": -1}, {"value": float("inf")}, {"value": float("nan")},
    {"value": 2**100}, {"units": "seconds"}, {"state": "estimated"},
    {"state": "unknown"}, {"state": "unattributed"}, {"additive": True},
    {"state": "shared"}, {"shared_owner": "unexpected"}, {"name": "/unsafe/path"},
])
def test_sample_refuses_invalid_or_falsely_observed_fields(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(FieldSample("sample", "bytes", 1, "observed", "caller"), **changes).validate()


def test_records_are_defensive_and_duplicate_samples_fail() -> None:
    recorder = PhaseRecorder(clock=_clock(1, 2))
    sample = FieldSample("sample", "bytes", 1, "observed", "caller")
    with recorder.scope(_identity()) as scope:
        scope.observe(sample)
        with pytest.raises(ValueError, match="duplicate"):
            scope.observe(sample)
    copy = recorder.records[0]
    copy["identity"]["task_id"] = "changed"
    copy["samples"].clear()
    assert recorder.records[0]["identity"]["task_id"] == "unit-supplement-01"
    assert len(recorder.records[0]["samples"]) == 1


@pytest.mark.parametrize("field,value", [
    ("additive_with_other_layers", True), ("origin_or_execution_qualification", True),
    ("elapsed_ns", True), ("elapsed_ns", -1), ("observation_complete", False),
    ("generation_ledger_defaults_imported", True), ("unexpected", "payload"),
])
def test_summary_rejects_rehashed_false_claims(field: str, value: object) -> None:
    recorder = PhaseRecorder(clock=_clock(1, 2))
    with recorder.scope(_identity()):
        pass
    row = recorder.records[0]
    row[field] = value
    row["record_sha256"] = digest_json({key: item for key, item in row.items() if key != "record_sha256"})
    with pytest.raises(ValueError):
        summarize_phase_costs([row])


def test_summary_rejects_duplicate_and_foreign_attempt_records() -> None:
    recorder = PhaseRecorder(clock=_clock(1, 2, 3, 4))
    with recorder.scope(_identity()):
        pass
    with recorder.scope(_identity(attempt_id="foreign", operation_id="other")):
        pass
    with pytest.raises(ValueError, match="duplicate"):
        summarize_phase_costs([recorder.records[0], recorder.records[0]])
    with pytest.raises(ValueError, match="foreign_binding"):
        summarize_phase_costs(recorder.records)


def test_record_ceiling_prevents_unrecorded_work() -> None:
    recorder = PhaseRecorder(clock=_clock(1, 2), max_records=1)
    with recorder.scope(_identity()):
        pass
    with pytest.raises(ValueError, match="record_limit"):
        with recorder.scope(_identity(operation_id="second")):
            pytest.fail("unrecorded work must not begin")
