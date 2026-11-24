"""Source-level contracts for the closed offline W5 proof delivery."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import runner_pgvector_context as context
import runner_pgvector_discovery as discovery
import runner_pgvector_supplement_proof as proof
from retrieval_supplement_study_contracts import STUDY_SELECTOR
from runner_pgvector_prebuilt import ENV_OFFLINE_PLAN, INNER_PLAN, digest, json_bytes
from repomap_test_support.retrieval_supplement_runtime_fixture import runtime_provenance_fixture


def _proof_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    snapshot = {f"member-{index:03d}": {"sha256": "a" * 64, "mode": 0o644}
                for index in range(101)}
    source_sha = digest(json_bytes(snapshot))
    return {
        "schema": proof.PROOF_SCHEMA,
        "phase": proof.PROOF_PHASE,
        "role": proof.PROOF_ROLE,
        "profile": proof.PROOF_PROFILE,
        "selector": proof.PROOF_SELECTOR,
        "candidate_head": "c" * 40,
        "candidate_snapshot": snapshot,
        "candidate_snapshot_sha256": source_sha,
        "source_freeze_sha256": source_sha,
        "provider_sha256": proof.PROVIDER_SHA256,
        "sdk_image": proof.SDK_IMAGE,
        "archive": {"path": str(tmp_path / "runtime.tar"), "bytes": proof.PROOF_RUNTIME_ARCHIVE_BYTES,
                    "sha256": proof.PROOF_RUNTIME_ARCHIVE_SHA256},
        "derived": runtime_provenance_fixture(),
        "allocation_path": str(tmp_path / "allocation.json"),
        "declaration": proof.proof_declaration(),
    }


def test_proof_cohort_is_exact_and_has_no_study_allocation_authority() -> None:
    argv = [
        "--suite", "int", "--no-coverage", "--jobs", "1",
        "--pg-test-profile", "pgvector", "--report-dir", "reports", "--",
        "-s", proof.PROOF_SELECTOR,
    ]
    proof.validate_proof_cohort(argv)
    assert proof.PROOF_SELECTOR_REGISTRY == (proof.PROOF_SELECTOR,)
    assert not {"reservation_path", "study_workers", "model_workers", "registered_tasks"} & set(
        proof.PROOF_CAPS
    )
    assert all(proof.PROOF_CAPS[key] == 0 for key in (
        "materializations", "runtime_exports", "builds", "pulls",
    ))
    with pytest.raises(RuntimeError):
        proof.validate_proof_cohort([*argv[:-1], "other.int.test.py"])
    with pytest.raises(RuntimeError):
        proof.validate_proof_cohort([*argv[:4], "2", *argv[5:]])


def test_proof_plan_projection_preserves_source_and_runtime_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _proof_plan(tmp_path, monkeypatch)
    assert proof.validate_proof_plan(plan)["role"] == "proof"
    inner = proof.project_inner_plan(plan)
    assert inner["schema"] == proof.PROOF_INNER_SCHEMA
    assert "allocation_path" not in inner and "path" not in inner["archive"]
    assert proof.proof_runtime_identity(inner) == proof.PROOF_RUNTIME_CONFIG
    with pytest.raises(RuntimeError):
        proof.validate_proof_plan({**plan, "reservation_path": "forbidden"})


def test_actual_study_stream_flag_is_normalized_by_context_policy() -> None:
    argv = [
        "--suite", "int", "--no-coverage", "--jobs", "1",
        "--pg-test-profile", "pgvector", "--report-dir", "reports", "--",
        "-s", STUDY_SELECTOR,
    ]
    context.validate_cohort(argv, "study")


def test_discovery_forwards_study_stream_flag_without_broadening_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {"role": "study", "candidate_head": "a" * 40}
    monkeypatch.setattr(discovery, "offline_plan_requested", lambda _env: True)
    monkeypatch.setattr(discovery, "load_inner_plan", lambda _env: plan)
    request = {
        "environment": {ENV_OFFLINE_PLAN: INNER_PLAN, "REPOMAP_TEST_PG_PROFILE": "pgvector"},
        "suite": "int", "scoped": True, "cwd": "/workspace",
        "candidate": {"commit": plan["candidate_head"]},
        "pytest_args": ["-s", STUDY_SELECTOR],
    }
    assert discovery.plan_for_request(request) == plan
    request["pytest_args"] = [
        "--basetemp", "/sandbox-scratch/proof-pytest", "-o",
        "cache_dir=/sandbox-scratch/proof-pytest/cache", "-s", STUDY_SELECTOR,
    ]
    assert discovery.plan_for_request(request) == plan
    with pytest.raises(RuntimeError):
        discovery.plan_for_request({**request, "pytest_args": ["-s", "other.py"]})
