"""Offline admission and native lifecycle failures, with Docker fully replaced."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import subprocess
from typing import Any

import pytest

import runner_sandbox as materializer
import test_sandbox_entrypoint as entrypoint
from runner_pgvector_prebuilt import provider_facts
from runner_pgvector_qualification import qualify
from runner_pgvector_sandbox import admit_inner

ARGV = ["--suite", "int", "--no-coverage", "--pg-test-profile", "pgvector",
        "--", "src/test/int/python/repomap_test_support/pgvector_prebuilt_runtime.int.test.py"]
IMAGE = "sha256:" + "b" * 64
CONTAINER = "a" * 64


class NativeDouble:
    """Exercise the real qualifier; replace only external commands."""

    def __init__(self, failure: str = "") -> None:
        self.failure = failure
        self.calls: list[tuple[str, list[str]]] = []

    def execute(self, stage: str, command: list[str], *, check: bool = True,
                timeout: int = 300) -> subprocess.CompletedProcess[str]:
        self.calls.append((stage, command))
        assert 0 < timeout <= 300
        code, out, error = 0, "", ""
        if stage == "native_create":
            out = CONTAINER
            assert "--user" in command and command[command.index("--user") + 1] == "postgres"
            assert "--network" in command and command[command.index("--network") + 1] == "none"
            assert "--read-only" in command and "--cap-drop" in command and "--pull=never" in command
            assert "--mount" not in command and "-p" not in command and "-v" not in command
        elif stage in {"native_status_before", "native_status_after"}:
            out = provider_facts()["target_dpkg_status_sha256"] + "  /var/lib/dpkg/status\n"
            if self.failure == stage:
                out = "0" * 64
        elif stage == "native_postgres_version":
            out = "16.16-1.pgdg13+1"
        elif stage == "native_libc_version":
            out = "2.41-12+deb13u4"
        elif stage == "native_provides":
            out = "postgresql-16-jit-llvm (= 19), postgresql-contrib-16"
            if self.failure == stage:
                out = "postgresql-contrib-16"
        elif stage == "native_breaks_constraint":
            code = 0 if self.failure == stage else 1
        elif stage == "native_extension":
            out = "16|0.8.7|0|1\n/usr/lib/postgresql/16/lib\n/usr/share/postgresql/16\noff\n"
            substitutions = {"version": ("0.8.7", "0.8.6"), "major": ("16|", "17|"),
                "self": ("|0|1", "|0.5|1"), "orthogonal": ("|0|1", "|0|0.9"),
                "jit": ("off", "on"), "path": ("/usr/lib/postgresql/16/lib", "/wrong")}
            if self.failure in substitutions:
                out = out.replace(*substitutions[self.failure])
            if self.failure in {"load", "symbol", "dependency", "abi", "sql"}:
                code, error = 1, "synthetic " + self.failure + " failure"
        elif stage == "native_index_rollback":
            name = "wrong_index" if self.failure == "index" else "cloe_native_vectors_cosine"
            out = json.dumps([{"Plan": {"Node Type": "Limit", "Plans": [
                {"Node Type": "Index Scan", "Index Name": name}]}}])
            out += "\n" + ("f" if self.failure == "rollback" else "t") + "\n"
        elif stage == "native_cleanup_readback" and self.failure == stage:
            out = CONTAINER
        if self.failure == stage and stage not in {
            "native_status_before", "native_status_after", "native_provides", "native_breaks_constraint",
            "native_cleanup_readback",
        }:
            code, error = 2, "synthetic command failure"
        result = subprocess.CompletedProcess(command, code, out, error)
        if check and code:
            raise RuntimeError(stage + ": " + error)
        return result


def test_native_success_owns_one_cluster_and_verifies_rollback_cleanup():
    native = NativeDouble()
    report = qualify(native.execute, lambda *args, **kwargs: None, IMAGE)
    assert report["native_lifecycles"] == 1 and report["cleanup_proven"]
    assert report["production_or_scientific_admission"] is False
    stages = [stage for stage, _ in native.calls]
    assert stages.count("native_create") == 1 and stages.count("native_index_rollback") == 1
    assert stages.count("native_cleanup") == 1 and stages.count("native_cleanup_readback") == 1
    assert not any("make" in command or "apk" in command or "pull" in command
                   or "commit" in command for _, command in native.calls)


@pytest.mark.parametrize("failure", [
    "load", "symbol", "dependency", "abi", "sql", "version", "major", "self", "orthogonal",
    "jit", "path", "index", "rollback", "native_status_before", "native_status_after",
    "native_postgres_constraint", "native_libc_constraint", "native_breaks_constraint",
    "native_provides", "native_readiness", "native_cleanup", "native_cleanup_readback", "native_diagnostics",
])
def test_native_failure_never_returns_qualified_and_always_attempts_exact_cleanup(failure):
    native = NativeDouble(failure)
    with pytest.raises(RuntimeError):
        qualify(native.execute, lambda *args, **kwargs: None, IMAGE)
    stages = [stage for stage, _ in native.calls]
    assert stages.count("native_create") == 1
    assert "native_cleanup" in stages and "native_cleanup_readback" in stages


def test_core_never_acquires_or_admits_a_provider():
    with patch.object(materializer, "admit_inner") as admit, \
         patch.object(materializer.subprocess, "run") as command:
        assert materializer._materialize_inner_pgvector_if_requested(
            ["--suite", "int", "--no-coverage", "--", "test.py"], {}) is None
    admit.assert_not_called()
    command.assert_not_called()


def test_body_role_uses_body_admission_without_automatic_qualification(monkeypatch):
    from retrieval_body_study_contracts import BODY_SELECTOR
    import runner_body_study_sandbox as body_sandbox
    calls = []
    monkeypatch.setattr("runner_pgvector_context.load_inner_plan", lambda _env: {"role": "body-study"})
    def body_admission(argv, environment, execute):
        calls.append((tuple(argv), environment, execute))
        return {"pgvector_setup": "synthetic-body-admission", "automatic_native_lifecycles": 0}
    monkeypatch.setattr(body_sandbox, "admit_body_study_inner", body_admission)
    forbidden = MagicMock(side_effect=AssertionError("no legacy automatic qualification"))
    monkeypatch.setattr(materializer, "admit_inner", forbidden)
    monkeypatch.setattr(materializer, "admit_study_inner", forbidden)
    argv = [*ARGV[:-1], "-s", BODY_SELECTOR]
    report = materializer._materialize_inner_pgvector_if_requested(argv, {}, runner_fn=forbidden)
    assert len(calls) == 1 and calls[0][0] == tuple(argv)
    assert report == {"pgvector_setup": "synthetic-body-admission", "automatic_native_lifecycles": 0}
    forbidden.assert_not_called()


def test_legacy_source_hook_does_not_reactivate_the_retired_compiler():
    commands = MagicMock()
    with pytest.raises(RuntimeError, match="retired"):
        materializer._materialize_inner_pgvector_if_requested(
            ARGV, {}, runner_fn=commands, archive_verifier_fn=lambda *args: None)
    commands.assert_not_called()


def test_import_admission_does_not_start_an_automatic_smoke_cluster(monkeypatch):
    config: dict[str, Any] = {"rootfs": {"diff_ids": ["sha256:" + "c" * 64]},
        "config": {"Labels": {"owner": "synthetic"}, "Entrypoint": ["synthetic-entry"]}}
    plan = {"role": "native", "derived": {"runtime_config": IMAGE, "runtime_config_document": config}}
    observed = {"Id": IMAGE, "Os": "linux", "Architecture": "arm64",
        "RootFS": {"Layers": config["rootfs"]["diff_ids"]},
        "Config": {"Cmd": ["postgres"], **config["config"]}}
    monkeypatch.setattr("runner_pgvector_sandbox.inner_plan", lambda *args: plan)
    calls: list[list[str]] = []
    def execute(stage, command):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps([observed]), "")
    environment: dict[str, str] = {}
    report = admit_inner(ARGV, environment, execute)
    assert report["automatic_native_lifecycles"] == 0 and report["image_built"] is False
    assert environment["REPOMAP_TEST_PG_IMAGE"] == IMAGE
    assert environment["REPOMAP_TEST_PG_PROFILE"] == "pgvector"
    assert [command[1:3] for command in calls] == [["image", "inspect"]]


@pytest.mark.parametrize("mutation", ["architecture", "diffids", "labels", "entrypoint"])
def test_import_identity_failure_does_not_publish_environment(monkeypatch, mutation):
    config: dict[str, Any] = {"rootfs": {"diff_ids": ["sha256:" + "c" * 64]},
              "config": {"Labels": {"owner": "synthetic"}, "Entrypoint": ["synthetic-entry"]}}
    plan = {"role": "native", "derived": {"runtime_config": IMAGE, "runtime_config_document": config}}
    observed: dict[str, Any] = {"Id": IMAGE, "Os": "linux", "Architecture": "arm64",
                "RootFS": {"Layers": config["rootfs"]["diff_ids"]},
                "Config": {"Cmd": ["postgres"], **config["config"]}}
    if mutation == "architecture":
        observed["Architecture"] = "amd64"
    elif mutation == "diffids":
        observed["RootFS"] = {"Layers": []}
    elif mutation == "labels":
        observed["Config"] = dict(observed["Config"], Labels={})
    else:
        observed["Config"] = dict(observed["Config"], Entrypoint=[])
    monkeypatch.setattr("runner_pgvector_sandbox.inner_plan", lambda *args: plan)
    calls: list[list[str]] = []
    def execute(stage, command):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps([observed]), "")
    env: dict[str, str] = {}
    with pytest.raises(RuntimeError):
        admit_inner(ARGV, env, execute)
    assert "REPOMAP_TEST_PG_IMAGE" not in env and "REPOMAP_TEST_PG_PROFILE" not in env
    assert len(calls) == 1 and calls[0][1:3] == ["image", "inspect"]


def test_setup_diagnostics_bound_redact_and_preserve_timeout(capsys):
    token = "test-private-token"
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 300, output=b"out\xff", stderr=(
            token + " /Users/example/private/archive " + "x" * 5000).encode())
    setup = materializer._SetupCommands({"AUTH_TOKEN": token}, timeout)
    with pytest.raises(RuntimeError, match="archive_transfer: status=124"):
        setup.execute("archive_transfer", ["docker", "exec"], payload=b"\x00\xff")
    output = capsys.readouterr().out
    assert token not in output and "/Users/example" not in output
    event = [json.loads(line) for line in output.splitlines() if line.startswith("{")][-1]
    assert event["returncode"] == 124 and event["output_truncated"]
    assert len(event["stderr"]) == 4096 and event["stderr_bytes"] > 4096


@pytest.mark.parametrize("cleanup_failed", [False, True])
def test_entrypoint_never_launches_tests_after_admission_failure(cleanup_failed):
    daemon = MagicMock()
    daemon.poll.return_value = None
    if cleanup_failed:
        daemon.terminate.side_effect = OSError("daemon cleanup denied")
    with patch.object(entrypoint.subprocess, "Popen", return_value=daemon) as launch, \
         patch("pathlib.Path.exists", return_value=True), \
         patch.object(entrypoint, "_test_environment", return_value={}), \
         patch.object(entrypoint, "_materialize_inner_pgvector_if_requested",
                      side_effect=RuntimeError("offline identity failed")), \
         pytest.raises(RuntimeError, match="offline identity failed") as error:
        entrypoint.main(["--docker-group", "0", "--", *ARGV])
    assert launch.call_count == 1
    daemon.terminate.assert_called_once()
    if cleanup_failed:
        assert "cleanup also failed" in error.value.__notes__[0]
