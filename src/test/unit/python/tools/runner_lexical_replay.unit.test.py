"""Synthetic launcher dry path with byte, role and authority custody checks."""
from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import Mock

import pytest

from offline_qualification_values import BASE_CONFIG, LEXICAL_REPLAY_CAPS, SDK_IMAGE, digest, json_bytes
import runner_lexical_replay as launcher
import test_sandbox as sandbox
import test_sandbox_entrypoint as entrypoint


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def test_launcher_dry_path_has_no_foreign_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    source = root / "source"
    private = root / "dry"
    protocol = source / launcher.PROTOCOL
    protocol.parent.mkdir(parents=True)
    protocol.write_bytes(b"Sanitized launcher proof.\n")
    private.mkdir(mode=0o700)
    archive = b"synthetic archive envelope"
    snapshot = {launcher.PROTOCOL: {"mode": 420, "sha256": digest(protocol.read_bytes())}}
    plan: dict[str, Any] = {
        "schema": launcher.PLAN_SCHEMA, "phase": launcher.PHASE, "role": launcher.ROLE,
        "attempt": "lexical-proof-unit", "sdk_image": SDK_IMAGE, "core_image": BASE_CONFIG,
        "candidate_head": "a" * 40, "candidate_snapshot": snapshot,
        "protocol_sha256": digest(protocol.read_bytes()), "input_pin_sha256": launcher.INPUT_PIN,
        "schedule": launcher.LIMITS, "archive": {"bytes": len(archive), "sha256": digest(archive)},
    }
    approval = json_bytes({"schema": "cloe-lexical-replay-approval-v1", "selection": "A",
                           "protocol_sha256": plan["protocol_sha256"], "input_pin_sha256": launcher.INPUT_PIN})
    expected = {key: plan[key] for key in ("attempt", "candidate_head", "protocol_sha256",
                                          "sdk_image", "core_image", "archive", "schedule")}
    expected["candidate_snapshot_sha256"] = digest(json_bytes(snapshot))
    review = json_bytes({"schema": "cloe-lexical-replay-review-v1", "disposition": "accepted", "candidate": expected})
    plan.update(approval_sha256=digest(approval), review_sha256=digest(review))
    _write_private(private / "approval.json", approval)
    _write_private(private / "review.json", review)
    _write_private(private / "core-image.tar", archive)
    _write_private(private / "allocation.json", json_bytes({
        "schema": "cloe-pgvector-allocation-v1", "caps": LEXICAL_REPLAY_CAPS,
        "used": dict.fromkeys(LEXICAL_REPLAY_CAPS, 0), "records": {},
    }))
    path = private / "plan.json"
    _write_private(path, json_bytes(plan))
    verifier = Mock()
    monkeypatch.setattr(launcher, "verify_checkout", verifier)
    context = launcher.host_context(path, source)
    verifier.assert_called_once_with(source, snapshot, plan["candidate_head"])
    assert (private / "consumed.json").is_file()
    with pytest.raises(RuntimeError, match="attempt already used"):
        launcher.host_context(path, source)

    adapter = launcher.LexicalReplaySandbox(context, Mock(), Mock())
    commands: list[tuple[str, ...]] = []

    def command(container: str, *args: str) -> CompletedProcess[str]:
        assert container == "c" * 64
        commands.append(args)
        answer = ""
        if args[:2] == ("docker", "info"):
            answer = "overlay2 aarch64"
        elif args[:4] == ("docker", "image", "ls", "--all") and commands.count(args) == 2:
            answer = BASE_CONFIG
        elif args[:3] == ("docker", "image", "inspect"):
            facts = launcher.provider_facts()
            config = launcher.strict_object("".join(facts["base_config_utf8_chunks"]).encode())
            document = {"Id": BASE_CONFIG, "Os": "linux", "Architecture": "arm64",
                        "RootFS": {"Layers": config["rootfs"]["diff_ids"]}, "Config": config["config"]}
            answer = json_bytes([document]).decode()
        return CompletedProcess(args, 0, stdout=answer, stderr="")

    send_file, send_plan = Mock(), Mock()
    monkeypatch.setattr(adapter, "command", command)
    monkeypatch.setattr(launcher, "send_file", send_file)
    monkeypatch.setattr(launcher, "send_plan", send_plan)

    def supervisor(argv: list[str], **callbacks: Any) -> int:
        assert argv == [launcher.LEXICAL_REPLAY_ARG]
        outer = callbacks["outer_run_command"](
            image_id=SDK_IMAGE, container_name="lexical-dry", repo_root=source, host_gid=0, argv=argv,
        )
        assert "--pull=never" in outer and outer[outer.index("--network") + 1] == "none"
        assert "CLOE_LEXICAL_REPLAY_SEAL=" + digest(json_bytes(adapter.inner_document())) in outer
        callbacks["ownership_reporter"]("c" * 64, SDK_IMAGE, "d" * 64)
        callbacks["prepare_images"](Mock(), "c" * 64)
        return 0

    monkeypatch.setattr(sandbox._execution, "run_in_sandbox", supervisor)
    assert sandbox.run_in_sandbox(
        [launcher.LEXICAL_REPLAY_ARG], repo_root=source, image_id=SDK_IMAGE,
        offline_override=adapter, ownership_reporter=adapter.record_owner,
    ) == 0
    send_file.assert_called_once()
    send_plan.assert_called_once()
    assert send_file.call_args.args[1:4] == (private / "core-image.tar", "image.tar", plan["archive"])
    assert any(command[:3] == ("docker", "image", "load") for command in commands)
    assert not any("pull" in command or "build" in command for command in commands)
    assert (private / "outer-owner.json").is_file()
    with context.allocation().session() as meter:
        assert meter.used["sdk_lexical_replay"] == meter.used["core_imports"] == 1
        assert meter.used["postgres_lifecycles"] == 0
        assert "lexical_replay_sdk_result" in meter.records


def test_lexical_entrypoint_cannot_reach_default_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    start = tmp_path / "start-tests"
    start.touch()
    process = Mock()
    process.wait.return_value = 0
    process.poll.return_value = 0
    popen = Mock(return_value=process)
    fallback = Mock(side_effect=AssertionError("foreign setup"))
    monkeypatch.setattr(entrypoint, "START_PATH", start)
    monkeypatch.setattr(entrypoint, "_test_environment", lambda: {"PYTHONPATH": "/workspace:/workspace/tools"})
    monkeypatch.setattr(entrypoint, "_materialize_inner_pgvector_if_requested", fallback)
    monkeypatch.setattr(entrypoint.subprocess, "Popen", popen)
    assert entrypoint.main(["--docker-group", "0", "--", launcher.LEXICAL_REPLAY_ARG]) == 0
    fallback.assert_not_called()
    assert len(popen.call_args_list) == 2
    launch = popen.call_args_list[1]
    assert launch.args[0] == ("python3", "-B", "/workspace/tools/run_lexical_replay.py", "--inner")
    assert launch.kwargs["cwd"] == "/workspace"
    assert launch.kwargs["env"]["PYTHONPATH"].endswith(":/workspace/src/test/support/python")
