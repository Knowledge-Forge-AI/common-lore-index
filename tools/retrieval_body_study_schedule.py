"""Finite BODY lifetime and byte forecasts for the final source draft.

The forecast is deliberately a pure calculation.  Callers must supply the
fresh closure-guard and custody measurements; this module never discovers a
smaller population to make a cap pass.
"""
from __future__ import annotations
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any
from retrieval_body_study_contracts import (
    BODY_CONTROLLER_SECONDS, BODY_FORECAST_SCHEMA, BODY_RUNTIME_ARCHIVE_BYTES,
    BODY_SCHEDULE_SCHEMA, BODY_SDK_SECONDS, INNER_SOURCE_GUARD_LIMIT,
    INNER_SOURCE_READ_LIMIT, MAX_PLAN_BYTES, MAX_PROVIDER_BYTES, MAX_READ_BYTES,
    MAX_REPORT_ARCHIVE_BYTES, MAX_REQUEST_STREAM_BYTES, MAX_RESPONSE_STREAM_BYTES,
    MAX_SCRATCH_BYTES, MAX_SOURCE_CLOSURE_BYTES, MAX_TRANSFER_BYTES,
    MAX_DOCKER_OPERATIONS, MAX_DOCKER_READ_BYTES, MAX_DOCKER_STREAM_FORECAST_BYTES,
    PG_TMPFS_BYTES, PARENT_ADMISSION_CHECKS, PARENT_ADMISSION_PINS,
    REGISTERED_INPUT_CHECKS, SOURCE_VERIFICATION_LIMIT, WORKER_AGGREGATE_SECONDS,
    WORKER_INIT_SECONDS, WORKER_MODEL_SECONDS, WORKER_READINESS_SECONDS,
    MAX_MODEL_WORKERS, BODY_POLL_SECONDS, refuse,
)
from retrieval_body_study_host_limits import derive_body_host_limits
from retrieval_body_study_evidence_budget import body_evidence_read_allowances

GUARD_METADATA_BYTES = 64 * 1024
MAX_GIT_OUTPUT_BYTES = 4 * 1024**2
GIT_STDERR_BYTES = 256 * 1024
MAX_METADATA_BYTES = 2 * 1024**2
MAX_WRAPPER_BYTES = 2 * 1024**2
MAX_PYTHON_BYTES = 256 * 1024**2
MAX_RECEIPT_BYTES = 128 * 1024
BODY_FACTORY_CREATES = 24
BODY_FACADE_REVALIDATIONS = 24
BODY_FULL_SNAPSHOT_READS = 4
BODY_INNER_INITIAL_SNAPSHOTS = 2
BODY_INNER_SEMANTIC_SNAPSHOTS = 4
BODY_INNER_PUBLICATION_SNAPSHOTS = 1
BODY_INNER_PREPARATION_SNAPSHOTS = 1
BODY_INNER_SELECTED_ADMISSION_SNAPSHOTS = 1
BODY_INNER_ADMISSION_CALLS = 48
BODY_INNER_SNAPSHOTS_PER_ADMISSION = 4
BODY_INNER_REGISTERED_SNAPSHOTS = (
    BODY_INNER_INITIAL_SNAPSHOTS + BODY_INNER_SEMANTIC_SNAPSHOTS
    + BODY_INNER_PUBLICATION_SNAPSHOTS + BODY_INNER_PREPARATION_SNAPSHOTS
    + BODY_INNER_SELECTED_ADMISSION_SNAPSHOTS + BODY_INNER_ADMISSION_CALLS * BODY_INNER_SNAPSHOTS_PER_ADMISSION
)
BODY_GIT_STATE_SNAPSHOTS = 4
BODY_GIT_REV_PARSE_COMMANDS_PER_PAIR = 4
BODY_GIT_LS_FILES_COMMANDS_PER_PAIR = 4
BODY_GIT_REV_PARSE_BYTES = 4096
BODY_GIT_CANDIDATE_INVENTORY_BYTES = 4 * 1024**2
BODY_ARTIFACT_BYTES = 182_037_753
BODY_WORKER_ENTRY_BYTES = 4 * 1024**2
_COMPAT_READ_KEYS = {
    "registered_body_input_rereads": "registered_host_snapshots_x4",
    "registered_factory_and_facade_snapshots_x192": "registered_inner_snapshots_x201",
    "inner_candidate_verifications_x32": "inner_whole_candidate_reads",
    "inner_evaluator_closure_hashes_x512": "inner_closure_guard_reads",
}
def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")
    return "sha256:" + hashlib.sha256(raw).hexdigest()
@dataclass(frozen=True)
class SchedulePhase:
    name: str
    budget_seconds: int
    window: str = "sdk"
    overlaps_worker_init: bool = False
    pre_request_for: str | None = None
    worker_representation: str | None = None
SDK_PHASES: tuple[SchedulePhase, ...] = (
    SchedulePhase("body_sdk_preflight", 180),
    SchedulePhase("body_inner_import_and_pg", 300),
    SchedulePhase("body_source_gate_publication", 120),
    SchedulePhase("body_pre_request_v1", 110, pre_request_for="canonical-node-v1"),
    SchedulePhase("body_worker_epoch_v1", WORKER_AGGREGATE_SECONDS,
                  worker_representation="canonical-node-v1"),
    SchedulePhase("body_between_workers", 110,
                  pre_request_for="canonical-node-public-text-v2"),
    SchedulePhase("body_worker_epoch_v2", WORKER_AGGREGATE_SECONDS,
                  worker_representation="canonical-node-public-text-v2"),
    SchedulePhase("body_query_aggregate_432", 300),
    SchedulePhase("body_quality_and_report", 130),
    SchedulePhase("body_sdk_cleanup", 150),
)
HOST_PRE_SECONDS = 300
HOST_POST_SECONDS = 240
EXPECTED_REPRESENTATIONS = ("canonical-node-v1", "canonical-node-public-text-v2")
@dataclass(frozen=True)
class WorkerWindow:
    representation: str
    pre_request_sdk_seconds: int = 110
    epoch_budget_seconds: int = WORKER_AGGREGATE_SECONDS
    initialization_seconds: int = WORKER_INIT_SECONDS
    readiness_seconds: int = WORKER_READINESS_SECONDS
    model_seconds: int = WORKER_MODEL_SECONDS
    admission_seconds: int = 120
    response_seconds: int = 600

    def validate(self) -> None:
        refuse(self.representation in EXPECTED_REPRESENTATIONS,
               "body schedule representation")
        refuse(self.pre_request_sdk_seconds <= self.readiness_seconds,
               "body schedule pre-request readiness")
        refuse(self.initialization_seconds == WORKER_INIT_SECONDS
               and self.readiness_seconds == WORKER_READINESS_SECONDS
               and self.model_seconds == WORKER_MODEL_SECONDS,
               "body schedule worker bounds")
        refuse(self.initialization_seconds + self.readiness_seconds
               + self.model_seconds == 1270, "body schedule worker phase sum")
        refuse(self.epoch_budget_seconds == WORKER_AGGREGATE_SECONDS
               and self.epoch_budget_seconds >= 1270 + self.admission_seconds,
               "body schedule aggregate")
def derive_worker_windows(phases: Sequence[SchedulePhase] = SDK_PHASES) -> tuple[WorkerWindow, WorkerWindow]:
    refuse(len(phases) == len(SDK_PHASES), "body schedule phase count")
    names = [phase.name for phase in phases]
    refuse(len(set(names)) == len(names), "body schedule duplicate phase")
    found: list[WorkerWindow] = []
    for index, phase in enumerate(phases):
        refuse(phase.window == "sdk" and phase.budget_seconds > 0
               and not phase.overlaps_worker_init, "body schedule phase")
        if phase.worker_representation is not None:
            refuse(index > 0 and phases[index - 1].pre_request_for
                   == phase.worker_representation, "body schedule worker pre-request")
            window = WorkerWindow(phase.worker_representation,
                                  phases[index - 1].budget_seconds,
                                  phase.budget_seconds)
            window.validate()
            found.append(window)
    refuse(tuple(window.representation for window in found) == EXPECTED_REPRESENTATIONS,
           "body schedule worker order")
    return found[0], found[1]
def build_schedule(phases: Sequence[SchedulePhase] = SDK_PHASES) -> dict[str, Any]:
    windows = derive_worker_windows(phases)
    host_limits = derive_body_host_limits()
    sdk_total = sum(phase.budget_seconds for phase in phases)
    refuse(sdk_total == BODY_SDK_SECONDS, "body schedule sdk lifetime")
    controller_total = HOST_PRE_SECONDS + sdk_total + HOST_POST_SECONDS
    refuse(controller_total <= BODY_CONTROLLER_SECONDS, "body schedule controller lifetime")
    record: dict[str, Any] = {
        "schema": BODY_SCHEDULE_SCHEMA, "phase": "CLOE-W5-BODY-STUDY1",
        "role": "body-study", "sdk_seconds": BODY_SDK_SECONDS,
        "controller_seconds": BODY_CONTROLLER_SECONDS,
        "host_pre_seconds": HOST_PRE_SECONDS, "host_post_seconds": HOST_POST_SECONDS,
        "sdk_total_seconds": sdk_total,
        "sdk_slack_seconds": BODY_SDK_SECONDS - sdk_total,
        "controller_total_seconds": controller_total,
        "controller_slack_seconds": BODY_CONTROLLER_SECONDS - controller_total,
        "sdk_phases": [{"name": phase.name, "budget_seconds": phase.budget_seconds,
                        "window": phase.window,
                        "overlaps_worker_init": phase.overlaps_worker_init,
                        "pre_request_for": phase.pre_request_for,
                        "worker_representation": phase.worker_representation}
                       for phase in phases],
        "worker_windows": [{"representation": item.representation,
                            "pre_request_sdk_seconds": item.pre_request_sdk_seconds,
                            "initialization": item.initialization_seconds,
                            "readiness": item.readiness_seconds,
                            "model": item.model_seconds,
                            "admission": item.admission_seconds,
                            "response": item.response_seconds,
                            "aggregate": item.epoch_budget_seconds,
                            "phase_sum": 1270,
                            "aggregate_slack": item.epoch_budget_seconds - 1270}
                           for item in windows],
        "worker_aggregate_seconds": WORKER_AGGREGATE_SECONDS,
        "worker_count": MAX_MODEL_WORKERS, "slot_count": 432,
        "prospective_records": 134, "poll_seconds": BODY_POLL_SECONDS,
        "model_poll_seconds": host_limits["model_poll_seconds"],
        "model_poll_count": host_limits["model_poll_count"],
        "docker_operation_limit": MAX_DOCKER_OPERATIONS, "docker_read_limit_bytes": MAX_DOCKER_READ_BYTES,
        "source_rechecks": SOURCE_VERIFICATION_LIMIT,
        "guarded_host_hops": host_limits["source_guard_calls"],
        "docker_operation_forecast": host_limits["docker_operation_calls"],
        "docker_stream_forecast": host_limits["docker_stream_bytes"],
        "docker_stream_allowance": MAX_DOCKER_STREAM_FORECAST_BYTES,
        "host_limit_terms": host_limits,
        "registered_input_checks": REGISTERED_INPUT_CHECKS,
        "registered_factory_creates": BODY_FACTORY_CREATES, "registered_facade_revalidations": BODY_FACADE_REVALIDATIONS,
        "parent_admission_checks": PARENT_ADMISSION_CHECKS,
        "parent_admission_pins": PARENT_ADMISSION_PINS,
        "source_memoization": False, "hard_preemption_claimed": False,
        "acquire": 0, "builds": 0, "pulls": 0, "materializations": 0,
        "runtime_exports": 0, "sdk_lifecycles": 1, "native_lifecycles": 1,
        "pg_lifecycles": 1, "inner_source_read_limit": INNER_SOURCE_READ_LIMIT,
        "inner_source_guard_limit": INNER_SOURCE_GUARD_LIMIT,
        "inner_guard_metadata_bytes": GUARD_METADATA_BYTES,
        "inner_report_name": "body-study-source-read-budget.json",
        "runner_control_prefixes": ["body_epoch", "generic_ready", "body_component_ack"],
        "runner_poll_seconds": BODY_POLL_SECONDS,
        "runner_marker_live_join": True, "runner_process_group_settlement": True,
        "runner_sdk_absence_recheck": True,
        "call_map": {"whole_candidate_reads": INNER_SOURCE_READ_LIMIT,
                     "closure_guard_reads": INNER_SOURCE_GUARD_LIMIT,
                     "host_source_hops": SOURCE_VERIFICATION_LIMIT,
                     "factory_create": BODY_FACTORY_CREATES,
                     "facade_revalidation": BODY_FACADE_REVALIDATIONS,
                     "registered_inner_snapshot_reads": BODY_INNER_REGISTERED_SNAPSHOTS,
                     "registered_initial": BODY_INNER_INITIAL_SNAPSHOTS,
                     "registered_semantic": BODY_INNER_SEMANTIC_SNAPSHOTS,
                     "registered_publication": BODY_INNER_PUBLICATION_SNAPSHOTS,
                     "registered_preparation": BODY_INNER_PREPARATION_SNAPSHOTS,
                     "registered_selected_admissions": BODY_INNER_SELECTED_ADMISSION_SNAPSHOTS,
                     "registered_admission_calls": BODY_INNER_ADMISSION_CALLS,
                     "registered_snapshots_per_admission": BODY_INNER_SNAPSHOTS_PER_ADMISSION,
                     "full_snapshot_reads_per_semantic_boundary": BODY_FULL_SNAPSHOT_READS,
                     "git_state_snapshots": BODY_GIT_STATE_SNAPSHOTS},
    }
    record["schedule_sha256"] = _digest(record)
    return record
def validate_schedule(schedule: Mapping[str, Any]) -> None:
    refuse(isinstance(schedule, Mapping) and dict(schedule) == build_schedule(),
           "body schedule reproduction")
@dataclass
class CooperativeBudget:
    total_seconds: int
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        refuse(type(self.total_seconds) is int and self.total_seconds > 0,
               "body budget total")
        self._started = float(self.clock())
        self._phase: str | None = None
        self._phase_started = self._started
        self._phase_seconds = 0.0

    @property
    def current_phase(self) -> str | None:
        return self._phase

    def remaining(self) -> float:
        return self._started + self.total_seconds - float(self.clock())

    def begin_phase(self, name: str, budget_seconds: int) -> None:
        refuse(bool(name) and type(budget_seconds) is int and budget_seconds > 0,
               "body budget phase")
        refuse(self.remaining() >= budget_seconds, "body budget phase cannot fit")
        self._phase, self._phase_started, self._phase_seconds = (
            name, float(self.clock()), float(budget_seconds))

    def checkpoint(self) -> None:
        refuse(self.remaining() > 0, "body budget expired")
        refuse(self._phase is None or float(self.clock()) - self._phase_started
               <= self._phase_seconds, "body budget phase expired")
def _bounded(name: str, value: int, maximum: int | None = None) -> int:
    refuse(type(value) is int and value >= 0, f"body forecast {name}")
    if maximum is not None:
        refuse(value <= maximum, f"body forecast {name}")
    return value


def build_byte_forecast(*, source_bytes: int, request_bytes: Sequence[int],
                        provider_bytes: int = 287_502, candidate_bytes: int = 0,
                        worker_bytes: int = 0, treatment_bytes: int = 0,
                        registered_input_bytes: int = 0,
                        guard_regular_file_bytes: int | None = None,
                        guard_file_visits: int | None = None,
                        guard_limit: int = INNER_SOURCE_GUARD_LIMIT,
                        whole_read_limit: int = INNER_SOURCE_READ_LIMIT,
                        git_custody_bytes: int = 0,
                        git_state_snapshots: int = BODY_GIT_STATE_SNAPSHOTS,
                        wrapper_bytes: int = 0, python_bytes: int = 0,
                        plan_overhead_bytes: int = 0,
                        legacy_inner_allowance_bytes: int = 0) -> dict[str, Any]:
    """Build an exact finite bound; omitted dynamic measurements are conservative.

    The old one-gigabyte evaluator allowance is zero by default.  A caller
    may provide it only as a separately named item, which keeps it visible in
    the digest and prevents counting the same guard bytes twice.
    """
    source_bytes = _bounded("source bytes", source_bytes, MAX_SOURCE_CLOSURE_BYTES)
    treatment_bytes = _bounded("treatment bytes", treatment_bytes, MAX_SOURCE_CLOSURE_BYTES)
    worker_bytes = _bounded("worker bytes", worker_bytes, MAX_SOURCE_CLOSURE_BYTES)
    registered_input_bytes = _bounded("registered input bytes", registered_input_bytes,
                                      MAX_SOURCE_CLOSURE_BYTES)
    if registered_input_bytes == 0:
        # Retain the old inert unit-call shape; prepared BODY plans always bind
        # the measured value, which must meet the 12 MiB floor.
        registered_input_bytes = 12 * 1024**2
    refuse(registered_input_bytes >= 12 * 1024**2, "body forecast registered input minimum")
    candidate_bytes = _bounded("candidate bytes", candidate_bytes, 128 * 1024**2)
    provider_bytes = _bounded("provider bytes", provider_bytes, MAX_PROVIDER_BYTES)
    refuse(len(request_bytes) == 2 and all(type(item) is int and 0 <= item <= MAX_REQUEST_STREAM_BYTES
                                           for item in request_bytes), "body forecast requests")
    closure_bytes = source_bytes + treatment_bytes
    refuse(closure_bytes <= MAX_SOURCE_CLOSURE_BYTES, "body forecast closure bytes")
    refuse(type(guard_limit) is int and type(whole_read_limit) is int
           and guard_limit == INNER_SOURCE_GUARD_LIMIT and whole_read_limit == INNER_SOURCE_READ_LIMIT,
           "body forecast inner read limits")
    if guard_regular_file_bytes is None:
        guard_regular_file_bytes = closure_bytes + worker_bytes
    if guard_file_visits is None:
        guard_file_visits = 0
    guard_regular_file_bytes = _bounded("guard regular bytes", guard_regular_file_bytes, 32 * 1024**2)
    guard_file_visits = _bounded("guard file visits", guard_file_visits, 100_000)
    guard_reserved = guard_regular_file_bytes + guard_file_visits + MAX_PLAN_BYTES + GUARD_METADATA_BYTES
    refuse(guard_reserved <= 40 * 1024**2, "body forecast guard reservation")
    git_custody_bytes = _bounded("git custody bytes", git_custody_bytes, 8 * 1024**3)
    git_state_snapshots = _bounded("git state snapshots", git_state_snapshots, 16)
    wrapper_bytes = _bounded("wrapper bytes", wrapper_bytes, 3 * MAX_WRAPPER_BYTES)
    python_bytes = _bounded("python bytes", python_bytes, MAX_PYTHON_BYTES)
    plan_overhead_bytes = _bounded("plan overhead bytes", plan_overhead_bytes, 16 * MAX_PLAN_BYTES)
    legacy_inner_allowance_bytes = _bounded("legacy inner allowance", legacy_inner_allowance_bytes, 1024**3)
    archive = BODY_RUNTIME_ARCHIVE_BYTES
    reads = {
        "runtime_archive_verify_send_receive_load_x4": 4 * (archive + 1),
        "plan_parent_receipt_and_reservation_reads": MAX_PLAN_BYTES + 128 * 1024,
        "parent_admission_receipt_rereads": PARENT_ADMISSION_PINS * (MAX_PLAN_BYTES + 1) * PARENT_ADMISSION_CHECKS,
        "admission_reservation_transitions": 3 * 128 * 1024,
        "body_admission_file_read": MAX_PLAN_BYTES,
        "artifact_host_and_worker_reservations_x10": 10 * BODY_ARTIFACT_BYTES,
        "initial_source_freeze": closure_bytes + worker_bytes + len(request_bytes),
        "source_rechecks": derive_body_host_limits()["source_guard_calls"] * closure_bytes,
        "source_guard_metadata": SOURCE_VERIFICATION_LIMIT * (guard_file_visits + 1),
        "worker_closure_rechecks": derive_body_host_limits()["source_guard_calls"] * worker_bytes,
        "registered_host_snapshots_x4": 2 * (registered_input_bytes + 1) * REGISTERED_INPUT_CHECKS,
        "registered_body_input_rereads": 2 * (registered_input_bytes + 1) * REGISTERED_INPUT_CHECKS,
        "registered_prepare_snapshots_x4": REGISTERED_INPUT_CHECKS * (registered_input_bytes + 1),
        "registered_inner_snapshots_x201": BODY_INNER_REGISTERED_SNAPSHOTS * (registered_input_bytes + 1),
        "worker_discovery_and_copy": 3 * worker_bytes + BODY_WORKER_ENTRY_BYTES,
        "whole_candidate_hashes_x5": 5 * (candidate_bytes + 4 * 1024**2),
        "inner_whole_candidate_reads": whole_read_limit * (candidate_bytes + MAX_PLAN_BYTES),
        "inner_candidate_verifications_x32": whole_read_limit * (candidate_bytes + MAX_PLAN_BYTES),
        "inner_closure_guard_reads": guard_limit * guard_reserved,
        "inner_evaluator_closure_hashes_x512": guard_limit * guard_reserved,
        "git_working_tree_custody": git_state_snapshots * git_custody_bytes,
        "git_metadata_and_index": git_state_snapshots * (
            BODY_GIT_REV_PARSE_COMMANDS_PER_PAIR * (BODY_GIT_REV_PARSE_BYTES + GIT_STDERR_BYTES + 2)
            + BODY_GIT_LS_FILES_COMMANDS_PER_PAIR * (MAX_GIT_OUTPUT_BYTES + GIT_STDERR_BYTES + 2)
            + 2 * MAX_METADATA_BYTES)
            + BODY_GIT_CANDIDATE_INVENTORY_BYTES + GIT_STDERR_BYTES + 2,
        "git_wrapper_python_and_plan": 2 * wrapper_bytes + 2 * (python_bytes + 1) + plan_overhead_bytes,
        "legacy_inner_closure_allowance": legacy_inner_allowance_bytes,
        "bounded_docker_stream_reservations": MAX_DOCKER_STREAM_FORECAST_BYTES,
        "runner_stdout_log": 8 * 1024**2,
        "provider_content": provider_bytes,
        "request_v1": request_bytes[0], "request_v2": request_bytes[1],
        "response_v1": MAX_RESPONSE_STREAM_BYTES, "response_v2": MAX_RESPONSE_STREAM_BYTES,
        **body_evidence_read_allowances(),
    }
    transfers = {
        "archive_host_to_sdk": archive, "archive_sdk_to_inner": archive,
        "source_send": closure_bytes, "request_v1": request_bytes[0],
        "request_v2": request_bytes[1], "response_v1": MAX_RESPONSE_STREAM_BYTES,
        "response_v2": MAX_RESPONSE_STREAM_BYTES, "report_export": MAX_REPORT_ARCHIVE_BYTES,
        "worker_source_copy": worker_bytes, "worker_entry_copy": BODY_WORKER_ENTRY_BYTES,
    }
    scratch = {"sdk_workspace": 4 * 1024**3, "sdk_scratch": 2 * 1024**3,
               "run_tmpfs": 64 * 1024**2, "sdk_shm": 64 * 1024**2,
               "pg_data": PG_TMPFS_BYTES, "pg_tmpfs": 32 * 1024**2, "pg_shm": 64 * 1024**2,
               "model_scratch": 768 * 1024**2, "model_tmpfs": 64 * 1024**2,
               "model_shm": 64 * 1024**2, "host_outputs": 256 * 1024**2}
    read_total = sum(value for key, value in reads.items() if key not in _COMPAT_READ_KEYS)
    transfer_total, scratch_total = map(sum, (transfers.values(), scratch.values()))
    refuse(read_total <= MAX_READ_BYTES, "body forecast read cap")
    refuse(transfer_total <= MAX_TRANSFER_BYTES, "body forecast transfer cap")
    refuse(scratch_total <= MAX_SCRATCH_BYTES, "body forecast scratch cap")
    host_limits = derive_body_host_limits()
    record: dict[str, Any] = {
        "schema": BODY_FORECAST_SCHEMA, "read_items": reads,
        "transfer_items": transfers, "scratch_items": scratch,
        "read_total_bytes": read_total, "read_cap_bytes": MAX_READ_BYTES,
        "transfer_total_bytes": transfer_total, "transfer_cap_bytes": MAX_TRANSFER_BYTES,
        "scratch_total_bytes": scratch_total, "scratch_cap_bytes": MAX_SCRATCH_BYTES,
        "source_recheck_limit": SOURCE_VERIFICATION_LIMIT,
        "inner_guard_limit": guard_limit, "inner_whole_read_limit": whole_read_limit, "docker_operation_limit": MAX_DOCKER_OPERATIONS, "docker_read_limit_bytes": MAX_DOCKER_READ_BYTES,
        "source_memoization": False, "source_bytes": source_bytes,
        "treatment_bytes": treatment_bytes, "closure_bytes": closure_bytes,
        "worker_bytes": worker_bytes, "registered_input_bytes": registered_input_bytes,
        "registered_inner_snapshot_reads": BODY_INNER_REGISTERED_SNAPSHOTS,
        "registered_snapshot_count": BODY_INNER_REGISTERED_SNAPSHOTS,
        "registered_snapshot_bytes": BODY_INNER_REGISTERED_SNAPSHOTS * (registered_input_bytes + 1),
        "candidate_bytes": candidate_bytes, "guard_regular_file_bytes": guard_regular_file_bytes,
        "guard_file_visits": guard_file_visits, "guard_reserved_bytes_per_read": guard_reserved,
        "git_custody_bytes": git_custody_bytes, "git_state_snapshots": git_state_snapshots,
        "archive_bytes": archive, "provider_bytes": provider_bytes,
        "wrapper_bytes": wrapper_bytes, "python_bytes": python_bytes, "plan_overhead_bytes": plan_overhead_bytes,
        "runtime_imports": 1, "runtime_exports": 0, "model_workers": 2,
        "physical_os_pg_costs": "unknown_not_measured_by_controlled_counters",
        "host_limits": host_limits,
        "read_item_aliases": dict(_COMPAT_READ_KEYS),
    }
    record["byte_forecast_sha256"] = _digest(record)
    return record

__all__ = ["BODY_FACTORY_CREATES", "BODY_FACADE_REVALIDATIONS", "BODY_FULL_SNAPSHOT_READS",
           "BODY_INNER_INITIAL_SNAPSHOTS", "BODY_INNER_SEMANTIC_SNAPSHOTS", "BODY_INNER_PUBLICATION_SNAPSHOTS",
           "BODY_INNER_PREPARATION_SNAPSHOTS", "BODY_INNER_SELECTED_ADMISSION_SNAPSHOTS", "BODY_INNER_ADMISSION_CALLS",
           "BODY_INNER_SNAPSHOTS_PER_ADMISSION", "BODY_INNER_REGISTERED_SNAPSHOTS", "BODY_GIT_STATE_SNAPSHOTS",
           "CooperativeBudget", "GUARD_METADATA_BYTES", "HOST_POST_SECONDS", "HOST_PRE_SECONDS", "SDK_PHASES",
           "SchedulePhase", "WorkerWindow", "build_byte_forecast", "build_schedule", "derive_worker_windows",
           "validate_schedule"]
