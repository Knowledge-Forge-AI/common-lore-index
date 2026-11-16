"""Closed, source-bound parent admission for one physical supplement study."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from typing import Any

from retrieval_supplement_study_contracts import (
    ARTIFACT_PIN_DOCUMENT_SHA256,
    ARTIFACT_SOURCE_IDENTITY_SHA256,
    ARTIFACT_TOTAL_BYTES,
    ARTIFACT_FILE_COUNT,
    INPUT_PIN_SHA256,
    STUDY_PHASE,
    WORKER_CLOSURE_SHA256,
    refuse,
)
from retrieval_supplement_study_plan import MAX_PLAN_BYTES, plan_digest, validate_study_plan
from retrieval_supplement_primitives import digest_bytes, digest_json, strict_json
from offline_qualification_files import private_file, read_regular

SCHEMA = "cloe-supplement-physical-study-parent-admission-v1"
FIELDS = frozenset({"schema", "phase", "attempt_id", "plan_sha256", "evaluator_closure_sha256",
    "input_pin_sha256", "source_review", "scoped_proof", "artifact_gate", "operator_selection",
    "execution_authorized", "model_workers", "slots", "held_out_authorized", "publication_authorized"})
REFS = ("source_review", "scoped_proof", "artifact_gate", "operator_selection")
_SHA256 = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_AUTHORITY_HASHES = frozenset({
    INPUT_PIN_SHA256, ARTIFACT_SOURCE_IDENTITY_SHA256,
    ARTIFACT_PIN_DOCUMENT_SHA256, WORKER_CLOSURE_SHA256,
})
_SOURCE_FIELDS = frozenset({
    "schema", "phase", "status", "candidate_head", "candidate_snapshot_sha256",
    "evaluator_closure_sha256", "input_pin_sha256", "independent_review_sha256",
    "read_coverage_passed",
})
_PROOF_FIELDS = frozenset({
    "schema", "phase", "status", "candidate_head", "candidate_snapshot_sha256",
    "evaluator_closure_sha256", "input_pin_sha256", "unit_passed",
    "integration_passed", "static_passed", "cleanup_proven", "complete_suites",
    "coverage", "proof_receipts_sha256",
})
_ARTIFACT_FIELDS = frozenset({
    "schema", "phase", "status", "artifact_source_identity_sha256",
    "artifact_pin_document_sha256", "worker_closure_sha256", "artifact_count",
    "total_bytes", "external_gate_sha256",
})
_OPERATOR_FIELDS = frozenset({
    "binding_adr", "binding_contract", "decision_packet_sha256",
    "no_in_progress_dispatch_interruptions", "operator_messages", "phase",
    "public_staging_publication_after_substantial_reviewed_milestone",
    "routine_allocation", "scientific_heldout_production_main_release_acceptance",
    "selection", "source_review_scoped_proof_and_fresh_admission_required",
})


def _require_sha256(value: object, code: str, *, aliases: frozenset[str] = frozenset()) -> None:
    refuse(type(value) is str and _SHA256.fullmatch(value) is not None and value not in aliases, code)


def _identity_aliases(plan: Mapping[str, Any]) -> frozenset[str]:
    values = {
        plan[key] for key in (
            "input_pin_sha256", "artifact_source_identity_sha256",
            "artifact_pin_document_sha256", "worker_closure_sha256",
            "evaluator_closure_sha256", "review1_receipt_sha256",
            "operator_decision_sha256", "schedule_sha256", "byte_forecast_sha256",
        ) if type(plan.get(key)) is str
    }
    values.update({digest_json(plan["candidate_snapshot"]), plan_digest(plan)})
    return _AUTHORITY_HASHES | values


def _require_common_authority(record: Mapping[str, Any], plan: Mapping[str, Any], code: str) -> None:
    refuse(record["phase"] == plan["phase"] == STUDY_PHASE, f"{code}:phase")
    refuse(record["candidate_head"] == plan["candidate_head"], f"{code}:candidate_head")
    refuse(record["candidate_snapshot_sha256"] == digest_json(plan["candidate_snapshot"]),
           f"{code}:candidate_snapshot")
    refuse(record["evaluator_closure_sha256"] == plan["evaluator_closure_sha256"],
           f"{code}:evaluator_closure")
    refuse(record["input_pin_sha256"] == plan["input_pin_sha256"] == INPUT_PIN_SHA256,
           f"{code}:input_pin")


def validate_authority_record(name: str, record: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    """Validate one closed authority receipt against the already validated plan."""
    refuse(name in REFS and isinstance(record, Mapping), "physical_admission:foreign_role")
    assert isinstance(record, Mapping)
    if name == "source_review":
        refuse(set(record) == _SOURCE_FIELDS and record["schema"] ==
               "cloe-supplement-source-acceptance-v1" and record["status"] == "passed",
               "physical_admission:source_review_schema")
        _require_common_authority(record, plan, "physical_admission:source_review")
        _require_sha256(record["independent_review_sha256"],
                        "physical_admission:source_review_review_hash",
                        aliases=_identity_aliases(plan))
        refuse(record["read_coverage_passed"] is True,
               "physical_admission:source_review_coverage")
        return
    if name == "scoped_proof":
        refuse(set(record) == _PROOF_FIELDS and record["schema"] ==
               "cloe-supplement-scoped-proof-acceptance-v1" and record["status"] == "passed",
               "physical_admission:scoped_proof_schema")
        _require_common_authority(record, plan, "physical_admission:scoped_proof")
        for key in ("unit_passed", "integration_passed", "static_passed", "cleanup_proven"):
            refuse(record[key] is True, f"physical_admission:scoped_proof_{key}")
        refuse(record["complete_suites"] is False and record["coverage"] is False,
               "physical_admission:scoped_proof_scope")
        receipts = record["proof_receipts_sha256"]
        refuse(type(receipts) is list and 3 <= len(receipts) <= 64,
               "physical_admission:scoped_proof_receipts")
        for receipt in receipts:
            _require_sha256(receipt, "physical_admission:scoped_proof_receipt")
        refuse(len(set(receipts)) == len(receipts),
               "physical_admission:scoped_proof_receipt_duplicates")
        return
    if name == "artifact_gate":
        refuse(set(record) == _ARTIFACT_FIELDS and record["schema"] ==
               "cloe-supplement-artifact-gate-v1" and record["phase"] == STUDY_PHASE
               and record["status"] == "passed", "physical_admission:artifact_gate_schema")
        refuse(record["artifact_source_identity_sha256"] == ARTIFACT_SOURCE_IDENTITY_SHA256
               and record["artifact_pin_document_sha256"] == ARTIFACT_PIN_DOCUMENT_SHA256
               and record["worker_closure_sha256"] == WORKER_CLOSURE_SHA256
               and record["artifact_count"] == ARTIFACT_FILE_COUNT
               and record["total_bytes"] == ARTIFACT_TOTAL_BYTES,
               "physical_admission:artifact_gate_identity")
        _require_sha256(record["external_gate_sha256"],
                        "physical_admission:artifact_gate_external_hash",
                        aliases=_identity_aliases(plan))
        return
    refuse(set(record) == _OPERATOR_FIELDS, "physical_admission:operator_selection_schema")
    refuse(record["phase"] == plan["phase"] == STUDY_PHASE and record["selection"] == "A"
           and record["binding_adr"] == "0203"
           and record["binding_contract"] == "docs/specs/cloe-w5-supplement-study-v1.md"
           and record["routine_allocation"] == "infinite"
           and record["source_review_scoped_proof_and_fresh_admission_required"] is True
           and record["no_in_progress_dispatch_interruptions"] is True
           and record["scientific_heldout_production_main_release_acceptance"] is False
           and record["public_staging_publication_after_substantial_reviewed_milestone"] is True,
           "physical_admission:operator_selection_scope")
    refuse(record["operator_messages"] == ["A", "Proceed"]
           and type(record["decision_packet_sha256"]) is str
           and _HEX64.fullmatch(record["decision_packet_sha256"]) is not None,
           "physical_admission:operator_selection_identity")


@dataclass(frozen=True)
class PhysicalStudyAdmission:
    record: Mapping[str, Any] = field(repr=False)
    raw_sha256: str
    pins: Mapping[Path, tuple[int, str]] = field(repr=False)

    def require_current(self, charge_bytes: Callable[[int], None]) -> None:
        for path, (size, digest) in self.pins.items():
            private_file(path)
            refuse(path.stat().st_size == size, "physical_admission:receipt_changed")
            charge_bytes(size + 1)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                raw = stream.read(size + 1)
            refuse(len(raw) == size and digest_bytes(raw) == digest, "physical_admission:receipt_changed")


def read_parent_admission(path: Path, *, expected_sha256: str,
                          plan: Mapping[str, Any]) -> PhysicalStudyAdmission:
    """Re-read exact private receipts; names and booleans alone never suffice."""
    validated = validate_study_plan(plan)
    private_file(path)
    refuse(0 < path.stat().st_size <= MAX_PLAN_BYTES, "physical_admission:bounds")
    raw = read_regular(path, MAX_PLAN_BYTES)
    refuse(digest_bytes(raw) == expected_sha256, "physical_admission:identity")
    record = strict_json(raw)
    refuse(isinstance(record, dict) and set(record) == FIELDS and record["schema"] == SCHEMA
           and record["phase"] == STUDY_PHASE, "physical_admission:closed_schema")
    refuse(record["attempt_id"] == validated["attempt_id"]
           and record["plan_sha256"] == plan_digest(validated)
           and record["evaluator_closure_sha256"] == validated["evaluator_closure_sha256"]
           and record["input_pin_sha256"] == INPUT_PIN_SHA256, "physical_admission:source_join")
    refuse(record["execution_authorized"] is True and type(record["model_workers"]) is int
           and record["model_workers"] == 2 and type(record["slots"]) is int and record["slots"] == 432
           and record["held_out_authorized"] is False and record["publication_authorized"] is False,
           "physical_admission:scope")
    pins = {path: (len(raw), expected_sha256)}
    for name in REFS:
        reference = record[name]
        refuse(isinstance(reference, dict) and set(reference) == {"path", "sha256"}
               and type(reference["path"]) is str and type(reference["sha256"]) is str
               and _SHA256.fullmatch(reference["sha256"]) is not None,
               "physical_admission:reference_schema")
        receipt = Path(reference["path"])
        private_file(receipt)
        refuse(0 < receipt.stat().st_size <= MAX_PLAN_BYTES, "physical_admission:reference_bounds")
        receipt_raw = read_regular(receipt, MAX_PLAN_BYTES)
        refuse(digest_bytes(receipt_raw) == reference["sha256"],
               "physical_admission:reference_identity")
        receipt_record = strict_json(receipt_raw)
        validate_authority_record(name, receipt_record, validated)
        pins[receipt] = (len(receipt_raw), reference["sha256"])
    refuse(record["source_review"]["sha256"] == validated["review1_receipt_sha256"]
           and record["operator_selection"]["sha256"] == validated["operator_decision_sha256"],
           "physical_admission:authority_join")
    refuse(len(pins) == 5 and sum(size for size, _digest in pins.values()) <= 128 * 1024,
           "physical_admission:current_receipt_set_bounds")
    return PhysicalStudyAdmission(record, expected_sha256, pins)
