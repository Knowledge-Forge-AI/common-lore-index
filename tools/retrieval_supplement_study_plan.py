"""Closed study plan, consume-once reservation and host-to-SDK projection.
The old core/native plan is never widened; study execution requires a fresh
reservation consumed before runner/SDK/model/query start.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
from typing import Any
from offline_qualification_files import read_regular

from retrieval_supplement_study_contracts import (
    ARTIFACT_PIN_DOCUMENT_SHA256, ARTIFACT_SOURCE_IDENTITY_SHA256, INPUT_PIN_SHA256,
    MAX_ARCHIVE_BYTES, MAX_MODEL_WORKERS,
    PROVIDER_MANIFEST_SHA256, SDK_IMAGE_SHA256, RUNTIME_ARCHIVE_BYTES,
    RUNTIME_ARCHIVE_SHA256, STUDY_ADMISSION_SCHEMA,
    STUDY_INNER_PLAN_SCHEMA, STUDY_PHASE, STUDY_PLAN_SCHEMA, STUDY_PROFILE,
    STUDY_ROLE, STUDY_SELECTOR, SLOT_COUNT, WORKER_CLOSURE_SHA256, refuse,
)
from retrieval_supplement_primitives import ARMS, canonical_bytes, digest_bytes, digest_json, strict_json
from retrieval_supplement_study_reservation import (
    consume_reservation,
    create_reservation,
    private_file,
    read_reservation,
    require_private_destination,
    settle_reservation,
    write_private_once,
)
from retrieval_supplement_study_provenance import (
    _validate_candidate_snapshot, _validate_derived,
    validate_study_candidate_snapshot, validate_study_runtime_provenance,
)
_SHA = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_TOKEN = object()
MAX_PLAN_BYTES = 2 * 1024**2
CLEANUP_STATES = frozenset({"proven", "unknown", "failed"})
COMMON_PLAN_FIELDS = frozenset({
    "schema", "phase", "role", "profile", "selector", "attempt_id",
    "input_pin_sha256", "artifact_source_identity_sha256", "artifact_pin_document_sha256",
    "worker_closure_sha256", "evaluator_closure_sha256", "sdk_image", "provider_sha256",
    "candidate_head", "candidate_snapshot", "archive", "declaration",
    "schedule_sha256", "byte_forecast_sha256", "derived", "review1_receipt_sha256",
    "operator_decision_sha256",
})
PLAN_FIELDS = COMMON_PLAN_FIELDS | {"allocation_path", "reservation_path"}
INNER_PLAN_FIELDS = COMMON_PLAN_FIELDS | {"host_plan_sha256", "study_admission"}
ADMISSION_FIELDS = frozenset({
    "schema", "plan_sha256", "attempt_id", "reservation_sha256", "evaluator_closure_sha256",
})
def _digest(value: object, label: str) -> str:
    refuse(type(value) is str and _SHA.match(str(value)) is not None and value != "sha256:" + "0" * 64,
           f"study_plan:{label}")
    return str(value)
def _validate_common_plan(record: dict[str, Any], *, inner: bool) -> None:
    refuse(record["schema"] == (STUDY_INNER_PLAN_SCHEMA if inner else STUDY_PLAN_SCHEMA),
           "study_plan:schema")
    refuse(record["phase"] == STUDY_PHASE, "study_plan:phase")
    refuse(record["role"] == STUDY_ROLE, "study_plan:role_must_be_study")
    refuse(record["profile"] == STUDY_PROFILE, "study_plan:profile")
    refuse(record["selector"] == STUDY_SELECTOR, "study_plan:selector")
    refuse(type(record["attempt_id"]) is str
           and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,76}", record["attempt_id"]) is not None,
           "study_plan:attempt")
    refuse(record["sdk_image"] == SDK_IMAGE_SHA256, "study_plan:sdk_identity")
    refuse(record["provider_sha256"] == PROVIDER_MANIFEST_SHA256.removeprefix("sha256:"),
           "study_plan:provider_identity")
    refuse(_digest(record["input_pin_sha256"], "input_pin") == INPUT_PIN_SHA256,
           "study_plan:input_pin_mismatch")
    identity = _digest(record["artifact_source_identity_sha256"], "artifact_identity")
    refuse(identity == ARTIFACT_SOURCE_IDENTITY_SHA256, "study_plan:artifact_identity_mismatch")
    pin_doc = _digest(record["artifact_pin_document_sha256"], "pin_document")
    refuse(pin_doc == ARTIFACT_PIN_DOCUMENT_SHA256, "study_plan:pin_document_mismatch")
    refuse(identity != pin_doc, "study_plan:hash_domain_confusion")
    refuse(_digest(record["worker_closure_sha256"], "worker_closure") == WORKER_CLOSURE_SHA256,
           "study_plan:worker_closure_mismatch")
    for key, label in (("evaluator_closure_sha256", "evaluator_closure"),
                       ("schedule_sha256", "schedule"), ("byte_forecast_sha256", "byte_forecast"),
                       ("review1_receipt_sha256", "review_receipt"),
                       ("operator_decision_sha256", "operator_decision")):
        _digest(record[key], label)
    authority = (record["review1_receipt_sha256"], record["operator_decision_sha256"])
    refuse(len(set(authority)) == 2 and record["evaluator_closure_sha256"] not in authority,
           "study_plan:authority_alias")
    refuse(not set(authority) & {
        INPUT_PIN_SHA256, ARTIFACT_SOURCE_IDENTITY_SHA256,
        ARTIFACT_PIN_DOCUMENT_SHA256, WORKER_CLOSURE_SHA256,
    }, "study_plan:authority_domain_collision")
    refuse(record["evaluator_closure_sha256"] not in {
        INPUT_PIN_SHA256, ARTIFACT_SOURCE_IDENTITY_SHA256,
        ARTIFACT_PIN_DOCUMENT_SHA256, WORKER_CLOSURE_SHA256,
    }, "study_plan:closure_domain_collision")
    _validate_candidate_snapshot(record)
    declaration = record["declaration"]
    refuse(isinstance(declaration, Mapping) and declaration.get("execution_authorized") is False
           and declaration.get("queries_executed") is False
           and declaration.get("model_workers_started") == 0, "study_plan:declaration_must_be_inert")
    manifest = declaration.get("slot_manifest")
    refuse(isinstance(manifest, list) and len(manifest) == SLOT_COUNT and all(
        isinstance(row, Mapping) and set(row) == {"task_id", "arm", "repetition", "generation"}
        for row in manifest), "study_plan:declaration_slot_manifest")
    refuse(len({(row["task_id"], row["arm"], row["repetition"], row["generation"]) for row in manifest}) == SLOT_COUNT
           and all(type(row["task_id"]) is str and bool(row["task_id"])
               and row["arm"] in ARMS and type(row["repetition"]) is int and row["repetition"] in (1, 2, 3)
               and type(row["generation"]) is int and row["generation"] == 1 for row in manifest)
           and declaration.get("slot_set_sha256") == digest_json(manifest), "study_plan:declaration_slot_set")
    task_ids = list(dict.fromkeys(row["task_id"] for row in manifest))
    refuse(len(task_ids) == 24 and manifest == [
        {"task_id": task, "arm": arm, "repetition": repetition, "generation": 1}
        for task in task_ids for arm in ARMS for repetition in (1, 2, 3)], "study_plan:declaration_slot_order")
    archive = record["archive"]
    refuse(isinstance(archive, Mapping) and type(archive.get("bytes")) is int
           and 0 < int(archive["bytes"]) <= MAX_ARCHIVE_BYTES
           and archive["bytes"] == RUNTIME_ARCHIVE_BYTES
           and type(archive.get("sha256")) is str
           and archive["sha256"] == RUNTIME_ARCHIVE_SHA256.removeprefix("sha256:"),
           "study_plan:archive_identity")
    if inner:
        refuse(set(archive) == {"bytes", "sha256"}, "study_plan:inner_archive_private_path_forbidden")
    else:
        refuse(set(archive) == {"path", "bytes", "sha256"}
               and type(archive.get("path")) is str and Path(str(archive["path"])).is_absolute(),
               "study_plan:archive_path")
    _validate_derived(record)
def validate_study_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the closed host study plan shape; old roles/schemas refuse."""
    refuse(isinstance(plan, Mapping), "study_plan:object_required")
    record = dict(plan)
    refuse(set(record) == PLAN_FIELDS, "study_plan:closed_fields")
    _validate_common_plan(record, inner=False)
    for key in ("allocation_path", "reservation_path"):
        refuse(type(record[key]) is str and Path(str(record[key])).is_absolute(), f"study_plan:{key}")
    return record
def is_inner_study_plan(plan: Mapping[str, Any]) -> bool:
    return (isinstance(plan, Mapping) and plan.get("schema") == STUDY_INNER_PLAN_SCHEMA
            and "study_admission" in plan)
def _validate_admission_mapping(
    value: object, *, expected_plan_sha256: str, attempt_id: str, evaluator_closure_sha256: str,
) -> dict[str, str]:
    refuse(isinstance(value, Mapping) and set(value) == ADMISSION_FIELDS, "study_admission:fields")
    assert isinstance(value, Mapping)
    mapping = dict(value)
    refuse(mapping.get("schema") == STUDY_ADMISSION_SCHEMA, "study_admission:schema")
    refuse(_digest(mapping.get("plan_sha256"), "admission_plan") == expected_plan_sha256,
           "study_admission:plan_mismatch")
    refuse(mapping.get("attempt_id") == attempt_id, "study_admission:attempt_mismatch")
    _digest(mapping.get("reservation_sha256"), "admission_reservation")
    refuse(_digest(mapping.get("evaluator_closure_sha256"), "admission_closure")
           == evaluator_closure_sha256, "study_admission:closure_mismatch")
    return {str(key): str(item) for key, item in mapping.items()}
def validate_inner_study_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the projected inner SDK study plan (no private host paths)."""
    refuse(isinstance(plan, Mapping), "study_plan:object_required")
    record = dict(plan)
    refuse(set(record) == INNER_PLAN_FIELDS, "study_plan:inner_closed_fields")
    _validate_common_plan(record, inner=True)
    host_sha = _digest(record["host_plan_sha256"], "host_plan_sha256")
    record["study_admission"] = _validate_admission_mapping(
        record["study_admission"], expected_plan_sha256=host_sha,
        attempt_id=str(record["attempt_id"]),
        evaluator_closure_sha256=str(record["evaluator_closure_sha256"]))
    return record


def read_inner_study_plan(path: Path) -> dict[str, Any]:
    """Read the privately delivered plan within its declared two-MiB class."""
    private_file(path)
    return validate_inner_study_plan(strict_json(read_regular(path, MAX_PLAN_BYTES), maximum_bytes=MAX_PLAN_BYTES))
def plan_digest(plan: Mapping[str, Any]) -> str:
    return digest_bytes(canonical_bytes(dict(plan)))
@dataclass(frozen=True)
class OwnershipEvidence:
    """Exact owned-resource settlement; ``unknown`` blocks admission and reuse."""

    sdk_container: str
    pg_lifecycle: str
    model_containers: tuple[str, ...]
    cleanup: str  # "proven" | "unknown" | "failed"

    def require_proven(self) -> None:
        refuse(self.cleanup == "proven", "study_ownership:cleanup_not_proven")
        refuse(bool(self.sdk_container) and bool(self.pg_lifecycle), "study_ownership:identity")
        refuse(len(self.model_containers) <= MAX_MODEL_WORKERS, "study_ownership:worker_count")
@dataclass(frozen=True)
class StudyAdmission:
    """Fresh parent-issued admission; constructible only via trusted factories."""

    plan_sha256: str
    attempt_id: str
    reservation_sha256: str
    evaluator_closure_sha256: str
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        refuse(self._token is _TOKEN, "study_admission:forged")

    def to_dict(self) -> dict[str, str]:
        return {"schema": STUDY_ADMISSION_SCHEMA, "plan_sha256": self.plan_sha256,
                "attempt_id": self.attempt_id, "reservation_sha256": self.reservation_sha256,
                "evaluator_closure_sha256": self.evaluator_closure_sha256}
def admission_file_for_plan(plan_path: Path) -> Path:
    return plan_path.with_suffix(".admission.json")

def write_admission_file(path: Path, admission: StudyAdmission) -> str:
    refuse(type(admission) is StudyAdmission, "study_admission:required")
    return write_private_once(path, admission.to_dict())

def read_admission_file(path: Path) -> dict[str, Any]:
    private_file(path)
    refuse(path.stat().st_size <= MAX_PLAN_BYTES, "study_admission:cap")
    value = strict_json(path.read_bytes())
    refuse(isinstance(value, dict) and value.get("schema") == STUDY_ADMISSION_SCHEMA,
           "study_admission:schema")
    return dict(value)

def issue_admission(
    plan: Mapping[str, Any], *, reservation_path: Path,
    evaluator_closure_sha256: str, admission_path: Path | None = None,
) -> StudyAdmission:
    """Consume the fresh reservation on the host before any runner/SDK launch."""
    record = validate_study_plan(plan)
    refuse(Path(str(record["reservation_path"])) == reservation_path,
           "study_admission:reservation_path_mismatch")
    refuse(_digest(evaluator_closure_sha256, "evaluator_closure")
           == record["evaluator_closure_sha256"], "study_admission:closure_mismatch")
    if admission_path is not None:
        refuse(admission_path != reservation_path, "study_admission:path_alias")
        require_private_destination(admission_path)
    digest = plan_digest(record)
    consumed = consume_reservation(
        reservation_path, plan_sha256=digest, attempt_id=str(record["attempt_id"]),
    )
    admission = StudyAdmission(digest, str(record["attempt_id"]),
                               digest_bytes(canonical_bytes(consumed) + b"\n"),
                               evaluator_closure_sha256, _token=_TOKEN)
    if admission_path is not None:
        write_admission_file(admission_path, admission)
    return admission

def require_reservation_admitted_for_runner(plan: Mapping[str, Any], *, plan_path: Path) -> dict[str, str]:
    """Runner host gate: reservation must already be consumed & in-progress with matching admission."""
    record = validate_study_plan(plan)
    digest = plan_digest(record)
    reservation_path = Path(str(record["reservation_path"]))
    refuse(reservation_path.parent == plan_path.parent, "study_reservation:nonlocal")
    reservation = read_reservation(reservation_path)
    refuse(reservation.get("plan_sha256") == digest, "study_reservation:stale_plan")
    refuse(reservation.get("attempt_id") == record["attempt_id"], "study_reservation:attempt_mismatch")
    refuse(reservation.get("consumed") is True and reservation.get("cleanup") == "in_progress",
           "study_reservation:not_admitted_for_runner")
    validated = _validate_admission_mapping(
        read_admission_file(admission_file_for_plan(plan_path)),
        expected_plan_sha256=digest, attempt_id=str(record["attempt_id"]),
        evaluator_closure_sha256=str(record["evaluator_closure_sha256"]))
    refuse(validated["reservation_sha256"] == digest_bytes(canonical_bytes(reservation) + b"\n"),
           "study_admission:reservation_drift")
    return validated

def project_inner_study_plan(
    plan: Mapping[str, Any], *, admission: StudyAdmission | Mapping[str, Any],
) -> dict[str, Any]:
    """Strip host private paths and embed verified admission for ``/sandbox-scratch/pgvector/plan.json``."""
    record = validate_study_plan(plan)
    digest = plan_digest(record)
    admission_map = admission.to_dict() if type(admission) is StudyAdmission else admission
    validated = _validate_admission_mapping(
        admission_map, expected_plan_sha256=digest, attempt_id=str(record["attempt_id"]),
        evaluator_closure_sha256=str(record["evaluator_closure_sha256"]))
    inner: dict[str, Any] = {key: record[key] for key in COMMON_PLAN_FIELDS if key != "archive"}
    inner["schema"] = STUDY_INNER_PLAN_SCHEMA
    inner["archive"] = {"bytes": int(record["archive"]["bytes"]), "sha256": str(record["archive"]["sha256"])}
    inner["host_plan_sha256"] = digest
    inner["study_admission"] = validated
    return validate_inner_study_plan(inner)

def admission_from_inner_plan(plan: Mapping[str, Any]) -> StudyAdmission:
    """Rehydrate a verified :class:`StudyAdmission` inside the authenticated study SDK."""
    adm = validate_inner_study_plan(plan)["study_admission"]
    return StudyAdmission(adm["plan_sha256"], adm["attempt_id"], adm["reservation_sha256"],
                          adm["evaluator_closure_sha256"], _token=_TOKEN)

def require_admission_current(admission: StudyAdmission, plan: Mapping[str, Any]) -> None:
    """Re-bind admission to the exact current host or inner plan before every hop."""
    refuse(type(admission) is StudyAdmission, "study_admission:required")
    if is_inner_study_plan(plan):
        inner = validate_inner_study_plan(plan)
        refuse(admission.plan_sha256 == inner["host_plan_sha256"], "study_admission:lost_authority")
        refuse(admission.attempt_id == inner["attempt_id"], "study_admission:attempt_drift")
        refuse(admission.to_dict() == inner["study_admission"], "study_admission:inner_drift")
        return
    record = validate_study_plan(plan)
    refuse(admission.plan_sha256 == plan_digest(record), "study_admission:lost_authority")
    refuse(admission.attempt_id == record["attempt_id"], "study_admission:attempt_drift")
    refuse(hashlib.sha256(canonical_bytes(record)).hexdigest() == admission.plan_sha256[7:],
           "study_admission:digest_drift")

__all__ = [
    "ADMISSION_FIELDS", "CLEANUP_STATES", "COMMON_PLAN_FIELDS", "INNER_PLAN_FIELDS",
    "MAX_PLAN_BYTES", "OwnershipEvidence", "PLAN_FIELDS", "StudyAdmission",
    "admission_file_for_plan", "admission_from_inner_plan", "consume_reservation",
    "create_reservation", "is_inner_study_plan", "issue_admission", "plan_digest",
    "project_inner_study_plan", "read_admission_file", "read_reservation",
    "require_admission_current", "require_reservation_admitted_for_runner",
    "settle_reservation", "validate_inner_study_plan", "validate_study_plan",
    "validate_study_candidate_snapshot", "validate_study_runtime_provenance",
    "write_admission_file",
]
