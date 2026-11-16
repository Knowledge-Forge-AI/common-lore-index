"""F1/F2 non-circular schedule proof, cooperative budgets and byte/scratch forecast."""

from __future__ import annotations

import pytest

from retrieval_supplement_study_contracts import (
    MAX_READ_BYTES,
    MAX_SCRATCH_BYTES,
    MAX_SOURCE_CLOSURE_BYTES,
    MAX_TRANSFER_BYTES,
    StudyRefusal,
)
from retrieval_supplement_study_schedule import (
    SDK_PHASES,
    CooperativeBudget,
    SchedulePhase,
    WorkerWindow,
    build_byte_forecast,
    build_schedule,
    derive_worker_windows,
    validate_schedule,
)


def test_nested_schedule_derives_windows_from_sdk_phases() -> None:
    schedule = build_schedule()
    validate_schedule(schedule)
    names = [row["name"] for row in schedule["sdk_phases"]]
    assert schedule["sdk_total_seconds"] == 4720 <= 4800
    assert schedule["controller_total_seconds"] == 5340 <= 5400
    assert schedule["existing_sdk_seconds"] == 1200
    assert schedule["existing_command_seconds"] == 300
    for required in (
        "sdk_import_runtime_once", "boundary_probes_inventory_inspect",
        "pg_socket_cluster_and_publish", "pre_request_v1_prepare_and_publish",
        "worker_epoch_v1", "pre_request_v2_recheck_and_publish",
        "worker_epoch_v2", "vector_batch_import_and_joins",
        "query_slots_432", "grading_and_report_export", "sdk_teardown_cleanup",
    ):
        assert required in names
    teardown = next(p for p in schedule["sdk_phases"] if p["name"] == "sdk_teardown_cleanup")
    assert teardown["budget_seconds"] == 300
    assert schedule["hard_native_preemption_claimed"] is False
    for row in schedule["worker_windows"]:
        assert row["early_setup_overlap_seconds"] == 0
        assert row["pre_request_sdk_seconds"] == 110
        assert row["aggregate_slack"] == 430


def test_schedule_refuses_early_overlap_cut_teardown_or_missing_pre_request() -> None:
    early_overlap = (
        SchedulePhase("sdk_import_runtime_once", 180, "sdk", overlaps_worker_init=True),
        *SDK_PHASES[1:],
    )
    with pytest.raises(StudyRefusal):
        derive_worker_windows(early_overlap)

    cut_teardown = (*SDK_PHASES[:-1], SchedulePhase("sdk_teardown_cleanup", 60, "sdk"))
    with pytest.raises(StudyRefusal):
        derive_worker_windows(cut_teardown)

    missing_v2_pre = tuple(
        p for p in SDK_PHASES if p.name != "pre_request_v2_recheck_and_publish"
    )
    with pytest.raises(StudyRefusal):
        derive_worker_windows(missing_v2_pre)


@pytest.mark.parametrize("kwargs", [
    {"initialization_seconds": 601}, {"readiness_seconds": 121},
    {"model_seconds": 551}, {"admission_seconds": 121},
    {"response_seconds": 601}, {"pre_request_sdk_seconds": 0},
    {"pre_request_sdk_seconds": 121}, {"early_setup_overlap_seconds": 10},
    {"epoch_budget_seconds": 1699},
])
def test_worker_window_refuses_grown_or_invalid(kwargs) -> None:
    with pytest.raises(StudyRefusal):
        WorkerWindow("canonical-node-v1", **kwargs).validate()


def test_cooperative_budget_real_default_clock_and_expiry() -> None:
    live_budget = CooperativeBudget(100)
    assert 0 < live_budget.remaining() <= 100.0
    now = [0.0]
    budget = CooperativeBudget(100, clock=lambda: now[0])
    budget.begin_phase("prepare", 40)
    now[0] = 41.0
    with pytest.raises(StudyRefusal):
        budget.checkpoint()
    with pytest.raises(StudyRefusal):
        CooperativeBudget(10, clock=lambda: 0.0).begin_phase("too-big", 11)


def test_byte_forecast_charges_host_gate_request_reads_and_scratch_ledger() -> None:
    forecast = build_byte_forecast(source_bytes=4096, request_bytes=(1000, 900))
    assert forecast["read_total_bytes"] <= MAX_READ_BYTES
    assert forecast["transfer_total_bytes"] <= MAX_TRANSFER_BYTES
    assert forecast["scratch_total_bytes"] <= MAX_SCRATCH_BYTES
    assert forecast["source_cap_bytes"] == MAX_SOURCE_CLOSURE_BYTES
    assert forecast["runtime_imports"] == 1 and forecast["runtime_exports"] == 0
    assert forecast["model_workers"] == 2
    assert "unknown" in forecast["physical_os_pg_costs"]
    assert set(forecast["read_items"]) >= {
        "artifact_pass_host_gate", "artifact_pass_v1", "artifact_pass_v2",
        "request_read_v1", "request_read_v2", "report",
    }
    assert set(forecast["scratch_items"]) >= {
        "pg_disposable_tmpfs", "sequential_worker_peak_scratch_and_tmp", "sdk_inner_docker_tmpfs",
    }


@pytest.mark.parametrize("kwargs", [
    {"source_bytes": -1},
    {"source_bytes": MAX_SOURCE_CLOSURE_BYTES + 1},
    {"provider_bytes": 4 * 1024**2 + 1},
    {"request_bytes": (8 * 1024**2 + 1, 1)},
])
def test_byte_forecast_refusals(kwargs) -> None:
    base: dict = {"source_bytes": 1, "request_bytes": (1, 1)}
    base.update(kwargs)
    with pytest.raises(StudyRefusal):
        build_byte_forecast(**base)


def test_byte_forecast_requires_two_requests() -> None:
    with pytest.raises(StudyRefusal):
        build_byte_forecast(source_bytes=1, request_bytes=(1,))
