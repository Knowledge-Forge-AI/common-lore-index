"""Fresh source-bound physical admission for one BODY host attempt."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any, cast

from retrieval_body_study_contracts import (
    BODY_INPUT_PIN_SHA256, BODY_PARENT_ADMISSION_SCHEMA, BODY_PHASE, BODY_ROLE,
    BODY_PROTOCOL_SHA256, MAX_PLAN_BYTES,
    SLOT_COUNT, refuse, strict_json,
)
from retrieval_body_study_plan import (
    ARTIFACT_PIN_DOCUMENT_SHA256, ARTIFACT_SOURCE_IDENTITY_SHA256,
    plan_digest, validate_body_plan,
)
from retrieval_body_study_reservation import private_file

_SHA = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_HEX = re.compile(r"\A[0-9a-f]{64}\Z")
REFS = ("source_review", "scoped_proof", "artifact_gate", "operator_selection")
FIELDS = frozenset({
    "schema", "phase", "role", "attempt_id", "plan_sha256", "source_closure_sha256",
    "treatment_sha256", "worker_closure_sha256", "input_pin_sha256", "protocol_sha256",
    "source_review", "scoped_proof", "artifact_gate", "operator_selection",
    "execution_authorized", "model_workers", "slots", "held_out_authorized",
    "publication_authorized",
})


def _sha(value: object, code: str, *, scheme: bool = True) -> str:
    text = value if type(value) is str else ""
    good = (_SHA.fullmatch(text) is not None if scheme else _HEX.fullmatch(text) is not None)
    refuse(good, code)
    return text


def _raw(path: Path, *, maximum_bytes: int = MAX_PLAN_BYTES) -> bytes:
    from retrieval_supplement_files import read_member
    refuse(type(maximum_bytes) is int and 0 < maximum_bytes <= MAX_PLAN_BYTES,
           "body admission read bound")
    private_file(path)
    parent = path.parent.stat()
    refuse(path.parent.is_dir() and parent.st_uid == os.getuid()
           and stat.S_IMODE(parent.st_mode) == 0o700,
           "body admission parent custody")
    refuse(0 < path.stat().st_size <= maximum_bytes, "body admission receipt bounds")
    raw = read_member(path.parent, path.name, max_bytes=maximum_bytes)
    private_file(path)
    return raw


def _decode(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _raw(path)
    try:
        value = strict_json(raw, maximum_bytes=MAX_PLAN_BYTES)
    except (UnicodeDecodeError, ValueError):
        raise ValueError("body admission receipt JSON") from None
    refuse(isinstance(value, dict), "body admission receipt object")
    return value, raw


def _common(record: Mapping[str, Any], plan: Mapping[str, Any], code: str) -> None:
    refuse(record.get("phase") == BODY_PHASE and record.get("role") == BODY_ROLE,
           f"{code} phase/role")
    refuse(record.get("attempt_id") == plan["attempt_id"]
           and record.get("candidate_head", plan["candidate_head"]) == plan["candidate_head"],
           f"{code} candidate")
    for key in ("input_pin_sha256", "protocol_sha256", "source_closure_sha256",
                "treatment_sha256", "worker_closure_sha256"):
        refuse(record.get(key) == plan[key], f"{code} {key}")


def validate_authority_record(name: str, record: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    refuse(name in REFS and isinstance(record, Mapping), "body authority role")
    if name == "source_review":
        refuse(record.get("schema") == "cloe-body-study-source-acceptance-v1"
               and record.get("status") == "passed" and record.get("read_coverage_passed") is True,
               "body source review")
        _common(record, plan, "body source review")
        _sha(record.get("independent_review_sha256"), "body source review receipt")
        return
    if name == "scoped_proof":
        refuse(record.get("schema") == "cloe-body-study-scoped-proof-acceptance-v1"
               and record.get("status") == "passed", "body scoped proof")
        _common(record, plan, "body scoped proof")
        for key in ("unit_passed", "integration_passed", "static_passed", "cleanup_proven"):
            refuse(record.get(key) is True, f"body scoped proof {key}")
        receipts = record.get("proof_receipts_sha256")
        refuse(isinstance(receipts, list) and receipts and len(set(receipts)) == len(receipts),
               "body scoped proof receipts")
        receipt_rows = cast(list[object], receipts)
        for receipt in receipt_rows:
            _sha(receipt, "body scoped proof receipt")
        return
    if name == "artifact_gate":
        refuse(record.get("schema") == "cloe-body-study-artifact-gate-v1"
               and record.get("status") == "passed" and record.get("phase") == BODY_PHASE,
               "body artifact gate")
        refuse(record.get("artifact_source_identity_sha256") == ARTIFACT_SOURCE_IDENTITY_SHA256
               and record.get("artifact_pin_document_sha256") == ARTIFACT_PIN_DOCUMENT_SHA256
               and record.get("worker_closure_sha256") == plan["worker_closure_sha256"],
               "body artifact identity")
        _sha(record.get("external_gate_sha256"), "body artifact gate receipt")
        return
    refuse(record.get("schema") == "cloe-body-study-operator-selection-v1"
           and record.get("phase") == BODY_PHASE and record.get("selection") == "A"
           and record.get("binding_adr") == "0206"
           and record.get("binding_contract") == "docs/specs/cloe-w5-body-calibration-protocol-v1.md"
           and record.get("source_review_scoped_proof_and_fresh_admission_required") is True
           and record.get("scientific_heldout_production_main_release_acceptance") is False,
           "body operator selection")
    decision = record.get("decision_packet_sha256")
    refuse(decision == plan["operator_decision_sha256"], "body operator decision")


@dataclass(frozen=True)
class PhysicalBodyAdmission:
    record: Mapping[str, Any] = field(repr=False)
    raw_sha256: str
    pins: Mapping[Path, tuple[int, str]] = field(repr=False)

    def require_current(self, charge_bytes: Callable[[int], None]) -> None:
        for path, (size, expected) in self.pins.items():
            charge_bytes(size + 1)
            current = _raw(path, maximum_bytes=size)
            refuse(len(current) == size, "body admission receipt size drift")
            refuse("sha256:" + hashlib.sha256(current).hexdigest() == expected,
                   "body admission receipt drift")


def read_parent_admission(path: Path, *, expected_sha256: str,
                          plan: Mapping[str, Any]) -> PhysicalBodyAdmission:
    checked = validate_body_plan(plan)
    raw = _raw(path)
    refuse("sha256:" + hashlib.sha256(raw).hexdigest() == expected_sha256,
           "body parent admission identity")
    try:
        record = strict_json(raw, maximum_bytes=MAX_PLAN_BYTES)
    except (UnicodeDecodeError, ValueError):
        raise ValueError("body parent admission JSON") from None
    refuse(isinstance(record, dict) and set(record) == FIELDS
           and record["schema"] == BODY_PARENT_ADMISSION_SCHEMA,
           "body parent admission closed fields")
    _common(record, checked, "body parent admission")
    refuse(record["plan_sha256"] == plan_digest(checked)
           and record["input_pin_sha256"] == BODY_INPUT_PIN_SHA256
           and record["protocol_sha256"] == BODY_PROTOCOL_SHA256
           and record["execution_authorized"] is True
           and record["model_workers"] == 2 and record["slots"] == SLOT_COUNT
           and record["held_out_authorized"] is False
           and record["publication_authorized"] is False,
           "body parent admission scope")
    pins: dict[Path, tuple[int, str]] = {path: (len(raw), expected_sha256)}
    operator_decision: object = None
    for name in REFS:
        reference = record[name]
        refuse(isinstance(reference, Mapping) and set(reference) == {"path", "sha256"}
               and type(reference["path"]) is str and Path(reference["path"]).is_absolute(),
               "body parent receipt reference")
        receipt_path = Path(reference["path"])
        receipt, receipt_raw = _decode(receipt_path)
        receipt_sha = "sha256:" + hashlib.sha256(receipt_raw).hexdigest()
        refuse(receipt_sha == reference["sha256"], "body parent receipt identity")
        validate_authority_record(name, receipt, checked)
        if name == "operator_selection":
            operator_decision = receipt["decision_packet_sha256"]
        pins[receipt_path] = (len(receipt_raw), receipt_sha)
    refuse(record["source_review"]["sha256"] == checked["review1_receipt_sha256"],
           "body parent source receipt join")
    refuse(operator_decision == checked["operator_decision_sha256"],
           "body parent operator receipt join")
    refuse(len(pins) == 5, "body parent receipt count")
    return PhysicalBodyAdmission(record, expected_sha256, pins)


PhysicalStudyAdmission = PhysicalBodyAdmission

__all__ = [
    "FIELDS", "PhysicalBodyAdmission", "PhysicalStudyAdmission", "REFS", "read_parent_admission",
    "validate_authority_record",
]
