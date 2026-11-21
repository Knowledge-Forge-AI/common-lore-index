"""Owners for BODY parent receipt joins and candidate/operator binding."""
from __future__ import annotations

import inspect
import pytest

from retrieval_body_study_contracts import BodyStudyRefusal, BODY_PHASE
from retrieval_body_study_host_admission import (
    PhysicalBodyAdmission, read_parent_admission, validate_authority_record,
)


def test_parent_admission_is_receipt_object_not_boolean() -> None:
    assert "expected_sha256" in inspect.signature(read_parent_admission).parameters
    assert "require_current" in PhysicalBodyAdmission.__dict__


def test_operator_receipt_binds_body_route_without_stale_proceed_message() -> None:
    plan = {"operator_decision_sha256": "sha256:" + "a" * 64}
    record = {"schema": "cloe-body-study-operator-selection-v1", "phase": BODY_PHASE,
              "selection": "A", "binding_adr": "0206",
              "binding_contract": "docs/specs/cloe-w5-body-calibration-protocol-v1.md",
              "source_review_scoped_proof_and_fresh_admission_required": True,
              "scientific_heldout_production_main_release_acceptance": False,
              "decision_packet_sha256": plan["operator_decision_sha256"]}
    validate_authority_record("operator_selection", record, plan)
    stale = {**record, "selection": "A\nProceed"}
    with pytest.raises(BodyStudyRefusal):
        validate_authority_record("operator_selection", stale, plan)
