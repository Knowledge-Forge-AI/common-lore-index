"""Actual W5 body-study ordering and terminal orchestration."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from retrieval_body_study_preflight import (
    WorkerEpochReceipt,
    freeze_common_positive,
    parse_worker_epoch_receipt,
    validate_record_census,
)
from retrieval_body_study_prepare import BodyPublicationPreparation
from retrieval_body_study_report import (
    FAILURE_EVIDENCE_NAME, build_actual_body_report, build_body_failure_envelope,
    write_body_failure_envelope,
)
from retrieval_body_study_requests import BodyStudyRequest, BodyStudyRequests
from retrieval_body_study_slots import execute_body_slots
from retrieval_body_study_values import PROSPECTIVE_CENSUS, require


@dataclass(frozen=True, slots=True)
class BodyStudyOutcome:
    status: str
    attempt_id: str
    observed_slots: int
    unstarted_slots: int
    token_records: int
    cleanup_proven: bool
    report_sha256: str
    report: Mapping[str, Any]
    evaluation: Mapping[str, Any]
    journal_receipt: Mapping[str, Any]


def _response(value: object) -> Mapping[str, Any]:
    raw = getattr(value, "response", value)
    require(isinstance(raw, Mapping), "body.driver:worker_response")
    assert isinstance(raw, Mapping)
    return raw


def _cleanup_flag(cleanup: Mapping[str, Any]) -> bool | None:
    value = cleanup.get("cleanup_ok")
    if type(value) is bool:
        return value
    relations, container = cleanup.get("relations_clean"), cleanup.get("container_absent")
    if type(relations) is bool and type(container) is bool:
        return relations and container
    return None


def _failure_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if type(code) is str and code and len(code) <= 128 and all(
            char.isalnum() or char in "_:-." for char in code):
        return code
    return type(error).__name__


def _failure_root(journal: Any) -> Path | None:
    root = getattr(journal, "root", None)
    candidate = root.parent if isinstance(root, Path) else None
    return candidate if isinstance(candidate, Path) and candidate.is_absolute() else None


def _failure_outcome(error: BaseException) -> str:
    return "inadmissible" if "inadmissible" in _failure_code(error) else "incomplete_technical"


class BodyStudyFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def run_actual_body_study(
    *,
    preparation: BodyPublicationPreparation,
    requests: BodyStudyRequests,
    exchange: Callable[[Any], object],
    pins: Mapping[str, Any],
    execute_slot: Callable[[Mapping[str, Any], Any], Mapping[str, Any]],
    journal: Any,
    cleanup: Mapping[str, Any],
    private_markers: Sequence[str] = (),
    response_adapter: Callable[[BodyStudyRequest, object, BodyPublicationPreparation], WorkerEpochReceipt]
    | None = None,
    source_guard: Callable[[], None] | None = None,
    report_guard: Callable[[], None] | None = None,
) -> BodyStudyOutcome:
    """Run two sequential worker epochs and retain failures before cleanup acceptance."""
    require(source_guard is None or callable(source_guard), "body.driver:source_guard")
    require(report_guard is None or callable(report_guard), "body.driver:report_guard")
    require(isinstance(cleanup, Mapping), "body.driver:cleanup")
    cleanup_state: dict[str, Any] = cleanup if isinstance(cleanup, dict) else dict(cleanup)
    responses: list[WorkerEpochReceipt] = []
    common_freeze: Any = None
    batch: Any = None
    counts: Mapping[str, int] = {}
    stage = "source_guard"
    finalize = cleanup_state.pop("_finalize_cleanup", None)
    finalized = False

    def finish_cleanup() -> None:
        nonlocal finalized
        if not finalized and callable(finalize):
            finalized = True
            finalize()

    def persist_failure(error: BaseException, *, precleanup: bool,
                        primary_sha256: str | None = None) -> str | None:
        root = _failure_root(journal)
        if root is None:
            return None
        request_v1 = getattr(requests, "v1", None)
        descriptor = getattr(request_v1, "descriptor", None)
        attempt_id = descriptor.get("attempt_id") if isinstance(descriptor, Mapping) else None
        envelope = build_body_failure_envelope(
            attempt_id=attempt_id,
            failure_code=_failure_code(error), failure_stage=stage, cleanup=cleanup_state,
            journal=journal, worker_receipts=responses, slots=batch,
            functional_only=response_adapter is None,
            precleanup_envelope_sha256=primary_sha256,
        )
        filename = FAILURE_EVIDENCE_NAME if precleanup else None
        return write_body_failure_envelope(
            root, envelope, **({"filename": filename} if filename else {}),
        )

    try:
        if source_guard is None:
            preparation.require_current()
        else:
            source_guard()
        requests.require_current()
        require(callable(exchange), "body.driver:exchange")
        require(callable(execute_slot), "body.driver:slot_executor")
        require(journal is not None and callable(getattr(journal, "freeze_common", None)),
                "body.driver:journal")
        stage = "worker_exchange"
        for request in requests.ordered():
            evidence = exchange(request)
            if response_adapter is None:
                response = _response(evidence)
                responses.append(parse_worker_epoch_receipt(request, response))
            else:
                responses.append(response_adapter(request, evidence, preparation))
            stage = "worker_exchange_v2" if request.ordinal == 1 else "record_census"
        v1, v2 = responses
        counts = validate_record_census(v1, v2)
        require(counts == dict(PROSPECTIVE_CENSUS), "body.driver:record_census")
        eligibility = tuple(v1.eligibility) + tuple(v2.eligibility)
        require(len(eligibility) == 48, "body.driver:eligibility_join")
        positives = [{
            "task_id": case.task.task_id, "family": case.task.family,
            "intent_id": case.to_dict()["intent_id"],
            "wording_stratum": case.to_dict()["wording_stratum"], "case_kind": case.kind,
        } for case in preparation.catalog.cases if case.kind == "body_positive"]
        stage = "common_freeze"
        common_freeze = freeze_common_positive(
            task_meta=positives, eligibility=eligibility, worker_receipts=responses,
        )
        journal.freeze_common(common_freeze.common)
        stage = "slot_execution"
        batch = execute_body_slots(
            preparation=preparation, common_freeze=common_freeze, pins=pins,
            operation=journal, execute=execute_slot, private_markers=private_markers,
            source_guard=source_guard,
        )
        prevalidated_report = source_guard is not None
        stage = "report_guard"
        if report_guard is not None:
            report_guard()
        elif source_guard is not None:
            source_guard()
        stage = "cleanup"
        finish_cleanup()
        cleanup_ok = _cleanup_flag(cleanup_state)
        require(cleanup_ok is True, "body.driver:cleanup_not_proven")
        stage = "report"
        report = build_actual_body_report(
            preparation=preparation, requests=requests, worker_receipts=responses,
            common_freeze=common_freeze, slots=batch, cleanup=cleanup_state,
            functional_only=response_adapter is None, prevalidated=prevalidated_report,
        )
        base_report_sha256 = str(report["report_identity"])
        evaluation = report["evaluation"]
        failure_sha256 = None
        if str(evaluation["outcome"]) in {"incomplete_technical", "inadmissible"}:
            failure_sha256 = persist_failure(
                BodyStudyFailure(str(evaluation["outcome"])), precleanup=False,
            )
        failure_stage_for_terminal = stage if failure_sha256 else None
        stage = "journal_terminal"
        integrity_ok = cleanup_state.get("integrity_ok") if type(cleanup_state.get("integrity_ok")) is bool else (
            str(evaluation["outcome"]) != "inadmissible"
        )
        ownership_ok = cleanup_state.get("ownership_ok") if type(cleanup_state.get("ownership_ok")) is bool else None
        journal_receipt = journal.terminal(
            outcome=str(evaluation["outcome"]), report_sha256=base_report_sha256,
            integrity_ok=integrity_ok, ownership_ok=ownership_ok, cleanup_ok=cleanup_ok,
            expected_common_identity=common_freeze.common.identity,
            failure_envelope_sha256=failure_sha256,
            failure_stage=failure_stage_for_terminal,
        )
        report["journal"] = {
            "terminal": dict(journal_receipt), "basis_report_sha256": base_report_sha256,
            "cycle_excluded_from_report_identity": True,
        }
        observed = sum(row.get("execution_state") != "unstarted" for row in batch.actual_slots)
        return BodyStudyOutcome(
            status=("functional" if response_adapter is None else (
                "completed" if evaluation["outcome"] not in {"incomplete_technical", "inadmissible"}
                else "failed")),
            attempt_id=str(report.get("attempt_id") or ""),
            observed_slots=observed, unstarted_slots=batch.unstarted_slots,
            token_records=sum(counts.values()), cleanup_proven=True,
            report_sha256=base_report_sha256, report=report, evaluation=evaluation,
            journal_receipt=journal_receipt,
        )
    except BaseException as error:
        primary = error
        pre_sha = None
        try:
            pre_sha = persist_failure(primary, precleanup=True)
        except BaseException as evidence_error:
            cleanup_state["failure_evidence_error"] = type(evidence_error).__name__
        try:
            stage = "cleanup"
            finish_cleanup()
        except BaseException as cleanup_error:
            cleanup_state["cleanup_error"] = type(cleanup_error).__name__
        try:
            final_sha = persist_failure(primary, precleanup=False, primary_sha256=pre_sha)
            if final_sha is not None and callable(getattr(journal, "terminal", None)):
                outcome = _failure_outcome(primary)
                journal.terminal(
                    outcome=outcome, report_sha256=final_sha,
                    integrity_ok=cleanup_state.get("integrity_ok")
                    if type(cleanup_state.get("integrity_ok")) is bool else None,
                    ownership_ok=cleanup_state.get("ownership_ok")
                    if type(cleanup_state.get("ownership_ok")) is bool else None,
                    cleanup_ok=_cleanup_flag(cleanup_state),
                    failure_envelope_sha256=final_sha, failure_stage=stage,
                )
        except BaseException as terminal_error:
            cleanup_state["failure_terminal_error"] = type(terminal_error).__name__
        raise

def run_body_study(**kwargs: Any) -> BodyStudyOutcome:
    """Compatibility name used by the authenticated body native owner."""
    return run_actual_body_study(**kwargs)


__all__ = ["BodyStudyOutcome", "run_actual_body_study", "run_body_study"]
