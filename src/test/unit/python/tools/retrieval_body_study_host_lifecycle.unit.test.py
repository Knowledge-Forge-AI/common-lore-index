"""BODY lifecycle holds: cancellation, source loss, and unknown cleanup."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from retrieval_body_study_contracts import BODY_SDK_IMAGE_SHA256, BODY_SELECTOR, BodyStudyRefusal
from retrieval_body_study_host_admission import PhysicalBodyAdmission
import retrieval_body_study_host_runner_lease as lease_subject
import retrieval_body_study_host_runner_settlement as settlement_subject
from retrieval_body_study_host_runner_settlement import (
    _probe_owned_process_group, merge_cleanup, settle_runner, worker_record,
)
from retrieval_body_study_host_runner_lease import RunnerLease
from retrieval_body_study_reservation import (
    consume_reservation, settle_reservation, write_private_once,
)
from common_lore_index_test_support.retrieval_body_study_host_cases import body_plan, reservation_record
from retrieval_body_study_plan import plan_digest


class _Channel:
    def __init__(self) -> None:
        self.closed = False
        self.receipt: dict[str, object] | None = None

    def poll(self) -> dict[str, object] | None:
        return self.receipt

    def close(self) -> None:
        self.closed = True


def test_unstarted_cancellation_does_not_claim_cleanup(tmp_path: Path) -> None:
    channel = _Channel()
    result = settle_runner(None, channel, charge_bytes=lambda _count: None,
                           charge_operation=lambda _name: None, deadline=1.0)
    assert result["cleanup"] == "unknown"
    assert result["runner_started"] is False and channel.closed is True


def test_ended_runner_without_attested_receipt_keeps_cleanup_unknown() -> None:
    channel = _Channel()
    runner = type("EndedRunner", (), {
        "process": type("Process", (), {"poll": lambda self: 1, "returncode": 1})(),
        "config": object(),
    })()
    result = settle_runner(cast(Any, runner), channel, charge_bytes=lambda _count: None,
                           charge_operation=lambda _name: None, deadline=1.0)
    assert result["cleanup"] == "unknown" and result["runner_started"] is True
    assert channel.closed is True


def test_owned_group_probe_reports_only_bound_group_presence() -> None:
    runner = SimpleNamespace(process=SimpleNamespace(pid=71))
    seen: list[tuple[int, int]] = []

    def present(group_id: int, number: int) -> None:
        seen.append((group_id, number))

    assert _probe_owned_process_group(cast(Any, runner), group_id_probe=lambda pid: pid,
                                      group_probe=present) is True
    assert seen == [(71, 0)]

    def absent(_group_id: int, _number: int) -> None:
        raise ProcessLookupError

    assert _probe_owned_process_group(cast(Any, runner), group_id_probe=lambda pid: pid,
                                      group_probe=absent) is False
    assert _probe_owned_process_group(cast(Any, runner), group_id_probe=lambda _pid: 72,
                                      group_probe=present) is None


@pytest.mark.parametrize(("group_presence", "expected_cleanup"), (
    (True, "unknown"), (None, "unknown"), (False, "proven"),
))
def test_ended_runner_cannot_claim_sdk_absence_while_group_is_present(
    monkeypatch: pytest.MonkeyPatch, group_presence: bool | None, expected_cleanup: str,
) -> None:
    channel = _Channel()
    channel.receipt = {"cid": "c" * 64}
    runner = SimpleNamespace(
        process=SimpleNamespace(pid=71, poll=lambda: 1, returncode=0), birth=1.0,
        config=SimpleNamespace(source_root=Path("/sandbox")),
    )
    monkeypatch.setattr(settlement_subject, "_probe_owned_process_group",
                        lambda _runner: group_presence)
    docker_calls: list[tuple[str, ...]] = []

    def absent_sdk(argv: tuple[str, ...], **_kwargs: object) -> Any:
        docker_calls.append(argv)
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"No such container")

    monkeypatch.setattr(settlement_subject, "_bounded_docker", absent_sdk)
    result = settle_runner(cast(Any, runner), channel, charge_bytes=lambda _count: None,
                           charge_operation=lambda _operation: None, deadline=10**12)
    assert result["cleanup"] == expected_cleanup
    assert docker_calls == [("docker", "container", "inspect", "c" * 64)]
    assert result["sdk_absent"] is True


def test_unknown_cleanup_settles_reservation_as_blocked_and_nonreusable(tmp_path: Path) -> None:
    plan = body_plan()
    path = tmp_path / "reservation.json"
    record = reservation_record(plan)
    write_private_once(path, record)
    consumed = consume_reservation(path, plan_sha256=plan_digest(plan), attempt_id=plan["attempt_id"])
    assert consumed["status"] == "admitted" and consumed["consumed"] is True
    settled = settle_reservation(path, plan_sha256=plan_digest(plan), cleanup="unknown")
    assert settled["state"] == "settled" and settled["status"] == "blocked"
    with pytest.raises(BodyStudyRefusal):
        consume_reservation(path, plan_sha256=plan_digest(plan), attempt_id=plan["attempt_id"])


def test_sdk_cleanup_proof_retains_failed_worker_and_actual_count() -> None:
    result = merge_cleanup({"cleanup": "proven", "sdk_container": "c" * 64},
                           [{"worker_count": 1, "worker_count_known": True,
                             "worker_count_upper_bound": 1, "status": "failed", "cleanup": "proven"}])
    assert result["cleanup"] == "proven" and result["model_workers"] == 1
    assert result["model_workers_known"] is True
    assert result["workers"][0]["status"] == "failed"


def test_unknown_worker_blocks_sdk_only_cleanup_proof() -> None:
    result = merge_cleanup({"cleanup": "proven", "sdk_container": "c" * 64},
                           [{"worker_count": None, "worker_count_known": False,
                             "worker_count_upper_bound": 1, "status": "failed", "cleanup": "unknown"}])
    assert result["cleanup"] == "unknown" and result["worker_cleanup"] == "unknown"
    assert result["model_workers"] == 0 and result["model_workers_known"] is False


def test_zero_worker_result_is_explicitly_known() -> None:
    result = worker_record(SimpleNamespace(model_container_id=None, cleanup_proven=True,
                                           status="failed", code="before_create"))
    merged = merge_cleanup({"cleanup": "proven"}, [result])
    assert result["worker_count"] == 0 and result["worker_count_known"] is True
    assert merged["model_workers"] == 0 and merged["model_workers_known"] is True


def test_creation_unknown_is_not_counted_as_zero() -> None:
    result = worker_record(SimpleNamespace(model_container_id=None, cleanup_proven=False,
                                           cleanup_code="creation_unknown", status="failed",
                                           code="creation_unknown"))
    assert result["worker_count"] is None and result["worker_count_known"] is False
    assert result["worker_count_upper_bound"] == 1 and result["cleanup"] == "unknown"


def test_unknown_zero_count_cannot_be_ignored() -> None:
    merged = merge_cleanup({"cleanup": "proven"}, [{
        "worker_count": 0, "worker_count_known": False,
        "worker_count_upper_bound": 1, "cleanup": "unknown",
    }])
    assert merged["cleanup"] == "unknown" and merged["model_workers_known"] is False


def test_lost_pinned_receipt_retains_read_reservation_on_refusal(tmp_path: Path) -> None:
    path = tmp_path / "parent-receipt.json"
    original = b"retained-parent-receipt"
    path.write_bytes(original)
    path.chmod(0o600)
    expected = "sha256:" + hashlib.sha256(original).hexdigest()
    admission = PhysicalBodyAdmission({}, expected, {path: (len(original), expected)})
    charged: list[int] = []
    admission.require_current(charged.append)
    assert charged == [len(original) + 1]
    path.write_bytes(b"lost-and-replaced")
    with pytest.raises(BodyStudyRefusal):
        admission.require_current(charged.append)
    assert charged == [len(original) + 1, len(original) + 1]


def test_runner_command_accepts_only_the_attested_outer_identity() -> None:
    lease = RunnerLease(cast(Any, SimpleNamespace()), lambda: None, lambda: 30.0,
                        lambda _count: None, lambda _operation: None)
    lease.observed_cid = "c" * 64
    lease._scope(["docker", "inspect", lease.observed_cid])
    lease._scope(["docker", "exec", lease.observed_cid, "python3", "-c", "pass"])
    with pytest.raises(BodyStudyRefusal, match="body runner docker scope"):
        lease._scope(["docker", "inspect", "d" * 64])
    lease.observed_cid = None
    with pytest.raises(BodyStudyRefusal, match="body runner docker scope"):
        lease._scope(["docker", "inspect", "c" * 64])


@pytest.mark.parametrize("failure", ("marker", "custody"))
def test_receipt_commitment_requires_a_fresh_outer_verification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str,
) -> None:
    cid = "c" * 64
    marker = "sha256:" + "a" * 64
    bad_marker = "sha256:" + "b" * 64
    control = {"attempt_id": "attempt-body"}
    receipt = {
        **control, "pid": 71, "birth": 1.0, "source_root": str(tmp_path.resolve()),
        "sdk_image_id": BODY_SDK_IMAGE_SHA256, "cid": cid, "marker_sha256": marker,
    }
    inspection = {
        "Id": cid, "Image": BODY_SDK_IMAGE_SHA256,
        "Config": {
            "Labels": {"org.repomap.test.sandbox": "true",
                       "org.repomap.test.sandbox.backend": "monolithic-dind-v1"},
            "Cmd": ["docker-init", "--", "python3", "/workspace-ro/tools/test_sandbox_entrypoint.py",
                    "--docker-group", "71", "--", "--suite", "int", "--jobs", "1",
                    "--no-coverage", "--pg-test-profile", "pgvector", "--", "-s", BODY_SELECTOR],
        },
        "State": {"Running": True},
        "Mounts": [{"Type": "bind", "Source": str(tmp_path.resolve()),
                    "Destination": "/workspace-ro", "RW": False}],
    }
    channel = SimpleNamespace(raw=bytearray(b"receipt"), control=control,
                              poll=lambda: receipt)
    runner = SimpleNamespace(
        process=SimpleNamespace(pid=71), birth=1.0,
        config=SimpleNamespace(channel=channel, source_root=tmp_path),
        require_live=lambda: None,
    )
    marker_values = [marker, bad_marker if failure == "marker" else marker]

    def docker(argv: tuple[str, ...], **_kwargs: object) -> Any:
        if argv[1] == "inspect":
            return SimpleNamespace(returncode=0, stdout=json.dumps([inspection]).encode(), stderr=b"")
        return SimpleNamespace(returncode=0, stdout=(marker_values.pop(0) + "\n").encode(), stderr=b"")

    monkeypatch.setattr(lease_subject, "_bounded_docker", docker)
    custody_calls = 0

    def custody() -> None:
        nonlocal custody_calls
        custody_calls += 1
        if failure == "custody" and custody_calls == 2:
            raise BodyStudyRefusal("body custody drift")

    lease = RunnerLease(cast(Any, runner), lambda: None, lambda: 30.0,
                        lambda _count: None, lambda _operation: None,
                        charge_transfer=lambda _count: None, custody_source_guard=custody)
    assert lease.require_outer() == cid
    assert lease.receipt_commitment().startswith("sha256:")
    with pytest.raises(BodyStudyRefusal):
        lease.require_outer()
    assert custody_calls == (2 if failure == "custody" else 1)
    with pytest.raises(BodyStudyRefusal, match="body runner receipt not verified"):
        lease.receipt_commitment()
