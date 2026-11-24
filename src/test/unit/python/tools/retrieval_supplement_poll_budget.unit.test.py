"""Exercise the bounded model poll and custody budgets through real call paths."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock
import zipfile

import psutil
import pytest

import retrieval_supplement_model_contract as contract
import retrieval_supplement_model_host as host
from retrieval_supplement_model_budget import TIMEOUTS
from retrieval_supplement_poll_budget import host_poll_budget
from retrieval_supplement_runner_channel import RunnerChannel
from retrieval_supplement_runner_lease import RunnerLease
from retrieval_supplement_runner_process import RunnerProcess, RunnerProcessConfig
from retrieval_supplement_study_contracts import (
    MODEL_LEASE_POLL_SECONDS, SDK_IMAGE_SHA256, STUDY_SELECTOR,
)
from test_sandbox_ownership import CONTROL_SCHEMA, RECEIPT_SCHEMA


OUTER_CID = "e" * 64
MODEL_CID = "d" * 64
MARKER_DIGEST = "sha256:" + "f" * 64


def _digest(letter: str) -> str:
    return "sha256:" + letter * 64


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _argv() -> tuple[str, ...]:
    return ("/usr/bin/python3", "-I", "-B", "tools/run_tests.py", "--suite", "int", "--jobs", "1",
            "--no-coverage", "--pg-test-profile", "pgvector", "--report-dir", "/reports/study", "--",
            "-s", STUDY_SELECTOR)


def _closure_identity(digests: dict[str, str]) -> str:
    raw = json.dumps({"closure": "worker", "files": dict(sorted(digests.items()))},
                     ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("ascii")).hexdigest()


def _model_inspection(command: tuple[str, ...], cid: str, labels: dict[str, str], running: bool) -> dict[str, object]:
    mounts: list[dict[str, object]] = []
    for index, item in enumerate(command):
        if item == "--mount":
            fields = dict(field.split("=", 1) for field in command[index + 1].split(",") if "=" in field)
            mounts.append({"Type": "bind", "Source": fields["src"], "Destination": fields["dst"],
                           "RW": "readonly" not in command[index + 1]})
    for index, item in enumerate(command):
        if item == "--tmpfs":
            destination, *_ = command[index + 1].split(":", 1)
            mounts.append({"Type": "tmpfs", "Destination": destination, "RW": True})
    user = command[command.index("--user") + 1]
    image_index = command.index(host.SDK_IMAGE_ID)
    tmpfs = {"/scratch": "rw,exec,nosuid,nodev,size=768m,mode=1777",
             "/tmp": "rw,noexec,nosuid,nodev,size=64m,mode=1777"}
    name = command[command.index("--name") + 1]
    return {"Id": cid, "Image": host.SDK_IMAGE_ID, "Name": "/" + name, "Path": "python3",
            "Config": {"Path": "python3", "Entrypoint": ["python3"], "Labels": labels,
                        "Cmd": list(command[image_index + 1:]), "User": user},
            "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                            "Memory": 2 * 1024**3, "NanoCpus": 1_000_000_000, "PidsLimit": 64,
                            "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"], "Tmpfs": tmpfs},
            "Mounts": mounts, "State": {"Running": running}}


def _outer_inspection(root: Path) -> dict[str, object]:
    return {"Id": OUTER_CID, "Image": SDK_IMAGE_SHA256, "State": {"Running": True},
            "Config": {"Labels": {"org.repomap.test.sandbox": "true",
                                    "org.repomap.test.sandbox.backend": "monolithic-dind-v1"},
                        "Cmd": ["docker-init", "--", "python3", "/workspace-ro/tools/test_sandbox_entrypoint.py",
                                "--docker-group", "100", "--", *_argv()[4:]]},
            "Mounts": [{"Type": "bind", "Source": str(root.resolve()),
                        "Destination": "/workspace-ro", "RW": False}]}


class _Channel:
    def __init__(self, root: Path, birth: float) -> None:
        self.source_root = root
        self.runner_argv = _argv()
        self.control = {"schema": CONTROL_SCHEMA, "challenge": "challenge",
                        "pid": os.getpid(), "birth": birth, "source_identity": _digest("a"),
                        "source_root": str(root.resolve()), "argv": list(_argv()[4:]),
                        "sdk_image_id": SDK_IMAGE_SHA256}
        self.receipt = dict(self.control, schema=RECEIPT_SCHEMA, cid=OUTER_CID,
                            marker_sha256=MARKER_DIGEST)
        self.raw = bytearray(b"receipt")

    def poll(self) -> dict[str, object]:
        return dict(self.receipt)


def _lease(root: Path, full: list[str], cheap: list[str], stream: list[int], operations: list[str]) -> RunnerLease:
    process = Mock()
    process.pid = os.getpid()
    process.poll.return_value = None
    birth = psutil.Process().create_time()
    channel = _Channel(root, birth)
    config = RunnerProcessConfig(_argv(), root, root / "stdout", root / "stderr", {}, cast(RunnerChannel, channel))
    runner = RunnerProcess(process, birth, config)
    return RunnerLease(runner, lambda: full.append("full"), lambda: 100000.0,
                       lambda count: stream.append(count), lambda name: operations.append(name),
                       custody_source_guard=lambda: cheap.append("cheap"))


class _DockerDouble:
    def __init__(self, root: Path, commands: list[tuple[str, ...]], stop_after: int = math.ceil(TIMEOUTS["model"] / MODEL_LEASE_POLL_SECONDS) + 1) -> None:
        self.root, self.commands, self.stop_after = root, commands, stop_after
        self.create_command: tuple[str, ...] | None = None
        self.started = False
        self.running = False
        self.polls = 0

    def __call__(self, argv: tuple[str, ...], _timeout: float, _limit: int) -> tuple[int, bytes, bytes, bool, bool]:
        self.commands.append(argv)
        operation = argv[1]
        if operation == "inspect" and len(argv) == 3 and argv[2] == OUTER_CID:
            return 0, _json([_outer_inspection(self.root)]), b"", False, False
        if operation == "exec" and len(argv) > 2 and argv[2] == OUTER_CID:
            return 0, (MARKER_DIGEST + "\n").encode(), b"", False, False
        if operation == "image":
            return 0, _json([{"Id": host.SDK_IMAGE_ID, "Architecture": "arm64",
                              "Config": {"Labels": {"base": "sdk"}, "Volumes": {}}}]), b"", False, False
        if operation == "create":
            self.create_command, self.started, self.running = argv, False, False
            return 0, (MODEL_CID + "\n").encode(), b"", False, False
        if operation == "inspect" and len(argv) == 3 and argv[2] == MODEL_CID:
            assert self.create_command is not None
            if self.started and self.running:
                self.polls += 1
                if self.polls >= self.stop_after:
                    self.running = False
            labels = {item.split("=", 1)[0]: item.split("=", 1)[1]
                      for index, item in enumerate(self.create_command)
                      if item == "--label" for item in [self.create_command[index + 1]]}
            return 0, _json([_model_inspection(self.create_command, MODEL_CID, labels, self.running)]), b"", False, False
        if operation == "start":
            self.started, self.running = True, True
            return 0, b"", b"", False, False
        if operation == "wait":
            self.running = False
            return 0, b"0\n", b"", False, False
        if operation == "stop":
            self.running = False
            return 0, b"", b"", False, False
        if operation == "rm":
            return 0, b"", b"", False, False
        if operation == "container":
            return 1, b"", (f"No such container: {MODEL_CID}").encode(), False, False
        raise AssertionError(argv)


def _response_files(case: SimpleNamespace) -> None:
    attempt = case.paths.output_root / case.request.attempt_id
    attempt.mkdir()
    archive_data = io.BytesIO()
    with zipfile.ZipFile(archive_data, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("inputs/request.json", case.request_raw)
        archive.writestr("inputs/admission.json", case.admission_raw)
        archive.writestr("sources/worker.py", b"pass\n")
    for name, value in {"declaration.json": b"{}", "source-manifest.json": b"{}",
                        "sources.zip": archive_data.getvalue(), "report.json": b"{}",
                        "terminal.json": _json({"observation_count": 0, "status": "completed"})}.items():
        (attempt / name).write_bytes(value)


def _case(tmp_path: Path, attempt: str) -> SimpleNamespace:
    root = tmp_path / attempt
    artifact, source, output = root / "artifacts", root / "source", root / "output"
    artifact.mkdir(parents=True)
    source.mkdir()
    output.mkdir()
    repo_tools = Path(__file__).resolve().parents[5] / "tools"
    closure: dict[str, str] = {}
    for name in contract.WORKER_SOURCE_FILES:
        raw = (repo_tools / name).read_bytes()
        (source / name).write_bytes(raw)
        closure[name] = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert _closure_identity(closure) == contract.WORKER_CLOSURE_ID
    total, count = contract.ARTIFACT_BYTES, contract.ARTIFACT_FILES
    base, remainder = divmod(total, count)
    for index in range(count):
        with (artifact / f"artifact-{index:02d}").open("wb") as member:
            member.truncate(base + (index < remainder))
    entry = root / "worker_entry.py"
    entry.write_bytes(b"trusted worker entry\n")
    identity = {"input_identity": _digest("1"), "representation_report_identity": _digest("2"),
                "publication_identity": _digest("3")}
    request = {"schema": contract.REQUEST_SCHEMA, "attempt_id": attempt, **identity,
               "artifact_profile_identity": contract.ARTIFACT_PROFILE_ID,
               "records": [{"record_id": "d1", "text": "document", "mode": "document"}]}
    request_raw = _json(request)
    admission = {"schema": contract.ADMISSION_SCHEMA, "phase": contract.PHASE,
                 "operator_authority_sha256": _digest("4"), "gate_references": {"study": _digest("5")},
                 "bounded_component_execution_admitted": True,
                 "request_sha256": "sha256:" + hashlib.sha256(request_raw).hexdigest(), **identity,
                 "artifact_profile_identity": contract.ARTIFACT_PROFILE_ID,
                 "candidate_closure_digests": closure, "scope": "one bounded model sibling"}
    admission_raw = _json(admission)
    request_path, admission_path = root / "request.json", root / "admission.json"
    request_path.write_bytes(request_raw)
    admission_path.write_bytes(admission_raw)
    paths = host.ModelHostPaths(request_path, admission_path, artifact, source, entry, output)
    request_value = host.ValidatedStudyRequest(
        attempt, "sha256:" + hashlib.sha256(request_raw).hexdigest(),
        "sha256:" + hashlib.sha256(admission_raw).hexdigest(), identity["input_identity"],
        identity["representation_report_identity"], identity["publication_identity"],
        source_closure=closure, record_count=1,
        worker_entry_sha256="sha256:" + hashlib.sha256(entry.read_bytes()).hexdigest())
    profile = SimpleNamespace(source_identity_sha256=contract.ARTIFACT_PROFILE_ID,
                              pinned_profile_sha256=contract.ARTIFACT_PIN_ID,
                              verified_files=tuple(range(contract.ARTIFACT_FILES)))
    case = SimpleNamespace(root=root, paths=paths, request=request_value, profile=profile,
                           request_raw=request_raw, admission_raw=admission_raw)
    _response_files(case)
    return case


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_poll_budget_uses_shared_cadence_and_rejects_legacy_full_rechecks() -> None:
    budget = host_poll_budget()
    assert budget["model_seconds_per_worker"] == TIMEOUTS["model"] == 550.0
    assert budget["cadence_seconds"] == MODEL_LEASE_POLL_SECONDS == 5.0
    assert budget["full_checks_per_lease"] == 1
    for cadence in (5.0, 10.0):
        observed = host_poll_budget(cadence=cadence)
        assert observed["source_verifications"] <= 1024
        assert observed["docker_operations"] <= 2048
        assert observed["docker_stream_reservation_bytes"] <= 2 * 1024**3
    with pytest.raises(ValueError, match="source_ceiling"):
        host_poll_budget(cadence=0.05)
    with pytest.raises(ValueError, match="source_ceiling"):
        host_poll_budget(full_checks_per_lease=2)


def test_two_max_model_windows_use_real_lease_callbacks_and_bounded_streams(tmp_path, monkeypatch) -> None:
    full: list[str] = []
    cheap: list[str] = []
    stream: list[int] = []
    operations: list[str] = []
    commands: list[tuple[str, ...]] = []
    source_bytes: list[int] = []
    clock = _Clock()
    monkeypatch.setattr(host.time, "sleep", clock.sleep)
    results = []
    for index in (1, 2):
        case = _case(tmp_path, f"study-{index}")
        docker = _DockerDouble(case.root, commands)
        monkeypatch.setattr(host, "_bounded_process", docker)
        lease = _lease(case.root, full, cheap, stream, operations)
        start = clock()
        result = host.run_model_sibling(
            case.request, case.paths, nonce=("c" if index == 1 else "d") * 64,
            require_outer=lease.require_outer, charge_bytes=source_bytes.append,
            charge_operation=operations.append, verify_artifacts=lambda _root: case.profile,
            charge_docker_bytes=stream.append, epoch_started=start,
            response_deadline=start + TIMEOUTS["aggregate"], clock=clock)
        results.append(result)
        assert result.status == "completed" and result.cleanup_proven
        assert docker.polls == math.ceil(TIMEOUTS["model"] / MODEL_LEASE_POLL_SECONDS) + 1
        assert clock() - start == pytest.approx((docker.polls - 2) * MODEL_LEASE_POLL_SECONDS)
    forecast = host_poll_budget()
    assert all(result.response_bundle for result in results)
    assert full and cheap and len(cheap) >= len(full)
    assert len(full) <= forecast["source_verifications"] <= 1024
    assert len(commands) == len(operations) <= forecast["docker_operations"] <= 2048
    assert sum(stream) <= forecast["docker_stream_reservation_bytes"] <= 2 * 1024**3
    assert all(value == MODEL_LEASE_POLL_SECONDS for value in clock.sleeps)


def test_postinspection_custody_loss_refuses_after_cid_binding(tmp_path, monkeypatch) -> None:
    full: list[str] = []
    cheap: list[str] = []
    stream: list[int] = []
    operations: list[str] = []
    commands: list[tuple[str, ...]] = []
    docker = _DockerDouble(tmp_path, commands)
    monkeypatch.setattr(host, "_bounded_process", docker)
    lease = _lease(tmp_path, full, cheap, stream, operations)
    def custody_loss() -> None:
        cheap.append("cheap")
        raise ValueError("custody lost")
    lease.custody_source_guard = custody_loss
    with pytest.raises(ValueError, match="custody lost"):
        lease.require_outer()
    assert lease.observed_cid == OUTER_CID
    assert full == ["full"] and cheap == ["cheap"]
