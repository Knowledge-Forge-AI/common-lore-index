"""Immutable leaves and shared validation for inert body records."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any

from retrieval_evaluation_schema import EvaluationTask
from retrieval_semantic_generator_inputs import INPUT_POLICY
from retrieval_semantic_generator_kernel_contracts import SemanticMode, SemanticText
from retrieval_supplement_primitives import ARMS
from retrieval_supplement_tasks import task_digests

MAX_QUERY_TASKS = 24
MAX_DOCUMENTS_PER_REPRESENTATION = 80
MAX_QUERIES_PER_REPRESENTATION = 24
MAX_COMBINED_RECORDS = 500
PROSPECTIVE_MAX_TOKENS = 512
PUBLIC_TEXT_V2_ARM, FUSION_ARM = ARMS[4], ARMS[5]
PUBLIC_TEXT_V2_REPRESENTATION = "canonical-node-public-text-v2"
_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


class BodyRecordError(ValueError):
    """A public-safe refusal while constructing an inert record bundle."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise BodyRecordError(code)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise BodyRecordError("identity_not_jsonable")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            _thaw(value), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as exc:
        raise BodyRecordError("identity_not_jsonable") from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _text(value: object, code: str) -> str:
    _require(type(value) is str and bool(value.strip()), code)
    assert isinstance(value, str)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BodyRecordError(code) from exc
    _require(not any(ord(char) < 32 or ord(char) == 127 for char in value), code)
    _require(len(encoded) <= 1024 * 1024, code)
    return value


def _digest_string(value: object, code: str) -> str:
    _require(type(value) is str and _DIGEST.fullmatch(value) is not None, code)
    assert isinstance(value, str)
    return value


def _task_digests(task: EvaluationTask) -> tuple[str, str]:
    _require(type(task) is EvaluationTask, "invalid_task")
    try:
        task.validate()
    except (KeyError, TypeError, ValueError) as exc:
        raise BodyRecordError("invalid_task") from exc
    digests = task_digests(task)
    return digests["task_sha256"], digests["retained_task_sha256"]


def _publication(value: object) -> Any:
    if value is None:
        return None
    _require(isinstance(value, Mapping), "publication_identity")
    return _freeze(value)


def _state(row: Mapping[str, Any]) -> tuple[str, str | None, str, str]:
    state = row.get("preparation")
    reason = row.get("reason")
    surface = row.get("actual_surface")
    fallback = row.get("fallback_policy")
    _require(type(state) is str and state in {"ready", "refused", "unavailable"}, "preparation_state")
    if state == "ready":
        _require(reason is None and type(surface) is str and surface != "none", "ready_fields")
        _require(fallback in {"none", "required", "record_actual"}, "ready_fallback")
    else:
        _require(type(reason) is str and bool(reason), "preparation_reason")
        _require(surface == "none" and fallback == "none", "nonready_fields")
    assert isinstance(state, str) and isinstance(surface, str) and isinstance(fallback, str)
    assert reason is None or isinstance(reason, str)
    return state, reason, surface, fallback


def _reject_execution_claims(row: Mapping[str, Any]) -> None:
    names = (
        "candidate_access_performed", "execution_authority_granted", "model_executed",
        "tokenization_observed", "scientific_acceptance", "heldout_acceptance",
        "production_acceptance",
    )
    for name in names:
        if name in row:
            _require(row[name] is False, "execution_claim")
    if "execution_state" in row:
        _require(row["execution_state"] == "not_executed", "execution_state")


@dataclass(frozen=True, slots=True)
class BodyPreparationDecision:
    """A defensive decision copy; it contains no question/source text."""

    class_id: str
    catalog_sha256: str
    case_sha256: str
    input_pin_sha256: str
    task_id: str
    task_sha256: str
    retained_task_sha256: str
    generation: int
    arm: str
    preparation: str
    reason: str | None
    actual_surface: str
    fallback_policy: str
    admission_identity: str
    publication_identity: Any
    representation_identity: str

    def to_dict(self) -> dict[str, Any]:
        return _thaw({name: getattr(self, name) for name in _DECISION_FIELDS})

    @property
    def identity(self) -> str:
        return _digest(self.to_dict())


_DECISION_FIELDS = (
    "class_id", "catalog_sha256", "case_sha256", "input_pin_sha256", "task_id",
    "task_sha256", "retained_task_sha256", "generation", "arm", "preparation", "reason",
    "actual_surface", "fallback_policy", "admission_identity", "publication_identity",
    "representation_identity",
)


@dataclass(frozen=True, slots=True)
class BodyQueryRecord:
    """One inert query; raw text lives only on ``semantic_text``."""

    semantic_text: SemanticText
    class_id: str
    catalog_sha256: str
    case_sha256: str
    input_pin_sha256: str
    task_id: str
    task_sha256: str
    retained_task_sha256: str
    generation: int
    admission_identity: str
    publication_identity: Any
    representation_identity: str
    input_policy: str = INPUT_POLICY

    def __post_init__(self) -> None:
        _require(type(self.semantic_text) is SemanticText, "query_record_semantic_text")
        _require(self.semantic_text.mode is SemanticMode.QUERY, "query_record_mode")
        _require(self.semantic_text.record_id == "query:" + self.task_id, "query_record_id")
        _require(self.input_policy == INPUT_POLICY, "query_record_policy")

    @property
    def question_sha256(self) -> str:
        return _digest_bytes(self.semantic_text.text.encode("utf-8"))

    @property
    def identity(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.semantic_text.record_id,
            "mode": SemanticMode(self.semantic_text.mode).value,
            "text_sha256": self.question_sha256,
            "text_utf8_bytes": len(self.semantic_text.text.encode("utf-8")),
            "class_id": self.class_id,
            "catalog_sha256": self.catalog_sha256,
            "case_sha256": self.case_sha256,
            "input_pin_sha256": self.input_pin_sha256,
            "task_id": self.task_id,
            "task_sha256": self.task_sha256,
            "retained_task_sha256": self.retained_task_sha256,
            "generation": self.generation,
            "admission_identity": self.admission_identity,
            "publication_identity": _thaw(self.publication_identity),
            "representation_identity": self.representation_identity,
            "input_policy": self.input_policy,
        }


@dataclass(frozen=True, slots=True)
class BodyInputFrame:
    """The one class/data/publication frame shared by a record ledger."""

    class_id: str
    catalog_sha256: str
    input_pin_sha256: str
    generation: int
    publication_identity: Any
    representation_identity: str = PUBLIC_TEXT_V2_REPRESENTATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "catalog_sha256": self.catalog_sha256,
            "input_pin_sha256": self.input_pin_sha256,
            "generation": self.generation,
            "publication_identity": _thaw(self.publication_identity),
            "representation_identity": self.representation_identity,
            "input_policy": INPUT_POLICY,
        }


@dataclass(frozen=True, slots=True)
class BodyQueryLedger:
    """Immutable queries plus every supplied v2/fusion preparation decision."""

    frame: BodyInputFrame
    records: tuple[BodyQueryRecord, ...]
    decisions: tuple[BodyPreparationDecision, ...]

    @property
    def query_records(self) -> tuple[BodyQueryRecord, ...]:
        return self.records

    @property
    def planned_embedding_count(self) -> int:
        return len(self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "cloe-body-query-record-ledger-v1",
            "frame": self.frame.to_dict(),
            "records": [item.to_dict() for item in self.records],
            "decisions": [item.to_dict() for item in self.decisions],
            "fusion_query_dependencies": {
                item.task_id: "query:" + item.task_id
                for item in self.decisions if item.arm == FUSION_ARM and item.preparation == "ready"
            },
            "planned_embedding_count": self.planned_embedding_count,
            "embedding_count_status": "PLANNED",
            "token_counts": "unobserved",
            "truncation": "unobserved",
            "runtime_costs": "unobserved",
            "origin_proven": False,
            "execution_state": "not_executed",
        }


__all__ = [
    "BodyInputFrame", "BodyPreparationDecision", "BodyQueryLedger", "BodyQueryRecord",
    "BodyRecordError", "FUSION_ARM", "INPUT_POLICY", "MAX_COMBINED_RECORDS",
    "MAX_DOCUMENTS_PER_REPRESENTATION", "MAX_QUERY_TASKS", "MAX_QUERIES_PER_REPRESENTATION",
    "PROSPECTIVE_MAX_TOKENS", "PUBLIC_TEXT_V2_ARM", "PUBLIC_TEXT_V2_REPRESENTATION",
]
