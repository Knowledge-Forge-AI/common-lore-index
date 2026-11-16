"""Owned evidence and staged request helpers for the actual supplement driver."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
import secrets
from pathlib import Path
from typing import Any

from retrieval_public_text_census import retained_task_sha256
from retrieval_public_text_descriptors import PublicTextPreparation
from retrieval_public_text_request import check_request_caps
from retrieval_semantic_generator_inputs import PublicGenerationInput, build_worker_request
from retrieval_semantic_generator_kernel import SemanticMode, SemanticText
from retrieval_semantic_generator_worker_contracts import ATTEMPT_ID_PATTERN, REQUEST_SCHEMA
from retrieval_semantic_generator_worker_protocol import parse_worker_request
from retrieval_supplement_epoch import (
    PreparationEpoch, acknowledgement, await_acknowledgement, parse_epoch, prepare_epoch,
)
from retrieval_supplement_files import publish_member
from retrieval_supplement_study_adapter import (
    EXPECTED_COUNTS, WorkerExchange, parse_exchanges, validate_built_request_records,
)
from retrieval_supplement_study_contracts import INPUT_PIN_SHA256, SLOT_COUNT, TOTAL_RECORDS, refuse
from retrieval_supplement_study_live_requests import LiveStudyRequests
from retrieval_supplement_study_schedule import build_byte_forecast
from retrieval_supplement_primitives import canonical_bytes, digest_bytes, digest_json

@dataclass(frozen=True)
class V1RequestStage:
    """The v1 request prepared after epoch 1 and before worker 1."""

    input: PublicGenerationInput
    exchange: WorkerExchange
    next_exchange: WorkerExchange
    request: Mapping[str, Any]
    request_raw: bytes

@dataclass
class EpochEvidence:
    ordinal: int
    representation: str
    request_attempt_id: str
    epoch_sha256: str
    mode: str
    acknowledged: bool
    raw_paths: list[str] = field(default_factory=list)
    byte_charges: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal, "representation": self.representation,
            "request_attempt_id": self.request_attempt_id, "epoch_sha256": self.epoch_sha256,
            "mode": self.mode, "acknowledged": self.acknowledged,
            "raw_paths": list(self.raw_paths), "byte_charges": list(self.byte_charges),
        }

@dataclass
class WorkerExchangeEvidence:
    representation: str
    request_attempt_id: str
    request_sha256: str
    admission_sha256: str | None
    response_members: dict[str, str] = field(default_factory=dict)
    raw_paths: list[str] = field(default_factory=list)
    response: Any = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "representation": self.representation,
            "request_attempt_id": self.request_attempt_id,
            "request_sha256": self.request_sha256,
            "admission_sha256": self.admission_sha256,
            "response_members": dict(self.response_members),
            "raw_paths": list(self.raw_paths),
            "response_received": self.response is not None,
        }


@dataclass
class InnerEvidence:
    epochs: list[EpochEvidence] = field(default_factory=list)
    workers: list[WorkerExchangeEvidence] = field(default_factory=list)
    secondary_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "cloe-supplement-study-inner-evidence-v1",
            "epochs": [item.to_dict() for item in self.epochs],
            "workers": [item.to_dict() for item in self.workers],
            "secondary_errors": list(self.secondary_errors),
        }


def _attempt_id(attempt_id: str, ordinal: int) -> str:
    result = f"{attempt_id}-v{ordinal}"
    refuse(ATTEMPT_ID_PATTERN.fullmatch(result) is not None, "inner_evidence:attempt_id")
    return result


def _raw_path(root: Path, relative: str, raw: bytes) -> str:
    publish_member(root, relative, raw)
    return relative


def _epoch_record(root: Path, epoch: PreparationEpoch, *, mode: str, acknowledged: bool,
                  charges: Sequence[int]) -> EpochEvidence:
    prefix = f"epochs/v{epoch.ordinal}"
    paths = [_raw_path(root, prefix + "/epoch.json", epoch.raw)]
    if acknowledged:
        paths.append(_raw_path(root, prefix + "/ack.json", acknowledgement(epoch)))
    return EpochEvidence(
        epoch.ordinal, epoch.representation, epoch.request_attempt_id, epoch.identity,
        mode, acknowledged, paths, list(charges),
    )


def begin_preparation_epoch(
    *, attempt_id: str, ordinal: int, evaluator_closure_sha256: str,
    functional_only: bool, evidence_root: Path, require_current: Callable[[], None],
    charge: Callable[[int], None], ledger: InnerEvidence,
) -> PreparationEpoch:
    """Publish and, only for actual mode, await the host preparation ACK."""
    require_current()
    charges: list[int] = []

    def counted(value: int) -> None:
        charges.append(value)
        charge(value)

    if functional_only:
        epoch = parse_epoch({
            "schema": "cloe-supplement-preparation-epoch-v1",
            "attempt_id": attempt_id, "ordinal": ordinal,
            "representation": "canonical-node-v1" if ordinal == 1 else "canonical-node-public-text-v2",
            "nonce": secrets.token_hex(32), "request_attempt_id": _attempt_id(attempt_id, ordinal),
            "input_pin_sha256": INPUT_PIN_SHA256,
            "evaluator_closure_sha256": evaluator_closure_sha256,
        })
        ledger.epochs.append(_epoch_record(
            evidence_root, epoch, mode="functional_synthetic", acknowledged=True, charges=charges,
        ))
        return epoch

    epoch = prepare_epoch(
        attempt_id=attempt_id, ordinal=ordinal, evaluator_closure_sha256=evaluator_closure_sha256,
        charge=counted,
    )
    ledger.epochs.append(_epoch_record(
        evidence_root, epoch, mode="host_acknowledged", acknowledged=False, charges=charges,
    ))
    await_acknowledgement(
        epoch, charge=counted, require_current=require_current,
    )
    evidence = ledger.epochs[-1]
    evidence.raw_paths.append(_raw_path(evidence_root, f"epochs/v{ordinal}/ack.json", acknowledgement(epoch)))
    evidence.acknowledged = True
    evidence.byte_charges.extend(charges[len(evidence.byte_charges):])
    require_current()
    return epoch


def begin_worker_evidence(
    *, evidence_root: Path, representation: str, exchange: Any, ledger: InnerEvidence,
) -> WorkerExchangeEvidence:
    """Retain request and any admission bytes immediately after publication/admission."""
    prefix = "workers/" + ("v1" if representation == "canonical-node-v1" else "v2")
    paths = [_raw_path(evidence_root, prefix + "/request.json", exchange.request_raw)]
    if exchange.admission_raw is not None:
        paths.append(_raw_path(evidence_root, prefix + "/admission.json", exchange.admission_raw))
    evidence = WorkerExchangeEvidence(
        representation, exchange.attempt_id, digest_bytes(exchange.request_raw),
        None if exchange.admission_raw is None else digest_bytes(exchange.admission_raw), raw_paths=paths,
    )
    ledger.workers.append(evidence)
    return evidence


def finish_worker_evidence(*, evidence_root: Path, evidence: WorkerExchangeEvidence,
                           members: Mapping[str, bytes]) -> None:
    prefix = "workers/" + ("v1" if evidence.representation == "canonical-node-v1" else "v2")
    for name, raw in sorted(members.items()):
        relative = f"{prefix}/response/{name}"
        evidence.raw_paths.append(_raw_path(evidence_root, relative, raw))
        evidence.response_members[name] = digest_bytes(raw)


def bind_worker_admission(*, evidence_root: Path, evidence: WorkerExchangeEvidence,
                          exchange: Any) -> None:
    if exchange.admission_raw is None:
        return
    prefix = "workers/" + ("v1" if evidence.representation == "canonical-node-v1" else "v2")
    relative = prefix + "/admission.json"
    evidence.raw_paths.append(_raw_path(evidence_root, relative, exchange.admission_raw))
    evidence.admission_sha256 = digest_bytes(exchange.admission_raw)


def build_v1_request_stage(
    preparation: Mapping[str, Any], *, v1_input: PublicGenerationInput,
    attempt_id: str, artifact_profile_identity: str,
) -> V1RequestStage:
    """Build only v1 after epoch 1; v2 preparation remains deferred."""
    refuse(type(v1_input) is PublicGenerationInput, "inner_evidence:v1_input_type")
    # Parsing the inert shared contract is epoch-1 work; v2 request construction stays deferred.
    v1_exchange, v2_exchange = parse_exchanges(preparation, expected_counts=EXPECTED_COUNTS,
                                                expected_total=TOTAL_RECORDS, expected_slots=SLOT_COUNT)
    v1_pairs = {(d.representation.source_binding_id, d.representation.canonical_key) for d in v1_input.documents}
    expected = {(str(row["binding_id"]), str(row["canonical_key"])) for row in v1_exchange.documents}
    refuse(len(v1_input.documents) == len(v1_exchange.documents) and v1_pairs == expected,
           "inner_evidence:v1_documents")
    admitted = [d.task_id for d in v1_input.query_decisions if d.record_id is not None]
    refuse(admitted == [str(q["task_id"]) for q in v1_exchange.queries], "inner_evidence:v1_queries")
    request = build_worker_request(
        v1_input, attempt_id=_attempt_id(attempt_id, 1), artifact_profile_identity=artifact_profile_identity,
    )
    validate_built_request_records(request, v1_exchange, v1_input)
    raw = canonical_bytes(dict(request))
    parse_worker_request(raw)
    return V1RequestStage(v1_input, v1_exchange, v2_exchange, request, raw)


def build_v2_live_requests(
    stage: V1RequestStage, *, public_text: PublicTextPreparation, tasks: Sequence[Any],
    attempt_id: str, artifact_profile_identity: str,
) -> LiveStudyRequests:
    """Complete the existing LiveStudyRequests only after epoch 2 is ACKed."""
    refuse(type(public_text) is PublicTextPreparation, "inner_evidence:public_text_type")
    v2 = stage.next_exchange
    tuples = {
        (str(doc.descriptor["source_binding_id"]), str(doc.descriptor["canonical_key"]),
        str(doc.descriptor["source_relative_path"]), int(doc.descriptor["start_line"]),
        int(doc.descriptor["end_line"]))
        for doc in public_text.documents
    }
    expected = {(str(row["binding_id"]), str(row["canonical_key"]), str(row["source_relative_path"]),
                 int(row["start_line"]), int(row["end_line"])) for row in v2.documents}
    refuse(len(public_text.documents) == len(v2.documents) and tuples == expected, "inner_evidence:v2_documents")
    tasks_by_id = {task.task_id: task for task in tasks}
    records: list[SemanticText] = []
    sources: list[str] = []
    for ordinal, document in enumerate(public_text.documents, start=1):
        records.append(SemanticText(f"ptx.d.{ordinal:04d}", document.model_text, SemanticMode.DOCUMENT))
        sources.append(document.identity)
    query_tasks: list[str] = []
    for query in v2.queries:
        task_id = str(query["task_id"])
        task = tasks_by_id.get(task_id)
        if task is None:
            refuse(False, "inner_evidence:v2_query")
            continue
        refuse(task.question == query["text"], "inner_evidence:v2_query")
        records.append(SemanticText(f"ptx.q.{task_id}", task.question, SemanticMode.QUERY))
        sources.append(retained_task_sha256(task))
        query_tasks.append(task_id)
    prep_identity = digest_json(public_text.to_dict())
    manifest = {"schema": "cloe-supplement-study-v2-input-v1", "preparation_identity": prep_identity,
                "publication_identity": public_text.publication_identity, "records": [
                    [record.record_id, SemanticMode(record.mode).value,
                     digest_bytes(record.text.encode("utf-8")), source]
                    for record, source in zip(records, sources, strict=True)
                ]}
    request: dict[str, Any] = {
        "schema": REQUEST_SCHEMA, "attempt_id": _attempt_id(attempt_id, 2),
        "input_identity": digest_json(manifest), "representation_report_identity": prep_identity,
        "publication_identity": public_text.publication_identity,
        "artifact_profile_identity": artifact_profile_identity,
        "records": [
            {"record_id": record.record_id, "text": record.text, "mode": SemanticMode(record.mode).value}
            for record in records
        ],
    }
    validate_built_request_records(request, v2, None)
    raw = canonical_bytes(request)
    check_request_caps(records, request_bytes=len(raw))
    _parsed, parsed_records = parse_worker_request(raw)
    refuse(parsed_records == tuple(records), "inner_evidence:v2_roundtrip")
    return LiveStudyRequests(
        stage.exchange, v2, stage.request, request, stage.request_raw, raw,
        tuple(records), tuple(sources), tuple(query_tasks),
    )


def candidate_snapshot_bytes(repo_root: Path, snapshot: Mapping[str, Any]) -> int:
    """Read and bind regular candidate members for the byte forecast."""
    total = 0
    for name, row in snapshot.items():
        if "symlink" in row:
            continue
        raw = (repo_root / str(name)).read_bytes()
        refuse(digest_bytes(raw).removeprefix("sha256:") == str(row["sha256"]),
               "inner_evidence:candidate_snapshot")
        total += len(raw)
    return total

def byte_forecast_evidence(
    plan: Mapping[str, Any], *, source_bytes: int, request_bytes: Sequence[int], candidate_bytes: int,
) -> dict[str, Any]:
    """Bind actual requests to the parent forecast, refusing an unbound hash."""
    actual = build_byte_forecast(source_bytes=source_bytes, request_bytes=request_bytes,
                                 candidate_bytes=candidate_bytes)
    binding = plan.get("derived", {}).get("byte_forecast_binding")
    if binding is None:
        refuse(actual["byte_forecast_sha256"] == plan["byte_forecast_sha256"],
               "inner_evidence:byte_forecast_mismatch")
        return {"mode": "exact_actual", "actual": actual}
    refuse(isinstance(binding, Mapping)
           and set(binding) == {"source_bytes", "request_bytes", "candidate_bytes"},
           "inner_evidence:byte_forecast_binding")
    planned = build_byte_forecast(source_bytes=int(binding["source_bytes"]),
                                  request_bytes=tuple(int(item) for item in binding["request_bytes"]),
                                  candidate_bytes=int(binding["candidate_bytes"]))
    refuse(planned["byte_forecast_sha256"] == plan["byte_forecast_sha256"],
           "inner_evidence:byte_forecast_plan_hash")
    refuse(source_bytes <= int(binding["source_bytes"])
           and len(request_bytes) == len(binding["request_bytes"])
           and all(actual <= int(bound) for actual, bound in zip(request_bytes, binding["request_bytes"], strict=True))
           and candidate_bytes == int(binding["candidate_bytes"]),
           "inner_evidence:byte_forecast_actual_bounds")
    return {"mode": "planned_bounds", "binding": dict(binding), "planned": planned, "actual": actual}

def unstarted_slot_rows(catalog: Any, outcomes: Sequence[Any]) -> list[dict[str, Any]]:
    observed = {(item.task_id, item.arm, item.repetition) for item in outcomes}
    from retrieval_supplement_declaration import ordered_slots
    return [
        slot.to_dict() for slot in ordered_slots(catalog)
        if (slot.task_id, slot.arm, slot.repetition) not in observed
    ]

def error_code(error: BaseException, fallback: str) -> str:
    code = getattr(error, "code", None)
    return code if type(code) is str and code else fallback

def settle_cleanup(
    primary: BaseException | None, cleanup: Callable[[], Any], ledger: InnerEvidence,
) -> tuple[BaseException | None, Any]:
    """Run cleanup once, preserving the first exception and retaining later failure."""
    try:
        return primary, cleanup()
    except BaseException as error:
        code = error_code(error, "cleanup_failed")
        if primary is None:
            return error, None
        ledger.secondary_errors.append(code)
        return primary, None

def attach_slot_metrics(outcomes: Sequence[Any], report: Mapping[str, Any]) -> tuple[Any, ...]:
    """Carry the maintained report grader's actual metrics into paired rows."""
    metrics = {
        (str(row["slot"]["task_id"]), str(row["slot"]["arm"]), int(row["slot"]["repetition"])):
        row["grading"]["metrics"] for row in report["slots"]
    }
    return tuple(replace(item, metrics=metrics.get((item.task_id, item.arm, item.repetition)))
                 for item in outcomes)


def report_payload(*, attempt_id: str, functional_only: bool, declaration_source_closure: str,
                   evaluator_closure: str, worker_closure: str, workers: Sequence[WorkerExchangeEvidence],
                   observed_slots: int, token_records: int, candidate_bytes: int,
                   byte_forecast: Mapping[str, Any], evidence: InnerEvidence,
                   supplement_report: Mapping[str, Any], paired_analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Build the non-acceptance bundle while retaining independent closure domains."""
    return {
        "schema": "cloe-supplement-study-bundle-v1", "attempt_id": attempt_id,
        "functional_only": functional_only, "input_pin_sha256": INPUT_PIN_SHA256,
        "declaration_source_closure_sha256": declaration_source_closure,
        "evaluator_closure_sha256": evaluator_closure, "worker_closure_sha256": worker_closure,
        "worker_identities": [worker.to_dict() for worker in workers],
        "observed_slot_count": observed_slots, "token_record_count": token_records,
        "candidate_bytes": candidate_bytes, "byte_forecast": dict(byte_forecast),
        "inner_evidence": evidence.to_dict(), "supplement_report": dict(supplement_report),
        "paired_analysis": dict(paired_analysis), "scientific_acceptance_claimed": False,
        "held_out_acceptance_claimed": False, "production_acceptance_claimed": False,
    }


def failure_receipt(*, attempt_id: str, failure_code: str, catalog: Any, outcomes: Sequence[Any],
                    relations_clean: bool, container_absent: bool, cleanup_receipt: Any,
                    evidence: InnerEvidence, evaluator_closure: str, worker_closure: str) -> bytes:
    """Serialize a failure without dropping observed raw exchange or unstarted slots."""
    unstarted = unstarted_slot_rows(catalog, outcomes)
    return canonical_bytes({
        "schema": "cloe-supplement-study-failure-v1", "attempt_id": attempt_id,
        "failure_code": failure_code, "observed_slot_count": len(outcomes),
        "unstarted_slot_count": len(unstarted), "observed_slots": [item.to_dict() for item in outcomes],
        "unstarted_slots": unstarted, "pg_relations_clean": relations_clean,
        "pg_container_absent": container_absent, "cleanup_receipt": cleanup_receipt,
        "secondary_errors": list(evidence.secondary_errors), "input_pin_sha256": INPUT_PIN_SHA256,
        "evaluator_closure_sha256": evaluator_closure, "worker_closure_sha256": worker_closure,
        "inner_evidence": evidence.to_dict(), "scientific_acceptance_claimed": False,
    })


__all__ = [
    "EpochEvidence", "InnerEvidence", "V1RequestStage", "WorkerExchangeEvidence",
    "begin_preparation_epoch", "begin_worker_evidence", "bind_worker_admission", "build_v1_request_stage",
    "attach_slot_metrics", "build_v2_live_requests", "byte_forecast_evidence",
    "candidate_snapshot_bytes", "error_code", "failure_receipt", "finish_worker_evidence",
    "report_payload", "settle_cleanup", "unstarted_slot_rows",
]
