"""Focused model-host boundary tests; no native runtime or Docker is used here."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
import zipfile

import pytest

import retrieval_supplement_model_contract as contract
import retrieval_supplement_model_host as host


CID = "a" * 64
FOREIGN_CID = "b" * 64
def digest(letter: str) -> str:
    return "sha256:" + letter * 64


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _fixture(tmp_path: Path):
    root = tmp_path / "candidate"
    artifact, source, output = root / "artifacts", root / "source", root / "output"
    artifact.mkdir(parents=True)
    source.mkdir()
    output.mkdir()
    entry = root / "worker_entry.py"
    entry.write_bytes(b"trusted worker entry")
    closure = {name: digest(hashlib.sha256(name.encode()).hexdigest()[:1]) for name in contract.WORKER_SOURCE_FILES}
    identity = {"input_identity": digest("1"), "representation_report_identity": digest("2"), "publication_identity": digest("3")}
    request = {"schema": contract.REQUEST_SCHEMA, "attempt_id": "study-1", **identity,
               "artifact_profile_identity": contract.ARTIFACT_PROFILE_ID,
               "records": [{"record_id": "d1", "text": "document", "mode": "document"}]}
    request_raw = _json(request)
    admission = {"schema": contract.ADMISSION_SCHEMA, "phase": contract.PHASE,
                 "operator_authority_sha256": digest("4"), "gate_references": {"study": digest("5")},
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
        "study-1", "sha256:" + hashlib.sha256(request_raw).hexdigest(),
        "sha256:" + hashlib.sha256(admission_raw).hexdigest(), identity["input_identity"],
        identity["representation_report_identity"], identity["publication_identity"],
        source_closure=closure, record_count=1,
        worker_entry_sha256="sha256:" + hashlib.sha256(entry.read_bytes()).hexdigest(),
    )
    profile = SimpleNamespace(source_identity_sha256=contract.ARTIFACT_PROFILE_ID,
                              pinned_profile_sha256=contract.ARTIFACT_PIN_ID,
                              verified_files=tuple(range(contract.ARTIFACT_FILES)))
    return SimpleNamespace(root=root, paths=paths, request=request_value, profile=profile,
                           request_raw=request_raw, admission_raw=admission_raw)


def _response_files(case) -> None:
    attempt = case.paths.output_root / case.request.attempt_id
    attempt.mkdir()
    source_archive = io.BytesIO()
    with zipfile.ZipFile(source_archive, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("inputs/request.json", case.request_raw)
        archive.writestr("inputs/admission.json", case.admission_raw)
        archive.writestr("sources/worker.py", b"pass\n")
    files = {
        "declaration.json": b"{}", "source-manifest.json": b"{}",
        "sources.zip": source_archive.getvalue(), "report.json": b"{}",
        "terminal.json": _json({"observation_count": 0, "status": "completed"}),
    }
    for name, value in files.items():
        (attempt / name).write_bytes(value)


def _inspect_info(command: tuple[str, ...], cid: str, labels: dict[str, str], running: bool) -> dict[str, object]:
    mounts: list[dict[str, object]] = []
    for index, item in enumerate(command):
        if item == "--mount":
            fields = dict(field.split("=", 1) for field in command[index + 1].split(",") if "=" in field)
            mounts.append({"Type": "bind", "Source": fields["src"], "Destination": fields["dst"],
                           "RW": "readonly" not in command[index + 1]})
    for index, item in enumerate(command):
        if item == "--tmpfs":
            destination, *options = command[index + 1].split(":", 1)
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


class DockerDouble:
    def __init__(self, *, fail_rm: bool = False, exit_code: int = 0, lose_on: str | None = None):
        self.fail_rm, self.exit_code, self.lose_on = fail_rm, exit_code, lose_on
        self.commands: list[tuple[str, ...]] = []
        self.create_command: tuple[str, ...] | None = None
        self.running = False
        self.lost = False
        self.running_inspections = 0

    def __call__(self, argv: tuple[str, ...], _timeout: float, _limit: int) -> tuple[int, bytes, bytes, bool, bool]:
        self.commands.append(argv)
        operation = argv[1]
        if operation == "image":
            return 0, _json([{"Id": host.SDK_IMAGE_ID, "Architecture": "arm64", "Config": {"Labels": {"base": "sdk"}, "Volumes": {}}}]), b"", False, False
        if operation == "create":
            self.create_command = argv
            if self.lose_on == "malformed_create":
                return 0, b"ambiguous", b"", False, False
            if self.lose_on == "create":
                self.lost = True
            return 0, (CID + "\n").encode(), b"", False, False
        if operation == "inspect" and len(argv) == 3:
            assert self.create_command is not None
            if self.running:
                self.running_inspections += 1
                if self.running_inspections >= 2:
                    self.running = False
                    if self.lose_on == "model_poll":
                        self.lost = True
            phase_labels = dict(item.split("=", 1) for i, item in enumerate(self.create_command) if item == "--label" for item in [self.create_command[i + 1]])
            return 0, _json([_inspect_info(self.create_command, CID, phase_labels, self.running)]), b"", False, False
        if operation == "start":
            self.running = True
            if self.lose_on == "start":
                self.lost = True
            return 0, b"", b"", False, False
        if operation == "wait":
            self.running = False
            if self.lose_on == "wait":
                self.lost = True
            return 0, f"{self.exit_code}\n".encode(), b"", False, False
        if operation == "stop":
            self.running = False
            return 0, b"", b"", False, False
        if operation == "rm":
            return (1 if self.fail_rm else 0), b"", b"cleanup detail" if self.fail_rm else b"", False, False
        if operation == "container":
            return 1, b"", f"Error response from daemon: No such container: {CID}".encode(), False, False
        raise AssertionError(argv)


def _run(case, monkeypatch, docker: DockerDouble, *, lease=None, clock=lambda: 100.0,
         epoch_started=100.0, response_deadline=700.0):
    monkeypatch.setattr(host, "_source_closure", lambda *_args: None)
    monkeypatch.setattr(host, "_artifact_bank", lambda _root, _charge, _guard, verify: verify(_root))
    monkeypatch.setattr(host, "_bounded_process", docker)
    lease_state = {"lost": False}
    def require_outer() -> str:
        if lease_state["lost"] or docker.lost or (lease is not None and lease.lost):
            raise RuntimeError("private lease detail")
        if docker.lose_on == "foreign" and docker.create_command is not None:
            return FOREIGN_CID
        return CID
    return host.run_model_sibling(case.request, case.paths, nonce="c" * 64,
        require_outer=require_outer, charge_bytes=lambda _count: None,
        charge_operation=lambda _name: None, verify_artifacts=lambda _root: case.profile,
        epoch_started=epoch_started, response_deadline=response_deadline, clock=clock), lease_state


def test_model_sibling_launches_exact_contained_container_and_cleans_owned_cid(tmp_path, monkeypatch):
    case = _fixture(tmp_path)
    _response_files(case)
    docker = DockerDouble()
    result, _state = _run(case, monkeypatch, docker)
    assert result.status == "completed" and result.cleanup_proven
    assert result.response_bundle is not None and result.code == "completed"
    assert docker.commands[-1][1:3] == ("container", "inspect")
    create = docker.create_command
    assert create is not None
    assert "--pull=never" in create and "--network=none" in create and "--read-only" in create
    assert "--cap-drop=ALL" in create and "--security-opt=no-new-privileges" in create
    assert ("--pids-limit", "64") in zip(create, create[1:])
    assert ("--memory", "2g") in zip(create, create[1:])
    with zipfile.ZipFile(io.BytesIO(result.response_bundle)) as archive:
        assert set(archive.namelist()) == host.RESPONSE_MEMBERS
        assert archive.read("request.json") == case.request_raw


@pytest.mark.parametrize("bad_field", ["request_sha256", "worker_closure_sha256"])
def test_identity_refusal_performs_no_docker_operation(tmp_path, monkeypatch, bad_field):
    case = _fixture(tmp_path)
    if bad_field == "request_sha256":
        case.request = replace(case.request, request_sha256=digest("f"))
    else:
        case.request = replace(case.request, worker_closure_sha256=digest("f"))
    docker = DockerDouble()
    if bad_field == "worker_closure_sha256":
        with pytest.raises(host.ModelHostRefusal, match="model_identity"):
            _run(case, monkeypatch, docker)
        assert docker.commands == []
        return
    result, _state = _run(case, monkeypatch, docker)
    assert result.status == "failed" and result.code in {"request_identity", "model_identity"}
    assert docker.commands == []


@pytest.mark.parametrize("bad_field", ["phase", "gate"])
def test_wrong_admission_phase_and_hash_domain_refuse_before_docker(tmp_path, monkeypatch, bad_field):
    case = _fixture(tmp_path)
    admission = json.loads(case.admission_raw)
    if bad_field == "phase":
        admission["phase"] = "old-cal5-phase"
    else:
        admission["gate_references"]["study"] = "sha512:" + "0" * 64
    case.paths.admission.write_bytes(_json(admission))
    case.request = replace(case.request, admission_sha256="sha256:" + hashlib.sha256(case.paths.admission.read_bytes()).hexdigest())
    docker = DockerDouble()
    result, _state = _run(case, monkeypatch, docker)
    assert result.code in {"admission_scope", "admission_gates"} and docker.commands == []


@pytest.mark.parametrize("lose_on", ["create", "foreign", "start", "model_poll", "wait"])
def test_foreign_or_lost_outer_authority_never_authorizes_model_result(tmp_path, monkeypatch, lose_on):
    case = _fixture(tmp_path)
    _response_files(case)
    docker = DockerDouble(lose_on=lose_on)
    result, state = _run(case, monkeypatch, docker)
    assert result.status == "failed"
    assert result.primary_code in {"outer_authority_lost", "outer_authority_changed", "model_host_refused"}
    if lose_on in {"start", "model_poll", "wait"}:
        assert result.cleanup_proven and any(command[1] == "rm" for command in docker.commands)


def test_return_authority_loss_keeps_response_evidence_and_cleanup_failure_separate(tmp_path, monkeypatch):
    case = _fixture(tmp_path)
    _response_files(case)
    docker = DockerDouble(fail_rm=True)
    original_response = host._response_bundle
    def lose_after_response(*args, **kwargs):
        bundle = original_response(*args, **kwargs)
        docker.lost = True
        return bundle
    monkeypatch.setattr(host, "_response_bundle", lose_after_response)
    result, _state = _run(case, monkeypatch, docker)
    assert result.code == "cleanup_failed"
    assert result.primary_code == "outer_authority_lost" and result.cleanup_code == "cleanup_failed"
    assert result.response_bundle is not None


def test_reused_output_directory_refuses_after_owned_cleanup(tmp_path, monkeypatch):
    case = _fixture(tmp_path)
    _response_files(case)
    (case.paths.output_root / "foreign-attempt").mkdir()
    docker = DockerDouble()
    result, _state = _run(case, monkeypatch, docker)
    assert result.code == "response_attempt_set" and result.cleanup_proven


def test_stdout_cap_is_charged_and_refused_before_memory_growth(monkeypatch):
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(host, "_bounded_process", lambda *_args: (0, b"x" * 8, b"", False, True))
    with pytest.raises(host.ModelHostRefusal, match="docker_output_bounds"):
        host._bounded_docker(("docker", "inspect", CID), 30.0, 8,
                             lambda name: calls.append((name, 1)), lambda count: calls.append(("bytes", count)))
    assert calls == [("docker.inspect", 1), ("bytes", 2 * (8 + 1) + 65536)]


def test_artifact_verification_reserves_host_and_two_worker_passes_before_failure(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "one").write_bytes(b"x")
    old_files, old_bytes = contract.ARTIFACT_FILES, contract.ARTIFACT_BYTES
    contract.ARTIFACT_FILES, contract.ARTIFACT_BYTES = 1, 1
    try:
        charged: list[int] = []
        profile = SimpleNamespace(source_identity_sha256=contract.ARTIFACT_PROFILE_ID,
                                  pinned_profile_sha256=contract.ARTIFACT_PIN_ID, verified_files=(1,))
        contract._artifact_bank(root, charged.append, verify=lambda _root: profile)
        assert charged == [1, 1, 1, 1, 1]
        with pytest.raises(contract.ModelHostRefusal, match="artifact_profile_identity"):
            contract._artifact_bank(root, charged.append, verify=lambda _root: SimpleNamespace())
        assert charged[-1] == 1
    finally:
        contract.ARTIFACT_FILES, contract.ARTIFACT_BYTES = old_files, old_bytes


def test_budget_distinguishes_phase_deadline_from_aggregate_deadline():
    now = [0.0]
    budget = contract._Budget(lambda: now[0], epoch_started=0.0, response_deadline=3000.0)
    budget.begin("initialization")
    now[0] = 600.0
    with pytest.raises(contract.ModelHostRefusal, match="initialization_timeout"):
        budget.check()
    now[0] = 1700.0
    with pytest.raises(contract.ModelHostRefusal, match="aggregate_timeout"):
        budget.check()


def test_late_function_entry_uses_epoch_anchored_initialization_deadline(tmp_path, monkeypatch):
    case = _fixture(tmp_path)
    docker = DockerDouble()
    result, _state = _run(case, monkeypatch, docker, clock=lambda: 700.0,
                           epoch_started=0.0, response_deadline=800.0)
    assert result.code == "initialization_timeout" and docker.commands == []


def test_epoch_must_not_be_future_and_response_deadline_must_remain(tmp_path, monkeypatch):
    case = _fixture(tmp_path)
    with pytest.raises(host.ModelHostRefusal, match="epoch_invalid"):
        _run(case, monkeypatch, DockerDouble(), clock=lambda: 100.0,
             epoch_started=101.0, response_deadline=700.0)
    with pytest.raises(host.ModelHostRefusal, match="response_deadline_invalid"):
        _run(case, monkeypatch, DockerDouble(), clock=lambda: 100.0,
             epoch_started=100.0, response_deadline=100.0)


def test_response_deadline_expires_during_model_phase():
    now = [100.0]
    budget = contract._Budget(lambda: now[0], epoch_started=0.0, response_deadline=200.0)
    budget.begin("initialization")
    now[0] = 110.0
    budget.begin("model")
    now[0] = 200.0
    with pytest.raises(contract.ModelHostRefusal, match="response_timeout"):
        budget.check()


def test_worker_closure_shape_rejects_extra_file_before_hash_reads(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    expected = {}
    for name in contract.WORKER_SOURCE_FILES:
        payload = name.encode("ascii")
        (source / name).write_bytes(payload)
        expected[name] = "sha256:" + hashlib.sha256(payload).hexdigest()
    (source / "unexpected.py").write_bytes(b"foreign")
    paths = cast(host.ModelHostPaths, SimpleNamespace(source_root=source))
    with pytest.raises(contract.ModelHostRefusal, match="worker_closure_shape"):
        contract._source_closure(paths, expected, lambda _count: None)


def test_response_file_cap_refuses_before_bundle_creation(tmp_path):
    case = _fixture(tmp_path)
    _response_files(case)
    report = case.paths.output_root / case.request.attempt_id / "report.json"
    with report.open("wb") as stream:
        stream.truncate(host.MAX_RESPONSE_BYTES + 1)
    with pytest.raises(host.ModelHostRefusal, match="response_file_bounds"):
        host._response_bundle(case.paths, case.request, case.request_raw, case.admission_raw, lambda _count: None)


def _inspection(case, running=False):
    labels = {"repomap.phase": host.PHASE, "repomap.component": "model-sibling",
              "repomap.attempt": case.request.attempt_id, "repomap.nonce": "c" * 64,
              "repomap.owner": "host-model-controller"}
    name = "repomap-supplement-model-" + "c" * 64
    command = tuple(host._model_command(case.paths, case.request.admission_sha256, labels, name))
    return labels, name, command, _inspect_info(command, CID, labels, running)


def test_stopped_engine_may_omit_actual_tmpfs_but_keeps_configured_bounds(tmp_path):
    case = _fixture(tmp_path)
    labels, name, command, info = _inspection(case)
    info["Mounts"] = [mount for mount in info["Mounts"] if mount["Type"] != "tmpfs"]
    host._verify_model(info, CID, command, labels, name, case.paths)


@pytest.mark.parametrize("mutation", ["partial", "duplicate", "unknown"])
def test_running_engine_requires_exact_two_actual_tmpfs_mounts(tmp_path, mutation):
    case = _fixture(tmp_path)
    labels, name, command, info = _inspection(case, running=True)
    mounts = list(info["Mounts"])
    if mutation == "partial":
        info["Mounts"] = [mount for mount in mounts if mount.get("Destination") != "/tmp"]
    elif mutation == "duplicate":
        info["Mounts"] = mounts + [dict(next(mount for mount in mounts if mount["Type"] == "tmpfs"))]
    else:
        info["Mounts"] = mounts + [{"Type": "tmpfs", "Destination": "/unknown", "RW": True}]
    with pytest.raises(host.ModelHostRefusal, match="model_(mounts|tmpfs)"):
        host._verify_model(info, CID, command, labels, name, case.paths)


def test_uncertain_create_never_claims_cleanup_proven(tmp_path, monkeypatch):
    case = _fixture(tmp_path)
    docker = DockerDouble(lose_on="malformed_create")
    result, _state = _run(case, monkeypatch, docker)
    assert result.status == "failed" and result.cleanup_proven is False
    assert result.cleanup_code == "creation_unknown" and result.model_container_id is None
