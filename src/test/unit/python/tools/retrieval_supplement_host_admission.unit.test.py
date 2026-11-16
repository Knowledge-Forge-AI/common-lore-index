"""One-use physical admission joins current private receipts to reviewed source."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys

import pytest

from repomap_test_support.retrieval_supplement_study_fixtures import make_study_plan
from retrieval_supplement_host_admission import (
    SCHEMA,
    read_parent_admission,
    validate_authority_record,
)
from retrieval_supplement_host_controller import ActualStudyConfig, child_environment
from retrieval_supplement_study_contracts import (
    ARTIFACT_PIN_DOCUMENT_SHA256,
    ARTIFACT_SOURCE_IDENTITY_SHA256,
    ARTIFACT_TOTAL_BYTES,
    ARTIFACT_FILE_COUNT,
    INPUT_PIN_SHA256,
    WORKER_CLOSURE_SHA256,
)
from retrieval_supplement_study_plan import plan_digest
from retrieval_supplement_values import canonical_bytes, digest_bytes, digest_json


def authority_fixture(name, plan):
    common = {
        "phase": plan["phase"], "candidate_head": plan["candidate_head"],
        "candidate_snapshot_sha256": digest_json(plan["candidate_snapshot"]),
        "evaluator_closure_sha256": plan["evaluator_closure_sha256"],
        "input_pin_sha256": INPUT_PIN_SHA256,
    }
    if name == "source_review":
        return {"schema": "cloe-supplement-source-acceptance-v1", "status": "passed",
                **common, "independent_review_sha256": "sha256:" + "d" * 64,
                "read_coverage_passed": True}
    if name == "scoped_proof":
        return {"schema": "cloe-supplement-scoped-proof-acceptance-v1", "status": "passed",
                **common, "unit_passed": True, "integration_passed": True,
                "static_passed": True, "cleanup_proven": True,
                "complete_suites": False, "coverage": False,
                "proof_receipts_sha256": ["sha256:" + digit * 64 for digit in "123"]}
    if name == "artifact_gate":
        return {"schema": "cloe-supplement-artifact-gate-v1", "phase": plan["phase"],
                "status": "passed", "artifact_source_identity_sha256": ARTIFACT_SOURCE_IDENTITY_SHA256,
                "artifact_pin_document_sha256": ARTIFACT_PIN_DOCUMENT_SHA256,
                "worker_closure_sha256": WORKER_CLOSURE_SHA256,
                "artifact_count": ARTIFACT_FILE_COUNT, "total_bytes": ARTIFACT_TOTAL_BYTES,
                "external_gate_sha256": "sha256:" + "e" * 64}
    return {
        "binding_adr": "0203", "binding_contract": "docs/specs/cloe-w5-supplement-study-v1.md",
        "decision_packet_sha256": "f" * 64, "no_in_progress_dispatch_interruptions": True,
        "operator_messages": ["A", "Proceed"], "phase": plan["phase"],
        "public_staging_publication_after_substantial_reviewed_milestone": True,
        "routine_allocation": "infinite",
        "scientific_heldout_production_main_release_acceptance": False,
        "selection": "A", "source_review_scoped_proof_and_fresh_admission_required": True,
    }


def admission_fixture(root: Path):
    plan = make_study_plan(root)
    refs = {}
    for name in ("source_review", "scoped_proof", "artifact_gate", "operator_selection"):
        path = root / f"{name}.json"
        raw = canonical_bytes(authority_fixture(name, plan))
        path.write_bytes(raw)
        path.chmod(0o600)
        refs[name] = {"path": str(path), "sha256": digest_bytes(raw)}
    plan["review1_receipt_sha256"] = refs["source_review"]["sha256"]
    plan["operator_decision_sha256"] = refs["operator_selection"]["sha256"]
    record = {"schema": SCHEMA, "phase": plan["phase"], "attempt_id": plan["attempt_id"],
        "plan_sha256": plan_digest(plan), "evaluator_closure_sha256": plan["evaluator_closure_sha256"],
        "input_pin_sha256": plan["input_pin_sha256"], **refs, "execution_authorized": True,
        "model_workers": 2, "slots": 432, "held_out_authorized": False, "publication_authorized": False}
    path = root / "admission.json"
    raw = canonical_bytes(record)
    path.write_bytes(raw)
    path.chmod(0o600)
    return path, digest_bytes(raw), plan, record


@pytest.mark.parametrize("name,mutation", [
    ("foreign_role", lambda record, plan: None),
    ("source_review", lambda record, plan: record.update(status="BLOCK")),
    ("source_review", lambda record, plan: record.update(input_pin_sha256="sha256:" + "f" * 64)),
    ("scoped_proof", lambda record, plan: record.update(
        candidate_snapshot_sha256=digest_json({"stale": True}))),
    ("scoped_proof", lambda record, plan: record.update(complete_suites=True)),
    ("artifact_gate", lambda record, plan: record.update(
        artifact_pin_document_sha256="sha256:" + "f" * 64)),
])
def test_authority_roles_reject_foreign_or_stale_scope(name, mutation, tmp_path):
    plan = make_study_plan(tmp_path)
    record = authority_fixture("source_review" if name == "foreign_role" else name, plan)
    if name == "foreign_role":
        with pytest.raises(ValueError):
            validate_authority_record("foreign_role", record, plan)
        return
    mutation(record, plan)
    with pytest.raises(ValueError):
        validate_authority_record(name, record, plan)


def test_each_current_receipt_is_rehashed_before_authority_reuse(tmp_path):
    path, digest, plan, record = admission_fixture(tmp_path)
    admitted = read_parent_admission(path, expected_sha256=digest, plan=plan)
    charged: list[int] = []
    admitted.require_current(charged.append)
    assert len(charged) == 5 and admitted.raw_sha256 == digest
    proof = Path(record["scoped_proof"]["path"])
    raw = proof.read_bytes()
    proof.write_bytes(raw.replace(b"scoped", b"forged"))
    with pytest.raises(ValueError, match="receipt_changed"):
        admitted.require_current(charged.append)


@pytest.mark.parametrize("field,value", [("execution_authorized", False), ("model_workers", True),
    ("slots", 431), ("held_out_authorized", True), ("publication_authorized", True),
    ("attempt_id", "foreign-attempt"), ("evaluator_closure_sha256", "sha256:" + "f" * 64)])
def test_wrong_source_or_broader_scope_cannot_admit(tmp_path, field, value):
    path, _, plan, record = admission_fixture(tmp_path)
    record[field] = value
    raw = canonical_bytes(record)
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        read_parent_admission(path, expected_sha256=digest_bytes(raw), plan=plan)


@pytest.mark.parametrize("mutation", ["changed", "mode", "symlink", "alias"])
def test_current_receipt_custody_and_role_aliases_are_rejected(tmp_path, mutation):
    path, digest, plan, record = admission_fixture(tmp_path)
    receipt = Path(record["artifact_gate"]["path"])
    if mutation == "changed": receipt.write_bytes(b"changed")
    elif mutation == "mode": receipt.chmod(0o644)
    elif mutation == "symlink":
        receipt.unlink()
        receipt.symlink_to(Path(record["source_review"]["path"]))
    else:
        record["artifact_gate"] = record["source_review"]
        raw = canonical_bytes(record)
        path.write_bytes(raw)
        digest = digest_bytes(raw)
    with pytest.raises((ValueError, RuntimeError)):
        read_parent_admission(path, expected_sha256=digest, plan=plan)


def test_child_environment_discards_credentials_and_rejects_daemon_redirection(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_CONTEXT", "synthetic-approved-context")
    environment = dict(os.environ, GH_TOKEN="synthetic-unit-token", HTTPS_PROXY="synthetic-proxy")
    config = ActualStudyConfig(tmp_path, tmp_path / "plan", tmp_path / "allocation", tmp_path,
        tmp_path / "artifacts", Path(sys.executable), tmp_path / "admission", "sha256:" + "a" * 64, environment)
    child = child_environment(config)
    assert "GH_TOKEN" not in child and "HTTPS_PROXY" not in child
    assert child["DOCKER_CONTEXT"] == "synthetic-approved-context"
    redirected = dict(environment, DOCKER_CONTEXT="synthetic-foreign-context")
    with pytest.raises(ValueError, match="environment_drift"):
        child_environment(replace(config, environment=redirected))
