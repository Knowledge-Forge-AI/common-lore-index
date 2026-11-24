"""Closed constants and errors for the inert W5 body-study source unit (ADR0206).

This module issues no runner, model, tokenizer or execution authority.  It
reuses the frozen body-v2 input pin and arm identities from the existing body
owners and never casts anything to ``SupplementIdentity``.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from retrieval_body_values import (
    BODY_FAMILIES as BODY_FAMILIES,
    REGISTERED_IDENTITY as REGISTERED_IDENTITY,
    REGISTERED_INPUT_PIN_SHA256 as REGISTERED_INPUT_PIN_SHA256,
)
from retrieval_supplement_primitives import ARMS as ARMS

STUDY_PHASE = "CLOE-W5-BODY-STUDY1"
STUDY_SCHEMA = "cloe-body-study-selected-matrix-v1"
COMMON_SET_SCHEMA = "cloe-body-study-common-positive-v1"
QUALITY_SCHEMA = "cloe-body-study-quality-v1"
PROTOCOL_ID = "cloe-w5-body-calibration-protocol-v1"
# Distinct study identity: the frozen class/corpus inputs are reused, the
# study cohort identity is new and is not a supplement identity.
STUDY_CLASS_ID = "body-study1-captured-text-calibration"
SOURCE_CLASS_ID = REGISTERED_IDENTITY.class_id
SOURCE_CORPUS_ID = REGISTERED_IDENTITY.corpus_id

NATIVE_ARM, GRAPH_ARM, FTS_ARM, V1_ARM, V2_ARM, FUSION_ARM = ARMS
REPETITIONS = 3
TASK_COUNT = 24
ROW_COUNT = 144
SLOT_COUNT = 432
BODY_POSITIVE_COUNT = 12
ANCHOR_COUNT = 4
CONTROL_COUNT = 8
FLOOR_INTENTS = 8
FLOOR_PER_FAMILY = 2
NDCG_GAIN_MIN = 0.05
FAMILY_LOSS_MAX = 0.05
CUTOFF = 10

# Prospective source census (not execution evidence): 66+4 v1, 44+20 v2.
PROSPECTIVE_CENSUS = {"v1_documents": 66, "v1_queries": 4, "v2_documents": 44, "v2_queries": 20}
PROSPECTIVE_RECORDS = 134
MAX_DOCUMENTS_PER_REPRESENTATION = 80
MAX_QUERIES_PER_REPRESENTATION = 24
MAX_COMBINED_RECORDS = 500
MAX_TOKENS = 512
MAX_EXCERPT_BYTES = 4096
MAX_STREAM_BYTES = 8 * 1024 * 1024

V1_REPRESENTATION = "canonical-node-v1"
V2_REPRESENTATION = "canonical-node-public-text-v2"
NATIVE_OBSERVED_SURFACE = "native_filesystem"
ARM_BACKENDS = {
    NATIVE_ARM: "sqlite", GRAPH_ARM: "postgresql", FTS_ARM: "postgresql",
    V1_ARM: "postgresql", V2_ARM: "postgresql", FUSION_ARM: "postgresql",
}
ARM_REPRESENTATIONS = {
    NATIVE_ARM: "native-tools-baseline", GRAPH_ARM: "current-graph-baseline:postgresql",
    FTS_ARM: "postgres-native-fts:postgresql", V1_ARM: V1_REPRESENTATION,
    V2_ARM: V2_REPRESENTATION, FUSION_ARM: V2_REPRESENTATION,
}
COMPARISON_ARMS = (NATIVE_ARM, FTS_ARM, V2_ARM, FUSION_ARM)
CASE_KINDS = ("body_positive", "metadata_anchor", "absent", "ambiguous", "privacy", "unsupported")
OUTCOMES = ("inadmissible", "incomplete_technical", "inconclusive_floor",
            "calibration_signal_present", "signal_not_demonstrated")
SLOT_STATES = frozenset({
    "completed", "refused", "unavailable", "failed", "not_run", "unstarted",
    "missing", "timed_out",
})
PROSPECTIVE_UNIVERSE = {"body_positive": 22, "metadata_anchor": 11}
_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


class BodyStudyError(ValueError):
    """Public-safe refusal carrying a stable code; never raw payload text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise BodyStudyError(code)


def is_digest(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(v) for v in value]
    return value


def digest_value(value: Any) -> str:
    try:
        raw = json.dumps(thaw(value), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as exc:
        raise BodyStudyError("body_study:not_jsonable") from exc
    return "sha256:" + hashlib.sha256(raw).hexdigest()


INERT_FLAGS: dict[str, Any] = {
    "origin_proven": False, "execution_state": "not_executed",
    "execution_authority_granted": False, "model_executed": False,
    "tokenizer_observed": False, "scientific_acceptance": False,
    "controller_admission_join_required": True,
}

__all__ = [
    "ANCHOR_COUNT", "ARMS", "ARM_BACKENDS", "ARM_REPRESENTATIONS", "BODY_FAMILIES",
    "BODY_POSITIVE_COUNT", "BodyStudyError", "CASE_KINDS", "COMMON_SET_SCHEMA",
    "COMPARISON_ARMS", "CONTROL_COUNT", "CUTOFF", "FAMILY_LOSS_MAX", "FLOOR_INTENTS",
    "FLOOR_PER_FAMILY", "FTS_ARM", "FUSION_ARM", "GRAPH_ARM", "INERT_FLAGS",
    "MAX_COMBINED_RECORDS", "MAX_DOCUMENTS_PER_REPRESENTATION", "MAX_EXCERPT_BYTES",
    "MAX_QUERIES_PER_REPRESENTATION", "MAX_STREAM_BYTES", "MAX_TOKENS", "NATIVE_ARM",
    "NATIVE_OBSERVED_SURFACE", "NDCG_GAIN_MIN", "OUTCOMES", "PROSPECTIVE_RECORDS",
    "PROSPECTIVE_CENSUS", "PROSPECTIVE_UNIVERSE", "PROTOCOL_ID", "QUALITY_SCHEMA",
    "REGISTERED_IDENTITY", "REGISTERED_INPUT_PIN_SHA256", "REPETITIONS", "ROW_COUNT",
    "SLOT_COUNT", "SLOT_STATES", "SOURCE_CLASS_ID", "SOURCE_CORPUS_ID", "STUDY_CLASS_ID",
    "STUDY_PHASE", "STUDY_SCHEMA", "TASK_COUNT", "V1_ARM", "V1_REPRESENTATION",
    "V2_ARM", "V2_REPRESENTATION", "digest_value", "is_digest", "require", "thaw",
]
