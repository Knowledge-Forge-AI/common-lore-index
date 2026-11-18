"""Actual body-study report assembly and terminal validation."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from retrieval_body_capabilities import expected_capabilities
from retrieval_body_catalog import BodyCatalog
from retrieval_body_study_prepare import BodyPublicationPreparation
from retrieval_body_study_preflight import CommonFreezeReceipt, WorkerEpochReceipt
from retrieval_body_study_quality import evaluate_body_study
from retrieval_body_study_slots import BodySlotBatch
from retrieval_body_study_observation import (
    FAILURE_EVIDENCE_NAME, FAILURE_REPORT_NAME, FAILURE_REPORT_SCHEMA,
    build_body_failure_envelope, validate_body_failure_envelope,
    write_body_failure_envelope,
)
from retrieval_body_study_values import (
    ARMS,
    OUTCOMES,
    PROSPECTIVE_UNIVERSE,
    QUALITY_SCHEMA,
    REGISTERED_INPUT_PIN_SHA256,
    SLOT_COUNT,
    STUDY_PHASE,
    STUDY_SCHEMA,
    digest_value,
    require,
)
from retrieval_semantic_provenance import VerifiedPublicationPin

REPORT_SCHEMA = "cloe-body-study-actual-report-v1"


def _task_inputs(catalog: Any) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, int | None]]:
    meta: dict[str, dict[str, Any]] = {}
    golds: dict[str, Any] = {}
    universes: dict[str, int | None] = {}
    for case in catalog.cases:
        task = case.task
        raw = case.to_dict()
        caps = expected_capabilities(task, case.kind)
        meta[task.task_id] = {
            "task_id": task.task_id,
            "case_kind": case.kind,
            "family": task.family,
            "intent_id": raw["intent_id"],
            "wording_stratum": raw["wording_stratum"],
            "expected_arm_outcomes": {
                arm: {
                    "state": {"ready": "completed", "refused": "refused", "unavailable": "unavailable"}[caps[arm]["preparation"]],
                    "refusal_code": caps[arm]["reason"] if caps[arm]["preparation"] == "refused" else None,
                }
                for arm in ARMS
            },
        }
        golds[task.task_id] = tuple(task.gold_targets)
        universes[task.task_id] = PROSPECTIVE_UNIVERSE.get(case.kind)
    return meta, golds, universes


def _public_pin(pin: Any) -> dict[str, Any]:
    return {
        "backend": pin.backend,
        "graph_id": pin.graph_id,
        "generation": pin.generation,
        "publication_bundle_id": pin.publication_bundle_id,
        "snapshot_manifest_id": pin.snapshot_manifest_id,
        "receipt_identity": pin.receipt_identity,
        "identity": digest_value(pin.to_dict()),
    }


def _actual_slot_rows(batch: BodySlotBatch) -> list[dict[str, Any]]:
    rows = [dict(row) for row in batch.actual_slots]
    require(len(rows) == SLOT_COUNT, "body.report:actual_slots")
    for row in rows:
        require(type(row.get("task_id")) is str and type(row.get("arm")) is str,
                "body.report:slot_identity")
        require(row.get("generation") == 1, "body.report:slot_generation")
        require(type(row.get("repetition")) is int and 1 <= row["repetition"] <= 3,
                "body.report:slot_repetition")
    require(len({(row["task_id"], row["arm"], row["repetition"]) for row in rows}) == SLOT_COUNT,
            "body.report:slot_uniqueness")
    actual_order = tuple((row["task_id"], row["arm"], row["repetition"]) for row in rows)
    expected_order = tuple((row["task_id"], row["arm"], row["repetition"]) for row in batch.policy_slots)
    require(actual_order == expected_order, "body.report:slot_order")
    require(tuple(row["execution_state"] for row in rows) == tuple(
        row["execution_state"] for row in batch.policy_slots), "body.report:slot_state_join")
    return rows


def build_actual_body_report(
    *,
    preparation: BodyPublicationPreparation,
    requests: Any,
    worker_receipts: Sequence[WorkerEpochReceipt],
    common_freeze: CommonFreezeReceipt,
    slots: BodySlotBatch,
    cleanup: Mapping[str, Any],
    integrity_broken: bool = False,
    privacy_disclosed: bool = False,
    forbidden_work_executed: bool = False,
    functional_only: bool = False,
    source_guard: Any | None = None,
    prevalidated: bool = False,
) -> dict[str, Any]:
    """Evaluate the maintained policy view only after actual grading audits pass."""
    require(type(prevalidated) is bool, "body.report:prevalidated")
    if source_guard is not None:
        require(not prevalidated, "body.report:guard_prevalidated_join")
        require(callable(source_guard), "body.report:source_guard")
        source_guard()
    elif not prevalidated:
        preparation.require_current()
    requests.require_current()
    require(len(worker_receipts) == 2, "body.report:worker_receipts")
    for receipt, request in zip(worker_receipts, requests.ordered(), strict=True):
        receipt.require_current(request)
    common_freeze.require_current()
    slots.require_current()
    require(isinstance(cleanup, Mapping), "body.report:cleanup")
    require(type(functional_only) is bool, "body.report:functional_only")
    sqlite_pin = getattr(preparation, "sqlite_pin", None)
    postgres_pin = getattr(preparation, "postgres_pin", None)
    inputs = getattr(preparation, "inputs", None)
    catalog = getattr(preparation, "catalog", None)
    from retrieval_body_pin import RegisteredBodyInputs
    registered_inputs = (type(inputs) is RegisteredBodyInputs
                         and RegisteredBodyInputs.is_factory_issued(inputs))
    # The cleanup mapping is retained provenance, never authority.  Actual
    # origin comes from the sealed input handle that the source guard checked
    # immediately before this report boundary.
    if registered_inputs:
        assert isinstance(inputs, RegisteredBodyInputs)
        input_pin_sha256 = inputs._pin_sha256
    else:
        cleanup_pin = cleanup.get("input_pin_sha256")
        require(type(cleanup_pin) is str, "body.report:input_pin")
        assert isinstance(cleanup_pin, str)
        input_pin_sha256 = cleanup_pin
    catalog_issued = (type(catalog) is BodyCatalog
                      and BodyCatalog.is_factory_issued(catalog))
    sqlite_issued = (type(sqlite_pin) is VerifiedPublicationPin
                     and sqlite_pin.is_factory_issued())
    postgres_issued = (type(postgres_pin) is VerifiedPublicationPin
                      and postgres_pin.is_factory_issued())
    registered_origin = (
        registered_inputs
        and isinstance(catalog, BodyCatalog)
        and catalog.registered
        and catalog_issued
        and catalog.identity.reserved
        and input_pin_sha256 == REGISTERED_INPUT_PIN_SHA256
        and sqlite_issued
        and postgres_issued
    )
    worker_origin = all(WorkerEpochReceipt.is_factory_issued(item) for item in worker_receipts)
    origin_proven = (not functional_only) and registered_origin and worker_origin
    preparation_identity = cleanup.get("preparation_identity")
    if preparation_identity is None:
        preparation_identity = preparation.identity
    cleanup_proven = cleanup.get("cleanup_ok") is True or (
        cleanup.get("relations_clean") is True and cleanup.get("container_absent") is True
    )
    task_meta, golds, universes = _task_inputs(preparation.catalog)
    evaluation = evaluate_body_study(
        task_meta=task_meta,
        golds=golds,
        universes=universes,
        common_task_ids=common_freeze.common,
        floor_passed=common_freeze.common.floor_passed,
        slots=slots.policy_slots,
        integrity_broken=integrity_broken,
        privacy_disclosed=privacy_disclosed,
        forbidden_work_executed=forbidden_work_executed,
        preparation_complete=True,
        telemetry_cleanup_proven=cleanup_proven,
    )
    require(evaluation["outcome"] in OUTCOMES, "body.report:evaluation_outcome")
    actual_slots = _actual_slot_rows(slots)
    slot_state_counts = {
        state: sum(row.get("execution_state") == state for row in slots.policy_slots)
        for state in sorted({"completed", "refused", "unavailable", "failed", "not_run",
                             "unstarted", "missing", "timed_out"})
    }
    worker_summary = [
        {
            "identity": item.identity,
            "representation": item.representation,
            "response_identity": item.response_identity,
            "admission_identity": item.admission_identity,
            "reachability_identity": item.reachability_identity,
            "record_count": len(item.token_receipts),
            "token_count_used": sum(row.get("token_count_used", row.get("token_count") or 0)
                                    for row in item.token_provenance),
            "token_count_before_limit": sum(row.get("token_count_before_limit", row.get("token_count") or 0)
                                            for row in item.token_provenance),
            "overflow_chunks": sum(row.get("overflow_chunks", 0) for row in item.token_provenance),
            "truncated_records": sum(bool(row.get("truncated")) for row in item.token_provenance),
            "eligibility_count": len(item.eligibility),
            "token_provenance": [dict(row) for row in item.token_provenance],
            "transport_receipt": dict(item.transport_receipt),
            "eligibility": [dict(row) for row in item.eligibility],
        }
        for item in worker_receipts
    ]
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "phase": STUDY_PHASE,
        "role": "body-study",
        "protocol": "cloe-w5-body-calibration-protocol-v1",
        "study_schema": STUDY_SCHEMA,
        "quality_schema": QUALITY_SCHEMA,
        "origin_proven": origin_proven,
        "functional_only": functional_only,
        "scientific_acceptance": False,
        "scientific_acceptance_claimed": False,
        "execution_state": (
            "completed"
            if evaluation["outcome"] in {
                "inconclusive_floor", "signal_not_demonstrated", "calibration_signal_present",
            }
            else "failed"
        ),
        "attempt_id": requests.v1.descriptor.get("attempt_id"),
        "preparation_identity": preparation_identity,
        "input_pin_sha256": input_pin_sha256,
        "catalog_sha256": preparation.catalog.digest,
        "inert_preparation": {
            "identity": preparation_identity,
            "matrix": preparation.matrix,
            "origin_proven": False,
            "execution_authority_granted": False,
            "model_executed": False,
        },
        "publications": {
            "sqlite": _public_pin(preparation.sqlite_pin),
            "postgresql": _public_pin(preparation.postgres_pin),
        },
        "requests": {
            "v1": {"identity": requests.v1.identity, "counts": {
                "documents": requests.v1.document_count, "queries": requests.v1.query_count,
            }},
            "v2": {"identity": requests.v2.identity, "counts": {
                "documents": requests.v2.document_count, "queries": requests.v2.query_count,
            }},
            "total_records": len(requests.v1.records) + len(requests.v2.records),
        },
        "worker_receipts": worker_summary,
        "workers": worker_summary,
        "common_freeze": {
            "identity": common_freeze.freeze_identity,
            "common_identity": common_freeze.common.identity,
            "task_ids": list(common_freeze.common.task_ids),
            "planned": [dict(row) for row in common_freeze.common.planned],
            "excluded": [dict(row) for row in common_freeze.common.excluded],
            "floor_passed": common_freeze.common.floor_passed,
            "frozen_before_ranking": common_freeze.frozen_before_ranking,
            "eligibility_identity": common_freeze.eligibility_identity,
        },
        "slot_count": SLOT_COUNT,
        "slot_state_counts": slot_state_counts,
        "observed_slot_count": SLOT_COUNT - slots.unstarted_slots,
        "unstarted_slot_count": slots.unstarted_slots,
        "exposure_count": slots.exposure_count,
        "slots": actual_slots,
        "policy_slots": [dict(row) for row in slots.policy_slots],
        "cleanup": dict(cleanup),
        "owned_cleanup": dict(cleanup),
        "evaluation": evaluation,
        "outcome": evaluation["outcome"],
        "quality_outcome": evaluation["outcome"],
        "inert_preparation_retained": True,
        "inert_preparation_origin_proven": False,
        "report_basis": "actual-pin-snapshot-hit-grading-v1",
    }
    # The host-owned closure/plan join is supplied with cleanup evidence when
    # available.  Missing joins remain missing; this report never invents a
    # source identity from an inert preparation report.
    for field in ("plan_sha256", "source_closure_sha256", "treatment_sha256",
                  "worker_closure_sha256", "evaluator_closure_sha256",
                  "protocol_sha256", "operator_decision_sha256"):
        if field in cleanup:
            report[field] = cleanup[field]
    report["report_identity"] = digest_value({key: value for key, value in report.items() if key != "report_identity"})
    return report


def validate_actual_body_report(report: object) -> dict[str, Any]:
    require(isinstance(report, Mapping), "body.report:mapping")
    assert isinstance(report, Mapping)
    require(report.get("schema") == REPORT_SCHEMA, "body.report:schema")
    require(report.get("phase") == STUDY_PHASE, "body.report:phase")
    functional_only = report.get("functional_only", False)
    require(type(functional_only) is bool, "body.report:functional_only")
    require(report.get("origin_proven") is True and not functional_only, "body.report:origin")
    require(report.get("scientific_acceptance") is False, "body.report:scientific_acceptance")
    require(report.get("slot_count") == SLOT_COUNT, "body.report:slot_count")
    require(isinstance(report.get("slots"), Sequence) and len(report["slots"]) == SLOT_COUNT,
            "body.report:slots")
    evaluation = report.get("evaluation")
    require(isinstance(evaluation, Mapping) and evaluation.get("outcome") in OUTCOMES,
            "body.report:evaluation")
    require(report.get("report_identity") == digest_value({
        key: value for key, value in report.items()
        if key not in {"report_identity", "journal"}
    }), "body.report:identity")
    return dict(report)


__all__ = [
    "FAILURE_EVIDENCE_NAME", "FAILURE_REPORT_NAME", "FAILURE_REPORT_SCHEMA",
    "REPORT_SCHEMA", "build_actual_body_report", "build_body_failure_envelope",
    "validate_actual_body_report", "validate_body_failure_envelope",
    "write_body_failure_envelope",
]
