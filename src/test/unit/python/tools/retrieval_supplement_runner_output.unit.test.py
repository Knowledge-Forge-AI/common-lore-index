"""Real append-only log framing and closed study-runner command selection."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from retrieval_semantic_bridge_transport import READY_PREFIX
from retrieval_semantic_experiment_bridge import ADMISSION_ACK_PREFIX
from retrieval_supplement_epoch import EPOCH_PREFIX
from retrieval_supplement_runner_channel import RunnerChannel
from retrieval_supplement_runner_output import MAX_LINE_BYTES, MAX_LOG_BYTES, RunnerOutput
from retrieval_supplement_runner_process import RunnerProcess, RunnerProcessConfig, validate_config
from retrieval_supplement_study_contracts import SDK_IMAGE_SHA256, STUDY_SELECTOR


@pytest.fixture
def reader(tmp_path: Path, monkeypatch):
    argv = (sys.executable, "-I", "-B", "tools/run_tests.py", "--suite", "int", "--jobs", "1",
        "--no-coverage", "--pg-test-profile", "pgvector", "--", "-s", STUDY_SELECTOR)
    channel = RunnerChannel(tmp_path, argv, "sha256:" + "a" * 64, SDK_IMAGE_SHA256)
    path = tmp_path / "stdout"
    path.write_bytes(b"")
    path.chmod(0o600)
    config = RunnerProcessConfig(argv, tmp_path, path, tmp_path / "stderr", {}, channel)
    handle = RunnerProcess(Mock(), 1.0, config)
    live = Mock()
    monkeypatch.setattr(handle, "require_live", live)
    charges: list[int] = []
    value = RunnerOutput(handle, charges.append)
    try:
        yield value, path, live, charges
    finally:
        channel.close()


def append(path: Path, raw: bytes) -> None:
    with path.open("ab") as stream:
        stream.write(raw)


def test_split_frames_wait_for_newline_and_ignore_embedded_tokens(reader):
    value, path, live, charges = reader
    append(path, b"normal pytest diagnostic " + EPOCH_PREFIX.encode() + b"{}\n")
    append(path, EPOCH_PREFIX.encode() + b'{"ordinal":')
    value.poll()
    assert not value.events
    append(path, b"1}\n")
    checked: list[str] = []
    raw = value.await_frame(EPOCH_PREFIX, deadline=value.clock() + 2,
        require_current=lambda: checked.append("source-and-sdk"))
    assert raw == EPOCH_PREFIX.encode() + b'{"ordinal":1}'
    assert checked == ["source-and-sdk"] and sum(charges) == path.stat().st_size
    live.assert_not_called()
    value.finish()


def test_real_append_preserves_control_order_and_consumes_once(reader):
    value, path, _, _ = reader
    for prefix in (EPOCH_PREFIX, READY_PREFIX, ADMISSION_ACK_PREFIX):
        append(path, prefix.encode() + b"{}\n")
    for prefix in (EPOCH_PREFIX, READY_PREFIX, ADMISSION_ACK_PREFIX):
        assert value.await_frame(prefix, deadline=value.clock() + 2,
            require_current=lambda: None) == prefix.encode() + b"{}"
    value.finish()
    assert value.offset == path.stat().st_size


def test_wrong_control_phase_cannot_be_treated_as_readiness(reader):
    value, path, _, _ = reader
    append(path, ADMISSION_ACK_PREFIX.encode() + b"{}\n")
    with pytest.raises(ValueError, match="control_order"):
        value.await_frame(READY_PREFIX, deadline=value.clock() + 2, require_current=lambda: None)


@pytest.mark.parametrize("mutation", ["replace", "truncate", "symlink", "hardlink", "mode", "oversize"])
def test_log_custody_or_boundedness_change_refuses(reader, mutation):
    value, path, _, _ = reader
    append(path, b"initial\n")
    value.poll()
    if mutation == "replace":
        replacement = path.with_suffix(".new")
        replacement.write_bytes(b"replacement\n")
        replacement.chmod(0o600)
        replacement.replace(path)
    elif mutation == "truncate":
        path.write_bytes(b"")
    elif mutation == "symlink":
        target = path.with_suffix(".target")
        path.replace(target)
        path.symlink_to(target)
    elif mutation == "hardlink":
        os.link(path, path.with_suffix(".alias"))
    elif mutation == "mode":
        path.chmod(0o644)
    else:
        with path.open("r+b") as stream:
            stream.truncate(MAX_LOG_BYTES + 1)
    with pytest.raises((ValueError, OSError)):
        value.poll()


def test_no_newline_and_excess_control_frames_are_bounded(reader):
    value, path, _, _ = reader
    append(path, b"x" * (MAX_LINE_BYTES + 1))
    value.poll()
    with pytest.raises(ValueError, match="partial_line_bounds"):
        value.poll()


def test_timeout_and_lost_runner_produce_no_authority(reader):
    value, _, live, _ = reader
    value.clock = lambda: 10.
    with pytest.raises(RuntimeError, match="frame_timeout"):
        value.await_frame(READY_PREFIX, deadline=9., require_current=lambda: None)
    live.side_effect = RuntimeError("runner lost")
    with pytest.raises(RuntimeError, match="runner lost"):
        value.await_frame(READY_PREFIX, deadline=11., require_current=lambda: None)


def test_unconsumed_or_unterminated_control_frame_blocks_finish(reader):
    value, path, _, _ = reader
    append(path, EPOCH_PREFIX.encode() + b"{}")
    with pytest.raises(ValueError, match="unconsumed_control_frame"):
        value.finish()


def test_exact_canonical_command_requires_visible_stdout_and_single_selector(reader):
    value, _, _, _ = reader
    config = value.runner.config
    validate_config(config)
    from dataclasses import replace
    for argv in (tuple(x for x in config.argv if x != "-s"),
                 (*config.argv, "foreign.int.test.py"),
                 tuple("2" if x == "1" else x for x in config.argv)):
        with pytest.raises(ValueError):
            validate_config(replace(config, argv=argv))
