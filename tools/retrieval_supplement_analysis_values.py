"""Closed values for retained supplement analysis (CLOE-W5-SUPPLEMENT-ANALYSIS1).

Pure leaf: no filesystem, model, runtime or network work. Native and synthetic
origins are separate schemas; a synthetic bundle or public projection can never
be admitted as native, and analyzer source identity never substitutes for
original study, evaluator or worker identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import re
from types import MappingProxyType
from typing import Any, NoReturn

from retrieval_supplement_primitives import (
    ACTUAL_SURFACES,
    ARMS,
    CASE_KINDS,
    EXECUTIONS,
    FALLBACK_POLICIES,
    GRADING_RULES,
    OTHER_FAMILY,
    POSITIVE_FAMILIES,
    RATIONALE_FAMILIES,
    REGISTERED_INPUT_PIN_SHA256,
    is_digest,
)
from retrieval_supplement_study_contracts import (
    SLOT_COUNT as NATIVE_SLOT_COUNT,
    TASK_COUNT as NATIVE_TASK_COUNT,
    TOTAL_RECORDS as NATIVE_TOKEN_RECORDS,
    WORKER_CLOSURE_SHA256 as NATIVE_WORKER_CLOSURE_SHA256,
)

NATIVE_SCHEMA = "cloe-supplement-study-bundle-v1"
SYNTHETIC_SCHEMA = "cloe-supplement-analysis-synthetic-bundle-v1"
PUBLIC_OBSERVATION_SCHEMA = "cloe-w5-supplement-native-observation-v1"
PUBLIC_COST_CENSUS_SCHEMA = "cloe-supplement-public-cost-census-v1"
FAILURE_RECEIPT_SCHEMA = "cloe-supplement-study-failure-v1"
SUPPLEMENT_REPORT_SCHEMA = "cloe-calibration-supplement-report-v1"
PAIRED_ANALYSIS_SCHEMA = "cloe-supplement-study-paired-analysis-v1"
COST_SUMMARY_SCHEMA = "cloe-supplement-phase-cost-summary-v1"
COST_RECORD_SCHEMA = "cloe-supplement-phase-observation-v1"
ANALYSIS_SCHEMA = "cloe-supplement-retained-analysis-v1"

ORIGINS = frozenset({"native", "synthetic"})
SLOT_STATES = ("completed", "failed", "not_run", "refused", "unavailable")
GRADING_STATES = frozenset({"correct", "partial", "incorrect", "ungraded", "not_applicable"})
COST_STATES = frozenset({"completed", "failed"})
ELAPSED_OBSERVATIONS = frozenset({"caller_phase_boundary", "unknown"})
CAPABILITY_STATES = frozenset({
    "available", "partial", "refused", "stale", "unavailable", "failed",
    "not_run", "unsupported_mode",
})
MATCH_SURFACES = frozenset({
    "captured_text", "stored_metadata", "graph_topology", "direct_handoff", "none",
})
CUTOFFS = (1, 3, 5, 10)
FILE_METRICS = tuple(f"file_{kind}@{k}" for kind in ("recall", "mrr", "ndcg") for k in CUTOFFS)
SPAN_METRICS = tuple(f"{kind}@{k}" for kind in ("recall", "mrr", "ndcg") for k in CUTOFFS)
METRIC_NAMES = frozenset(FILE_METRICS + SPAN_METRICS)
METRIC_ORDER = FILE_METRICS + SPAN_METRICS
DECLARED_METRIC_NAMESPACES = ("file", "span")

# Calibrated against the 432-slot native bundle (~85k string keys+values and
# ~6.6k objects when 432 PhaseRecorder query records are duplicated from
# supplement_report.phase_costs into paired_analysis.cost_summary, and ~150k
# strings if per-slot FieldSamples are attached), bounded within the 4 MiB
# MAX_REPORT_ARCHIVE_BYTES cap.
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 32
MAX_OBJECTS = 65536
MAX_STRINGS = 262144
MAX_STRING_BYTES = 16384
MAX_LIST = 4096
MAX_OUTPUT_JSON_BYTES = 4 * 1024 * 1024
MAX_REPORT_BYTES = 512 * 1024
DISPLAY_DIGITS = 6

# Closed public-safe categories; raw attached strings or open tokens never reach output.
REASON_CATEGORIES = frozenset({
    "binding_privacy_not_authorized",
    "captured_text_surface_unsupported_by_metadata_graph",
    "metadata_representation_not_comparable",
    "unsupported_semantic_query_mode",
    "synthetic_refusal",
    "unavailable",
    "other_registered_reason",
    "none",
})
REGISTERED_SAMPLE_NAMES = frozenset({
    "query_ns",
    "query_sample",
    "worker_elapsed",
    "rows_returned",
    "wire_bytes",
    "logical_bytes",
    "observed_statements",
    "statements_executed",
    "written_files",
    "unseen_storage",
})
_SAFE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_TOKEN = re.compile(r"\A[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_EXPECTATION_KEYS = frozenset({"execution", "reason", "actual_surface", "fallback_policy"})


class AnalysisRefusal(ValueError):
    """Public-safe refusal carrying a stable code and no payload text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def refuse(condition: object, code: str) -> None:
    if not condition:
        raise AnalysisRefusal(code)


def fail(code: str) -> NoReturn:
    raise AnalysisRefusal(code)


def exact_keys(value: object, expected: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"analysis:{label}:object_required")
    refuse(set(value) == set(expected), f"analysis:{label}:closed_shape")
    return value


def subset_keys(
    value: object, known: frozenset[str], required: frozenset[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"analysis:{label}:object_required")
    refuse(set(value) <= set(known), f"analysis:{label}:unknown_fields")
    refuse(set(required) <= set(value), f"analysis:{label}:missing_fields")
    return value


def as_str(value: object, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str:
        fail(f"analysis:{label}:string_required")
    return value


def as_safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str or _SAFE_ID.match(value) is None:
        fail(f"analysis:{label}:invalid_id")
    return value


def as_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        fail(f"analysis:{label}:bool_required")
    return value


def optional_bool(value: object, label: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    fail(f"analysis:{label}:bool_required")


def as_int(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or type(value) is not int:
        fail(f"analysis:{label}:integer_required")
    refuse(minimum <= value <= maximum, f"analysis:{label}:out_of_range")
    return value


def as_metric(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"analysis:{label}:number_required")
    number = float(value)
    refuse(math.isfinite(number) and 0.0 <= number <= 1.0, f"analysis:{label}:out_of_range")
    return round(number, DISPLAY_DIGITS)


def as_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str or not is_digest(value):
        fail(f"analysis:{label}:invalid_sha256")
    return value


def as_token(value: object, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str or _TOKEN.match(value) is None:
        fail(f"analysis:{label}:invalid_token")
    return value


def reason_category(value: object) -> str:
    """Map an attached reason string to a closed category; never echo it."""
    if value is None:
        return "none"
    if isinstance(value, str) and type(value) is str and value in REASON_CATEGORIES:
        return value
    return "other_registered_reason"


def sample_name_category(value: str) -> str:
    """Map an open FieldSample token name to a closed registered sample category."""
    return value if value in REGISTERED_SAMPLE_NAMES else "other_registered_sample"


def capability_category(value: str) -> str:
    """Map capability_state to a closed category; never echo open tokens."""
    return value if value in CAPABILITY_STATES else "other_capability_state"


def shared_owner_category(owner: str | None) -> str | None:
    """Never emit raw shared-owner identifiers that could carry host/runtime names."""
    if owner is None:
        return None
    return "shared_owner"


def _expected_rule_for_kind(kind: str) -> str:
    if kind == "absent":
        return "absent_answer"
    if kind == "privacy":
        return "safety_refusal"
    return "ranked_positive"


@dataclass(frozen=True)
class TaskDescriptor:
    """Caller-supplied frozen task metadata joined against every slot."""

    task_id: str
    family: str
    kind: str
    grading_rule: str
    task_digests: Mapping[str, str] = field(default_factory=dict)
    arm_expectations: Mapping[str, Mapping[str, Any]] | None = None

    def __post_init__(self) -> None:
        as_safe_id(self.task_id, "task_id")
        refuse(self.family in RATIONALE_FAMILIES, "analysis:task_family")
        refuse(self.kind in CASE_KINDS, "analysis:task_kind")
        refuse(self.grading_rule in GRADING_RULES, "analysis:task_grading_rule")
        if self.kind == "positive":
            refuse(self.family in POSITIVE_FAMILIES, "analysis:positive_family")
            refuse(self.grading_rule == "ranked_positive", "analysis:positive_rule")
        else:
            refuse(self.family == OTHER_FAMILY, "analysis:control_family")
            refuse(
                self.grading_rule == _expected_rule_for_kind(self.kind),
                "analysis:control_grading_rule",
            )
        refuse(
            isinstance(self.task_digests, Mapping)
            and set(self.task_digests) == {"task_sha256", "retained_task_sha256"},
            "analysis:task_digests_shape",
        )
        frozen_digests = {
            str(k): as_digest(v, "task_digest") for k, v in self.task_digests.items()
        }
        object.__setattr__(self, "task_digests", MappingProxyType(frozen_digests))
        if self.arm_expectations is not None:
            refuse(
                isinstance(self.arm_expectations, Mapping)
                and set(self.arm_expectations) == set(ARMS),
                "analysis:arm_expectations_arms",
            )
            frozen_exp: dict[str, Mapping[str, Any]] = {}
            for arm in ARMS:
                raw_exp = self.arm_expectations[arm]
                exp = exact_keys(raw_exp, _EXPECTATION_KEYS, "arm_expectation")
                refuse(exp["execution"] in EXECUTIONS, "analysis:arm_expectation_execution")
                refuse(exp["actual_surface"] in ACTUAL_SURFACES, "analysis:arm_expectation_surface")
                refuse(
                    exp["fallback_policy"] in FALLBACK_POLICIES,
                    "analysis:arm_expectation_fallback",
                )
                refuse(
                    exp["reason"] is None or type(exp["reason"]) is str,
                    "analysis:arm_expectation_reason",
                )
                frozen_exp[arm] = MappingProxyType(dict(exp))
            object.__setattr__(self, "arm_expectations", MappingProxyType(frozen_exp))


@dataclass(frozen=True)
class ExpectedProvenance:
    """Caller-supplied original-study identity; analyzer identity stays separate."""

    expected_raw_sha256: str
    input_pin_sha256: str
    attempt_id: str
    evaluator_closure_sha256: str
    worker_closure_sha256: str
    origin: str = "native"
    predecessor_failure_codes: tuple[str, ...] = ()
    declaration_source_closure_sha256: str | None = None

    def __post_init__(self) -> None:
        as_digest(self.expected_raw_sha256, "expected_raw")
        as_digest(self.input_pin_sha256, "input_pin")
        as_digest(self.evaluator_closure_sha256, "evaluator_closure")
        as_digest(self.worker_closure_sha256, "worker_closure")
        if self.declaration_source_closure_sha256 is not None:
            as_digest(self.declaration_source_closure_sha256, "declaration_closure")
        as_token(self.attempt_id, "attempt_id")
        refuse(self.origin in ORIGINS, "analysis:origin")
        refuse(
            type(self.predecessor_failure_codes) is tuple
            and len(self.predecessor_failure_codes) <= 64,
            "analysis:predecessors",
        )
        for code in self.predecessor_failure_codes:
            as_token(code, "predecessor_failure_code")
        if self.origin == "native":
            refuse(
                self.input_pin_sha256 == REGISTERED_INPUT_PIN_SHA256,
                "analysis:native_input_pin",
            )
            refuse(
                self.worker_closure_sha256 == NATIVE_WORKER_CLOSURE_SHA256,
                "analysis:native_worker_closure",
            )


__all__ = [
    "ANALYSIS_SCHEMA", "AnalysisRefusal", "CAPABILITY_STATES", "COST_RECORD_SCHEMA",
    "COST_STATES", "COST_SUMMARY_SCHEMA", "CUTOFFS", "DECLARED_METRIC_NAMESPACES",
    "DISPLAY_DIGITS", "ELAPSED_OBSERVATIONS", "ExpectedProvenance", "FAILURE_RECEIPT_SCHEMA",
    "FILE_METRICS", "GRADING_STATES", "MATCH_SURFACES", "MAX_BUNDLE_BYTES", "MAX_DEPTH",
    "MAX_LIST", "MAX_OBJECTS", "MAX_OUTPUT_JSON_BYTES", "MAX_REPORT_BYTES", "MAX_STRINGS",
    "MAX_STRING_BYTES", "METRIC_NAMES", "METRIC_ORDER", "NATIVE_SCHEMA", "NATIVE_SLOT_COUNT",
    "NATIVE_TASK_COUNT", "NATIVE_TOKEN_RECORDS", "ORIGINS", "PAIRED_ANALYSIS_SCHEMA",
    "PUBLIC_COST_CENSUS_SCHEMA", "PUBLIC_OBSERVATION_SCHEMA", "REASON_CATEGORIES",
    "REGISTERED_SAMPLE_NAMES", "SLOT_STATES", "SPAN_METRICS", "SUPPLEMENT_REPORT_SCHEMA",
    "SYNTHETIC_SCHEMA", "TaskDescriptor", "as_bool", "as_digest", "as_int", "as_metric",
    "as_safe_id", "as_str", "as_token", "capability_category", "exact_keys", "fail",
    "optional_bool", "reason_category", "refuse", "sample_name_category",
    "shared_owner_category", "subset_keys",
]
