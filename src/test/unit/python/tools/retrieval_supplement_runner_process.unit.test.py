"""Bounded launch-failure custody without starting a real runner process."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import sys
from typing import Any, cast

import pytest

import retrieval_supplement_runner_process as owner
from retrieval_supplement_runner_channel import RunnerChannel
from retrieval_supplement_study_contracts import STUDY_SELECTOR


@dataclass
class _ChannelDouble:
    source_root: Path
    runner_argv: tuple[str, ...]
    started_error: BaseException | None = None
    close_calls: int = 0
    started_calls: list[tuple[int, float]] = field(default_factory=list)

    def child_settings(self) -> tuple[dict[str, str], tuple[int, ...]]:
        return {}, ()

    def started(self, pid: int, birth: float) -> None:
        self.started_calls.append((pid, birth))
        if self.started_error is not None:
            raise self.started_error

    def close(self) -> None:
        self.close_calls += 1


@dataclass
class _ProcessDouble:
    pid: int = 4312
    returncode: int = 125
    wait_error: BaseException | None = None
    wait_calls: list[float] = field(default_factory=list)

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(-1.0 if timeout is None else timeout)
        if self.wait_error is not None:
            raise self.wait_error
        return self.returncode


def _config(tmp_path: Path) -> tuple[owner.RunnerProcessConfig, _ChannelDouble]:
    root = tmp_path / "runner-root"
    root.mkdir(mode=0o700)
    argv = (
        sys.executable, "-I", "-B", "tools/run_tests.py", "--suite", "int", "--jobs", "1",
        "--no-coverage", "--pg-test-profile", "pgvector", "--", "-s", STUDY_SELECTOR,
    )
    channel = _ChannelDouble(root, argv)
    config = owner.RunnerProcessConfig(
        argv, root, root / "stdout.log", root / "stderr.log", {},
        cast(RunnerChannel, channel),
    )
    return config, channel


def _identity_process(_pid: int) -> Any:
    class Identity:
        def create_time(self) -> float:
            return 17.0

    return Identity()


def test_post_popen_channel_failure_reports_started_runner_and_reaps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = RuntimeError("synthetic channel-start failure")
    config, channel = _config(tmp_path)
    process = _ProcessDouble()
    channel.started_error = original
    monkeypatch.setattr(owner.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(owner.psutil, "Process", _identity_process)

    with pytest.raises(owner.RunnerLaunchFailure) as raised:
        owner.launch_runner(config)

    failure = raised.value
    assert failure.primary_code == "runner_channel_started_failed"
    assert failure.code == failure.primary_code
    assert failure.runner_started is True and failure.gate_released is False
    assert failure.cleanup_proven is True and failure.cleanup_code is None
    assert failure.__cause__ is original
    assert process.wait_calls == [30.0] and channel.close_calls == 1


def test_popen_failure_is_distinct_and_has_no_started_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = OSError("synthetic spawn failure")
    config, channel = _config(tmp_path)

    def fail_spawn(*_args: object, **_kwargs: object) -> None:
        raise original

    monkeypatch.setattr(owner.subprocess, "Popen", fail_spawn)
    with pytest.raises(owner.RunnerLaunchFailure) as raised:
        owner.launch_runner(config)

    failure = raised.value
    assert failure.primary_code == "runner_spawn_failed"
    assert failure.runner_started is False and failure.gate_released is False
    assert failure.cleanup_proven is True and failure.cleanup_code is None
    assert failure.__cause__ is original and channel.close_calls == 1


def test_gate_failure_is_distinct_from_channel_start_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, channel = _config(tmp_path)
    process = _ProcessDouble()
    monkeypatch.setattr(owner.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(owner.psutil, "Process", _identity_process)

    def fail_gate(_fd: int, _payload: bytes) -> int:
        raise OSError("synthetic gate failure")

    monkeypatch.setattr(owner.os, "write", fail_gate)
    with pytest.raises(owner.RunnerLaunchFailure) as raised:
        owner.launch_runner(config)

    failure = raised.value
    assert failure.primary_code == "runner_gate_release_failed"
    assert failure.runner_started is True and failure.gate_released is False
    assert failure.cleanup_proven is True and process.wait_calls == [30.0]
    assert channel.started_calls and channel.close_calls == 1


def test_failed_bootstrap_wait_is_unknown_not_proven_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = RuntimeError("synthetic channel-start failure")
    config, channel = _config(tmp_path)
    process = _ProcessDouble(wait_error=subprocess.TimeoutExpired("runner", 30))
    channel.started_error = original
    monkeypatch.setattr(owner.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(owner.psutil, "Process", _identity_process)

    with pytest.raises(owner.RunnerLaunchFailure) as raised:
        owner.launch_runner(config)

    failure = raised.value
    assert failure.runner_started is True and failure.gate_released is False
    assert failure.cleanup_proven is False
    assert failure.cleanup_code == "runner_bootstrap_wait_timeout"
    assert failure.__cause__ is original


def test_finally_fd_failure_is_wrapped_with_post_release_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, channel = _config(tmp_path)
    process = _ProcessDouble(returncode=0)
    monkeypatch.setattr(owner.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(owner.psutil, "Process", _identity_process)
    real_close = owner.os.close
    calls = 0

    def fail_once(fd: int) -> None:
        nonlocal calls
        calls += 1
        real_close(fd)
        if calls == 1:
            raise OSError("synthetic fd close failure")

    monkeypatch.setattr(owner.os, "close", fail_once)
    with pytest.raises(owner.RunnerLaunchFailure) as raised:
        owner.launch_runner(config)

    failure = raised.value
    assert failure.primary_code == "runner_fd_close_failed"
    assert failure.runner_started is True and failure.gate_released is True
    assert failure.cleanup_proven is False
    assert failure.cleanup_code == "runner_fd_close_failed"
    assert channel.close_calls == 1 and process.wait_calls == [30.0]
