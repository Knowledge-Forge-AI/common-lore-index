"""Body-only SDK dispatch, spend-before-launch and source-read boundaries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from typing import Any, cast

import pytest

from offline_qualification_values import BODY_CAPS, SDK_IMAGE
from retrieval_body_study_contracts import BodyStudyRefusal
import runner_body_study
import runner_body_study_sandbox as body
import runner_pgvector_context as contexts
from runner_pgvector_budget import Allocation, CAPS, PROOF_CAPS, STUDY_CAPS
from test_sandbox_contract import INNER_DOCKER_HOST, outer_run_command


def _allocation(root: Path) -> Allocation:
    path = root / "body-ledger.json"
    path.write_text(json.dumps({"schema": "cloe-pgvector-allocation-v1", "caps": BODY_CAPS,
        "used": {key: 0 for key in BODY_CAPS}, "records": {}}))
    path.chmod(0o600)
    return Allocation(path, BODY_CAPS)


def test_body_adapter_prepays_then_spends_sdk_once_without_build_or_pull(tmp_path, monkeypatch):
    source = tmp_path / "synthetic-source.py"
    source.write_bytes(b"# public synthetic source\n")
    allocation = _allocation(tmp_path)
    plan = {"role": "body-study", "candidate_snapshot": {source.name: {
        "mode": 0o644, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}},
        "derived": {"byte_forecast_binding": {"source_bytes": 16}}}
    context = SimpleNamespace(plan=plan, allocation=lambda: allocation,
        repo_root=tmp_path, fixed=lambda: None)
    validations = []
    monkeypatch.setattr(runner_body_study, "validate_body_runner_plan", lambda value: validations.append(value))
    monkeypatch.setattr(body.time, "monotonic", lambda: 100.0)
    forbidden = Mock(side_effect=AssertionError("command construction must not execute Docker"))
    def reserve(_plan, ledger, _root):
        with ledger.session() as meter:
            if meter.used["sdk_body_study"]:
                raise RuntimeError("body SDK already spent")
            meter.charge("read_bytes", 17)
    monkeypatch.setattr("retrieval_body_study_inner_read_reservation.prepay_inner_reads", reserve)
    adapter = body.BodyOfflineSandbox(context, forbidden, forbidden)
    assert adapter.deadline == 4900.0 and validations == [plan]
    command = outer_run_command(image_id=SDK_IMAGE, container_name="unit-body-sdk",
        repo_root=tmp_path, host_gid=20, argv=["--suite", "int"])
    transformed = adapter.outer_command(command)
    assert "--pull=never" in transformed
    assert transformed.count("--shm-size") == 1
    assert transformed[transformed.index("--shm-size") + 1] == "64m"
    assert "/sandbox-scratch:rw,exec,nosuid,nodev,size=2g" in transformed
    assert "/sandbox-scratch:rw,exec,nosuid,nodev,size=8g" not in transformed
    mounts = [transformed[index + 1] for index, value in enumerate(transformed)
              if value in {"--mount", "--volume", "-v"}]
    assert not any("docker.sock" in value for value in mounts)
    assert "DOCKER_HOST=" + INNER_DOCKER_HOST in transformed
    with allocation.session() as meter:
        assert meter.used["sdk_body_study"] == 1
        assert meter.used["read_bytes"] == 17
        assert meter.used["builds"] == meter.used["pulls"] == meter.used["materializations"] == 0
        spent = dict(meter.used)
    with pytest.raises(RuntimeError, match="already spent"):
        adapter.outer_command(command)
    with allocation.session() as meter:
        assert meter.used == spent
    forbidden.assert_not_called()


@pytest.mark.parametrize("role", ["study", "proof", "core", "native"])
def test_old_role_cannot_construct_extended_body_adapter(tmp_path, monkeypatch, role):
    monkeypatch.setattr(runner_body_study, "validate_body_runner_plan", lambda _plan: None)
    forbidden = Mock(side_effect=AssertionError("invalid role cannot execute"))
    with pytest.raises((RuntimeError, ValueError), match="body cohort"):
        body.BodyOfflineSandbox(SimpleNamespace(plan={"role": role}), forbidden, forbidden)
    forbidden.assert_not_called()


def test_body_inner_source_verification_is_reserved_before_whole_checkout_read(tmp_path, monkeypatch):
    path = tmp_path / "inner-plan.json"
    plan = {"schema": "cloe-body-study-inner-plan-v1", "role": "body-study",
            "candidate_snapshot": {"synthetic.py": {}}, "candidate_head": "a" * 40}
    path.write_text(json.dumps(plan))
    path.chmod(0o600)
    events = []
    monkeypatch.setattr(contexts, "INNER_PLAN", str(path))
    monkeypatch.setattr(contexts, "validate_plan", lambda _plan: events.append("plan"))
    monkeypatch.setattr("test_sandbox.active_sandbox", lambda _env: True)
    monkeypatch.setattr(contexts, "body_inner_source_read", lambda _plan: events.append("reserve"))
    monkeypatch.setattr(contexts, "verify_checkout", lambda *_args: events.append("verify"))
    result = contexts.load_inner_plan({contexts.ENV_OFFLINE_PLAN: str(path)})
    assert result == plan
    assert events == ["plan", "reserve", "verify"]


def test_body_source_reservation_failure_blocks_any_checkout_work(tmp_path, monkeypatch):
    path = tmp_path / "inner-plan.json"
    path.write_text(json.dumps({"role": "body-study"}))
    path.chmod(0o600)
    monkeypatch.setattr(contexts, "INNER_PLAN", str(path))
    monkeypatch.setattr(contexts, "validate_plan", lambda _plan: None)
    monkeypatch.setattr("test_sandbox.active_sandbox", lambda _env: True)
    reservation = Mock(side_effect=RuntimeError("unit exhausted source reservations"))
    checkout = Mock(side_effect=AssertionError("unreserved checkout read"))
    monkeypatch.setattr(contexts, "body_inner_source_read", reservation)
    monkeypatch.setattr(contexts, "verify_checkout", checkout)
    with pytest.raises(RuntimeError, match="exhausted"):
        contexts.load_inner_plan({contexts.ENV_OFFLINE_PLAN: str(path)})
    checkout.assert_not_called()


def test_sdk_settlement_preserves_separate_host_terminal_record(tmp_path, monkeypatch):
    from retrieval_body_study_host_meter import HostMeter

    source = tmp_path / "synthetic.py"
    source.write_bytes(b"# public synthetic source\n")
    allocation = _allocation(tmp_path)
    with allocation.session() as ledger:
        ledger.charge("sdk_body_study", 1)
    plan = {"candidate_head": "a" * 40, "candidate_snapshot": {source.name: {
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "mode": 0o644}}}
    adapter = object.__new__(body.BodyOfflineSandbox)
    adapter.context = cast(contexts.OfflineContext, SimpleNamespace(plan=plan, repo_root=tmp_path,
        allocation=lambda: allocation, fixed=lambda: None))
    verified = []
    monkeypatch.setattr(contexts, "verify_checkout", lambda *args: verified.append(args))
    adapter.finished(0)
    terminal = {"schema": "cloe-body-study-result-v1", "status": "completed",
                "cleanup": "proven", "attempt_id": "unit-settlement"}
    HostMeter(allocation).record_result(terminal)
    with allocation.session() as ledger:
        assert ledger.records["body_study_sdk_result"]["exit"] == 0
        assert ledger.records["body_study_result"] == terminal
        assert ledger.used["sdk_body_study"] == 1
    assert len(verified) == 1


@pytest.mark.parametrize("caps", [CAPS, PROOF_CAPS, STUDY_CAPS])
def test_body_sdk_result_record_cannot_enter_an_old_cohort(tmp_path, caps):
    path = tmp_path / "old-ledger.json"
    path.write_text(json.dumps({"schema": "cloe-pgvector-allocation-v1", "caps": caps,
        "used": {key: 0 for key in caps}, "records": {}}))
    path.chmod(0o600)
    allocation = Allocation(path, caps)
    with allocation.session() as ledger:
        with pytest.raises(RuntimeError, match="already bound allocation record"):
            ledger.bind("body_study_sdk_result", {"exit": 0})
        assert ledger.records == {} and not any(ledger.used.values())


def test_body_inner_report_path_matches_canonical_export_root():
    from retrieval_body_study_contracts import BODY_ADMISSION_SCHEMA
    from retrieval_body_study_plan import plan_digest, project_inner_body_plan
    from common_lore_index_test_support.retrieval_body_study_host_cases import body_plan

    plan = body_plan()
    admission = {"schema": BODY_ADMISSION_SCHEMA, "plan_sha256": plan_digest(plan),
        "attempt_id": plan["attempt_id"], "reservation_sha256": "sha256:" + "a" * 64,
        **{key: plan[key] for key in ("source_closure_sha256", "treatment_sha256",
                                     "protocol_sha256", "operator_decision_sha256")}}
    inner = project_inner_body_plan(plan, admission=admission)
    assert inner["inner"] == {"report_dir": "/sandbox-report/report/body-study",
                              "scratch_dir": "/sandbox-scratch/pgvector"}
    assert "allocation_path" not in inner and "reservation_path" not in inner


def test_real_body_prepayment_charges_before_sdk_and_refuses_spent_sdk(tmp_path, monkeypatch):
    from retrieval_body_study_evidence_budget import INNER_EPOCH_PREPARATION_BYTES
    from retrieval_body_study_contracts import MAX_PLAN_BYTES
    from retrieval_body_study_inner_read_reservation import prepay_inner_reads
    from common_lore_index_test_support.retrieval_body_study_host_cases import body_plan

    # Synthetic closure measurement isolates this pure accounting boundary;
    # the real plan validator and on-disk candidate-size checks still execute.
    plan = body_plan()
    (tmp_path / "README.md").write_bytes(b"x")
    budget = plan["derived"]["body_budget"]
    measured = {"regular_file_bytes": budget["guard_regular_file_bytes"],
                "file_visits": budget["guard_file_visits"],
                "reserved_bytes_per_guard": budget["guard_reserved_bytes_per_read"]}
    monkeypatch.setattr("retrieval_body_study_inner_read_reservation.closure_guard_read_bound",
                        lambda _plan, _root: measured)
    allocation = _allocation(tmp_path)
    reserved = prepay_inner_reads(plan, cast(Any, allocation), tmp_path)
    assert reserved["candidate_bytes"] == 1
    assert reserved["registered_snapshot_count"] == 201
    assert reserved["bookkeeping_bytes"] == 2 * 16 * 1024 + MAX_PLAN_BYTES + INNER_EPOCH_PREPARATION_BYTES
    with allocation.session() as ledger:
        assert ledger.used["read_bytes"] == reserved["reserved_read_bytes"] > 0
        assert ledger.used["sdk_body_study"] == 0
        assert ledger.used["body_study_workers"] == 0
        ledger.charge("sdk_body_study", 1)
        spent = dict(ledger.used)
    with pytest.raises(BodyStudyRefusal, match="reservation already spent"):
        prepay_inner_reads(plan, cast(Any, allocation), tmp_path)
    with allocation.session() as ledger:
        assert ledger.used == spent


def test_real_body_prepayment_refuses_measured_closure_drift_before_charge(tmp_path, monkeypatch):
    from retrieval_body_study_inner_read_reservation import prepay_inner_reads
    from common_lore_index_test_support.retrieval_body_study_host_cases import body_plan

    plan = body_plan()
    (tmp_path / "README.md").write_bytes(b"x")
    budget = plan["derived"]["body_budget"]
    monkeypatch.setattr("retrieval_body_study_inner_read_reservation.closure_guard_read_bound",
        lambda _plan, _root: {"regular_file_bytes": budget["guard_regular_file_bytes"],
                            "file_visits": budget["guard_file_visits"] + 1,
                            "reserved_bytes_per_guard": budget["guard_reserved_bytes_per_read"]})
    allocation = _allocation(tmp_path)
    with pytest.raises(BodyStudyRefusal, match="budget binding drift"):
        prepay_inner_reads(plan, cast(Any, allocation), tmp_path)
    with allocation.session() as ledger:
        assert not any(ledger.used.values()) and ledger.records == {}


def test_body_ledger_never_accepts_legacy_results_or_reopens_spent_sdk(tmp_path):
    from common_lore_index_test_support.retrieval_body_study_runner_cases import body_ledger_case
    body_ledger_case(tmp_path)
