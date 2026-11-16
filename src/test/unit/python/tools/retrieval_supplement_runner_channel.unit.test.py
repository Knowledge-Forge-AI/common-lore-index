"""Study host pipe framing and live source/SDK/marker joins without Docker."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import psutil
import pytest

from retrieval_supplement_runner_channel import RunnerChannel
from retrieval_supplement_runner_lease import RunnerLease
from retrieval_supplement_runner_process import RunnerProcess, RunnerProcessConfig
from retrieval_supplement_ownership_wire import encode_receipt, scope_sha256
from retrieval_supplement_study_contracts import SDK_IMAGE_SHA256, STUDY_SELECTOR
from test_sandbox_ownership import CHANNEL_ENV, SCOPE_ENV, prepare_ownership_reporter


def argv() -> tuple[str, ...]:
    return (sys.executable, "-I", "-B", "tools/run_tests.py", "--suite", "int", "--jobs", "1",
        "--no-coverage", "--pg-test-profile", "pgvector", "--report-dir", "/reports/study", "--",
        "-s", STUDY_SELECTOR)


@pytest.fixture
def pipe_channel(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    channel = RunnerChannel(tmp_path, argv(), "sha256:" + "a" * 64, SDK_IMAGE_SHA256)
    settings, descriptors = channel.child_settings()
    # Model child-side descriptor ownership without launching any process.
    reader, writer = (os.dup(descriptor) for descriptor in descriptors)
    identities = {descriptor: (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino,
        os.fstat(descriptor).st_mode) for descriptor in (reader, writer)}
    for name, descriptor in zip(CHANNEL_ENV, (reader, writer)):
        monkeypatch.setenv(name, str(descriptor))
    for name in SCOPE_ENV:
        monkeypatch.setenv(name, settings[name])
    channel.started(os.getpid(), psutil.Process().create_time())
    try:
        yield channel, reader, writer
    finally:
        channel.close()
        for descriptor in (reader, writer):
            try:
                observed = os.fstat(descriptor)
                if (observed.st_dev, observed.st_ino, observed.st_mode) == identities[descriptor]:
                    os.close(descriptor)
            except OSError:
                pass


def test_real_pipe_parent_child_roundtrip_is_closed_once(pipe_channel, tmp_path):
    channel, _, _ = pipe_channel
    assert channel.poll() is None
    reporter = prepare_ownership_reporter(list(argv()[4:]), tmp_path)
    assert reporter is not None
    reporter("c" * 64, SDK_IMAGE_SHA256, "e" * 64)
    receipt = channel.poll()
    assert receipt is not None and receipt["cid"] == "c" * 64
    assert receipt["marker_sha256"] == "sha256:" + hashlib.sha256(("e" * 64).encode()).hexdigest()
    assert channel.poll() == receipt
    with pytest.raises(ValueError, match="already_started"):
        channel.started(os.getpid(), psutil.Process().create_time())


@pytest.mark.parametrize("payload", [b"{}", b"x" * 4097, b'{"schema":1,"schema":2}'])
def test_receipt_rejects_partial_oversized_and_duplicate_frames(pipe_channel, payload):
    channel, _, writer = pipe_channel
    if len(payload) <= 512:
        os.write(writer, payload)
        os.close(writer)
        with pytest.raises((ValueError, RuntimeError)):
            channel.poll()
        return
    failure = None
    for offset in range(0, len(payload), 512):
        os.write(writer, payload[offset:offset + 512])
        try:
            channel.poll()
        except (ValueError, RuntimeError) as error:
            failure = error
            break
    os.close(writer)
    assert isinstance(failure, (ValueError, RuntimeError))


def test_closed_empty_channel_is_missing_authority(pipe_channel):
    channel, _, writer = pipe_channel
    os.close(writer)
    assert channel.poll() is None and channel.eof


def test_complete_receipt_still_waits_for_owned_sender_eof(pipe_channel, tmp_path):
    channel, _, writer = pipe_channel
    extra = os.dup(writer)
    try:
        reporter = prepare_ownership_reporter(list(argv()[4:]), tmp_path)
        assert reporter is not None
        reporter("c" * 64, SDK_IMAGE_SHA256, "e" * 64)
        assert channel.poll() is None
    finally:
        os.close(extra)
    assert channel.poll() is not None


@pytest.mark.parametrize("change", ["scope", "challenge", "image"])
def test_compact_receipt_join_rejects_scope_challenge_or_image(pipe_channel, change):
    channel, _, writer = pipe_channel
    assert channel.control is not None
    scope = scope_sha256(channel.control["argv"], channel.control["source_root"],
                         channel.control["source_identity"], channel.control["sdk_image_id"])
    if change == "scope":
        scope = "sha256:" + "f" * 64
    challenge = "b" * 64 if change == "challenge" else channel.control["challenge"]
    image = "sha256:" + "f" * 64 if change == "image" else channel.control["sdk_image_id"]
    os.write(writer, encode_receipt(challenge, scope, "c" * 64, image, "sha256:" + "e" * 64))
    os.close(writer)
    with pytest.raises((ValueError, RuntimeError), match="join|frame"):
        channel.poll()


def test_compact_receipt_frame_is_bounded_and_duplicate_keys_refuse(pipe_channel):
    channel, _, writer = pipe_channel
    os.write(writer, b'{"schema":"cloe-study-host-receipt-v2","schema":"x"}')
    os.close(writer)
    with pytest.raises((ValueError, RuntimeError)):
        channel.poll()


def inspection(root: Path) -> dict:
    return {"Id": "c" * 64, "Image": SDK_IMAGE_SHA256, "State": {"Running": True},
        "Config": {"Labels": {"org.repomap.test.sandbox": "true",
            "org.repomap.test.sandbox.backend": "monolithic-dind-v1"},
            "Cmd": ["docker-init", "--", "python3", "/workspace-ro/tools/test_sandbox_entrypoint.py",
                "--docker-group", "100", "--", *argv()[4:]]},
        "Mounts": [{"Type": "bind", "Source": str(root), "Destination": "/workspace-ro", "RW": False}]}


def make_lease(channel, root: Path, remaining=100.0):
    process = Mock()
    process.pid = os.getpid()
    process.poll.return_value = None
    config = RunnerProcessConfig(argv(), root, root / "stdout", root / "stderr", {}, channel)
    handle = RunnerProcess(process, psutil.Process().create_time(), config)
    checks = []
    lease = RunnerLease(handle, lambda: checks.append("source"), lambda: remaining,
        lambda _count: None, lambda _operation: None)
    return lease, checks


def test_live_receipt_inspection_marker_and_source_are_all_required(pipe_channel, tmp_path, monkeypatch):
    import json
    import subprocess
    import retrieval_supplement_runner_lease as owner

    channel, _, _ = pipe_channel
    reporter = prepare_ownership_reporter(list(argv()[4:]), tmp_path)
    assert reporter is not None
    reporter("c" * 64, SDK_IMAGE_SHA256, "e" * 64)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        output = (json.dumps([inspection(tmp_path)]).encode() if command[1] == "inspect" else
            ("sha256:" + hashlib.sha256(("e" * 64).encode()).hexdigest() + "\n").encode())
        assert kwargs["timeout"] <= 30
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"")
    monkeypatch.setattr(owner, "_bounded_docker", run)
    lease, checks = make_lease(channel, tmp_path)
    assert lease.require_outer() == "c" * 64
    assert [command[1] for command in calls] == ["inspect", "exec"]
    assert len(checks) == 2 and lease.receipt_commitment().startswith("sha256:")
    assert channel.receipt is not None
    channel.receipt["source_identity"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="control_join"):
        lease.require_outer()
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["image", "source", "selector", "marker", "stopped"])
def test_changed_sdk_scope_or_marker_refuses(pipe_channel, tmp_path, monkeypatch, change):
    import json
    import subprocess
    import retrieval_supplement_runner_lease as owner

    channel, _, _ = pipe_channel
    reporter = prepare_ownership_reporter(list(argv()[4:]), tmp_path)
    assert reporter is not None
    reporter("c" * 64, SDK_IMAGE_SHA256, "e" * 64)
    observed = inspection(tmp_path)
    if change == "image": observed["Image"] = "sha256:" + "f" * 64
    if change == "source": observed["Mounts"][0]["Source"] = "/foreign"
    if change == "selector": observed["Config"]["Cmd"][-1] = "foreign.int.test.py"
    if change == "stopped": observed["State"]["Running"] = False
    def run(command, **kwargs):
        marker = "f" * 64 if change == "marker" else "e" * 64
        raw = (json.dumps([observed]).encode() if command[1] == "inspect" else
            ("sha256:" + hashlib.sha256(marker.encode()).hexdigest() + "\n").encode())
        return subprocess.CompletedProcess(command, 0, stdout=raw, stderr=b"")
    monkeypatch.setattr(owner, "_bounded_docker", run)
    lease, _ = make_lease(channel, tmp_path)
    with pytest.raises((ValueError, RuntimeError)):
        lease.require_outer()


def test_deadline_or_missing_receipt_starts_no_docker(pipe_channel, tmp_path, monkeypatch):
    import retrieval_supplement_runner_lease as owner
    channel, _, _ = pipe_channel
    run = Mock(side_effect=AssertionError("Docker must not run"))
    monkeypatch.setattr(owner, "_bounded_docker", run)
    lease, _ = make_lease(channel, tmp_path, remaining=0.0)
    with pytest.raises(ValueError, match="receipt_pending"):
        lease.require_outer()
    reporter = prepare_ownership_reporter(list(argv()[4:]), tmp_path)
    assert reporter is not None
    reporter("c" * 64, SDK_IMAGE_SHA256, "e" * 64)
    with pytest.raises(ValueError, match="deadline"):
        lease.require_outer()
    run.assert_not_called()
