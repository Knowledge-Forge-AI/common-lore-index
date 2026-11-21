"""Fixed ordinary/offline scratch sizes require current inner authority."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import runner_pgvector_context as context
import runner_pgvector_supplement_proof as proof
import test_sandbox_capacity as capacity
from repomap_test_support.retrieval_supplement_runtime_fixture import runtime_provenance_fixture
from repomap_test_support.retrieval_supplement_study_fixtures import make_study_plan
from retrieval_supplement_study_plan import (
    create_reservation, issue_admission, plan_digest, project_inner_study_plan,
)
from retrieval_supplement_study_contracts import StudyRefusal
from runner_pgvector_prebuilt import ENV_OFFLINE_PLAN, digest, json_bytes

GIB = 1024**3


def _plan(tmp_path: Path, role: str) -> dict[str, Any]:
    if role == "study":
        plan = make_study_plan(tmp_path)
        reservation = tmp_path / "reservation.json"
        plan["reservation_path"] = str(reservation)
        create_reservation(reservation, plan_sha256=plan_digest(plan), attempt_id=plan["attempt_id"])
        admission = issue_admission(plan, reservation_path=reservation,
                                    evaluator_closure_sha256=plan["evaluator_closure_sha256"])
        return project_inner_study_plan(plan, admission=admission)
    snapshot = {f"member-{i:03d}": {"sha256": "a" * 64, "mode": 0o644} for i in range(101)}
    source = digest(json_bytes(snapshot))
    return proof.project_inner_plan({
        "schema": proof.PROOF_SCHEMA, "phase": proof.PROOF_PHASE, "role": "proof",
        "profile": "pgvector", "selector": proof.PROOF_SELECTOR, "candidate_head": "c" * 40,
        "candidate_snapshot": snapshot, "candidate_snapshot_sha256": source,
        "source_freeze_sha256": source, "provider_sha256": proof.PROVIDER_SHA256,
        "sdk_image": proof.SDK_IMAGE,
        "archive": {"path": str(tmp_path / "runtime.tar"), "bytes": proof.PROOF_RUNTIME_ARCHIVE_BYTES,
                    "sha256": proof.PROOF_RUNTIME_ARCHIVE_SHA256},
        "derived": runtime_provenance_fixture(), "allocation_path": str(tmp_path / "allocation.json"),
        "declaration": proof.proof_declaration(),
    })


@pytest.fixture
def sealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "sealed-plan.json"
    monkeypatch.setattr(context, "INNER_PLAN", str(path))
    monkeypatch.setattr("test_sandbox.active_sandbox", lambda _env: True)
    monkeypatch.delenv(ENV_OFFLINE_PLAN, raising=False)
    return path


def _write(path: Path, plan: dict[str, Any]) -> dict[str, str]:
    path.write_bytes(json_bytes(plan))
    path.chmod(0o600)
    return {ENV_OFFLINE_PLAN: str(path)}


def test_ordinary_keeps_eight_gib_and_arbitrary_size_flags_do_not_authorize(sealed: Path) -> None:
    assert context.sandbox_scratch_bytes({}) == 8 * GIB
    assert context.sandbox_scratch_bytes({"SCRATCH_BYTES": str(2 * GIB)}) == 8 * GIB


@pytest.mark.parametrize("role", ["proof", "study"])
def test_valid_sealed_inner_plan_selects_two_gib_without_whole_source_rehash(
    sealed: Path, role: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _write(sealed, _plan(sealed.parent, role))
    monkeypatch.setattr(context, "verify_checkout", lambda *_args: pytest.fail("capacity cannot reserve source verification"))
    assert context.sandbox_scratch_bytes(env) == 2 * GIB
    plan = json.loads(sealed.read_bytes())
    plan["provider_sha256"] = "0" * 64
    _write(sealed, plan)
    with pytest.raises((RuntimeError, StudyRefusal)):
        context.sandbox_scratch_bytes(env)


@pytest.mark.parametrize("fault", ["inactive", "wrong-path", "lost-env", "host-schema", "bad-mode", "duplicate"])
def test_profile_refuses_missing_or_substituted_authority(
    sealed: Path, fault: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(sealed.parent, "proof")
    env = _write(sealed, plan)
    if fault == "inactive":
        monkeypatch.setattr("test_sandbox.active_sandbox", lambda _env: False)
    elif fault == "wrong-path":
        env[ENV_OFFLINE_PLAN] = str(sealed.parent / "other.json")
    elif fault == "lost-env":
        env = {}
    elif fault == "host-schema":
        plan["schema"] = proof.PROOF_SCHEMA
        _write(sealed, plan)
    elif fault == "bad-mode":
        sealed.chmod(0o644)
    else:
        sealed.write_text('{"role":"proof","role":"study"}')
    with pytest.raises(RuntimeError):
        context.sandbox_scratch_bytes(env)


@pytest.mark.parametrize("scratch_total,free,docker_total,docker_free,reason", [
    (2 * GIB, GIB, 4 * GIB, GIB, None),
    (8 * GIB, GIB, 4 * GIB, GIB, "scratch_limit_mismatch"),
    (2 * GIB, GIB - 1, 4 * GIB, GIB, "scratch_headroom_insufficient"),
    (2 * GIB, GIB, 2 * GIB, GIB, "inner_docker_limit_mismatch"),
    (2 * GIB, GIB, 4 * GIB, 0, "inner_docker_capacity_refused: exhausted"),
])
def test_bounded_profile_preserves_pressure_and_inner_docker_guards(
    sealed: Path, monkeypatch: pytest.MonkeyPatch, scratch_total: int,
    free: int, docker_total: int, docker_free: int, reason: str | None,
) -> None:
    env = _write(sealed, _plan(sealed.parent, "proof"))
    monkeypatch.setenv(ENV_OFFLINE_PLAN, env[ENV_OFFLINE_PLAN])
    monkeypatch.setattr(capacity, "space", lambda path: (
        (free, scratch_total) if path == "/sandbox-scratch" else (docker_free, docker_total)))
    if reason:
        with pytest.raises(RuntimeError, match=reason):
            capacity.validate_scratch_capacity()
    else:
        capacity.validate_scratch_capacity()
