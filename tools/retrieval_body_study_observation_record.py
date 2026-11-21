"""Pure BODY observation record and functional sealing projections."""
from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from retrieval_body_study_values import SLOT_COUNT, digest_value, require, thaw

SCHEMA = "cloe-body-study-sealed-observation-v1"
MAX_SEALED_OBSERVATIONS = SLOT_COUNT
MAX_OBSERVATION_BYTES = 256 * 1024


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(thaw(value), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        require(False, "body.observation:not_jsonable")
        return b""


def _slot(value: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: value.get(key) for key in ("task_id", "arm", "repetition", "generation")}
    require(type(result["task_id"]) is str and type(result["arm"]) is str,
            "body.observation:slot_identity")
    require(type(result["repetition"]) is int and 1 <= result["repetition"] <= 3,
            "body.observation:slot_repetition")
    require(type(result["generation"]) is int and result["generation"] == 1,
            "body.observation:slot_generation")
    return result


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    require(isinstance(value, Mapping), code)
    assert isinstance(value, Mapping)
    return value


def _functional_slot(actual: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    """Project functional original rows without mutating either payload."""
    merged = dict(actual) | dict(policy)
    merged["generation"] = 1
    return _slot(merged)


def _record_body(observation: object, *, index: int, record_kind: str,
                 functional_only: bool = False) -> dict[str, Any]:
    require(type(index) is int and 0 <= index < MAX_SEALED_OBSERVATIONS,
            "body.observation:index")
    require(record_kind in {"observation", "failure"}, "body.observation:kind")
    actual = _mapping(getattr(observation, "actual", observation),
                      "body.observation:actual")
    policy = _mapping(getattr(observation, "policy", actual),
                      "body.observation:policy")
    grade = getattr(observation, "grade", None)
    if grade is not None and hasattr(grade, "to_dict"):
        grade = grade.to_dict()
    body = {
        "schema": SCHEMA, "index": index, "record_kind": record_kind,
        "slot": (_functional_slot(actual, policy) if functional_only
                 else _slot(dict(actual) | dict(policy))),
        "actual": thaw(dict(actual)), "policy": thaw(dict(policy)),
        "grade": thaw(grade),
        "origin_proven": False if record_kind == "failure" else bool(actual.get("origin_proven")),
        "scientific_acceptance": False,
    }
    body["observation_sha256"] = digest_value(body)
    raw = _canonical(body) + b"\n"
    require(len(raw) <= MAX_OBSERVATION_BYTES, "body.observation:byte_bound")
    return body


__all__ = ["MAX_OBSERVATION_BYTES", "MAX_SEALED_OBSERVATIONS", "SCHEMA"]
