from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, cast
from retrieval_body_study_closure import BODY_SOURCE_MEMBERS, BODY_TREATMENT_MEMBERS
from retrieval_body_study_contracts import (
    BODY_ADMISSION_SCHEMA, BODY_ARMS, BODY_BASE_CONFIG_SHA256, BODY_CONTROLLER_SECONDS,
    BODY_INPUT_PIN_SHA256, BODY_INNER_PLAN_SCHEMA, BODY_OPERATOR_DECISION_SHA256,
    BODY_PHASE, BODY_PLAN_SCHEMA, BODY_PROFILE, BODY_PROTOCOL_SHA256, BODY_PROVIDER_SHA256,
    BODY_REPRESENTATIONS, BODY_RUNTIME_ARCHIVE_BYTES, BODY_RUNTIME_ARCHIVE_SHA256,
    BODY_RUNTIME_CONFIG_SHA256, BODY_SDK_IMAGE_SHA256, BODY_SDK_RECIPE_SHA256,
    BODY_SDK_SECONDS, BODY_SELECTOR, MAX_ARCHIVE_BYTES, MAX_PLAN_BYTES, SLOT_COUNT,
    TASK_COUNT, V1_QUERIES, V2_QUERIES, REGISTERED_SNAPSHOT_READ_BYTES, refuse, strict_json,
)
from retrieval_body_study_reservation import (
    consume_reservation, private_file, read_reservation, reservation_digest, write_private_once,
)
from retrieval_body_study_files import read_member
_SHA = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_HEX = re.compile(r"\A[0-9a-f]{64}\Z"); _HEAD = re.compile(r"\A[0-9a-f]{40}\Z")
_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,76}\Z")
COMMON_PLAN_FIELDS = frozenset({
    "schema", "phase", "role", "profile", "selector", "attempt_id",
    "operator_selection", "input_pin_sha256", "protocol_sha256",
    "source_closure_sha256", "treatment_sha256", "worker_closure_sha256",
    "evaluator_closure_sha256",
    "artifact_source_identity_sha256", "artifact_pin_document_sha256",
    "sdk_image", "sdk_recipe_sha256", "provider_sha256", "candidate_head",
    "candidate_snapshot", "candidate_snapshot_sha256", "archive", "declaration",
    "schedule_sha256", "byte_forecast_sha256", "derived", "review1_receipt_sha256",
    "operator_decision_sha256", "sdk_seconds", "controller_seconds",
    "source_members", "treatment_members",
})
PLAN_FIELDS = COMMON_PLAN_FIELDS | {"allocation_path", "reservation_path"}; INNER_PLAN_FIELDS = COMMON_PLAN_FIELDS | {"host_plan_sha256", "body_admission", "inner"}
ADMISSION_FIELDS = frozenset({
    "schema", "plan_sha256", "attempt_id", "reservation_sha256",
    "source_closure_sha256", "treatment_sha256", "protocol_sha256",
    "operator_decision_sha256",
})
ARTIFACT_SOURCE_IDENTITY_SHA256 = "sha256:033cf41ffc5ca108b6e26638573554bac64b84be744903809904ef3364722180"
ARTIFACT_PIN_DOCUMENT_SHA256 = "sha256:f0237e38018f3524b87f85f7fff2bef41eda125b8f954d575aaee233541896b6"
def _digest(value: object, code: str) -> str:
    refuse(type(value) is str and _SHA.fullmatch(value) is not None and value != "sha256:" + "0" * 64, code); return str(value)
def _hex(value: object, code: str) -> str:
    refuse(type(value) is str and _HEX.fullmatch(value) is not None, code); return str(value)
def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")
def plan_digest(plan: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(dict(plan))).hexdigest()
def _safe_relative(value: object, code: str = "body plan relative path") -> str:
    refuse(type(value) is str and bool(value) and "\x00" not in value
           and "\\" not in value and not value.startswith("/"), code)
    parts = PurePosixPath(str(value)).parts
    refuse(all(part not in {"", ".", "..", "__pycache__"} for part in parts)
           and PurePosixPath(*parts).as_posix() == value, code)
    return str(value)
def _validate_symlink(name: str, target: object) -> None:
    refuse(type(target) is str and target and not target.startswith("/")
           and "\x00" not in target and "\\" not in target
           and PurePosixPath(target).as_posix() == target, "body plan snapshot symlink")
    depth = len(PurePosixPath(name).parent.parts)
    for part in PurePosixPath(str(target)).parts:
        depth += -1 if part == ".." else 1
        refuse(depth >= 0, "body plan snapshot symlink escape")
def _validate_snapshot(snapshot: object) -> dict[str, Any]:
    refuse(isinstance(snapshot, Mapping) and bool(snapshot), "body plan candidate snapshot")
    result: dict[str, Any] = {}
    for name, row in cast(Mapping[object, Mapping[str, Any]], snapshot).items():
        relative = _safe_relative(name)
        refuse(isinstance(row, Mapping), "body plan snapshot row")
        if set(row) == {"sha256", "mode"}:
            _hex(row["sha256"], "body plan snapshot hash")
            refuse(type(row["mode"]) is int and not isinstance(row["mode"], bool)
                   and 0 <= row["mode"] <= 0o777, "body plan snapshot mode")
        elif set(row) == {"symlink"}:
            _validate_symlink(relative, row["symlink"])
        else:
            raise ValueError("body plan snapshot row")
        result[relative] = dict(row)
    return result
def _validate_derived(derived: object) -> None:
    refuse(isinstance(derived, Mapping), "body plan derived"); derived = cast(Mapping[str, Any], derived)
    required = {"base_config", "runtime_config", "runtime_config_document",
                "base_config_document", "base_config_raw", "base_layers",
                "runtime_layers", "byte_forecast_binding"}
    refuse(required <= set(derived), "body plan derived fields")
    refuse(derived["base_config"] == BODY_BASE_CONFIG_SHA256
           and derived["runtime_config"] == BODY_RUNTIME_CONFIG_SHA256,
           "body plan runtime identity")
    for key in ("runtime_config_document", "base_config_document"):
        refuse(isinstance(derived[key], Mapping) and bool(derived[key]),
               f"body plan {key}")
    refuse(type(derived["base_config_raw"]) is str and bool(derived["base_config_raw"]),
           "body plan base config raw")
    for key in ("base_layers", "runtime_layers"):
        layers = derived[key]
        refuse(isinstance(layers, list) and layers and all(isinstance(row, Mapping)
               and type(row.get("sha256")) is str and _HEX.fullmatch(row["sha256"]) is not None
               and type(row.get("bytes")) is int and row["bytes"] >= 0 for row in layers),
               f"body plan {key}")
    refuse(len(derived["runtime_layers"]) == len(derived["base_layers"]) + 1,
           "body plan runtime layer correspondence")
    binding = derived["byte_forecast_binding"]
    refuse(isinstance(binding, Mapping)
           and set(binding) == {"source_bytes", "treatment_bytes", "worker_bytes",
                                "registered_input_bytes", "request_bytes", "candidate_bytes"}
           and type(binding["source_bytes"]) is int and binding["source_bytes"] >= 0
           and type(binding["treatment_bytes"]) is int and binding["treatment_bytes"] >= 0
           and type(binding["worker_bytes"]) is int and binding["worker_bytes"] >= 0
           and type(binding["registered_input_bytes"]) is int
           and binding["registered_input_bytes"] >= REGISTERED_SNAPSHOT_READ_BYTES
           and type(binding["candidate_bytes"]) is int and binding["candidate_bytes"] >= 0
           and isinstance(binding["request_bytes"], list)
           and len(binding["request_bytes"]) == 2
           and all(type(item) is int and item >= 0 for item in binding["request_bytes"]),
           "body plan byte binding")
def _validate_query_manifest(value: Mapping[str, Any]) -> None:
    manifest = value.get("query_manifest")
    refuse(isinstance(manifest, Mapping) and set(manifest) == set(BODY_REPRESENTATIONS),
           "body plan query manifest"); manifest = cast(Mapping[str, Any], manifest)
    for representation, count in zip(BODY_REPRESENTATIONS, (V1_QUERIES, V2_QUERIES)):
        rows = manifest[representation]
        refuse(isinstance(rows, list) and len(rows) == count, "body plan query rows")
        ids = [row.get("record_id") for row in rows if isinstance(row, Mapping)]
        refuse(len(ids) == count and all(type(item) is str for item in ids) and len(set(ids)) == count
               and all(isinstance(row, Mapping) and set(row) == {"record_id", "text_sha256"}
                       and type(row["record_id"]) is str and row["record_id"].startswith("query:")
                       for row in rows) and rows == sorted(rows, key=lambda row: row["record_id"]),
               "body plan query rows")
        for row in rows:
            _digest(row["text_sha256"], "body plan query text")
    expected = "sha256:" + hashlib.sha256(_canonical({
        "schema": "cloe-w5-body-query-manifest-v1", "rows": manifest})).hexdigest()
    refuse(value.get("query_manifest_sha256") == expected, "body plan query commitment")
def _validate_declaration(value: object) -> None:
    refuse(isinstance(value, Mapping), "body plan declaration"); value = cast(Mapping[str, Any], value)
    refuse(value.get("execution_authorized") is False
           and value.get("queries_executed") is False
           and value.get("model_workers_started") == 0,
           "body plan declaration must be inert")
    manifest = value.get("slot_manifest")
    refuse(isinstance(manifest, list) and len(manifest) == SLOT_COUNT,
           "body plan slot manifest")
    assert isinstance(manifest, list)
    expected = []
    tasks: list[str] = []
    for row in manifest:
        refuse(isinstance(row, Mapping)
               and set(row) == {"task_id", "arm", "repetition", "generation"},
               "body plan slot row")
        task = row["task_id"]
        refuse(type(task) is str and bool(_ID.fullmatch(task)), "body plan slot task")
        if task not in tasks:
            tasks.append(task)
        refuse(type(row["arm"]) is str and row["arm"] in BODY_ARMS and type(row["repetition"]) is int
               and row["repetition"] in (1, 2, 3) and type(row["generation"]) is int
               and row["generation"] == 1, "body plan slot values")
        expected.append((task, row["arm"], row["repetition"], row["generation"]))
    refuse(len(set(expected)) == SLOT_COUNT and len(tasks) == TASK_COUNT,
           "body plan slot uniqueness")
    _validate_query_manifest(value)
    canonical = [(task, arm, repetition, 1) for task in tasks for arm in BODY_ARMS
                 for repetition in (1, 2, 3)]
    refuse(expected == canonical and value.get("slot_set_sha256") ==
           "sha256:" + hashlib.sha256(_canonical(manifest)).hexdigest(),
           "body plan slot commitment")
def _validate_common(plan: Mapping[str, Any], *, inner: bool) -> dict[str, Any]:
    refuse(len(_canonical(plan)) + 1 <= MAX_PLAN_BYTES, "body plan serialized byte cap")
    expected_schema = BODY_INNER_PLAN_SCHEMA if inner else BODY_PLAN_SCHEMA
    refuse(set(plan) == (INNER_PLAN_FIELDS if inner else PLAN_FIELDS),
           "body plan closed fields")
    refuse(plan["schema"] == expected_schema and plan["phase"] == BODY_PHASE
           and plan["role"] == "body-study" and plan["profile"] == BODY_PROFILE
           and plan["selector"] == BODY_SELECTOR and plan["operator_selection"] == "ADR0206 Route A",
           "body plan identity")
    refuse(type(plan["attempt_id"]) is str and _ID.fullmatch(plan["attempt_id"]) is not None,
           "body plan attempt")
    refuse(plan["input_pin_sha256"] == BODY_INPUT_PIN_SHA256
           and plan["protocol_sha256"] == BODY_PROTOCOL_SHA256,
           "body plan protocol/input")
    for key in ("source_closure_sha256", "treatment_sha256", "worker_closure_sha256",
                "evaluator_closure_sha256",
                "artifact_source_identity_sha256", "artifact_pin_document_sha256",
                "sdk_image", "review1_receipt_sha256", "operator_decision_sha256",
                "byte_forecast_sha256"):
        _digest(plan[key], f"body plan {key}")
    refuse(plan["evaluator_closure_sha256"] == plan["source_closure_sha256"],
           "body plan evaluator/source bridge")
    refuse(plan["sdk_image"] == BODY_SDK_IMAGE_SHA256
           and plan["sdk_recipe_sha256"] == BODY_SDK_RECIPE_SHA256
           and plan["provider_sha256"] == BODY_PROVIDER_SHA256
           and plan["artifact_source_identity_sha256"] == ARTIFACT_SOURCE_IDENTITY_SHA256
           and plan["artifact_pin_document_sha256"] == ARTIFACT_PIN_DOCUMENT_SHA256
           and plan["operator_decision_sha256"] == BODY_OPERATOR_DECISION_SHA256,
           "body plan retained asset identity")
    refuse(plan["sdk_seconds"] == BODY_SDK_SECONDS
           and plan["controller_seconds"] == BODY_CONTROLLER_SECONDS,
           "body plan lifetime")
    archive = plan["archive"]
    refuse(isinstance(archive, Mapping) and type(archive.get("bytes")) is int
           and archive["bytes"] == BODY_RUNTIME_ARCHIVE_BYTES
           and archive.get("sha256") == BODY_RUNTIME_ARCHIVE_SHA256.removeprefix("sha256:")
           and archive["bytes"] <= MAX_ARCHIVE_BYTES, "body plan archive")
    if inner:
        refuse(set(archive) == {"bytes", "sha256"}, "body inner private archive path")
    else:
        refuse(set(archive) == {"path", "bytes", "sha256"}
               and type(archive.get("path")) is str and Path(archive["path"]).is_absolute(),
               "body plan archive path")
    _digest(plan["candidate_snapshot_sha256"], "body plan snapshot commitment")
    snapshot = _validate_snapshot(plan["candidate_snapshot"])
    refuse(plan["candidate_snapshot_sha256"] ==
           "sha256:" + hashlib.sha256(_canonical(snapshot)).hexdigest(),
           "body plan snapshot commitment")
    refuse(type(plan["candidate_head"]) is str and _HEAD.fullmatch(plan["candidate_head"]) is not None,
           "body plan candidate head")
    for field_name, defaults in (("source_members", BODY_SOURCE_MEMBERS),
                                 ("treatment_members", BODY_TREATMENT_MEMBERS)):
        members = plan[field_name]
        refuse(isinstance(members, list)
               and all(type(item) is str for item in members)
               and len(members) == len(set(members)) and all(_safe_relative(item) for item in members),
               f"body plan {field_name}")
        refuse(tuple(members) == defaults, f"body plan {field_name} scope")
    _validate_declaration(plan["declaration"])
    _validate_derived(plan["derived"])
    return dict(plan)
def validate_body_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    refuse(isinstance(plan, Mapping) and plan.get("schema") == BODY_PLAN_SCHEMA,
           "body plan schema")
    record = _validate_common(plan, inner=False)
    for key in ("allocation_path", "reservation_path"):
        refuse(type(record[key]) is str and Path(record[key]).is_absolute(),
               f"body plan {key}")
    return record
def is_body_plan(plan: object) -> bool:
    return isinstance(plan, Mapping) and plan.get("schema") in {BODY_PLAN_SCHEMA, BODY_INNER_PLAN_SCHEMA}
def is_inner_body_plan(plan: object) -> bool:
    return isinstance(plan, Mapping) and plan.get("schema") == BODY_INNER_PLAN_SCHEMA
def _validate_admission(value: object, *, plan_sha256: str, attempt_id: str,
                       source: str, treatment: str, protocol: str,
                       operator: str) -> dict[str, str]:
    refuse(isinstance(value, Mapping) and set(value) == ADMISSION_FIELDS,
           "body admission fields"); value = cast(Mapping[str, Any], value)
    result = dict(value)
    refuse(result["schema"] == BODY_ADMISSION_SCHEMA and result["plan_sha256"] == plan_sha256
           and result["attempt_id"] == attempt_id and result["source_closure_sha256"] == source
           and result["treatment_sha256"] == treatment and result["protocol_sha256"] == protocol
           and result["operator_decision_sha256"] == operator, "body admission join")
    _digest(result["reservation_sha256"], "body admission reservation")
    return {str(key): str(item) for key, item in result.items()}
@dataclass(frozen=True)
class BodyStudyAdmission:
    plan_sha256: str
    attempt_id: str
    reservation_sha256: str
    source_closure_sha256: str
    treatment_sha256: str
    protocol_sha256: str
    operator_decision_sha256: str
    _token: object = field(default=None, repr=False, compare=False)
    def __post_init__(self) -> None:
        refuse(self._token is _ADMISSION_TOKEN, "body admission forged")
    def to_dict(self) -> dict[str, str]:
        return {"schema": BODY_ADMISSION_SCHEMA, "plan_sha256": self.plan_sha256,
                "attempt_id": self.attempt_id, "reservation_sha256": self.reservation_sha256,
                "source_closure_sha256": self.source_closure_sha256, "treatment_sha256": self.treatment_sha256,
                "protocol_sha256": self.protocol_sha256, "operator_decision_sha256": self.operator_decision_sha256}
_ADMISSION_TOKEN = object()
def admission_file_for_plan(plan_path: Path) -> Path:
    return plan_path.with_suffix(".admission.json")
def write_admission_file(path: Path, admission: BodyStudyAdmission) -> str:
    refuse(type(admission) is BodyStudyAdmission, "body admission required")
    write_private_once(path, admission.to_dict())
    return reservation_digest(admission.to_dict())
def read_admission_file(path: Path) -> dict[str, Any]:
    private_file(path)
    raw = read_member(path.parent, path.name, max_bytes=MAX_PLAN_BYTES)
    refuse(0 < len(raw) <= MAX_PLAN_BYTES, "body admission bounds")
    value = strict_json(raw, maximum_bytes=MAX_PLAN_BYTES)
    refuse(isinstance(value, dict), "body admission JSON")
    return dict(value)
def issue_admission(
    plan: Mapping[str, Any], *, reservation_path: Path,
    source_closure_sha256: str | None = None, treatment_sha256: str | None = None,
    admission_path: Path | None = None, operator_decision_sha256: str | None = None,
) -> BodyStudyAdmission:
    record = validate_body_plan(plan)
    refuse(Path(record["reservation_path"]) == reservation_path,
           "body admission reservation path")
    source = source_closure_sha256 or record["source_closure_sha256"]
    treatment = treatment_sha256 or record["treatment_sha256"]
    operator = operator_decision_sha256 or record["operator_decision_sha256"]
    refuse(source == record["source_closure_sha256"] and treatment == record["treatment_sha256"]
           and operator == record["operator_decision_sha256"], "body admission closure join")
    consumed = consume_reservation(reservation_path, plan_sha256=plan_digest(record),
                                   attempt_id=record["attempt_id"])
    admission = BodyStudyAdmission(
        plan_digest(record), record["attempt_id"], reservation_digest(consumed), source,
        treatment, record["protocol_sha256"], operator, _token=_ADMISSION_TOKEN)
    refuse(admission_path is not None, "body admission destination required")
    target = cast(Path, admission_path)
    write_admission_file(target, admission)
    return admission
def require_reservation_admitted_for_runner(plan: Mapping[str, Any], *, plan_path: Path) -> dict[str, str]:
    record = validate_body_plan(plan)
    digest = plan_digest(record)
    reservation_path = Path(record["reservation_path"])
    refuse(reservation_path.parent == plan_path.parent, "body reservation nonlocal")
    reservation = read_reservation(reservation_path)
    refuse(reservation["plan_sha256"] == digest and reservation["attempt_id"] == record["attempt_id"]
           and reservation["source_closure_sha256"] == record["source_closure_sha256"]
           and reservation["treatment_sha256"] == record["treatment_sha256"]
           and reservation["worker_closure_sha256"] == record["worker_closure_sha256"]
           and reservation["protocol_sha256"] == record["protocol_sha256"]
           and reservation["operator_decision_sha256"] == record["operator_decision_sha256"]
           and reservation["consumed"] is True and reservation["cleanup"] == "in_progress"
           and reservation.get("status") == "admitted",
           "body reservation not admitted")
    admission = _validate_admission(read_admission_file(admission_file_for_plan(plan_path)),
        plan_sha256=digest, attempt_id=record["attempt_id"],
        source=record["source_closure_sha256"], treatment=record["treatment_sha256"],
        protocol=record["protocol_sha256"], operator=record["operator_decision_sha256"])
    refuse(admission["reservation_sha256"] == reservation_digest(reservation),
           "body admission reservation drift")
    return admission
def project_inner_body_plan(plan: Mapping[str, Any], *, admission: BodyStudyAdmission | Mapping[str, Any]) -> dict[str, Any]:
    record = validate_body_plan(plan)
    digest = plan_digest(record)
    mapping = admission.to_dict() if isinstance(admission, BodyStudyAdmission) else admission
    validated = _validate_admission(mapping, plan_sha256=digest, attempt_id=record["attempt_id"],
        source=record["source_closure_sha256"], treatment=record["treatment_sha256"],
        protocol=record["protocol_sha256"], operator=record["operator_decision_sha256"])
    inner = {key: record[key] for key in COMMON_PLAN_FIELDS}
    inner["schema"] = BODY_INNER_PLAN_SCHEMA
    inner["archive"] = {"bytes": record["archive"]["bytes"], "sha256": record["archive"]["sha256"]}
    inner["host_plan_sha256"] = digest
    inner["body_admission"] = validated
    inner["inner"] = {"report_dir": "/sandbox-report/report/body-study",
                       "scratch_dir": "/sandbox-scratch/pgvector"}
    return validate_inner_body_plan(inner)
def validate_inner_body_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    refuse(isinstance(plan, Mapping) and plan.get("schema") == BODY_INNER_PLAN_SCHEMA,
           "body inner schema")
    record = _validate_common(plan, inner=True)
    _digest(record["host_plan_sha256"], "body inner host plan")
    refuse(record["inner"] == {"report_dir": "/sandbox-report/report/body-study",
                                "scratch_dir": "/sandbox-scratch/pgvector"}, "body inner paths")
    _validate_admission(record["body_admission"], plan_sha256=record["host_plan_sha256"],
        attempt_id=record["attempt_id"], source=record["source_closure_sha256"],
        treatment=record["treatment_sha256"], protocol=record["protocol_sha256"],
        operator=record["operator_decision_sha256"])
    return record
def read_inner_body_plan(path: Path) -> dict[str, Any]:
    private_file(path)
    raw = read_member(path.parent, path.name, max_bytes=MAX_PLAN_BYTES)
    refuse(0 < len(raw) <= MAX_PLAN_BYTES, "body inner plan bounds")
    return validate_inner_body_plan(strict_json(raw, maximum_bytes=MAX_PLAN_BYTES))
def admission_from_inner_plan(plan: Mapping[str, Any]) -> BodyStudyAdmission:
    record = validate_inner_body_plan(plan)
    value = record["body_admission"]
    return BodyStudyAdmission(value["plan_sha256"], value["attempt_id"],
        value["reservation_sha256"], value["source_closure_sha256"],
        value["treatment_sha256"], value["protocol_sha256"],
        value["operator_decision_sha256"], _token=_ADMISSION_TOKEN)
def require_admission_current(admission: BodyStudyAdmission, plan: Mapping[str, Any]) -> None:
    refuse(isinstance(admission, BodyStudyAdmission), "body admission required")
    if is_inner_body_plan(plan):
        record = validate_inner_body_plan(plan)
        refuse(admission.plan_sha256 == record["host_plan_sha256"]
               and admission.attempt_id == record["attempt_id"]
               and admission.to_dict() == record["body_admission"], "body admission inner drift")
    else:
        record = validate_body_plan(plan)
        expected = BodyStudyAdmission(admission.plan_sha256, record["attempt_id"],
            admission.reservation_sha256, record["source_closure_sha256"],
            record["treatment_sha256"], record["protocol_sha256"],
            record["operator_decision_sha256"], _token=_ADMISSION_TOKEN)
        refuse(admission.plan_sha256 == plan_digest(record)
               and admission.attempt_id == record["attempt_id"]
               and admission.to_dict() == expected.to_dict(), "body admission plan drift")
StudyAdmission = BodyStudyAdmission; is_inner_study_plan = is_inner_body_plan
validate_study_plan = validate_body_plan
validate_inner_study_plan = validate_inner_body_plan
project_inner_study_plan = project_inner_body_plan
__all__ = [
    "ADMISSION_FIELDS", "ARTIFACT_PIN_DOCUMENT_SHA256", "ARTIFACT_SOURCE_IDENTITY_SHA256",
    "COMMON_PLAN_FIELDS", "BodyStudyAdmission", "INNER_PLAN_FIELDS",
    "MAX_PLAN_BYTES", "PLAN_FIELDS", "admission_file_for_plan", "admission_from_inner_plan",
    "issue_admission", "is_body_plan", "is_inner_body_plan", "plan_digest",
    "is_inner_study_plan", "project_inner_study_plan", "StudyAdmission",
    "project_inner_body_plan", "read_admission_file", "read_inner_body_plan",
    "require_admission_current", "require_reservation_admitted_for_runner",
    "validate_body_plan", "validate_inner_body_plan", "validate_study_plan", "validate_inner_study_plan", "write_admission_file",
]
