"""Owned BODY runner argv, frame, and launch-failure protocol cases."""
from __future__ import annotations

from pathlib import Path
from subprocess import Popen
from types import SimpleNamespace
from typing import cast

import pytest

from retrieval_body_study_contracts import BODY_SDK_IMAGE_SHA256, BodyStudyRefusal
from retrieval_body_study_host_controller import RunnerChannel
from retrieval_body_study_host_runner_output import CONTROL_PREFIXES, RunnerOutput
from retrieval_body_study_host_runner_process import (
    BodyRunnerLaunchFailure, RunnerProcess, RunnerProcessConfig, validate_config,
)
from common_lore_index_test_support.retrieval_body_study_host_cases import body_plan

def _config(root: Path, argv: tuple[str, ...] | None = None) -> RunnerProcessConfig:
    selector = body_plan()["selector"]
    argv = argv or ("/tmp/python3", "-I", "-B", "tools/run_tests.py", "--suite", "int",
        "--jobs", "1", "--no-coverage", "--pg-container-port", "55433",
        "--pg-test-profile", "pgvector", "--report-dir", str(root / "report"), "--", "-s", selector)
    channel = SimpleNamespace(source_root=root, runner_argv=argv)
    return RunnerProcessConfig(argv, root, root / "stdout.log", root / "stderr.log", {}, channel)


def test_valid_runner_config_is_closed_scope(tmp_path: Path) -> None:
    validate_config(_config(tmp_path))


def test_runner_channel_owns_and_closes_real_pipe_pair(tmp_path: Path) -> None:
    config = _config(tmp_path)
    channel = RunnerChannel(config.source_root, config.argv, "sha256:" + "a" * 64, BODY_SDK_IMAGE_SHA256)
    environment, descriptors = channel.child_settings()
    assert len(descriptors) == 2 and all(name in environment for name in (
        "CLOE_SANDBOX_ATTEST_CONTROL_FD", "CLOE_SANDBOX_ATTEST_RECEIPT_FD"))
    channel.close()


@pytest.mark.parametrize("mutation", (
    lambda argv: argv[:1] + ("python",) + argv[2:],
    lambda argv: argv[:-2] + ("-s", "wrong-selector"),
    lambda argv: argv[:argv.index("--no-coverage")] + argv[argv.index("--no-coverage") + 1:],
    lambda argv: argv[:argv.index("--jobs") + 1] + ("2",) + argv[argv.index("--jobs") + 2:],
))
def test_runner_config_corruptions_refuse_without_spawn(tmp_path: Path, mutation) -> None:
    base = _config(tmp_path).argv
    changed = mutation(base)
    config = _config(tmp_path, changed)
    with pytest.raises(BodyStudyRefusal):
        validate_config(config)


def test_runner_output_reads_real_bounded_control_frame(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.stdout_path.write_bytes(CONTROL_PREFIXES[0] + b"body-epoch\n")
    config.stdout_path.chmod(0o600)
    runner = SimpleNamespace(config=config, process=SimpleNamespace(poll=lambda: None),
                             require_live=lambda: None)
    charged: list[int] = []
    reader = RunnerOutput(cast(RunnerProcess, runner), charged.append)
    reader.poll()
    assert reader.events.pop(0).startswith(CONTROL_PREFIXES[0])
    reader.finish()
    assert charged and sum(charged) == len(CONTROL_PREFIXES[0]) + len(b"body-epoch\n")


def test_runner_output_rejects_control_frame_order(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.stdout_path.write_bytes(CONTROL_PREFIXES[1] + b"wrong\n")
    config.stdout_path.chmod(0o600)
    runner = SimpleNamespace(config=config, process=SimpleNamespace(poll=lambda: None),
                             require_live=lambda: None)
    reader = RunnerOutput(cast(RunnerProcess, runner), lambda _count: None)
    reader.poll()
    with pytest.raises(BodyStudyRefusal):
        reader.await_frame(CONTROL_PREFIXES[0].decode("ascii"), deadline=float("inf"), require_current=lambda: None)


def test_launch_failure_preserves_primary_and_cleanup_state() -> None:
    failure = BodyRunnerLaunchFailure("body_runner_spawn_failed", runner_started=True,
                                      gate_released=False, cleanup_proven=False,
                                      cleanup_code="body_runner_cleanup_unknown")
    assert failure.code == "body_runner_spawn_failed"
    assert failure.runner_started is True and failure.gate_released is False
    assert failure.cleanup_proven is False and failure.cleanup_code == "body_runner_cleanup_unknown"


def test_launch_post_release_fd_failure_retains_the_exact_started_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import retrieval_body_study_host_runner_process as process_subject

    config_base = _config(tmp_path)
    wait_calls: list[object] = []
    process = cast(Popen[bytes], SimpleNamespace(pid=77, poll=lambda: None, returncode=None,
                              wait=lambda **_kwargs: wait_calls.append(object())))
    channel = SimpleNamespace(
        source_root=tmp_path, runner_argv=config_base.argv,
        child_settings=lambda: ({}, ()), started=lambda *_args: None,
    )
    config = RunnerProcessConfig(config_base.argv, tmp_path, config_base.stdout_path,
                                 config_base.stderr_path, {}, channel)
    output_fds = iter((11, 12))
    monkeypatch.setattr(process_subject, "_private_output", lambda _path: next(output_fds))
    monkeypatch.setattr(process_subject.os, "pipe", lambda: (13, 14))
    monkeypatch.setattr(process_subject.subprocess, "Popen",
                        lambda *_args, **_kwargs: process)
    monkeypatch.setattr(process_subject.psutil, "Process",
                        lambda _pid: SimpleNamespace(create_time=lambda: 1.0))
    monkeypatch.setattr(process_subject.os, "write", lambda _fd, _payload: 1)
    close_error = RuntimeError("stdout close")
    monkeypatch.setattr(process_subject, "_close_fd",
                        lambda fd: close_error if fd == 11 else None)

    with pytest.raises(BodyRunnerLaunchFailure) as raised:
        process_subject.launch_runner(config)
    failure = raised.value
    assert failure.runner_started is True and failure.gate_released is True
    assert failure.runner is not None and failure.runner.process is process
    assert failure.cleanup_proven is False
    assert wait_calls == []
