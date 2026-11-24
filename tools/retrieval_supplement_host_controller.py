"""Actual source-bound host orchestration for the selected W5 supplement.

The host owns the canonical SDK runner and two sequential sibling workers.
The SDK owns its imported PostgreSQL runtime, accepted stores and 432 query
slots. No callback-only counter controller, old calibration input factory, or
host PostgreSQL fallback participates in this entrypoint.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import sys
import time
from typing import Any

from retrieval_supplement_epoch import EPOCH_PREFIX, decode_epoch_line
from retrieval_supplement_host_admission import PhysicalStudyAdmission, read_parent_admission
from retrieval_supplement_host_inventory import snapshot as docker_snapshot
from retrieval_supplement_host_meter import HostMeter
from retrieval_supplement_host_report import validate_exported_study
from retrieval_supplement_study_inputs import load_study_inputs, require_study_manifest
from retrieval_supplement_inner_read_report import validate_exported_source_read_receipt
from retrieval_supplement_host_source import StudySourceGuard, private_directory, put_private
from retrieval_supplement_runner_channel import RunnerChannel
from retrieval_supplement_runner_lease import RunnerLease
from retrieval_supplement_runner_output import RunnerOutput
from retrieval_supplement_runner_process import RunnerLaunchFailure, RunnerProcess, RunnerProcessConfig, launch_runner
from retrieval_supplement_runner_settlement import settle_runner
from retrieval_supplement_study_contracts import (
    SDK_IMAGE_SHA256, STUDY_PHASE, STUDY_SELECTOR, refuse,
    MAX_REQUEST_STREAM_BYTES,
)
from retrieval_supplement_study_plan import (
    MAX_PLAN_BYTES, admission_file_for_plan, issue_admission, plan_digest, read_reservation,
    settle_reservation, validate_study_plan, project_inner_study_plan,
)
from retrieval_supplement_primitives import canonical_bytes, digest_bytes, strict_json
from retrieval_supplement_study_schedule import build_byte_forecast, build_schedule
from retrieval_supplement_study_closure import verify_study_artifact_gate
from runner_pgvector_budget import Allocation
from offline_qualification_files import private_file, read_regular
from offline_qualification_values import STUDY_CAPS, ENV_OFFLINE_PLAN


@dataclass(frozen=True)
class ActualStudyConfig:
    source_root: Path = field(repr=False)
    plan_path: Path = field(repr=False)
    allocation_path: Path = field(repr=False)
    attempt_root: Path = field(repr=False)
    artifact_root: Path = field(repr=False)
    python: Path = field(repr=False)
    parent_admission: Path = field(repr=False)
    parent_admission_sha256: str
    environment: Mapping[str, str] = field(repr=False)


def canonical_argv(config: ActualStudyConfig) -> tuple[str, ...]:
    return (str(config.python), "-I", "-B", "tools/run_tests.py", "--suite", "int", "--jobs", "1",
        "--no-coverage", "--pg-container-port", "55433", "--pg-test-profile", "pgvector",
        "--report-dir", str(config.attempt_root / "report"), "--", "-s", STUDY_SELECTOR)


def child_environment(config: ActualStudyConfig) -> dict[str, str]:
    allowed = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
        "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}
    # The supervisor and its child must address the same already-selected
    # daemon. Caller input cannot redirect one side of the ownership join.
    for key in allowed:
        refuse(config.environment.get(key) == os.environ.get(key), "host_controller:environment_drift")
    environment = {key: value for key, value in config.environment.items() if key in allowed}
    temporary = config.attempt_root / "tmp"
    environment.update({ENV_OFFLINE_PLAN: str(config.plan_path), "TMPDIR": str(temporary),
        "TMP": str(temporary), "TEMP": str(temporary), "PYTHONPYCACHEPREFIX": str(temporary / "pycache"),
        "REPOMAP_TEST_SCRATCH_ROOT": str(temporary / "resources"), "REPOMAP_TEST_PROJECT": "cloe_public",
        "REPOMAP_TEST_PHASE": STUDY_PHASE, "GIT_OPTIONAL_LOCKS": "0", "GOTOOLCHAIN": "local",
        "GOPROXY": "off", "PIP_NO_INDEX": "1", "PIP_CACHE_DIR": str(temporary / "pip"),
        "GOCACHE": str(temporary / "go"), "GOTMPDIR": str(temporary)})
    return environment


def _require_fresh_resources(config: ActualStudyConfig, meter: HostMeter, plan: Mapping[str, Any]) -> None:
    """Allow prepaid bytes, but no consumed execution or foreign resource paths."""
    refuse(Path(plan["allocation_path"]) == config.allocation_path
           and config.plan_path.parent == config.allocation_path.parent,
           "host_controller:allocation_join")
    for name in ("archive", "reservation"):
        path = Path(plan["archive"]["path"] if name == "archive" else plan["reservation_path"])
        private_file(path)
        refuse(path.parent == config.plan_path.parent, "host_controller:private_resource_join")
    reservation = read_reservation(Path(plan["reservation_path"]))
    refuse(reservation["plan_sha256"] == plan_digest(plan)
           and reservation["attempt_id"] == plan["attempt_id"]
           and reservation["consumed"] is False and reservation["cleanup"] == "unstarted"
           and all(reservation[key] == 0 for key in ("runtime_imports", "runtime_exports", "model_workers")),
           "host_controller:reservation_not_fresh")
    with meter.allocation.session() as allocation:
        refuse(all(value == 0 for key, value in allocation.used.items()
                   if key not in {"read_bytes", "transfer_bytes"})
               and set(allocation.records) <= {"derived_archive"}, "host_controller:allocation_not_fresh")
        refuse(not allocation.records or allocation.records["derived_archive"] == plan["archive"],
               "host_controller:allocation_archive_join")


def _verify_runtime_archive(plan: Mapping[str, Any], meter: HostMeter) -> None:
    descriptor = plan["archive"]
    path = Path(descriptor["path"])
    private_file(path)
    before = path.stat()
    size = int(descriptor["bytes"])
    refuse(before.st_size == size, "host_controller:archive_size")
    meter.read(size + 1)
    hasher, count = hashlib.sha256(), 0
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        while count <= size:
            raw = stream.read(min(1024**2, size + 1 - count))
            if not raw:
                break
            count += len(raw)
            hasher.update(raw)
        after = os.fstat(stream.fileno())
    refuse(count == size and hasher.hexdigest() == descriptor["sha256"], "host_controller:archive_identity")
    refuse((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
           == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
           "host_controller:archive_changed")


def _plan(config: ActualStudyConfig, meter: HostMeter) -> tuple[dict[str, Any], PhysicalStudyAdmission]:
    private_file(config.plan_path)
    refuse(config.plan_path.stat().st_size <= MAX_PLAN_BYTES, "host_controller:plan_bounds")
    meter.read(MAX_PLAN_BYTES)
    plan = validate_study_plan(strict_json(read_regular(config.plan_path, MAX_PLAN_BYTES), maximum_bytes=MAX_PLAN_BYTES))
    _require_fresh_resources(config, meter, plan)
    private_directory(config.attempt_root)
    refuse(not any(config.attempt_root.iterdir()), "host_controller:attempt_root_reused")
    meter.read(5 * MAX_PLAN_BYTES)
    parent = read_parent_admission(config.parent_admission,
        expected_sha256=config.parent_admission_sha256, plan=plan)
    refuse(config.python.is_absolute() and config.python.is_file()
           and config.python.samefile(Path(sys.executable)), "host_controller:python")
    child_environment(config)
    return plan, parent


def run_actual_study(config: ActualStudyConfig) -> dict[str, Any]:
    """Run one fresh admitted attempt; all failures retain their spend and cause."""
    # Import the actual exchange implementation rather than allow a launch
    # callback to manufacture successful worker or ownership observations.
    from retrieval_supplement_host_exchange import run_exchange
    from retrieval_supplement_host_transport import publish_epoch_ack

    meter = HostMeter(Allocation(config.allocation_path, STUDY_CAPS))
    plan, parent = _plan(config, meter)
    source = StudySourceGuard(config.source_root, plan, meter)
    source.freeze()
    frozen_inputs = load_study_inputs(config.source_root, charge_bytes=meter.read)
    require_study_manifest(plan, frozen_inputs.catalog)
    candidate_bytes = sum((config.source_root / name).stat().st_size
        for name, row in plan["candidate_snapshot"].items() if "symlink" not in row)
    forecast = build_byte_forecast(source_bytes=source.closure["total_bytes"],
        request_bytes=(MAX_REQUEST_STREAM_BYTES, MAX_REQUEST_STREAM_BYTES), candidate_bytes=candidate_bytes)
    refuse(forecast["byte_forecast_sha256"] == plan["byte_forecast_sha256"]
           and plan["derived"].get("byte_forecast_binding") == {
               "source_bytes": source.closure["total_bytes"],
               "request_bytes": [MAX_REQUEST_STREAM_BYTES, MAX_REQUEST_STREAM_BYTES],
               "candidate_bytes": candidate_bytes}, "host_controller:forecast_binding")
    refuse(build_schedule()["schedule_sha256"] == plan["schedule_sha256"], "host_controller:schedule_binding")
    meter.read(182_037_753)
    verify_study_artifact_gate(config.artifact_root,
        trusted_gate_receipt_sha256=parent.record["artifact_gate"]["sha256"])
    _verify_runtime_archive(plan, meter)
    def authority_current() -> None:
        parent.require_current(meter.read)
        source.require_current()
    def authority_custody_current() -> None:
        parent.require_current(meter.read)
        source.metadata_current()
    resources_before = docker_snapshot(charge_bytes=meter.docker_read, charge_operation=meter.operation,
        timeout=meter.remaining)
    flat = config.attempt_root / "worker-source"
    flat.mkdir(mode=0o700)
    worker_digests = source.copy_worker(flat)
    (config.attempt_root / "tmp").mkdir(mode=0o700)
    worker_entry = config.attempt_root / "worker-entry.py"
    entry_path = config.source_root / "tools/retrieval_supplement_worker_entry.py"
    meter.read(entry_path.stat().st_size)
    entry_bytes = entry_path.read_bytes()
    put_private(worker_entry, entry_bytes, meter=meter)
    admission = None
    runner: RunnerProcess | None = None
    launch_attempted = False
    primary: BaseException | None = None
    cleanup_error: BaseException | None = None
    cleanup: dict[str, object] = {"cleanup": "unknown"}
    exchanges: list[dict[str, Any]] = []
    report: dict[str, Any] | None = None
    source_read_receipt: dict[str, Any] | None = None
    source_identity = digest_bytes(canonical_bytes(plan["candidate_snapshot"]))
    channel: RunnerChannel | None = None
    try:
        authority_current()
        admission = issue_admission(plan, reservation_path=Path(plan["reservation_path"]),
            evaluator_closure_sha256=plan["evaluator_closure_sha256"],
            admission_path=admission_file_for_plan(config.plan_path))
        # The consume-once reservation precedes canonical runner creation,
        # SDK creation/import, PostgreSQL lifecycle and either model worker.
        channel = RunnerChannel(config.source_root, canonical_argv(config), source_identity, SDK_IMAGE_SHA256)
        authority_current()
        launch_attempted = True
        runner = launch_runner(RunnerProcessConfig(canonical_argv(config), config.source_root,
            config.attempt_root / "canonical.stdout.log", config.attempt_root / "canonical.stderr.log",
            child_environment(config), channel))
        lease = RunnerLease(runner, authority_current, meter.remaining, meter.docker_read, meter.operation,
            charge_transfer=meter.transfer, custody_source_guard=authority_custody_current)
        reader = RunnerOutput(runner, meter.read)
        for ordinal in (1, 2):
            raw_epoch = reader.await_frame(EPOCH_PREFIX,
                deadline=min(meter.deadline - 240, time.monotonic() + (420 if ordinal == 1 else 120)),
                require_current=lease.require_outer)
            epoch = decode_epoch_line(raw_epoch)
            refuse(epoch.attempt_id == admission.attempt_id and epoch.ordinal == ordinal
                   and epoch.evaluator_closure_sha256 == admission.evaluator_closure_sha256,
                   "host_controller:epoch_join")
            epoch_started = time.monotonic()
            publish_epoch_ack(epoch, require_outer=lease.require_outer,
                charge_bytes=meter.read, charge_operation=meter.operation,
                deadline=min(meter.deadline - 240, epoch_started + 120))
            exchange_root = config.attempt_root / f"worker-{ordinal}"
            exchange_root.mkdir(mode=0o700)
            # run_exchange validates the actual request, publishes admission,
            # observes SDK ACK, starts one real sibling and delivers its output.
            result = run_exchange(epoch=epoch, epoch_started=epoch_started, reader=reader,
                require_outer=lease.require_outer, source_guard=authority_current,
                charge_bytes=meter.read, charge_operation=meter.operation,
                charge_docker_bytes=meter.docker_read,
                before_worker=meter.worker, plan=plan, artifact_root=config.artifact_root,
                worker_source=flat, worker_entry=worker_entry, output_root=exchange_root,
                worker_digests=worker_digests,
                gate_references={"physical_study_parent_admission": config.parent_admission_sha256,
                    "artifact_gate_receipt_sha256": parent.record["artifact_gate"]["sha256"]})
            exchanges.append(result)
        while runner.process.poll() is None and time.monotonic() < meter.deadline - 240:
            source.metadata_current()
            reader.poll()
            time.sleep(.25)
        refuse(runner.process.poll() == 0, "host_controller:canonical_study_failed_or_expired")
        reader.finish()
        source.verify_whole_candidate()
        source_read_receipt = validate_exported_source_read_receipt(config.attempt_root / "report",
            inner_plan=project_inner_study_plan(plan, admission=admission),
            regular_file_bytes=candidate_bytes, charge_bytes=meter.read)
        report = validate_exported_study(config.attempt_root / "report", plan=plan, charge_bytes=meter.read,
            counted_worker_tokens=[item["token_evidence"] for item in exchanges], frozen_inputs=frozen_inputs)
        authority_current()
    except BaseException as exc:
        primary = exc
    finally:
        try:
            if runner is None:
                if channel is not None:
                    channel.close()
                if isinstance(primary, RunnerLaunchFailure):
                    cleanup = {"cleanup": "proven" if primary.cleanup_proven else "unknown",
                        "runner_started": primary.runner_started,
                        "sdk_started": None if primary.gate_released else False,
                        "launch_cleanup_code": primary.cleanup_code}
                else:
                    cleanup = {"cleanup": "unknown" if launch_attempted else "proven",
                        "runner_started": None if launch_attempted else False,
                        "sdk_started": None if launch_attempted else False}
            else:
                cleanup = settle_runner(runner, cleanup_required=primary is not None,
                    charge_bytes=meter.docker_read, charge_operation=meter.operation,
                    deadline=min(meter.deadline, time.monotonic() + 240))
            source.verify_whole_candidate()
            resources_after = docker_snapshot(charge_bytes=meter.docker_read, charge_operation=meter.operation,
                timeout=meter.remaining)
            refuse(resources_after == resources_before, "host_controller:docker_conservation_gap")
            cleanup["host_resource_sets_conserved"] = True
            refuse(cleanup["cleanup"] == "proven", "host_controller:ownership_cleanup_unproven")
        except BaseException as exc:
            cleanup_error = exc
            cleanup = {"cleanup": "unknown", "secondary_code": getattr(exc, "code", type(exc).__name__)}
            if primary is not None:
                primary.add_note("host controller settlement failed; retained secondary evidence")
        try:
            with meter.allocation.session() as used:
                spends = dict(used.used)
            if admission is not None:
                reservation = read_reservation(Path(plan["reservation_path"]))
                refuse(reservation.get("consumed") is True and reservation.get("cleanup") == "in_progress",
                       "host_controller:reservation_settlement_state")
                settle_reservation(Path(plan["reservation_path"]), plan_sha256=admission.plan_sha256,
                    cleanup=str(cleanup["cleanup"]), runtime_imports=spends["native_runtime_loads"],
                    runtime_exports=spends["runtime_exports"], model_workers=spends["study_workers"])
            else:
                cleanup["reservation"] = "not_issued_or_partial; retained_without_reuse"
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
            cleanup["cleanup"] = "unknown"
            if primary is not None:
                primary.add_note("reservation settlement failed; no retry authority follows")
        terminal = {"schema": "cloe-supplement-actual-host-terminal-v1", "phase": STUDY_PHASE,
            "attempt_id": plan["attempt_id"], "plan_sha256": plan_digest(plan),
            "parent_admission_sha256": config.parent_admission_sha256,
            "status": "completed" if primary is None and cleanup_error is None else "failed_incomplete",
            "primary_code": None if primary is None else getattr(primary, "code", type(primary).__name__),
            "underlying_primary_code": getattr(primary, "primary_code", None),
            "underlying_cleanup_code": getattr(primary, "cleanup_code", None),
            "cleanup": cleanup, "workers": exchanges, "report": report,
            "source_read_receipt": source_read_receipt,
            "declared_slot_manifest": plan["declaration"]["slot_manifest"],
            "source_byte_verification_count": source.byte_verifications,
            "docker_operation_count": len(meter.operations),
            "docker_read_reservation_bytes": meter.docker_read_reservations,
            "scientific_acceptance_claimed": False, "production_admitted": False}
        # Evidence settlement has its own fixed tiny reservation in the plan;
        # its failure must not replace the original experiment failure.
        try:
            put_private(config.attempt_root / "host-terminal.json", canonical_bytes(terminal), meter=meter)
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
            if primary is not None:
                primary.add_note("host terminal persistence failed")
    if primary is not None:
        raise primary
    if cleanup_error is not None:
        raise cleanup_error
    return terminal
