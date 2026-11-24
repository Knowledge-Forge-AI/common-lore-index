"""Capture real body-slot observations without synthesizing metric rows."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from types import MappingProxyType

from common_lore_index.retrieval.query_contracts import QueryEnvelope
from retrieval_body_study_grading import ActualGrade, grade_snapshot_hits
from retrieval_body_study_values import SLOT_STATES, digest_value, require
from retrieval_evaluation_grading import ExecutionState
from retrieval_semantic_provenance import VerifiedPublicationPin

_ISSUED = object()


def _observation_identity(value: "BodySlotObservation") -> str:
    grade = value.grade.to_dict() if value.grade is not None else None
    return digest_value({
        "actual": dict(value.actual), "policy": dict(value.policy), "grade": grade,
    })


def _value(raw: object, name: str, default: object = None) -> object:
    if isinstance(raw, Mapping):
        return raw.get(name, default)
    return getattr(raw, name, default)


def _label(raw: object, default: str | None = None) -> str | None:
    if raw is None:
        return default
    value = getattr(raw, "value", raw)
    return str(value) if value is not None else default


def _envelope_context(envelope: object) -> dict[str, Any]:
    require(envelope is not None, "body.capture:envelope_required")
    require(isinstance(envelope, QueryEnvelope), "body.capture:query_envelope")
    assert isinstance(envelope, QueryEnvelope)
    envelope.validate()
    scope = _value(envelope, "scope")
    capability = _label(_value(envelope, "capability_state"))
    mode = (
        _label(_value(envelope, "mode"))
        or _label(_value(envelope, "effective_mode"))
        or _label(_value(envelope, "requested_mode"))
        or _label(_value(scope, "mode"))
    )
    scope_complete = _value(envelope, "scope_complete")
    if scope_complete is None:
        scope_complete = _value(scope, "complete")
    establishes_absence = _value(envelope, "establishes_absence")
    if callable(establishes_absence):
        establishes_absence = establishes_absence()
    if establishes_absence is None:
        establishes_absence = _value(_value(envelope, "provenance"), "establishes_absence")
    require(type(capability) is str and bool(capability), "body.capture:capability")
    require(type(mode) is str and bool(mode), "body.capture:mode")
    require(type(scope_complete) is bool, "body.capture:scope_complete")
    require(type(establishes_absence) is bool, "body.capture:establishes_absence")
    assert isinstance(capability, str)
    assert isinstance(mode, str)
    assert isinstance(scope_complete, bool)
    assert isinstance(establishes_absence, bool)
    receipt = envelope.to_dict()
    return {
        "capability_state": capability,
        "mode": mode,
        "scope_complete": scope_complete,
        "establishes_absence": establishes_absence,
        "envelope_identity": digest_value(receipt),
        "envelope_receipt": receipt,
    }


@dataclass(frozen=True, slots=True)
class BodySlotObservation:
    """Rich retained actual row and its policy-view projection."""

    actual: Mapping[str, Any]
    policy: Mapping[str, Any]
    grade: ActualGrade | None
    _origin: object = field(default=None, repr=False, compare=False)
    _seal: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        require(self._origin is _ISSUED, "body.capture:factory_required")
        require(isinstance(self.actual, Mapping) and isinstance(self.policy, Mapping),
                "body.capture:factory_mapping")
        object.__setattr__(self, "actual", MappingProxyType(dict(self.actual)))
        object.__setattr__(self, "policy", MappingProxyType(dict(self.policy)))

    @staticmethod
    def is_factory_issued(value: object) -> bool:
        return (type(value) is BodySlotObservation and value._origin is _ISSUED
                and value._seal == _observation_identity(value))

    @property
    def exposed(self) -> bool:
        return self.policy.get("execution_state") == "completed" or self.policy.get("execution_state") == "refused"


def capture_body_slot(
    *,
    task: Any,
    task_id: str,
    arm: str,
    repetition: int,
    result: Mapping[str, Any],
    verified_pin: VerifiedPublicationPin,
    private_markers: Sequence[str] = (),
) -> BodySlotObservation:
    """Capture a slot from a real envelope, or preserve a typed terminal state."""
    require(isinstance(result, Mapping), "body.capture:result_mapping")
    require(type(verified_pin) is VerifiedPublicationPin and verified_pin.is_factory_issued(),
            "body.capture:pin")
    verified_pin.revalidate_current()
    state_raw = result.get("execution_state")
    state = _label(state_raw)
    if isinstance(state_raw, ExecutionState):
        state = state_raw.value
    require(state in SLOT_STATES, "body.capture:state")
    origin = result.get("observation_origin", "query_owner")
    require(origin in {"query_owner", "preparation_terminal"}, "body.capture:observation_origin")
    require(origin != "preparation_terminal" or state in {"refused", "unavailable"},
            "body.capture:preparation_terminal_state")
    envelope = result.get("envelope")
    context: dict[str, Any] = {}
    hits = tuple(result.get("snapshot_hits", result.get("hits", ())))
    grade: ActualGrade | None = None
    if state == "completed":
        context = _envelope_context(envelope)
        require(not isinstance(hits, (str, bytes, bytearray)), "body.capture:hits")
        grade = grade_snapshot_hits(
            returned_items=hits, gold_targets=task.gold_targets,
            verified_pin=verified_pin, task=task, private_markers=private_markers,
        )
        require(context["capability_state"] in {"available", "partial"}, "body.capture:completed_capability")
    elif state in {"refused", "unavailable"}:
        context = _envelope_context(envelope)
        require(not hits, "body.capture:terminal_hits")
        if state == "refused":
            require(type(result.get("refusal_code")) is str and result["refusal_code"],
                    "body.capture:refusal_code")
            require(context["capability_state"] == "refused", "body.capture:refusal_capability")
        else:
            require(type(result.get("unavailable_reason")) is str and result["unavailable_reason"],
                    "body.capture:unavailable_reason")
            require(context["capability_state"] in {"unavailable", "not_run"},
                    "body.capture:unavailable_capability")
    else:
        require(not hits, "body.capture:failed_hits")
        require(envelope is None or isinstance(envelope, Mapping), "body.capture:failed_envelope")

    ranked = tuple(grade.ranked_files if grade is not None else ())
    policy: dict[str, Any] = {
        "task_id": task_id,
        "arm": arm,
        "repetition": repetition,
        "execution_state": state,
        "ranked_files": ranked,
        "refusal_code": result.get("refusal_code") if state == "refused" else None,
        "returned_hit_count": len(hits),
    }
    if context:
        policy.update({
            "capability_state": context["capability_state"],
            "scope_complete": context["scope_complete"],
        })
        if task.comparability_stratum == "body" and getattr(task, "grading_rule", None) == "absent_answer":
            policy["absence_answer_correct"] = (
                state == "completed" and context["scope_complete"]
                and context["capability_state"] == "available" and not hits
                and context["establishes_absence"]
            )
    if state == "unavailable":
        policy["unavailable_reason"] = result["unavailable_reason"]
    if grade is not None:
        policy["metrics"] = dict(grade.metrics)
        policy["quality_metrics"] = dict(grade.metrics)

    actual = {
        **policy,
        "generation": int(task.generation),
        "envelope": dict(context),
        "snapshot_hits": [dict(item) for item in (grade.snapshot_hits if grade is not None else ())],
        "grade": grade.to_dict() if grade is not None else None,
        "pin_identity": digest_value(verified_pin.to_dict()),
        "observation_origin": origin,
        "query_execution_proven": origin == "query_owner" and grade is not None,
        "origin_proven": origin == "query_owner" and (grade is not None or state in {"refused", "unavailable"}),
        "preparation_only": False,
    }
    if state == "failed":
        actual["error_code"] = str(result.get("error_code") or "body_slot_failed")
    observation = BodySlotObservation(actual, policy, grade, _origin=_ISSUED)
    object.__setattr__(observation, "_seal", _observation_identity(observation))
    return observation


__all__ = ["BodySlotObservation", "capture_body_slot"]
