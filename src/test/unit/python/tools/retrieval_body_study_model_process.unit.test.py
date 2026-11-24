"""Pure child-process settlement proofs for the BODY model host."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

import retrieval_body_study_model_host as subject
from retrieval_body_study_model_contract import (
    ARTIFACT_PROFILE_ID, SDK_IMAGE_ID, WORKER_CLOSURE_ID,
    ModelHostPaths, ModelHostRefusal, ValidatedStudyRequest,
)

CID = "c" * 64


class _Stream:
    def __init__(self, fd: int) -> None:
        self._fd = fd
        self.closed = False

    def fileno(self) -> int:
        return self._fd

    def close(self) -> None:
        self.closed = True


class _Selector:
    def __init__(self, *, interrupt: bool) -> None:
        self.interrupt = interrupt
        self.closed = False

    def register(self, *_args: object) -> None:
        return None

    def get_map(self) -> dict[int, object]:
        return {1: object()}

    def select(self, *_args: object) -> list[object]:
        if self.interrupt:
            raise KeyboardInterrupt
        return []

    def close(self) -> None:
        self.closed = True


class _Process:
    def __init__(self, *, proves_absence: bool) -> None:
        self.stdout, self.stderr = _Stream(1), _Stream(2)
        self.proves_absence = proves_absence
        self.returncode: int | None = None
        self.kill_count = 0
        self.wait_count = 0

    def kill(self) -> None:
        self.kill_count += 1
        if self.proves_absence:
            self.returncode = -9

    def wait(self, *, timeout: float) -> int | None:
        self.wait_count += 1
        if self.proves_absence:
            self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


def _bounded_setup(monkeypatch: pytest.MonkeyPatch, process: _Process,
                   selector: _Selector) -> None:
    monkeypatch.setattr(subject.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(subject.selectors, "DefaultSelector", lambda: selector)
    monkeypatch.setattr(subject.os, "set_blocking", lambda *_args: None)


def test_keyboard_interrupt_settles_exact_child_and_preserves_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    process, selector = _Process(proves_absence=True), _Selector(interrupt=True)
    _bounded_setup(monkeypatch, process, selector)
    with pytest.raises(KeyboardInterrupt):
        subject._bounded_process(("docker", "inspect", CID), 1.0, 64)
    assert process.kill_count == 1 and process.wait_count == 1
    assert selector.closed and process.stdout.closed and process.stderr.closed


def test_unproven_child_settlement_raises_stable_cleanup_code(monkeypatch: pytest.MonkeyPatch) -> None:
    process, selector = _Process(proves_absence=False), _Selector(interrupt=True)
    _bounded_setup(monkeypatch, process, selector)
    with pytest.raises(ModelHostRefusal, match="docker_process_cleanup_unknown"):
        subject._bounded_process(("docker", "inspect", CID), 1.0, 64)
    assert process.kill_count == 2 and process.wait_count == 2
    assert selector.closed and process.stdout.closed and process.stderr.closed


def test_run_model_sibling_does_not_mask_cli_cleanup_uncertainty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    paths = ModelHostPaths(*(tmp_path / name for name in
                             ("request.json", "admission.json", "artifacts", "worker-ro", "entry.py", "output")))
    request = ValidatedStudyRequest(
        "attempt-body", "sha256:" + "a" * 64, "sha256:" + "b" * 64,
        "sha256:" + "c" * 64, "sha256:" + "d" * 64, "sha256:" + "e" * 64,
        "canonical-node-v1", 66, 4, ARTIFACT_PROFILE_ID, WORKER_CLOSURE_ID, 70, {},
        "sha256:" + hashlib.sha256(b"entry").hexdigest())
    inspect_count = 0
    image = {"Id": SDK_IMAGE_ID, "Architecture": "arm64", "Config": {"Labels": {}}}

    def docker(argv: tuple[str, ...], *_args: object, **_kwargs: object) -> object:
        nonlocal inspect_count
        if argv[1] == "image":
            return SimpleNamespace(returncode=0, stdout=json.dumps([image]).encode(), stderr=b"")
        if argv[1] == "create":
            return SimpleNamespace(returncode=0, stdout=(CID + "\n").encode(), stderr=b"")
        if argv[1] == "inspect":
            inspect_count += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps([{"State": {"Running": inspect_count == 2}}]).encode(), stderr=b"")
        if argv[1] == "start":
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if argv[1] == "wait":
            raise ModelHostRefusal("docker_process_cleanup_unknown")
        if argv[1] == "rm":
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if argv[1] == "container":
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"No such container")
        raise AssertionError(argv)

    monkeypatch.setattr(subject, "_fixed_path", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(subject, "_source_closure", lambda *_args: None)
    monkeypatch.setattr(subject, "_artifact_bank", lambda *_args: None)
    monkeypatch.setattr(subject, "_read_stable", lambda *_args: b"entry")
    monkeypatch.setattr(subject, "_validate_request", lambda *_args: {})
    monkeypatch.setattr(subject, "_validate_admission", lambda *_args: None)
    monkeypatch.setattr(subject, "_model_command", lambda *_args: ("docker", "create", "body"))
    monkeypatch.setattr(subject, "_verify_model", lambda *_args: None)
    monkeypatch.setattr(subject, "_bounded_docker", docker)
    result = subject.run_model_sibling(
        request, paths, nonce="a" * 64, require_outer=lambda: "e" * 64,
        charge_bytes=lambda _count: None, verify_artifacts=lambda _root: None,
        charge_operation=lambda _name: None, epoch_started=time.monotonic() - 1,
        response_deadline=time.monotonic() + 600)
    assert result.status == "failed"
    assert result.code == "docker_process_cleanup_unknown", (
        result.status, result.code, result.primary_code, result.cleanup_code)
    assert result.primary_code == "docker_process_cleanup_unknown"
    assert result.cleanup_code == "docker_process_cleanup_unknown"
    assert result.cleanup_proven is False
