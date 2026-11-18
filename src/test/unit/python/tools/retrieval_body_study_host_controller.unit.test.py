"""Owners for canonical BODY runner invocation and callback-free control."""
from __future__ import annotations

import inspect
import os
from pathlib import Path
import sys

import pytest

import retrieval_body_study_host_controller as controller

from retrieval_body_study_host_controller import (
    ActualBodyStudyConfig, RunnerChannel, RunnerLease, RunnerProcess,
    child_environment, run_actual_body_study,
)
from retrieval_body_study_contracts import BODY_PHASE, BodyStudyRefusal, ENV_BODY_ADMISSION
from retrieval_body_study_host_runner_settlement import settle_runner
from retrieval_body_study_host_exchange import HostExchangeError


def test_controller_has_real_owned_lifecycle_and_no_execute_callback() -> None:
    names = set(inspect.signature(run_actual_body_study).parameters)
    assert names == {"config"}
    assert {"RunnerChannel", "RunnerProcess", "RunnerLease"} <= {
        RunnerChannel.__name__, RunnerProcess.__name__, RunnerLease.__name__}


def test_config_keeps_source_plan_allocation_and_admission_fields() -> None:
    fields = set(ActualBodyStudyConfig.__dataclass_fields__)
    assert {"source_root", "plan_path", "allocation_path", "parent_admission",
            "parent_admission_sha256", "environment"} <= fields


def test_unstarted_channel_cleanup_is_not_claimed_proven() -> None:
    class Channel:
        def close(self) -> None:
            return None

    result = settle_runner(None, Channel(), charge_bytes=lambda _count: None,
                           charge_operation=lambda _name: None, deadline=1.0)
    assert result["cleanup"] == "unknown"


def test_child_environment_cannot_smuggle_body_admission(tmp_path) -> None:
    names = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
             "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")
    values = {name: os.environ[name] for name in names if name in os.environ}
    values[ENV_BODY_ADMISSION] = "forged"
    config = ActualBodyStudyConfig(tmp_path, tmp_path / "plan.json", tmp_path / "alloc.json",
        tmp_path / "attempt", tmp_path / "artifacts", tmp_path / "python", tmp_path / "parent.json",
        "sha256:" + "a" * 64, values)
    environment = child_environment(config)
    assert ENV_BODY_ADMISSION not in environment
    assert environment["REPOMAP_TEST_PHASE"] == BODY_PHASE


def test_runner_python_is_the_current_interpreter(tmp_path) -> None:
    current = ActualBodyStudyConfig(
        tmp_path, tmp_path / "plan.json", tmp_path / "alloc.json",
        tmp_path / "attempt", tmp_path / "artifacts", Path(sys.executable),
        tmp_path / "parent.json", "sha256:" + "a" * 64, {},
    )
    controller._validate_runner_python(current)
    foreign = ActualBodyStudyConfig(
        current.source_root, current.plan_path, current.allocation_path,
        current.attempt_root, current.artifact_root, tmp_path / "python3",
        current.parent_admission, current.parent_admission_sha256, current.environment,
    )
    with pytest.raises(BodyStudyRefusal, match="body controller python"):
        controller._validate_runner_python(foreign)


def test_child_temporary_directory_is_private_and_created_before_use(tmp_path) -> None:
    root = tmp_path / "attempt"
    root.mkdir(mode=0o700)
    controller._prepare_child_temporary(root)
    temporary = root / "tmp"
    assert temporary.is_dir() and temporary.stat().st_mode & 0o777 == 0o700
    with pytest.raises(BodyStudyRefusal, match="temporary root reused"):
        controller._prepare_child_temporary(root)


def test_child_temporary_alias_is_refused_without_touching_target(tmp_path) -> None:
    root, foreign = tmp_path / "attempt", tmp_path / "foreign"
    root.mkdir(mode=0o700)
    foreign.mkdir(mode=0o700)
    marker = foreign / "marker"
    marker.write_bytes(b"preserve synthetic foreign state")
    (root / "tmp").symlink_to(foreign, target_is_directory=True)
    with pytest.raises(BodyStudyRefusal, match="temporary root reused"):
        controller._prepare_child_temporary(root)
    assert marker.read_bytes() == b"preserve synthetic foreign state"


def test_exchange_error_retains_worker_failure_and_cleanup_identity() -> None:
    error = HostExchangeError("model_execution_failed", primary_code="model_execution_failed",
                              cleanup_code="cleanup_failed",
                              worker={"worker_count": 1, "worker_cid": "c" * 64, "cleanup": "unknown"})
    assert error.worker is not None
    assert error.worker["worker_cid"] == "c" * 64 and error.cleanup_code == "cleanup_failed"
