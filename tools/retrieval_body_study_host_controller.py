"""BODY host admission/controller orchestration for one actual attempt."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
from typing import Any

from offline_qualification_values import BODY_CAPS, ENV_OFFLINE_PLAN
from runner_pgvector_budget import Allocation
from retrieval_body_study_contracts import (
    BODY_NODE_ID, BODY_PHASE, BODY_ROLE, BODY_RUNTIME_ARCHIVE_BYTES,
    BODY_RUNTIME_ARCHIVE_SHA256, BODY_SDK_IMAGE_SHA256, BODY_SELECTOR,
    MAX_PLAN_BYTES, MAX_REQUEST_STREAM_BYTES, PARENT_ADMISSION_PINS, refuse, strict_json,
)
from retrieval_body_study_host_admission import PhysicalBodyAdmission, read_parent_admission
from retrieval_body_study_host_meter import HostMeter
from retrieval_body_study_host_evidence import BodyEvidenceRefusal, capture_body_evidence
from retrieval_body_study_host_runner_lease import RunnerLease
from retrieval_body_study_host_runner_output import RunnerOutput
from retrieval_body_study_host_runner_process import (
    BodyRunnerLaunchFailure, RunnerProcess, RunnerProcessConfig, launch_runner,
)
from retrieval_body_study_host_runner_settlement import merge_cleanup, settle_runner, worker_record
from retrieval_body_study_host_source import (
    BodySourceGuard, private_directory, put_private,
)
from retrieval_body_study_plan import (
    admission_file_for_plan, issue_admission, plan_digest, project_inner_body_plan,
    require_reservation_admitted_for_runner, validate_body_plan,
)
from retrieval_body_study_reservation import private_file, read_reservation
from retrieval_body_study_schedule import build_schedule
from retrieval_body_study_host_epoch import acknowledged_epoch_start, epoch_frame_window

@dataclass(frozen=True)
class ActualBodyStudyConfig:
    source_root: Path = field(repr=False)
    plan_path: Path = field(repr=False)
    allocation_path: Path = field(repr=False)
    attempt_root: Path = field(repr=False)
    artifact_root: Path = field(repr=False)
    python: Path = field(repr=False)
    parent_admission: Path = field(repr=False)
    parent_admission_sha256: str
    environment: Mapping[str, str] = field(repr=False)

def canonical_argv(config: ActualBodyStudyConfig) -> tuple[str, ...]:
    return (str(config.python), "-I", "-B", "tools/run_tests.py", "--suite", "int", "--jobs", "1",
            "--no-coverage", "--pg-container-port", "55433", "--pg-test-profile", "pgvector",
            "--report-dir", str(config.attempt_root / "report"), "--", "-s", BODY_SELECTOR)

def child_environment(config: ActualBodyStudyConfig) -> dict[str, str]:
    allowed = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
               "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}
    for key in allowed:
        refuse(config.environment.get(key) == os.environ.get(key), "body child environment drift")
    temporary = config.attempt_root / "tmp"
    result = {key: value for key, value in config.environment.items() if key in allowed}
    result.update({ENV_OFFLINE_PLAN: str(config.plan_path), "CLOE_BODY_STUDY_PLAN": str(config.plan_path),
                   "TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary),
                   "PYTHONPYCACHEPREFIX": str(temporary / "pycache"),
                   "REPOMAP_TEST_SCRATCH_ROOT": str(temporary / "resources"),
                   "REPOMAP_TEST_PROJECT": "cloe_public", "REPOMAP_TEST_PHASE": BODY_PHASE,
                   "REPOMAP_TEST_ROLE": BODY_ROLE, "REPOMAP_TEST_NODE": BODY_NODE_ID,
                   "GIT_OPTIONAL_LOCKS": "0", "GOTOOLCHAIN": "local", "GOPROXY": "off",
                   "PIP_NO_INDEX": "1", "PIP_CACHE_DIR": str(temporary / "pip"),
                   "GOCACHE": str(temporary / "go"), "GOTMPDIR": str(temporary)})
    refuse(not {key for key in result if any(token in key.lower()
                                             for token in ("token", "secret", "password", "credential", "api_key"))},
           "body child credential environment")
    return result

def _validate_runner_python(config: ActualBodyStudyConfig) -> None:
    refuse(config.python.is_absolute() and config.python.is_file()
           and config.python.samefile(Path(sys.executable)), "body controller python")

def _prepare_child_temporary(attempt_root: Path) -> None:
    private_directory(attempt_root)
    temporary = attempt_root / "tmp"
    refuse(not temporary.exists() and not temporary.is_symlink(),
           "body child temporary root reused")
    temporary.mkdir(mode=0o700)
    private_directory(temporary)

@dataclass(repr=False)
class RunnerChannel:
    """Phase-neutral ownership pipe with BODY-scoped argv/image values."""

    source_root: Path
    runner_argv: tuple[str, ...]
    source_identity: str
    sdk_image_id: str
    challenge: str = field(default_factory=lambda: secrets.token_hex(32), repr=False)
    control: dict[str, Any] | None = field(default=None, init=False, repr=False)
    receipt: dict[str, Any] | None = field(default=None, init=False, repr=False)
    raw: bytearray = field(default_factory=bytearray, init=False, repr=False)

    def __post_init__(self) -> None:
        self.control_read, self.control_write = os.pipe()
        self.receipt_read, self.receipt_write = os.pipe()
        os.set_blocking(self.receipt_read, False)

    def _close(self, name: str) -> None:
        value = getattr(self, name, -1)
        if value >= 0:
            setattr(self, name, -1)
            os.close(value)

    def child_settings(self) -> tuple[dict[str, str], tuple[int, ...]]:
        refuse(self.control is None, "body runner channel reused")
        from test_sandbox_ownership import CHANNEL_ENV, SCOPE_ENV
        return dict(zip(CHANNEL_ENV + SCOPE_ENV, (str(self.control_read), str(self.receipt_write),
                                                   self.source_identity, self.sdk_image_id))), (
            self.control_read, self.receipt_write)

    def started(self, pid: int, birth: float) -> None:
        refuse(self.control is None, "body runner channel started twice")
        self._close("control_read")
        self._close("receipt_write")
        self.control = {"challenge": self.challenge, "pid": pid, "birth": birth,
                        "source_identity": self.source_identity,
                        "source_root": str(self.source_root.resolve()),
                        "argv": list(self.runner_argv[4:]), "sdk_image_id": self.sdk_image_id}
        from retrieval_supplement_ownership_wire import encode_control, scope_sha256
        scope = scope_sha256(self.runner_argv[4:], self.control["source_root"],
                             self.source_identity, self.sdk_image_id)
        raw = encode_control(self.challenge, pid, birth, scope)
        refuse(os.write(self.control_write, raw) == len(raw), "body runner control write")
        self._close("control_write")

    def poll(self) -> dict[str, Any] | None:
        refuse(self.control is not None, "body runner channel not started")
        while self.receipt is None:
            try:
                block = os.read(self.receipt_read, 4097 - len(self.raw))
            except BlockingIOError:
                return None
            if not block:
                break
            self.raw.extend(block)
            refuse(len(self.raw) <= 4096, "body runner receipt bounds")
        if self.receipt is not None or not self.raw:
            return self.receipt
        from retrieval_supplement_ownership_wire import decode_receipt, scope_sha256
        compact = decode_receipt(bytes(self.raw))
        assert self.control is not None
        scope = scope_sha256(self.runner_argv[4:], self.control["source_root"],
                             self.source_identity, self.sdk_image_id)
        refuse(compact["challenge"] == self.challenge and compact["scope_sha256"] == scope
               and compact["sdk_image_id"] == self.sdk_image_id, "body runner receipt join")
        self.receipt = {**self.control, **compact}
        self._close("receipt_read")
        return dict(self.receipt)

    def close(self) -> None:
        for name in ("control_read", "control_write", "receipt_read", "receipt_write"):
            self._close(name)

def _read_plan(config: ActualBodyStudyConfig, meter: HostMeter) -> dict[str, Any]:
    private_directory(config.plan_path.parent)
    private_file(config.plan_path)
    from retrieval_supplement_files import read_member
    meter.read(MAX_PLAN_BYTES + 128 * 1024)
    raw = read_member(config.plan_path.parent, config.plan_path.name, max_bytes=MAX_PLAN_BYTES)
    refuse(0 < len(raw) <= MAX_PLAN_BYTES, "body plan bounds")
    plan = validate_body_plan(strict_json(raw, maximum_bytes=MAX_PLAN_BYTES))
    refuse(Path(plan["allocation_path"]) == config.allocation_path
           and Path(plan["reservation_path"]).parent == config.plan_path.parent,
           "body plan resource join")
    reservation_path = Path(plan["reservation_path"])
    reservation = read_reservation(reservation_path)
    refuse(reservation["plan_sha256"] == plan_digest(plan)
           and reservation["attempt_id"] == plan["attempt_id"]
           and reservation["consumed"] is False and reservation["cleanup"] == "unstarted"
           and reservation["status"] == "reserved", "body reservation not fresh")
    return plan

def _verify_archive(plan: Mapping[str, Any], meter: HostMeter) -> None:
    path = Path(plan["archive"]["path"])
    private_directory(path.parent)
    private_file(path)
    before = path.stat()
    refuse(before.st_size == BODY_RUNTIME_ARCHIVE_BYTES, "body runtime archive size")
    meter.read(BODY_RUNTIME_ARCHIVE_BYTES + 1)
    hasher = hashlib.sha256()
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
        remaining = BODY_RUNTIME_ARCHIVE_BYTES
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            refuse(block, "body runtime archive short read")
            hasher.update(block)
            remaining -= len(block)
        after = os.fstat(stream.fileno())
    refuse("sha256:" + hasher.hexdigest() == BODY_RUNTIME_ARCHIVE_SHA256
           and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
           == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
           "body runtime archive identity")

def run_actual_body_study(config: ActualBodyStudyConfig) -> dict[str, Any]:
    _validate_runner_python(config)
    from retrieval_body_study_epoch import EPOCH_PREFIX, decode_epoch_line
    from retrieval_body_study_host_exchange import HostExchangeError, run_exchange
    meter = HostMeter(Allocation(config.allocation_path, BODY_CAPS))
    plan = _read_plan(config, meter)
    meter.read(PARENT_ADMISSION_PINS * (MAX_PLAN_BYTES + 1))
    parent: PhysicalBodyAdmission = read_parent_admission(
        config.parent_admission, expected_sha256=config.parent_admission_sha256, plan=plan)
    source = BodySourceGuard(config.source_root, plan, meter)
    source.freeze()
    candidate_bytes = source._candidate_bytes()
    from retrieval_body_study_budget import build_body_byte_forecast
    forecast = build_body_byte_forecast(plan, config.source_root)
    refuse(plan["derived"]["byte_forecast_binding"] == {
        "source_bytes": source.closure["total_bytes"],
        "treatment_bytes": source.treatment["total_bytes"],
        "worker_bytes": source.worker["total_bytes"],
        "registered_input_bytes": plan["derived"]["byte_forecast_binding"]["registered_input_bytes"],
        "request_bytes": [MAX_REQUEST_STREAM_BYTES, MAX_REQUEST_STREAM_BYTES],
        "candidate_bytes": candidate_bytes}, "body forecast binding")
    refuse(forecast["byte_forecast_sha256"] == plan["byte_forecast_sha256"]
           and build_schedule()["schedule_sha256"] == plan["schedule_sha256"],
           "body forecast or schedule join")
    def authority_current() -> None:
        source.require_current()

    _verify_archive(plan, meter)
    private_directory(config.attempt_root)
    refuse(not any(config.attempt_root.iterdir()), "body attempt root reused")
    _prepare_child_temporary(config.attempt_root)
    worker_root = config.attempt_root / "worker-source"
    worker_root.mkdir(mode=0o700)
    worker_digests = source.copy_worker(worker_root)
    worker_entry = config.attempt_root / "worker-entry.py"
    entry_bytes = source.read_member("tools/retrieval_supplement_worker_entry.py")
    put_private(worker_entry, entry_bytes, meter=meter)
    admission = None
    runner: RunnerProcess | None = None
    channel: RunnerChannel | None = None
    exchanges: list[dict[str, Any]] = []
    primary: BaseException | None = None
    cleanup: dict[str, Any] = {"cleanup": "unknown"}
    report: dict[str, Any] | None = None
    try:
        authority_current()
        parent.require_current(meter.read)
        source.require_registered_inputs()
        admission = issue_admission(plan, reservation_path=Path(plan["reservation_path"]),
                                    source_closure_sha256=plan["source_closure_sha256"],
                                    treatment_sha256=plan["treatment_sha256"],
                                    admission_path=admission_file_for_plan(config.plan_path))
        require_reservation_admitted_for_runner(plan, plan_path=config.plan_path)
        authority_current()
        argv = canonical_argv(config)
        channel = RunnerChannel(config.source_root, argv, plan["candidate_snapshot_sha256"], BODY_SDK_IMAGE_SHA256)
        runner = launch_runner(RunnerProcessConfig(argv, config.source_root,
            config.attempt_root / "canonical.stdout.log", config.attempt_root / "canonical.stderr.log",
            child_environment(config), channel))
        lease = RunnerLease(runner, authority_current, meter.remaining, meter.docker_read,
                            meter.operation, meter.transfer,
                            custody_source_guard=source.metadata_current)
        reader = RunnerOutput(runner, meter.read)
        for ordinal in (1, 2):
            ready_window = epoch_frame_window(ordinal)
            raw = reader.await_frame(EPOCH_PREFIX, deadline=min(meter.deadline - 240,
                                                                time.monotonic() + ready_window),
                                     require_current=lease.require_outer)
            epoch = decode_epoch_line(raw)
            refuse(epoch.ordinal == ordinal and epoch.attempt_id == admission.attempt_id
                   and epoch.evaluator_closure_sha256 == plan["evaluator_closure_sha256"],
                   "body epoch admission join")
            parent.require_current(meter.read)
            source.require_registered_inputs()
            epoch_started = acknowledged_epoch_start(epoch, lease=lease, meter=meter)
            exchange_root = config.attempt_root / f"worker-{ordinal}"
            exchange_root.mkdir(mode=0o700)
            parent.require_current(meter.read)
            try: exchanges.append(run_exchange(
                    epoch=epoch, epoch_started=epoch_started, reader=reader,
                    require_outer=lease.require_outer, source_guard=authority_current,
                    charge_bytes=meter.read, charge_operation=meter.operation,
                    charge_docker_bytes=meter.docker_read, before_worker=meter.worker, plan=plan,
                    artifact_root=config.artifact_root, worker_source=worker_root,
                    worker_entry=worker_entry, output_root=exchange_root, worker_digests=worker_digests,
                    gate_references={"physical_body_parent_admission": config.parent_admission_sha256, "artifact_gate_receipt_sha256": parent.record["artifact_gate"]["sha256"]}))
            except HostExchangeError as error:
                record = dict(error.worker) if isinstance(error.worker, Mapping) else worker_record(None, unknown=True)
                record["exchange_error"] = error.code
                exchanges.append(record)
                raise
            except BaseException as error:
                record = worker_record(None, unknown=True)
                record["exchange_error"] = type(error).__name__
                exchanges.append(record)
                raise
        while runner.process.poll() is None and meter.remaining() > 240:
            source.metadata_current()
            reader.poll()
            time.sleep(min(5.0, max(0.0, meter.remaining() - 240)))
        refuse(runner.process.poll() == 0, "body canonical runner failed")
        reader.finish()
        from retrieval_body_study_report_custody import privatize_body_report_export
        privatize_body_report_export(config.attempt_root / "report")
        source.verify_whole_candidate()
        inner = project_inner_body_plan(plan, admission=admission)
        from retrieval_body_study_inner_read_report import validate_exported_source_read_receipt
        from retrieval_body_study_registered_guard import registered_body_stat_footprint
        source.metadata_current()
        footprint = registered_body_stat_footprint(
            config.source_root / "src/test/fixtures/retrieval_evaluation/body-v2")
        source.metadata_current()
        refuse(0 < footprint["registered_snapshot_file_bytes"] <=
               plan["derived"]["byte_forecast_binding"]["registered_input_bytes"],
               "body registered report footprint cap")
        validate_exported_source_read_receipt(config.attempt_root / "report", inner_plan=inner,
            source_root=config.source_root, ledger_root=config.attempt_root / "report",
            regular_file_bytes=candidate_bytes, active_file_bytes=footprint["active_file_bytes"],
            registered_snapshot_file_bytes=footprint["registered_snapshot_file_bytes"],
            expected_registered_snapshot_count=201, charge_bytes=meter.read)
    except BaseException as error:
        primary = error
        if isinstance(error, BodyRunnerLaunchFailure) and error.runner is not None:
            runner = error.runner
    finally:
        try:
            cleanup = settle_runner(runner, channel, charge_bytes=meter.docker_read,
                                    charge_operation=meter.operation, deadline=meter.deadline)
            if runner is None and isinstance(primary, BodyRunnerLaunchFailure):
                cleanup = {**cleanup, "cleanup": "proven" if primary.cleanup_proven else "unknown",
                           "runner_started": primary.runner_started,
                           "launch_cleanup_code": primary.cleanup_code}
        except BaseException as error:
            cleanup = {"cleanup": "unknown", "runner_started": runner is not None,
                       "sdk_container": None, "pg_lifecycle": "unknown", "model_workers": 0,
                       "settlement_error": type(error).__name__}
        cleanup = merge_cleanup(cleanup, exchanges)
        evidence = capture_body_evidence(config.attempt_root / "report", plan=plan,
                                         cleanup=cleanup, charge_bytes=meter.read)
        report = evidence.get("report")
        if primary is None and evidence["state"] != "retained":
            primary = BodyEvidenceRefusal("body_evidence_" + evidence["state"])
        try:
            reservation = read_reservation(Path(plan["reservation_path"]))
            if admission is not None or (reservation["consumed"] is True
                                         and reservation["cleanup"] == "in_progress"):
                with meter.allocation.session() as ledger:
                    counters = dict(ledger.used)
                from retrieval_body_study_reservation import settle_reservation
                settle_reservation(Path(plan["reservation_path"]),
                                   plan_sha256=plan_digest(plan), cleanup=cleanup["cleanup"],
                                   runtime_imports=counters.get("native_runtime_loads", 0),
                                   runtime_exports=counters.get("runtime_exports", 0),
                                   model_workers=(cleanup["model_workers"] if cleanup["model_workers_known"]
                                                  else cleanup["model_workers_upper_bound"]),
                                   result_evidence=evidence["binding"])
        except BaseException as error:
            cleanup = {**cleanup, "cleanup": "unknown", "settlement_error": type(error).__name__}
        terminal = {"schema": "cloe-body-study-host-terminal-v1", "phase": BODY_PHASE,
                    "role": BODY_ROLE, "attempt_id": plan["attempt_id"],
                    "plan_sha256": plan_digest(plan),
                    "parent_admission_sha256": config.parent_admission_sha256,
                    "status": "completed" if primary is None and cleanup["cleanup"] == "proven"
                              else "failed_incomplete",
                    "primary_code": None if primary is None else getattr(primary, "code", type(primary).__name__),
                    "underlying_primary_code": None if primary is None else getattr(primary, "primary_code", None),
                    "underlying_cleanup_code": None if primary is None else getattr(primary, "cleanup_code", None),
                    "cleanup": cleanup, "workers": exchanges, "model_workers": cleanup["model_workers"], "model_workers_known": cleanup["model_workers_known"], "model_workers_upper_bound": cleanup["model_workers_upper_bound"], "result_evidence": evidence["binding"], "evidence": evidence["artifacts"], "evidence_state": evidence["state"], "evidence_errors": evidence["errors"], "report": report,
                    "scientific_acceptance_claimed": False,
                    "source_byte_verification_count": source.byte_verifications,
                    "docker_operation_count": len(meter.operations),
                    "docker_read_reservation_bytes": meter.docker_read_reservations}
        try:
            meter.record_result({"schema": "cloe-body-study-result-v1", "status": terminal["status"],
                                 "cleanup": cleanup["cleanup"], "attempt_id": plan["attempt_id"]})
        except BaseException:
            terminal["result_record"] = "unknown"
        try:
            persisted = json.dumps({**terminal, "report": evidence["artifacts"].get("report"), "report_persisted_as": "hash_bound_export_reference"}, sort_keys=True, separators=(",", ":")).encode()
            refuse(len(persisted) <= 2 * 1024**2, "body host terminal bounds")
            put_private(config.attempt_root / "host-terminal.json", persisted, meter=meter)
        except BaseException:
            terminal["terminal_persist"] = "unknown"
            primary = primary or BodyEvidenceRefusal("body_terminal_persist")
    if primary is not None:
        raise primary
    refuse(cleanup["cleanup"] == "proven", "body ownership cleanup")
    assert report is not None
    return terminal

__all__ = [
    "ActualBodyStudyConfig", "RunnerChannel", "RunnerLease", "RunnerOutput",
    "BodyRunnerLaunchFailure", "RunnerProcess", "RunnerProcessConfig", "canonical_argv", "child_environment",
    "run_actual_body_study",
]
