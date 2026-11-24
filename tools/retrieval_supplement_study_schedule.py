"""F1/F2 non-circular nested schedule proof and controlled-byte/scratch forecast.

Every controlled operation is placed in an ordered phase with an explicit
sub-budget. Worker windows are derived directly from ``SDK_PHASES`` rather
than asserted from constants: neither worker may overlap early SDK/PostgreSQL
setup, each worker's pre-request SDK preparation is bounded below the 120s
readiness deadline, and each worker's full 1,270s phase sum plus 430s overhead
fits inside its own non-overlapped 1,700s epoch. ``sdk_teardown_cleanup``
retains its full 300s bound (SDK total 4,720s <= 4,800s; host wrapper
300 + 4,800 + 240 = 5,340s <= 5,400s).

The byte ledger charges five archive verification traversals, eleven artifact
read allowances (host checks, worker checks, offline installation and input),
request reads and transfers,
source/provider reads, responses, report export, and an explicit 8 GiB scratch
ledger. Budget checks are cooperative and never claim hard native preemption or
physical OS/PostgreSQL I/O measurement.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import time
from typing import Any

from retrieval_supplement_study_contracts import (
    ARTIFACT_TOTAL_BYTES,
    EXISTING_COMMAND_SECONDS,
    EXISTING_SDK_SECONDS,
    MAX_ARCHIVE_BYTES,
    MAX_MODEL_WORKERS,
    MAX_PROVIDER_BYTES,
    MAX_READ_BYTES,
    MAX_REPORT_ARCHIVE_BYTES,
    MAX_REQUEST_STREAM_BYTES,
    MAX_RESPONSE_STREAM_BYTES,
    MAX_RUNTIME_EXPORTS,
    MAX_RUNTIME_IMPORTS,
    MAX_SCRATCH_BYTES,
    MAX_SOURCE_CLOSURE_BYTES,
    MAX_TRANSFER_BYTES,
    MODEL_PHASE_SUM_SECONDS,
    SOURCE_VERIFICATION_LIMIT,
    PG_SHM_BYTES,
    PG_TMPFS_BYTES,
    PROVIDER_CONTENT_BYTES,
    REQUEST_ADMISSION_SECONDS,
    REQUEST_RESPONSE_SECONDS,
    RUNTIME_ARCHIVE_BYTES,
    STUDY_CONTROLLER_SECONDS,
    STUDY_SDK_SECONDS,
    WORKER_AGGREGATE_SECONDS,
    WORKER_INITIALIZATION_SECONDS,
    WORKER_MODEL_SECONDS,
    WORKER_READINESS_SECONDS,
    WORKER_SCRATCH_BYTES,
    WORKER_TMP_BYTES,
    refuse,
)
from retrieval_supplement_primitives import digest_json
from retrieval_supplement_poll_budget import host_poll_budget

SCHEDULE_SCHEMA = "cloe-supplement-study-schedule-v1"
FORECAST_SCHEMA = "cloe-supplement-study-byte-forecast-v1"
EXPECTED_REPRESENTATIONS = ("canonical-node-v1", "canonical-node-public-text-v2")


@dataclass(frozen=True)
class SchedulePhase:
    name: str
    budget_seconds: int
    window: str  # "host_pre" | "sdk" | "host_post"
    overlaps_worker_init: bool = False
    pre_request_for: str | None = None
    worker_representation: str | None = None


# Ordered SDK phases sum to 4,720 <= 4,800 (80s slack); sdk_teardown_cleanup is 300s.
SDK_PHASES: tuple[SchedulePhase, ...] = (
    SchedulePhase("sdk_import_runtime_once", 180, "sdk"),
    SchedulePhase("boundary_probes_inventory_inspect", 60, "sdk"),
    SchedulePhase("pg_socket_cluster_and_publish", 60, "sdk"),
    SchedulePhase("pre_request_v1_prepare_and_publish", 110, "sdk",
                  pre_request_for="canonical-node-v1"),
    SchedulePhase("worker_epoch_v1", WORKER_AGGREGATE_SECONDS, "sdk",
                  worker_representation="canonical-node-v1"),
    SchedulePhase("pre_request_v2_recheck_and_publish", 110, "sdk",
                  pre_request_for="canonical-node-public-text-v2"),
    SchedulePhase("worker_epoch_v2", WORKER_AGGREGATE_SECONDS, "sdk",
                  worker_representation="canonical-node-public-text-v2"),
    SchedulePhase("vector_batch_import_and_joins", 80, "sdk"),
    SchedulePhase("query_slots_432", 300, "sdk"),
    SchedulePhase("grading_and_report_export", 120, "sdk"),
    SchedulePhase("sdk_teardown_cleanup", 300, "sdk"),
)
HOST_PRE_SECONDS = 300
HOST_POST_SECONDS = 240
HOST_PHASES: tuple[SchedulePhase, ...] = (
    SchedulePhase("host_pre_admission", HOST_PRE_SECONDS, "host_pre"),
    SchedulePhase("sdk_lifetime_cap", STUDY_SDK_SECONDS, "sdk"),
    SchedulePhase("host_post_settlement", HOST_POST_SECONDS, "host_post"),
)


@dataclass(frozen=True)
class WorkerWindow:
    """One representation epoch derived from ``SDK_PHASES`` without circular constants."""

    representation: str
    pre_request_sdk_seconds: int = 110
    epoch_budget_seconds: int = WORKER_AGGREGATE_SECONDS
    initialization_seconds: int = WORKER_INITIALIZATION_SECONDS
    readiness_seconds: int = WORKER_READINESS_SECONDS
    model_seconds: int = WORKER_MODEL_SECONDS
    admission_seconds: int = REQUEST_ADMISSION_SECONDS
    response_seconds: int = REQUEST_RESPONSE_SECONDS
    early_setup_overlap_seconds: int = 0

    def validate(self) -> None:
        refuse(self.representation in EXPECTED_REPRESENTATIONS, "schedule:worker_representation")
        refuse(self.early_setup_overlap_seconds == 0, "schedule:early_setup_overlap_forbidden")
        refuse(self.initialization_seconds == WORKER_INITIALIZATION_SECONDS, "schedule:init_grown")
        refuse(self.readiness_seconds == WORKER_READINESS_SECONDS, "schedule:readiness_grown")
        refuse(self.model_seconds == WORKER_MODEL_SECONDS, "schedule:model_grown")
        refuse(self.admission_seconds == REQUEST_ADMISSION_SECONDS, "schedule:admission_grown")
        refuse(self.response_seconds == REQUEST_RESPONSE_SECONDS, "schedule:response_grown")
        refuse(0 < self.pre_request_sdk_seconds <= self.readiness_seconds,
               "schedule:pre_request_exceeds_readiness")
        phase_sum = self.initialization_seconds + self.readiness_seconds + self.model_seconds
        refuse(phase_sum == MODEL_PHASE_SUM_SECONDS, "schedule:phase_sum_drift")
        refuse(self.epoch_budget_seconds == WORKER_AGGREGATE_SECONDS, "schedule:epoch_budget_drift")
        refuse(phase_sum <= self.epoch_budget_seconds, "schedule:worker_aggregate_exceeded")
        overhead = self.epoch_budget_seconds - phase_sum
        refuse(overhead >= self.admission_seconds, "schedule:admission_overhead_unfunded")
        refuse(self.model_seconds <= self.response_seconds, "schedule:response_smaller_than_model")


def derive_worker_windows(phases: Sequence[SchedulePhase] = SDK_PHASES) -> tuple[WorkerWindow, WorkerWindow]:
    """Derive both sequential worker windows directly from the ordered SDK phase list."""
    refuse(len(phases) >= 7, "schedule:sdk_phases_incomplete")
    names = [phase.name for phase in phases]
    refuse(len(set(names)) == len(names), "schedule:duplicate_phase")
    for phase in phases:
        refuse(phase.window == "sdk" and phase.budget_seconds > 0, "schedule:invalid_sdk_phase")
        refuse(not phase.overlaps_worker_init, "schedule:early_setup_overlap_forbidden")
    derived: list[WorkerWindow] = []
    seen_worker_indices: list[int] = []
    for index, phase in enumerate(phases):
        if phase.worker_representation is None:
            continue
        refuse(index > 0, "schedule:worker_missing_pre_request_phase")
        preceding = phases[index - 1]
        refuse(preceding.pre_request_for == phase.worker_representation
               and preceding.worker_representation is None,
               "schedule:worker_pre_request_phase_mismatch")
        window = WorkerWindow(
            representation=phase.worker_representation,
            pre_request_sdk_seconds=preceding.budget_seconds,
            epoch_budget_seconds=phase.budget_seconds,
        )
        window.validate()
        derived.append(window)
        seen_worker_indices.append(index)
    refuse(tuple(w.representation for w in derived) == EXPECTED_REPRESENTATIONS,
           "schedule:worker_order_mismatch")
    refuse(seen_worker_indices[0] + 1 < seen_worker_indices[1], "schedule:workers_not_separated")
    teardown = phases[-1]
    refuse(teardown.name == "sdk_teardown_cleanup" and teardown.budget_seconds >= 300,
           "schedule:teardown_bound_cut")
    return derived[0], derived[1]


def build_schedule(phases: Sequence[SchedulePhase] = SDK_PHASES) -> dict[str, Any]:
    """Return the proved nested schedule derived from ``phases``; refuse any nonfitting total."""
    windows = derive_worker_windows(phases)
    sdk_total = sum(phase.budget_seconds for phase in phases)
    refuse(sdk_total <= STUDY_SDK_SECONDS, "schedule:sdk_exceeded")
    host_cap_total = sum(phase.budget_seconds for phase in HOST_PHASES)
    refuse(host_cap_total == HOST_PRE_SECONDS + STUDY_SDK_SECONDS + HOST_POST_SECONDS,
           "schedule:host_wrapper_drift")
    refuse(host_cap_total <= STUDY_CONTROLLER_SECONDS, "schedule:controller_exceeded")
    planned_controller_total = HOST_PRE_SECONDS + sdk_total + HOST_POST_SECONDS
    refuse(EXISTING_SDK_SECONDS == 1200 and EXISTING_COMMAND_SECONDS == 300,
           "schedule:existing_cohort_drift")
    record: dict[str, Any] = {
        "schema": SCHEDULE_SCHEMA,
        "sdk_seconds": STUDY_SDK_SECONDS,
        "controller_seconds": STUDY_CONTROLLER_SECONDS,
        "existing_sdk_seconds": EXISTING_SDK_SECONDS,
        "existing_command_seconds": EXISTING_COMMAND_SECONDS,
        "host_pre_seconds": HOST_PRE_SECONDS,
        "host_post_seconds": HOST_POST_SECONDS,
        "sdk_phases": [
            {"name": p.name, "budget_seconds": p.budget_seconds,
             "overlaps_worker_init": p.overlaps_worker_init,
             "pre_request_for": p.pre_request_for,
             "worker_representation": p.worker_representation}
            for p in phases
        ],
        "sdk_total_seconds": sdk_total,
        "sdk_slack_seconds": STUDY_SDK_SECONDS - sdk_total,
        "controller_total_seconds": host_cap_total,
        "controller_planned_seconds": planned_controller_total,
        "controller_slack_seconds": STUDY_CONTROLLER_SECONDS - host_cap_total,
        "worker_windows": [
            {"representation": w.representation,
             "pre_request_sdk_seconds": w.pre_request_sdk_seconds,
             "early_setup_overlap_seconds": w.early_setup_overlap_seconds,
             "initialization": w.initialization_seconds,
             "readiness": w.readiness_seconds,
             "model": w.model_seconds,
             "admission": w.admission_seconds,
             "response": w.response_seconds,
             "aggregate": w.epoch_budget_seconds,
             "phase_sum": MODEL_PHASE_SUM_SECONDS,
             "aggregate_slack": w.epoch_budget_seconds - MODEL_PHASE_SUM_SECONDS}
            for w in windows
        ],
        "host_poll_sub_budget": host_poll_budget(),
        "hard_native_preemption_claimed": False,
        "cooperative_budget_checks": True,
    }
    record["schedule_sha256"] = digest_json(record)
    return record


def validate_schedule(schedule: Mapping[str, Any]) -> None:
    rebuilt = build_schedule()
    refuse(dict(schedule) == rebuilt, "schedule:reproduction_mismatch")


class CooperativeBudget:
    """Remaining-budget checks between bounded operations (no preemption claim)."""

    def __init__(self, total_seconds: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        refuse(type(total_seconds) is int and total_seconds > 0, "budget:total")
        refuse(callable(clock), "budget:clock")
        self._total = float(total_seconds)
        self._clock = clock
        self._start = float(clock())
        self._phase: str | None = None
        self._phase_start = self._start
        self._phase_budget = 0.0

    @property
    def current_phase(self) -> str | None:
        return self._phase

    def remaining(self) -> float:
        return self._start + self._total - float(self._clock())

    def begin_phase(self, name: str, budget_seconds: int) -> None:
        refuse(type(name) is str and bool(name), "budget:phase_name")
        refuse(type(budget_seconds) is int and budget_seconds > 0, "budget:phase_seconds")
        refuse(self.remaining() > 0, "budget:expired_before_phase")
        refuse(float(budget_seconds) <= self.remaining(), "budget:phase_cannot_fit")
        self._phase = name
        self._phase_start = float(self._clock())
        self._phase_budget = float(budget_seconds)

    def checkpoint(self) -> None:
        refuse(self.remaining() > 0, "budget:cooperative_expiry")
        refuse(self._phase is None or float(self._clock()) - self._phase_start <= self._phase_budget,
               "budget:phase_exceeded")


def build_byte_forecast(
    *, source_bytes: int, request_bytes: Sequence[int],
    provider_bytes: int = PROVIDER_CONTENT_BYTES, candidate_bytes: int = 0,
) -> dict[str, Any]:
    """Charge every read/transfer/scratch hop explicitly; physical OS/PG costs stay unknown."""
    refuse(type(source_bytes) is int and 0 <= source_bytes <= MAX_SOURCE_CLOSURE_BYTES,
           "forecast:source_bytes")
    refuse(len(request_bytes) == MAX_MODEL_WORKERS
           and all(type(item) is int and 0 <= item <= MAX_REQUEST_STREAM_BYTES
                   for item in request_bytes),
           "forecast:request_bytes")
    refuse(type(provider_bytes) is int and 0 <= provider_bytes <= MAX_PROVIDER_BYTES,
           "forecast:provider_bytes")
    refuse(type(candidate_bytes) is int and 0 <= candidate_bytes <= 128 * 1024**2,
           "forecast:candidate_bytes")
    archive = RUNTIME_ARCHIVE_BYTES
    refuse(archive <= MAX_ARCHIVE_BYTES, "forecast:archive_cap")
    read_items = {
        "archive_verification_traversals_x5": 5 * archive,
        "artifact_pass_host_gate": ARTIFACT_TOTAL_BYTES,
        "artifact_sibling_host_verification_x2": 2 * ARTIFACT_TOTAL_BYTES,
        "artifact_pass_v1": 2 * ARTIFACT_TOTAL_BYTES,
        "artifact_pass_v2": 2 * ARTIFACT_TOTAL_BYTES,
        "offline_install_and_runtime_input_allowance_x4": 4 * ARTIFACT_TOTAL_BYTES,
        f"source_rechecks_max_{SOURCE_VERIFICATION_LIMIT}": SOURCE_VERIFICATION_LIMIT * source_bytes,
        "source_discovery_and_worker_copy": 4 * source_bytes,
        "whole_candidate_hashes_x4": 4 * candidate_bytes,
        "inner_candidate_hashes_x32": 32 * (candidate_bytes + 2 * 1024**2),
        "inner_evaluator_hashes_x128": 128 * source_bytes,
        "inner_admission_worker_closure_poll_allowance": 1024**3,
        "bounded_host_docker_stream_reservations": 2 * 1024**3,
        "bounded_protocol_polling_and_bookkeeping": 256 * 1024**2,
        "provider_content": provider_bytes,
        "request_read_v1": request_bytes[0],
        "request_read_v2": request_bytes[1],
        "response_v1": MAX_RESPONSE_STREAM_BYTES,
        "response_v2": MAX_RESPONSE_STREAM_BYTES,
        "report": MAX_REPORT_ARCHIVE_BYTES,
    }
    transfer_items = {
        "archive_host_to_sdk": archive,
        "archive_sdk_to_inner": archive,
        "request_v1": request_bytes[0],
        "request_v2": request_bytes[1],
        "response_v1": MAX_RESPONSE_STREAM_BYTES,
        "response_v2": MAX_RESPONSE_STREAM_BYTES,
        "report_export": MAX_REPORT_ARCHIVE_BYTES,
        "source_send": source_bytes,
    }
    scratch_items = {
        "sdk_inner_docker_tmpfs": 4 * 1024**3,
        "sdk_workspace_archive_inputs_and_reports_tmpfs": 2 * 1024**3,
        "sdk_run_tmpfs": 64 * 1024**2,
        "host_retained_runtime_copy": archive,
        "pg_disposable_tmpfs": PG_TMPFS_BYTES,
        "pg_shm": PG_SHM_BYTES,
        "pg_tmp": 32 * 1024**2,
        "sequential_worker_peak_scratch_and_tmp": WORKER_SCRATCH_BYTES + WORKER_TMP_BYTES,
        "host_both_exchange_outputs_and_logs": 192 * 1024**2,
        "phase_metadata_and_frozen_source": 96 * 1024**2,
    }
    read_total = sum(read_items.values())
    transfer_total = sum(transfer_items.values())
    scratch_total = sum(scratch_items.values())
    refuse(read_total <= MAX_READ_BYTES, "forecast:read_cap_exceeded")
    refuse(transfer_total <= MAX_TRANSFER_BYTES, "forecast:transfer_cap_exceeded")
    refuse(scratch_total <= MAX_SCRATCH_BYTES, "forecast:scratch_cap_exceeded")
    record: dict[str, Any] = {
        "schema": FORECAST_SCHEMA,
        "read_items": read_items,
        "transfer_items": transfer_items,
        "scratch_items": scratch_items,
        "read_total_bytes": read_total,
        "read_cap_bytes": MAX_READ_BYTES,
        "transfer_total_bytes": transfer_total,
        "transfer_cap_bytes": MAX_TRANSFER_BYTES,
        "scratch_total_bytes": scratch_total,
        "scratch_cap_bytes": MAX_SCRATCH_BYTES,
        "scratch_accounting": "simultaneous finite filesystem quotas plus retained host files; inner archive and reports are contained by SDK workspace quota",
        "source_recheck_limit": 1024,
        "candidate_bytes": candidate_bytes,
        "source_cap_bytes": MAX_SOURCE_CLOSURE_BYTES,
        "archive_cap_bytes": MAX_ARCHIVE_BYTES,
        "provider_cap_bytes": MAX_PROVIDER_BYTES,
        "runtime_imports": MAX_RUNTIME_IMPORTS,
        "runtime_exports": MAX_RUNTIME_EXPORTS,
        "model_workers": MAX_MODEL_WORKERS,
        "physical_os_pg_costs": "unknown_not_measured_by_controlled_counters",
    }
    record["byte_forecast_sha256"] = digest_json(record)
    return record


__all__ = [
    "EXPECTED_REPRESENTATIONS", "FORECAST_SCHEMA", "HOST_PHASES", "HOST_POST_SECONDS",
    "HOST_PRE_SECONDS", "SCHEDULE_SCHEMA", "SDK_PHASES", "CooperativeBudget",
    "SchedulePhase", "WorkerWindow", "build_byte_forecast", "build_schedule",
    "derive_worker_windows", "validate_schedule",
]
