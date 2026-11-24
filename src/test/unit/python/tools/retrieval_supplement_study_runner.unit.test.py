"""Runner, OfflineSandbox, discovery, dispatch and conftest study-cohort unit proofs."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from repomap_test_support import retrieval_supplement_study as study_support
from repomap_test_support.retrieval_supplement_study_fixtures import make_study_plan
from retrieval_supplement_study_contracts import STUDY_NODE_ID, STUDY_SELECTOR
from retrieval_supplement_study_plan import (
    admission_file_for_plan,
    create_reservation,
    issue_admission,
    plan_digest,
    project_inner_study_plan,
)
from retrieval_supplement_values import canonical_bytes
import runner_pgvector_discovery
from runner_pgvector_prebuilt import ENV_OFFLINE_PLAN, INNER_PLAN, SDK_IMAGE
from runner_pgvector_context import OfflineContext
from runner_pgvector_sandbox import Captured as CallableCompletedProcess
from runner_pgvector_sandbox import OfflineSandbox, admit_study_inner
from runner_pgvector_study import validate_study_cohort
import runner_sandbox
import test_sandbox_dispatch

STUDY_ARGV = [
    "--suite", "int", "--no-coverage", "--pg-test-profile", "pgvector",
    "--report-dir", "reports", "--", "-s", STUDY_SELECTOR,
]


def test_body_and_supplement_exact_cohorts_cannot_substitute_selectors() -> None:
    from retrieval_body_study_contracts import BODY_SELECTOR
    from runner_body_study import validate_body_cohort
    body_argv = [*STUDY_ARGV[:-1], BODY_SELECTOR]
    validate_body_cohort(body_argv)
    validate_study_cohort(STUDY_ARGV)
    with pytest.raises((RuntimeError, ValueError), match="exact"):
        validate_study_cohort(body_argv)
    with pytest.raises((RuntimeError, ValueError), match="exact"):
        validate_body_cohort(STUDY_ARGV)


def _offline_context(tmp_path: Path, plan: dict[str, Any]) -> OfflineContext:
    return OfflineContext(tmp_path / "unused-plan.json", plan,
                          "sha256:" + "0" * 64, tmp_path)


def _completed_process(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, "", "")


def test_study_cohort_allows_exact_pytest_stream_and_one_runner_job() -> None:
    validate_study_cohort([
        "--suite", "int", "--jobs", "1", "--no-coverage",
        "--pg-test-profile", "pgvector", "--report-dir", "reports",
        "--", "-s", STUDY_SELECTOR,
    ])
    with pytest.raises(RuntimeError):
        validate_study_cohort([
            "--suite", "int", "--jobs", "2", "--no-coverage",
            "--pg-test-profile", "pgvector", "--report-dir", "reports",
            "--", STUDY_SELECTOR,
        ])
    with pytest.raises(RuntimeError):
        validate_study_cohort([
            "--suite", "int", "--jobs", "1", "--no-coverage",
            "--pg-test-profile", "pgvector", "--report-dir", "reports",
            "--", STUDY_SELECTOR,
        ])
    with pytest.raises(RuntimeError):
        validate_study_cohort([
            "--suite", "int", "--no-coverage", "--pg-test-profile", "pgvector",
            "--report-dir", "reports", "--mystery", "value", "--", STUDY_SELECTOR,
        ])
    with pytest.raises(RuntimeError):
        validate_study_cohort([
            "--suite", "int", "--jobs=1", "--jobs=1", "--no-coverage",
            "--pg-test-profile", "pgvector", "--report-dir", "reports",
            "--", STUDY_SELECTOR,
        ])


def test_offline_sandbox_lifetime_4800_for_study_only_and_1200_for_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("runner_pgvector_sandbox.time.monotonic", lambda: 100.0)
    study_ctx = _offline_context(tmp_path, make_study_plan(tmp_path))
    native_ctx = _offline_context(tmp_path, {"role": "native"})
    plain_ctx = _offline_context(tmp_path, {})
    run: CallableCompletedProcess = _completed_process

    assert OfflineSandbox(study_ctx, run, run).deadline == 4900.0
    assert OfflineSandbox(native_ctx, run, run).deadline == 1300.0
    assert OfflineSandbox(plain_ctx, run, run).deadline == 1300.0


def test_runner_sandbox_dispatches_study_selector_to_admit_study_inner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = make_study_plan(tmp_path)
    res_path = tmp_path / "reservation.json"
    plan["reservation_path"] = str(res_path)
    create_reservation(res_path, plan_sha256=plan_digest(plan), attempt_id=plan["attempt_id"])
    adm = issue_admission(
        plan, reservation_path=res_path,
        evaluator_closure_sha256=plan["evaluator_closure_sha256"],
        admission_path=admission_file_for_plan(tmp_path / "plan.json"),
    )
    inner = project_inner_study_plan(plan, admission=adm)
    runtime = inner["derived"]["runtime_config_document"]
    monkeypatch.setattr("runner_pgvector_sandbox.inner_plan", lambda _argv, _env: inner)
    monkeypatch.setattr("runner_pgvector_context.load_inner_plan", lambda _env: inner)

    def fake_runner(cmd, **_kw):
        assert cmd[:3] == ["docker", "image", "inspect"]
        payload = [{
            "Id": inner["derived"]["runtime_config"],
            "Os": "linux", "Architecture": "arm64",
            "RootFS": {"Layers": runtime["rootfs"]["diff_ids"]},
            "Config": runtime["config"],
        }]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    env: dict[str, str] = {}
    report = runner_sandbox._materialize_inner_pgvector_if_requested(
        STUDY_ARGV, env, runner_fn=fake_runner,
    )
    assert report is not None
    assert report["pgvector_setup"] == "imported_verified_offline_study"
    assert report["study_attempt_id"] == adm.attempt_id
    assert env["REPOMAP_TEST_PG_IMAGE"] == inner["derived"]["runtime_config"]
    assert env["REPOMAP_TEST_PHASE"] == inner["phase"]
    assert admit_study_inner is not None


def test_discovery_accepts_study_selector_and_refuses_foreign_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = make_study_plan(tmp_path)
    monkeypatch.setattr(runner_pgvector_discovery, "offline_plan_requested", lambda _e: True)
    monkeypatch.setattr(runner_pgvector_discovery, "load_inner_plan", lambda _e: plan)
    req = {
        "environment": {ENV_OFFLINE_PLAN: INNER_PLAN, "REPOMAP_TEST_PG_PROFILE": "pgvector"},
        "suite": "int", "scoped": True, "cwd": "/workspace",
        "candidate": {"commit": plan["candidate_head"]},
        "pytest_args": ["-s", STUDY_SELECTOR],
    }
    assert runner_pgvector_discovery.plan_for_request(req) == plan
    bad = dict(req, pytest_args=["src/test/int/python/tools/other.int.test.py"])
    with pytest.raises(RuntimeError, match="discovery exact offline selector"):
        runner_pgvector_discovery.plan_for_request(bad)


def test_sandbox_dispatch_requires_ownership_for_study_and_forbids_for_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("test_sandbox.active_sandbox", lambda: False)
    monkeypatch.setattr("test_sandbox.cached_sandbox_image", lambda **_kw: SDK_IMAGE)
    def fake_run(_argv: list[str], **kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr("test_sandbox.run_in_sandbox", fake_run)
    monkeypatch.setattr("runner_pgvector_context.require_native_host", lambda: None)
    monkeypatch.setattr("repomap_test_support.unit_purity.require_live_resource_allowed", lambda _b: None)

    args = SimpleNamespace(suite="int", pg_test_profile="pgvector", d12_system_proof_json=None)
    study_offline = SimpleNamespace(plan={"role": "study", "sdk_image": SDK_IMAGE})
    native_offline = SimpleNamespace(plan={"role": "native", "sdk_image": SDK_IMAGE})
    reporter = SimpleNamespace(expected_sdk_image_id=SDK_IMAGE, close=lambda: None)

    # Study without ownership reporter fails closed (exit 2).
    monkeypatch.setattr("test_sandbox_ownership.prepare_ownership_reporter", lambda _a, _r: None)
    monkeypatch.setattr("runner_pgvector_context.host_context", lambda _a, _r: study_offline)
    assert test_sandbox_dispatch.integration_sandbox_dispatch(args, STUDY_ARGV, repo_root=tmp_path) == 2

    # Study with matching ownership reporter forwards it to run_in_sandbox.
    monkeypatch.setattr("test_sandbox_ownership.prepare_ownership_reporter", lambda _a, _r: reporter)
    assert test_sandbox_dispatch.integration_sandbox_dispatch(args, STUDY_ARGV, repo_root=tmp_path) == 0
    assert calls[-1]["ownership_reporter"] is reporter

    # Native with ownership reporter still refuses combination.
    monkeypatch.setattr("runner_pgvector_context.host_context", lambda _a, _r: native_offline)
    assert test_sandbox_dispatch.integration_sandbox_dispatch(args, STUDY_ARGV, repo_root=tmp_path) == 2


def test_study_support_and_conftest_deselection_and_authenticated_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arbitrary env string never admits (B3).
    with pytest.raises(study_support.SupplementStudyProfileError):
        study_support.study_admission_active({"CLOE_SUPPLEMENT_STUDY_ADMISSION": "nonempty"})

    # Ordinary empty environment returns False (deselects).
    assert study_support.study_admission_active({}, inner_plan_path=tmp_path / "plan.json") is False

    # Valid 0600 projected inner plan + active_sandbox admits.
    plan = make_study_plan(tmp_path)
    res_path = tmp_path / "res.json"
    plan["reservation_path"] = str(res_path)
    create_reservation(res_path, plan_sha256=plan_digest(plan), attempt_id=plan["attempt_id"])
    adm = issue_admission(
        plan, reservation_path=res_path,
        evaluator_closure_sha256=plan["evaluator_closure_sha256"],
    )
    inner = project_inner_study_plan(plan, admission=adm)
    inner_file = tmp_path / "inner-plan.json"
    inner_file.write_bytes(canonical_bytes(inner))
    inner_file.chmod(0o600)
    monkeypatch.setattr("test_sandbox.active_sandbox", lambda _env=None: True)
    env = {ENV_OFFLINE_PLAN: str(inner_file)}
    assert study_support.study_admission_active(env, inner_plan_path=inner_file) is True
    assert study_support.require_study_admission(env, inner_plan_path=inner_file).attempt_id == adm.attempt_id

    # Verify collection hook reconcile_supplement_study deselection vs sole-node admission.
    deselected: list = []
    cfg = SimpleNamespace(
        args=[], rootpath=tmp_path,
        hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)),
    )
    study_item = SimpleNamespace(
        nodeid=STUDY_NODE_ID,
        get_closest_marker=lambda name: True if name == study_support.MARKER else None,
    )
    other_item = SimpleNamespace(nodeid="other.py::test_x", get_closest_marker=lambda _n: None)

    monkeypatch.setattr(study_support, "study_admission_active", lambda: False)
    items = [study_item, other_item]
    study_support.reconcile_supplement_study(cfg, items)
    assert [i.nodeid for i in items] == ["other.py::test_x"] and deselected == [study_item]

    monkeypatch.setattr(study_support, "study_admission_active", lambda: True)
    sole = [study_item]
    study_support.reconcile_supplement_study(cfg, sole)
    assert [i.nodeid for i in sole] == [STUDY_NODE_ID]
    with pytest.raises(study_support.SupplementStudyProfileError):
        study_support.reconcile_supplement_study(cfg, [study_item, other_item])


def test_proof_admission_sets_the_existing_phase_marker_inside_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runner_pgvector_sandbox as sandbox
    from retrieval_supplement_study_contracts import STUDY_PHASE
    from runner_pgvector_supplement_proof import PROOF_ROLE, PROOF_RUNTIME_CONFIG
    plan = {"role": PROOF_ROLE, "derived": {"runtime_config": PROOF_RUNTIME_CONFIG}}
    checked: list[tuple[object, object]] = []
    monkeypatch.setattr(sandbox, "inner_plan", lambda _argv, _env: plan)
    monkeypatch.setattr(sandbox, "checked_image", lambda observed, loaded: checked.append((observed, loaded)))
    environment: dict[str, str] = {}
    result = sandbox.admit_proof_inner(
        [], environment,
        lambda *_args: subprocess.CompletedProcess([], 0, json.dumps([{"Id": PROOF_RUNTIME_CONFIG}]), ""),
    )
    assert checked and result["proof_only"] is True
    assert environment["REPOMAP_TEST_PHASE"] == STUDY_PHASE
    assert environment["REPOMAP_TEST_PG_PROFILE"] == "pgvector"
    assert environment["REPOMAP_TEST_PG_IMAGE"] == PROOF_RUNTIME_CONFIG


@pytest.mark.parametrize("collect_only,phase,offline_requested", [
    (True, "CLOE-W5-SUPPLEMENT-STUDY1", True),
    (False, "ordinary-integration", True),
    (False, "CLOE-W5-SUPPLEMENT-STUDY1", False),
])
def test_receipt_lookup_stays_out_of_collect_and_ordinary_lanes(
    monkeypatch: pytest.MonkeyPatch, collect_only: bool, phase: str, offline_requested: bool,
) -> None:
    import runner_pgvector_context as context
    monkeypatch.setenv("REPOMAP_TEST_PHASE", phase)
    if offline_requested:
        monkeypatch.setenv(ENV_OFFLINE_PLAN, INNER_PLAN)
    else:
        monkeypatch.delenv(ENV_OFFLINE_PLAN, raising=False)
    def forbidden(_environment: object) -> dict[str, Any]:
        raise AssertionError("ordinary/collect-only must not load a sealed plan")
    monkeypatch.setattr(context, "load_inner_plan", forbidden)
    assert context.source_read_receipt_for_report(collect_only=collect_only) is None


@pytest.mark.parametrize("role", ["study", "proof"])
def test_runtime_identity_refusal_preserves_every_existing_environment_field(
    monkeypatch: pytest.MonkeyPatch, role: str,
) -> None:
    import runner_pgvector_sandbox as sandbox
    import retrieval_supplement_study_plan as study_plan
    from runner_pgvector_supplement_proof import PROOF_RUNTIME_CONFIG
    plan = {"role": role, "derived": {"runtime_config": PROOF_RUNTIME_CONFIG}}
    monkeypatch.setattr(sandbox, "inner_plan", lambda _argv, _env: plan)
    monkeypatch.setattr(study_plan, "admission_from_inner_plan",
                        lambda _plan: SimpleNamespace(attempt_id="synthetic-admission"))
    def refusal(_observed: object, _plan: object) -> None:
        raise RuntimeError("synthetic-runtime-refusal")
    monkeypatch.setattr(sandbox, "checked_image", refusal)
    environment = {"retained": "sentinel"}
    admit = sandbox.admit_study_inner if role == "study" else sandbox.admit_proof_inner
    with pytest.raises(RuntimeError, match="synthetic-runtime-refusal"):
        admit([], environment, lambda *_args: subprocess.CompletedProcess(
            [], 0, json.dumps([{"Id": PROOF_RUNTIME_CONFIG}]), ""))
    assert environment == {"retained": "sentinel"}
