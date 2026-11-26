"""Closed study plan, reservation state machine, inner projection and cohort refusals."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path

import pytest

from common_lore_index_test_support.retrieval_supplement_study_fixtures import make_study_plan
from retrieval_supplement_study_contracts import STUDY_INNER_PLAN_SCHEMA, StudyRefusal
from retrieval_supplement_study_plan import (
    StudyAdmission,
    admission_file_for_plan,
    admission_from_inner_plan,
    consume_reservation,
    create_reservation,
    issue_admission,
    plan_digest,
    project_inner_study_plan,
    read_reservation,
    require_admission_current,
    require_reservation_admitted_for_runner,
    settle_reservation,
    validate_inner_study_plan,
    validate_study_plan,
)
from retrieval_supplement_values import canonical_bytes
from runner_pgvector_budget import STUDY_CAPS, Allocation
from runner_pgvector_context import validate_cohort, validate_plan
from runner_pgvector_study import study_sdk_lifetime, validate_study_cohort

ARGV = ["--suite", "int", "--no-coverage", "--pg-test-profile", "pgvector",
        "--report-dir", "reports", "--",
        "-s", "src/test/int/python/tools/retrieval_supplement_native.int.test.py"]


def _reserve(tmp_path: Path, plan: dict) -> Path:
    path = tmp_path / "reservation.json"
    plan["reservation_path"] = str(path)
    create_reservation(path, plan_sha256=plan_digest(plan), attempt_id=plan["attempt_id"])
    return path


@pytest.mark.parametrize("counter", ["runtime_imports", "model_workers"])
def test_unconsumed_reservation_with_runtime_spend_refuses(tmp_path: Path, counter: str) -> None:
    plan = make_study_plan(tmp_path)
    path = _reserve(tmp_path, plan)
    record = read_reservation(path)
    record[counter] = 1
    path.write_bytes(canonical_bytes(record))
    with pytest.raises(StudyRefusal, match="partial_runtime_spend"):
        consume_reservation(path, plan_sha256=plan_digest(plan), attempt_id=plan["attempt_id"])
    assert read_reservation(path)["consumed"] is False


def test_valid_plan_and_exact_cohort(tmp_path: Path) -> None:
    plan = make_study_plan(tmp_path)
    assert validate_study_plan(plan)["role"] == "study"
    validate_study_cohort(ARGV)
    validate_cohort(ARGV, "study")
    assert study_sdk_lifetime(plan) == 4800


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(role="native"),
    lambda p: p.update(schema="cloe-pgvector-offline-plan-v1"),
    lambda p: p.update(selector="src/test/int/python/tools/other.int.test.py"),
    lambda p: p.update(profile="core"),
    lambda p: p.update(sdk_image="sha256:" + "f" * 64),
    lambda p: p.update(provider_sha256="f" * 64),
    lambda p: p.update(artifact_source_identity_sha256=p["artifact_pin_document_sha256"]),
    lambda p: p.update(candidate_head="g" * 40),
    lambda p: p["candidate_snapshot"].update({"synthetic/bad.py": {"sha256": "c" * 64}}),
    lambda p: p["declaration"].update(execution_authorized=True),
    lambda p: p["declaration"].update(slot_manifest=[]),
    lambda p: p["derived"].update(runtime_config="sha256:" + "f" * 64),
    lambda p: p["derived"]["byte_forecast_binding"].update(candidate_bytes=4097),
    lambda p: p["derived"]["byte_forecast_binding"].update(request_bytes=[1, 1]),
    lambda p: p["derived"].update(private_path="/private/host/path"),
    lambda p: p.update(extra_field=True),
    lambda p: p.pop("evaluator_closure_sha256"),
    lambda p: p["archive"].update(path="relative/runtime.tar"),
])
def test_plan_refusals(tmp_path: Path, mutate) -> None:
    plan = make_study_plan(tmp_path)
    mutate(plan)
    with pytest.raises(StudyRefusal):
        validate_study_plan(plan)


def test_old_plan_cannot_gain_study_role(tmp_path: Path) -> None:
    plan = make_study_plan(tmp_path)
    plan["schema"] = "cloe-pgvector-offline-plan-v1"
    with pytest.raises(RuntimeError):
        validate_plan(plan)


@pytest.mark.parametrize("argv", [
    ["--suite", "unit", "--no-coverage", "--", ARGV[-1]],
    ["--suite", "int", "--", ARGV[-1]],
    ["--suite", "int", "--no-coverage", "--pg-test-profile", "core", "--report-dir", "r", "--", ARGV[-1]],
    ["--suite", "int", "--no-coverage", "--pg-test-profile", "pgvector", "--report-dir", "r", "--", "x.py"],
    ["--suite", "int", "--no-coverage", "--pg-test-profile", "pgvector", "--report-dir", "r", "--", ARGV[-1], ARGV[-1]],
])
def test_cohort_refusals(argv) -> None:
    with pytest.raises(RuntimeError):
        validate_study_cohort(argv)


def test_reservation_lifecycle_and_runner_gate(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan = make_study_plan(tmp_path)
    res_path = _reserve(tmp_path, plan)
    plan_path.write_bytes(canonical_bytes(plan))
    plan_path.chmod(0o600)

    # Unconsumed reservation refuses runner pre-dispatch gate (B3).
    with pytest.raises(StudyRefusal):
        require_reservation_admitted_for_runner(plan, plan_path=plan_path)

    admission = issue_admission(
        plan, reservation_path=res_path,
        evaluator_closure_sha256=plan["evaluator_closure_sha256"],
        admission_path=admission_file_for_plan(plan_path),
    )
    assert read_reservation(res_path)["cleanup"] == "in_progress"
    require_admission_current(admission, plan)
    validated = require_reservation_admitted_for_runner(plan, plan_path=plan_path)
    assert validated["attempt_id"] == plan["attempt_id"]

    # Reuse while in_progress refuses.
    with pytest.raises(StudyRefusal):
        consume_reservation(
            res_path, plan_sha256=plan_digest(plan), attempt_id=plan["attempt_id"],
        )

    # Settle to unknown blocks both runner gate and fresh admission (B4).
    settled = settle_reservation(
        res_path, plan_sha256=plan_digest(plan), cleanup="unknown",
        runtime_imports=1, model_workers=1,
    )
    assert settled["status"] == "blocked" and settled["cleanup"] == "unknown"
    with pytest.raises(StudyRefusal):
        require_reservation_admitted_for_runner(plan, plan_path=plan_path)
    with pytest.raises(StudyRefusal):
        issue_admission(plan, reservation_path=res_path,
                        evaluator_closure_sha256=plan["evaluator_closure_sha256"])


def test_inner_plan_projection_strips_private_host_paths(tmp_path: Path) -> None:
    plan = make_study_plan(tmp_path)
    res_path = _reserve(tmp_path, plan)
    admission = issue_admission(
        plan, reservation_path=res_path,
        evaluator_closure_sha256=plan["evaluator_closure_sha256"],
    )
    inner = project_inner_study_plan(plan, admission=admission)
    assert inner["schema"] == STUDY_INNER_PLAN_SCHEMA
    assert "allocation_path" not in inner and "reservation_path" not in inner
    assert "path" not in inner["archive"]
    rehydrated = admission_from_inner_plan(inner)
    require_admission_current(rehydrated, inner)
    assert rehydrated.to_dict() == admission.to_dict()

    leaked = copy.deepcopy(inner)
    leaked["archive"]["path"] = str(tmp_path / "runtime.tar")
    with pytest.raises(StudyRefusal):
        validate_inner_study_plan(leaked)


def test_stale_plan_and_forged_admission_refuse(tmp_path: Path) -> None:
    plan = make_study_plan(tmp_path)
    path = _reserve(tmp_path, plan)
    drifted = copy.deepcopy(plan)
    drifted["attempt_id"] = "studytest-attempt-2"
    with pytest.raises(StudyRefusal):
        issue_admission(drifted, reservation_path=path,
                        evaluator_closure_sha256=plan["evaluator_closure_sha256"])
    assert read_reservation(path)["consumed"] is False
    with pytest.raises(StudyRefusal):
        StudyAdmission(plan_digest(plan), plan["attempt_id"], plan_digest(plan),
                       plan["evaluator_closure_sha256"])


def test_spent_admission_output_is_checked_before_consumption(tmp_path: Path) -> None:
    plan = make_study_plan(tmp_path)
    path = _reserve(tmp_path, plan)
    output = tmp_path / "admission.json"
    output.write_bytes(b"{}\n")
    output.chmod(0o600)
    with pytest.raises(StudyRefusal, match="destination_spent"):
        issue_admission(
            plan, reservation_path=path,
            evaluator_closure_sha256=plan["evaluator_closure_sha256"],
            admission_path=output,
        )
    assert read_reservation(path)["consumed"] is False


def test_concurrent_consumers_commit_exactly_once(tmp_path: Path) -> None:
    plan = make_study_plan(tmp_path)
    path = _reserve(tmp_path, plan)

    def consume() -> str:
        try:
            consume_reservation(
                path, plan_sha256=plan_digest(plan), attempt_id=plan["attempt_id"],
            )
        except StudyRefusal as error:
            return error.code
        return "consumed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda _index: consume(), (1, 2)))
    assert sorted(outcomes) == ["consumed", "study_reservation:reused"]
    assert read_reservation(path)["cleanup"] == "in_progress"


def test_stale_disk_journal_refuses_without_reuse(tmp_path: Path) -> None:
    plan = make_study_plan(tmp_path)
    path = _reserve(tmp_path, plan)
    before = path.read_bytes()
    pending = path.with_name(path.name + ".next")
    pending.write_bytes(b"{}\n")
    os.chmod(pending, 0o600)
    with pytest.raises(StudyRefusal, match="ambiguous_pending"):
        read_reservation(path)
    assert path.read_bytes() == before


def test_reservation_private_custody_refuses_world_readable_disk_state(tmp_path: Path) -> None:
    plan = make_study_plan(tmp_path)
    path = _reserve(tmp_path, plan)
    path.chmod(0o644)
    with pytest.raises(StudyRefusal, match="file_custody"):
        read_reservation(path)


def test_study_allocation_caps_are_separate(tmp_path: Path) -> None:
    meter = Allocation(tmp_path / "allocation.json", STUDY_CAPS)
    assert meter.caps["sdk_study"] == 1 and meter.caps["runtime_exports"] == 0
    assert meter.caps["read_bytes"] == 12 * 1024**3
