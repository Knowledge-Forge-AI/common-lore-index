"""Offline bootstrap containment and error propagation without native imports."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import retrieval_supplement_worker_entry as owner


def profile():
    return SimpleNamespace(wheels=tuple(SimpleNamespace(name=f"example{i}", version="1.0",
        sha256="a" * 64) for i in range(20)))


def test_offline_install_is_hash_locked_target_only_and_never_network(tmp_path, monkeypatch):
    monkeypatch.setattr(owner, "TMP_ROOT", tmp_path / "tmp")
    monkeypatch.setattr(owner, "DEPS_ROOT", tmp_path / "deps")
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(owner.subprocess, "run", run)
    monkeypatch.setattr(owner.importlib.metadata, "distributions", lambda **_kwargs:
        [SimpleNamespace(metadata={"Name": wheel.name}, version=wheel.version) for wheel in profile().wheels])
    before = list(owner.sys.path)
    try:
        owner._install(profile())
        command, arguments = calls[0]
        assert all(flag in command for flag in ("--isolated", "--no-index", "--require-hashes",
            "--no-deps", "--no-compile", "--no-cache-dir", "--only-binary=:all:"))
        assert command[command.index("--target") + 1] == str(tmp_path / "deps")
        assert arguments["timeout"] == 120 and arguments["env"]["PIP_NO_CACHE_DIR"] == "1"
        assert arguments["env"]["TMPDIR"] == str(tmp_path / "tmp")
        retained = (tmp_path / "tmp/requirements.txt").read_text()
        assert retained.count(" --hash=sha256:") == 20
    finally:
        owner.sys.path[:] = before


@pytest.mark.parametrize("outcome", ["failed", "timeout", "closure"])
def test_installer_failure_never_adds_a_partial_overlay(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(owner, "TMP_ROOT", tmp_path / "tmp")
    monkeypatch.setattr(owner, "DEPS_ROOT", tmp_path / "deps")
    def run(command, **kwargs):
        if outcome == "timeout":
            raise owner.subprocess.TimeoutExpired(command, 120)
        return SimpleNamespace(returncode=1 if outcome == "failed" else 0)
    monkeypatch.setattr(owner.subprocess, "run", run)
    monkeypatch.setattr(owner.importlib.metadata, "distributions", lambda **_kwargs: [])
    before = list(owner.sys.path)
    try:
        with pytest.raises(owner.WorkerBootstrapError):
            owner._install(profile())
        assert owner.sys.path == before
    finally:
        owner.sys.path[:] = before


def test_supplied_runtime_is_passed_only_through_the_worker_contract(monkeypatch):
    runtime = object()
    run = Mock()
    class WorkerError(ValueError):
        pass
    monkeypatch.setattr(owner, "_source_imports", lambda:
        (lambda _root: profile(), run, WorkerError, ("2.5.4", "1.31.1", "0.23.3")))
    install = Mock(side_effect=AssertionError("No installation for supplied test runtime"))
    monkeypatch.setattr(owner, "_install", install)
    assert owner._run_worker("sha256:" + "a" * 64, runtime_factory=runtime) == 0
    assert run.call_args.kwargs["runtime_factory"] is runtime
    assert run.call_args.kwargs["expected_admission_sha256"] == "sha256:" + "a" * 64
    assert run.call_args.args == (Path("/request.json"), Path("/artifacts"), Path("/output"))
    install.assert_not_called()


def test_worker_failure_does_not_become_success_or_swallow_interrupt(monkeypatch):
    class WorkerError(ValueError):
        pass
    run = Mock(side_effect=WorkerError("synthetic"))
    monkeypatch.setattr(owner, "_source_imports", lambda:
        (lambda _root: profile(), run, WorkerError, ("2.5.4", "1.31.1", "0.23.3")))
    assert owner._run_worker("sha256:" + "a" * 64, runtime_factory=object()) == 1
    run.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        owner._run_worker("sha256:" + "a" * 64, runtime_factory=object())


@pytest.mark.parametrize("change", ["request", "artifacts", "output", "admission", "digest"])
def test_wrong_mount_or_admission_digest_refuses_before_any_source_import(monkeypatch, change):
    values = {"request": "/request.json", "artifacts": "/artifacts", "output": "/output",
        "admission": "/admission.json", "digest": "sha256:" + "a" * 64}
    values[change] = "/foreign" if change != "digest" else "a" * 64
    argv = ["worker", "--request", values["request"], "--artifacts", values["artifacts"],
        "--output", values["output"], "--admission", values["admission"],
        "--admission-sha256", values["digest"]]
    monkeypatch.setattr(owner.sys, "argv", argv)
    run = Mock(side_effect=AssertionError("Worker must not run"))
    monkeypatch.setattr(owner, "_run_worker", run)
    assert owner.main() == 2
    run.assert_not_called()
