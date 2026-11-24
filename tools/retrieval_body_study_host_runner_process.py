"""BODY-owned canonical runner launch and bootstrap custody."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any

import psutil

from retrieval_body_study_contracts import BODY_SELECTOR, refuse

_BOOTSTRAP = ("import os,sys;fd=int(sys.argv[1]);token=os.read(fd,1);os.close(fd);"
              "os._exit(125) if token != b'\\x01' else os.execv(sys.argv[2],sys.argv[2:])")
_CODE = re.compile(r"\A[a-z0-9_]+\Z")


class BodyRunnerLaunchFailure(RuntimeError):
    """Public-safe launch failure retaining truthful cleanup state."""

    def __init__(self, primary_code: str, *, runner_started: bool,
                 gate_released: bool, cleanup_proven: bool,
                 cleanup_code: str | None = None,
                 runner: RunnerProcess | None = None) -> None:
        self.primary_code = primary_code if _CODE.fullmatch(primary_code) else "body_runner_launch_failed"
        self.code = self.primary_code
        self.runner_started = bool(runner_started)
        self.gate_released = bool(gate_released)
        self.cleanup_proven = bool(cleanup_proven)
        self.cleanup_code = None if self.cleanup_proven else (
            cleanup_code if cleanup_code is not None and _CODE.fullmatch(cleanup_code)
            else "body_runner_cleanup_unknown")
        self.runner = runner
        super().__init__(self.primary_code)


@dataclass(frozen=True)
class RunnerProcessConfig:
    argv: tuple[str, ...]
    source_root: Path = field(repr=False)
    stdout_path: Path = field(repr=False)
    stderr_path: Path = field(repr=False)
    environment: Mapping[str, str] = field(repr=False)
    channel: Any = field(repr=False)


@dataclass(repr=False)
class RunnerProcess:
    process: subprocess.Popen[bytes]
    birth: float
    config: RunnerProcessConfig

    def require_live(self) -> None:
        try:
            current = psutil.Process(self.process.pid)
            refuse(current.create_time() == self.birth and current.is_running()
                   and current.status() != psutil.STATUS_ZOMBIE
                   and self.process.poll() is None, "body runner process identity")
        except psutil.Error:
            raise RuntimeError("body runner process identity unavailable") from None


def _private_output(path: Path) -> int:
    refuse(path.is_absolute() and not path.is_symlink(), "body runner output path")
    try:
        for parent in (path.parent, *path.parent.parents):
            info = parent.lstat()
            refuse(stat.S_ISDIR(info.st_mode) and not parent.is_symlink(),
                   "body runner output custody")
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                       getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), 0o600)
    except OSError:
        raise RuntimeError("body runner output unavailable") from None


def validate_config(config: RunnerProcessConfig) -> None:
    argv = config.argv
    refuse(type(argv) is tuple and len(argv) > 8 and all(type(item) is str and item for item in argv),
           "body runner argv")
    refuse(Path(argv[0]).is_absolute() and Path(argv[0]).name.startswith("python")
           and argv[1:4] == ("-I", "-B", "tools/run_tests.py"), "body runner entrypoint")
    refuse(config.source_root.is_absolute() and not config.source_root.is_symlink()
           and config.source_root.is_dir(), "body runner source root")
    refuse(argv.count("--") == 1 and argv[argv.index("--") + 1:] == ("-s", BODY_SELECTOR),
           "body runner selector")
    options = argv[4:argv.index("--")]
    for name, value in (("--suite", "int"), ("--jobs", "1"), ("--pg-test-profile", "pgvector")):
        refuse(options.count(name) == 1 and options.index(name) + 1 < len(options)
               and options[options.index(name) + 1] == value, "body runner scope")
    refuse(options.count("--no-coverage") == 1, "body runner coverage")
    refuse(config.channel.source_root == config.source_root and config.channel.runner_argv == argv,
           "body runner channel scope")


_LAUNCH_CODES = {
    "stdout_open": "body_runner_stdout_open_failed", "stderr_open": "body_runner_stderr_open_failed",
    "pipe_setup": "body_runner_pipe_setup_failed", "child_settings": "body_runner_channel_setup_failed",
    "spawn": "body_runner_spawn_failed", "identity": "body_runner_identity_failed",
    "channel_started": "body_runner_channel_started_failed", "gate_release": "body_runner_gate_release_failed",
    "released": "body_runner_post_release_failed",
}


def _close_fd(fd: int | None) -> BaseException | None:
    if fd is None:
        return None
    try:
        os.close(fd)
    except BaseException as error:
        return error
    return None


def _settle_failed_launch(process: subprocess.Popen[bytes] | None, channel: Any,
                          gate_write: int | None, *, runner_started: bool) -> tuple[bool, str | None]:
    failures: list[str] = []
    if _close_fd(gate_write) is not None:
        failures.append("body_runner_gate_close_failed")
    try:
        channel.close()
    except BaseException:
        failures.append("body_runner_channel_close_failed")
    if runner_started and process is None:
        failures.append("body_runner_process_unavailable")
    elif process is not None:
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            failures.append("body_runner_bootstrap_wait_timeout")
        except BaseException:
            failures.append("body_runner_bootstrap_wait_failed")
    return not failures, (failures[0] if failures else None)


def launch_runner(config: RunnerProcessConfig) -> RunnerProcess:
    validate_config(config)
    stdout_fd = stderr_fd = gate_read = gate_write = None
    process: subprocess.Popen[bytes] | None = None
    result: RunnerProcess | None = None
    runner_started = gate_released = False
    stage = "stdout_open"
    primary: BaseException | None = None
    cleanup_proven, cleanup_code = True, None
    close_failures: list[BaseException] = []
    try:
        stdout_fd = _private_output(config.stdout_path); stage = "stderr_open"
        stderr_fd = _private_output(config.stderr_path); stage = "pipe_setup"
        gate_read, gate_write = os.pipe(); stage = "child_settings"
        extra_environment, extra_fds = config.channel.child_settings()
        environment = dict(config.environment); environment.update(extra_environment)
        command = (config.argv[0], "-I", "-B", "-c", _BOOTSTRAP, str(gate_read), config.argv[0], *config.argv[1:])
        stage = "spawn"
        process = subprocess.Popen(command, cwd=config.source_root, env=environment,
            stdin=subprocess.DEVNULL, stdout=stdout_fd, stderr=stderr_fd,
            close_fds=True, pass_fds=(gate_read, *extra_fds), start_new_session=True)
        runner_started = True; stage = "identity"
        birth = psutil.Process(process.pid).create_time(); stage = "channel_started"
        config.channel.started(process.pid, birth); stage = "gate_release"
        refuse(os.write(gate_write, b"\x01") == 1, "body runner gate write")
        gate_released = True; stage = "released"; result = RunnerProcess(process, birth, config)
    except BaseException as error:
        primary = error; primary_code = _LAUNCH_CODES[stage]
        cleanup_proven, cleanup_code = _settle_failed_launch(process, config.channel, gate_write,
                                                              runner_started=runner_started)
        gate_write = None
    for fd in (stdout_fd, stderr_fd, gate_read):
        close_error = _close_fd(fd)
        if close_error is not None: close_failures.append(close_error)
    gate_error = _close_fd(gate_write)
    if gate_error is not None: close_failures.append(gate_error)
    else: gate_write = None
    if primary is not None:
        if close_failures: cleanup_proven, cleanup_code = False, cleanup_code or "body_runner_fd_close_failed"
        raise BodyRunnerLaunchFailure(primary_code, runner_started=runner_started,
                                      gate_released=gate_released, cleanup_proven=cleanup_proven,
                                      cleanup_code=cleanup_code) from primary
    if close_failures:
        assert result is not None and process is not None
        # The gate is already released: retain the exact owned process so the
        # outer controller can perform scoped group settlement.
        raise BodyRunnerLaunchFailure("body_runner_fd_close_failed", runner_started=runner_started,
                                      gate_released=gate_released, cleanup_proven=False,
                                      cleanup_code="body_runner_fd_close_failed", runner=result)
    assert result is not None
    return result


__all__ = ["BodyRunnerLaunchFailure", "RunnerProcess", "RunnerProcessConfig", "launch_runner", "validate_config"]
