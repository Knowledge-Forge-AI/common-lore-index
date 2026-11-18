"""Pure inert BODY host-proof cases; no files, assets, or services are read."""
from __future__ import annotations
from pathlib import Path

from copy import deepcopy
import hashlib
import json
from typing import Any, Callable

from retrieval_body_study_closure import BODY_SOURCE_MEMBERS, BODY_TREATMENT_MEMBERS
from retrieval_body_study_contracts import (
    BODY_ARMS,
    BODY_BASE_CONFIG_SHA256, BODY_CONTROLLER_SECONDS, BODY_INPUT_PIN_SHA256,
    BODY_OPERATOR_DECISION_SHA256, BODY_PHASE, BODY_PLAN_SCHEMA, BODY_PROFILE,
    BODY_PROTOCOL_SHA256, BODY_PROVIDER_SHA256, BODY_ROLE, BODY_RUNTIME_ARCHIVE_BYTES,
    BODY_RUNTIME_ARCHIVE_SHA256, BODY_RUNTIME_CONFIG_SHA256, BODY_SDK_IMAGE_SHA256,
    BODY_SDK_RECIPE_SHA256, BODY_SDK_SECONDS, BODY_SELECTOR, BODY_RESERVATION_SCHEMA,
    BODY_WORKER_CLOSURE_SHA256,
    TASK_COUNT, V1_QUERIES, V2_QUERIES,
)
from retrieval_body_study_plan import ARTIFACT_PIN_DOCUMENT_SHA256, ARTIFACT_SOURCE_IDENTITY_SHA256, plan_digest
from retrieval_body_study_reservation import reservation_digest
from retrieval_body_study_schedule import build_byte_forecast, build_schedule
from retrieval_body_study_contracts import REGISTERED_SNAPSHOT_READ_BYTES

RAW_A = "a" * 64
RAW_B = "b" * 64
RAW_C = "c" * 64
RAW_D = "d" * 64
RAW_E = "e" * 64
RAW_F = "f" * 64
SHA_A, SHA_B, SHA_C, SHA_D, SHA_E, SHA_F = (
    "sha256:" + value for value in (RAW_A, RAW_B, RAW_C, RAW_D, RAW_E, RAW_F)
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _sha(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _query_rows(task_ids: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"record_id": f"query:{task_id}", "text_sha256": SHA_A} for task_id in task_ids]


def _slots() -> list[dict[str, Any]]:
    task_ids = tuple(f"task-{task:02d}" for task in range(TASK_COUNT))
    return [{"task_id": task_id, "arm": arm, "repetition": repetition, "generation": 1}
            for task_id in task_ids for arm in BODY_ARMS for repetition in (1, 2, 3)]


def body_plan() -> dict[str, Any]:
    slots = _slots()
    task_ids = tuple(f"task-{task:02d}" for task in range(TASK_COUNT))
    manifest = {"canonical-node-v1": _query_rows(task_ids[:V1_QUERIES]),
                "canonical-node-public-text-v2": _query_rows(task_ids[:V2_QUERIES])}
    declaration = {"execution_authorized": False, "queries_executed": False, "model_workers_started": 0,
        "slot_manifest": slots, "slot_set_sha256": _sha(slots), "query_manifest": manifest,
        "query_manifest_sha256": _sha({"schema": "cloe-w5-body-query-manifest-v1", "rows": manifest})}
    derived = {"base_config": BODY_BASE_CONFIG_SHA256, "runtime_config": BODY_RUNTIME_CONFIG_SHA256,
        "runtime_config_document": {"name": "body-runtime"}, "base_config_document": {"name": "body-base"},
        "base_config_raw": "body-base-config", "base_layers": [{"sha256": RAW_A, "bytes": 1}],
        "runtime_layers": [{"sha256": RAW_A, "bytes": 1}, {"sha256": RAW_B, "bytes": 1}],
        "byte_forecast_binding": {"source_bytes": 1, "treatment_bytes": 1, "worker_bytes": 1,
            "registered_input_bytes": REGISTERED_SNAPSHOT_READ_BYTES, "request_bytes": [1, 1], "candidate_bytes": 1}}
    forecast = build_byte_forecast(source_bytes=1, treatment_bytes=1, worker_bytes=1,
        registered_input_bytes=REGISTERED_SNAPSHOT_READ_BYTES, request_bytes=(1, 1), candidate_bytes=1)
    derived["body_budget"] = {key: forecast[key] for key in ('candidate_bytes', 'guard_regular_file_bytes', 'guard_file_visits', 'guard_reserved_bytes_per_read', 'inner_guard_limit', 'inner_whole_read_limit', 'registered_input_bytes', 'registered_inner_snapshot_reads', 'registered_snapshot_count', 'registered_snapshot_bytes', 'git_custody_bytes', 'git_state_snapshots', 'wrapper_bytes', 'python_bytes', 'plan_overhead_bytes')}
    return {"schema": BODY_PLAN_SCHEMA, "phase": BODY_PHASE, "role": BODY_ROLE, "profile": BODY_PROFILE,
        "selector": BODY_SELECTOR, "attempt_id": "body-study1-unit", "operator_selection": "ADR0206 Route A",
        "input_pin_sha256": BODY_INPUT_PIN_SHA256, "protocol_sha256": BODY_PROTOCOL_SHA256,
        "source_closure_sha256": SHA_A, "treatment_sha256": SHA_B,
        "worker_closure_sha256": BODY_WORKER_CLOSURE_SHA256,
        "evaluator_closure_sha256": SHA_A, "artifact_source_identity_sha256": ARTIFACT_SOURCE_IDENTITY_SHA256,
        "artifact_pin_document_sha256": ARTIFACT_PIN_DOCUMENT_SHA256, "sdk_image": BODY_SDK_IMAGE_SHA256,
        "sdk_recipe_sha256": BODY_SDK_RECIPE_SHA256, "provider_sha256": BODY_PROVIDER_SHA256,
        "candidate_head": "1" * 40, "candidate_snapshot": {"README.md": {"sha256": RAW_A, "mode": 0o644}},
        "candidate_snapshot_sha256": _sha({"README.md": {"sha256": RAW_A, "mode": 0o644}}),
        "archive": {"path": "/tmp/body-study/runtime-export.tar", "bytes": BODY_RUNTIME_ARCHIVE_BYTES,
                    "sha256": BODY_RUNTIME_ARCHIVE_SHA256.removeprefix("sha256:")},
        "declaration": declaration, "schedule_sha256": build_schedule()["schedule_sha256"],
        "byte_forecast_sha256": forecast["byte_forecast_sha256"],
        "derived": derived, "review1_receipt_sha256": SHA_F,
        "operator_decision_sha256": BODY_OPERATOR_DECISION_SHA256, "sdk_seconds": BODY_SDK_SECONDS,
        "controller_seconds": BODY_CONTROLLER_SECONDS, "source_members": list(BODY_SOURCE_MEMBERS),
        "treatment_members": list(BODY_TREATMENT_MEMBERS), "allocation_path": "/tmp/body-study/allocation.json",
        "reservation_path": "/tmp/body-study/reservation.json"}


def authority_records(plan: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    plan = body_plan() if plan is None else plan
    common = {"phase": BODY_PHASE, "role": BODY_ROLE, "attempt_id": plan["attempt_id"],
        "input_pin_sha256": plan["input_pin_sha256"], "protocol_sha256": plan["protocol_sha256"],
        "source_closure_sha256": plan["source_closure_sha256"], "treatment_sha256": plan["treatment_sha256"],
        "worker_closure_sha256": plan["worker_closure_sha256"]}
    return {
        "source_review": {**common, "schema": "cloe-body-study-source-acceptance-v1", "status": "passed",
            "read_coverage_passed": True, "independent_review_sha256": SHA_A},
        "scoped_proof": {**common, "schema": "cloe-body-study-scoped-proof-acceptance-v1", "status": "passed",
            "unit_passed": True, "integration_passed": True, "static_passed": True, "cleanup_proven": True,
            "proof_receipts_sha256": [SHA_B]},
        "artifact_gate": {"schema": "cloe-body-study-artifact-gate-v1", "phase": BODY_PHASE, "status": "passed",
            "artifact_source_identity_sha256": ARTIFACT_SOURCE_IDENTITY_SHA256,
            "artifact_pin_document_sha256": ARTIFACT_PIN_DOCUMENT_SHA256,
            "worker_closure_sha256": plan["worker_closure_sha256"], "external_gate_sha256": SHA_C},
        "operator_selection": {"schema": "cloe-body-study-operator-selection-v1", "phase": BODY_PHASE,
            "selection": "A", "binding_adr": "0206",
            "binding_contract": "docs/specs/cloe-w5-body-calibration-protocol-v1.md",
            "source_review_scoped_proof_and_fresh_admission_required": True,
            "scientific_heldout_production_main_release_acceptance": False,
            "decision_packet_sha256": plan["operator_decision_sha256"]}}


def reservation_record(plan: dict[str, Any] | None = None, *, state: str = "reserved", cleanup: str | None = None) -> dict[str, Any]:
    plan = body_plan() if plan is None else plan
    consumed = state != "reserved"
    cleanup = cleanup or ("unstarted" if state == "reserved" else ("in_progress" if state == "consumed" else "unknown"))
    status = "reserved" if state == "reserved" else ("admitted" if state == "consumed" else ("settled" if cleanup == "proven" else "blocked"))
    return {"schema": BODY_RESERVATION_SCHEMA, "phase": BODY_PHASE, "role": BODY_ROLE, "nonce_sha256": RAW_D,
        "plan_sha256": plan_digest(plan), "attempt_id": plan["attempt_id"],
        "source_closure_sha256": plan["source_closure_sha256"], "treatment_sha256": plan["treatment_sha256"],
        "worker_closure_sha256": plan["worker_closure_sha256"], "protocol_sha256": plan["protocol_sha256"],
        "operator_decision_sha256": plan["operator_decision_sha256"], "consumed": consumed, "cleanup": cleanup,
        "state": state, "status": status, "runtime_imports": 0, "runtime_exports": 0,
        "model_workers": 0, "native_runtime_loads": 0, "body_study_workers": 0,
        "result_evidence": None if state != "settled" else {
            "schema": "cloe-body-study-attempt-evidence-binding-v1",
            "plan_sha256": plan_digest(plan), "attempt_id": plan["attempt_id"],
            "state": "unavailable", "report_sha256": None,
            "journal_sha256": None, "failure_sha256": None}}


def copy_case() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    plan = body_plan()
    return plan, authority_records(plan)


def mutate(value: dict[str, Any], change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    result = deepcopy(value)
    change(result)
    return result


__all__ = ["authority_records", "body_plan", "copy_case", "mutate", "reservation_digest", "reservation_record"]


def write_physical_authority_case(root: Path, *, wrong_decision: bool = False):
    """Public-safe file-backed protocol fixture; it starts no native resource."""
    from retrieval_body_study_contracts import BODY_PARENT_ADMISSION_SCHEMA
    from retrieval_body_study_plan import plan_digest

    root.mkdir(mode=0o700)
    plan = body_plan()
    records = authority_records(plan)
    if wrong_decision:
        records["operator_selection"]["decision_packet_sha256"] = SHA_A
    references = {}
    for role, record in records.items():
        path = root / (role + ".json")
        raw = _canonical(record) + b"\n"
        path.write_bytes(raw)
        path.chmod(0o600)
        references[role] = {"path": str(path), "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}
    plan["review1_receipt_sha256"] = references["source_review"]["sha256"]
    parent = {"schema": BODY_PARENT_ADMISSION_SCHEMA, "phase": BODY_PHASE, "role": BODY_ROLE,
        "attempt_id": plan["attempt_id"], "plan_sha256": plan_digest(plan),
        **{key: plan[key] for key in ("source_closure_sha256", "treatment_sha256",
            "worker_closure_sha256", "input_pin_sha256", "protocol_sha256")},
        **references, "execution_authorized": True, "model_workers": 2, "slots": 432,
        "held_out_authorized": False, "publication_authorized": False}
    path = root / "parent.json"
    raw = _canonical(parent) + b"\n"
    path.write_bytes(raw)
    path.chmod(0o600)
    return plan, path, "sha256:" + hashlib.sha256(raw).hexdigest()
