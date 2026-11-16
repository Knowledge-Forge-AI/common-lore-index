"""Strict bounded admission of one retained supplement study bundle.

Caller-supplied hash/pin/identities/descriptors join raw+nested slots, grades,
expectation matches, counted token evidence, denominators and phase-cost scopes
exactly. Public projections, failure receipts and synthetic bundles can never be
admitted as native.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from typing import Any, NoReturn

from retrieval_supplement_analysis_joins import (
    AdmittedBundle as AdmittedBundle,
    CostObservation as CostObservation,
    SlotRecord as SlotRecord,
    TokenRecordEvidence as TokenRecordEvidence,
    parse_counted_token_evidence,
    validate_bundle_costs,
)
from retrieval_supplement_analysis_slots import validate_slot_grid
from retrieval_supplement_analysis_values import (
    FAILURE_RECEIPT_SCHEMA,
    MAX_BUNDLE_BYTES,
    MAX_DEPTH,
    MAX_LIST,
    MAX_OBJECTS,
    MAX_STRING_BYTES,
    MAX_STRINGS,
    NATIVE_SCHEMA,
    NATIVE_SLOT_COUNT,
    NATIVE_TASK_COUNT,
    NATIVE_TOKEN_RECORDS,
    PAIRED_ANALYSIS_SCHEMA,
    PUBLIC_COST_CENSUS_SCHEMA,
    PUBLIC_OBSERVATION_SCHEMA,
    SUPPLEMENT_REPORT_SCHEMA,
    SYNTHETIC_SCHEMA,
    AnalysisRefusal,
    ExpectedProvenance,
    TaskDescriptor,
    as_bool,
    as_digest,
    as_int,
    exact_keys,
    fail,
    refuse,
    subset_keys,
)
from retrieval_supplement_primitives import ARMS, REGISTERED_PREFIX, digest_bytes

_TOP_REQUIRED = frozenset(
    "schema attempt_id functional_only input_pin_sha256 declaration_source_closure_sha256 "
    "evaluator_closure_sha256 worker_closure_sha256 worker_identities observed_slot_count "
    "token_record_count candidate_bytes byte_forecast inner_evidence supplement_report "
    "paired_analysis scientific_acceptance_claimed held_out_acceptance_claimed "
    "production_acceptance_claimed".split()
)
_TOP_KNOWN = _TOP_REQUIRED | frozenset({"cleanup"})
_REPORT_KNOWN = frozenset(
    "schema declaration_sha256 supplement_id input_pin_sha256 freeze_sha256 source_closure_sha256 "
    "slot_count slot_set_sha256 slots arm_summaries phase_costs phase_presence_only unobserved_phases "
    "phase_presence_is_not_per_slot_cost_coverage cost_completeness repetitions_are_independent_samples "
    "predecessor_pooling native_acceptance scientific_acceptance held_out_acceptance "
    "production_acceptance".split()
)
_REPORT_REQUIRED = frozenset(
    "schema slot_count input_pin_sha256 slots phase_costs cost_completeness "
    "phase_presence_is_not_per_slot_cost_coverage native_acceptance scientific_acceptance "
    "held_out_acceptance production_acceptance".split()
)
_PAIRED_KNOWN = frozenset(
    "schema slot_count task_count arms repetitions state_counts control_breakdown "
    "comparable_positive_tasks comparable_positive_count family_counts positive_floor family_floor "
    "floor_met criteria_proposal_allowed prospective_criteria raw_slots paired_tasks "
    "token_evidence_count expected_full_study_token_records cost_summary scientific_acceptance_claimed "
    "heldout_acceptance_claimed full_w5_acceptance_claimed production_acceptance_claimed "
    "incomplete_attempt_retained technical_incomplete_or_failed".split()
)
_PAIRED_REQUIRED = frozenset(
    "schema slot_count task_count state_counts raw_slots paired_tasks token_evidence_count "
    "comparable_positive_tasks comparable_positive_count family_counts floor_met "
    "criteria_proposal_allowed cost_summary scientific_acceptance_claimed heldout_acceptance_claimed "
    "full_w5_acceptance_claimed production_acceptance_claimed".split()
)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        refuse(key not in out, "analysis:json_duplicate_key")
        out[key] = value
    return out


def _reject_constant(_token: str) -> NoReturn:
    fail("analysis:json_nonfinite_number")


def _finite(token: str) -> float:
    value = float(token)
    refuse(math.isfinite(value), "analysis:json_nonfinite_number")
    return value


def _walk(value: Any, depth: int, counts: list[int]) -> None:
    refuse(depth <= MAX_DEPTH, "analysis:json_too_deep")
    if isinstance(value, dict):
        counts[0] += 1
        refuse(counts[0] <= MAX_OBJECTS, "analysis:json_too_many_objects")
        refuse(len(value) <= MAX_LIST, "analysis:json_object_too_large")
        for key, item in value.items():
            _walk(key, depth + 1, counts)
            _walk(item, depth + 1, counts)
    elif isinstance(value, list):
        refuse(len(value) <= MAX_LIST, "analysis:json_list_too_large")
        for item in value:
            _walk(item, depth + 1, counts)
    elif type(value) is str:
        counts[1] += 1
        refuse(counts[1] <= MAX_STRINGS, "analysis:json_too_many_strings")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            fail("analysis:json_invalid_unicode")
        refuse(size <= MAX_STRING_BYTES, "analysis:json_string_too_long")


def parse_bounded_json(raw: bytes) -> Any:
    """Strict JSON: bounded bytes/depth/objects/strings, no duplicates/non-finite."""
    refuse(type(raw) is bytes, "analysis:bytes_required")
    refuse(0 < len(raw) <= MAX_BUNDLE_BYTES, "analysis:bundle_too_large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        fail("analysis:json_invalid_utf8")
    refuse(not text.startswith("\ufeff"), "analysis:json_bom_forbidden")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_no_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite,
        )
    except AnalysisRefusal:
        raise
    except (ValueError, RecursionError):
        fail("analysis:json_malformed")
    _walk(value, 0, [0, 0])
    return value


def _check_native_workers_and_cleanup(value: dict[str, Any], *, native: bool) -> None:
    workers = value["worker_identities"]
    refuse(type(workers) is list and len(workers) <= 16, "analysis:worker_identities_shape")
    if native:
        refuse(len(workers) == 2, "analysis:native_worker_identities_count")
        reps = [w.get("representation") for w in workers if isinstance(w, dict)]
        refuse(
            reps == ["canonical-node-v1", "canonical-node-public-text-v2"]
            and all(isinstance(w, dict) and w.get("response_received") is True for w in workers),
            "analysis:native_worker_identities",
        )
    if "cleanup" in value:
        cleanup = exact_keys(
            value["cleanup"],
            frozenset({"relations_clean", "container_absent", "receipt"}),
            "cleanup",
        )
        rel_ok = as_bool(cleanup["relations_clean"], "relations_clean")
        abs_ok = as_bool(cleanup["container_absent"], "container_absent")
        refuse(cleanup["receipt"] is None or isinstance(cleanup["receipt"], dict),
               "analysis:cleanup_receipt_shape")
        if native:
            refuse(rel_ok is True and abs_ok is True, "analysis:native_cleanup_incomplete")


def admit_bundle(
    raw: bytes,
    *,
    provenance: ExpectedProvenance,
    tasks: Sequence[TaskDescriptor],
    counted_worker_tokens: Sequence[Mapping[str, Any]] | None = None,
) -> AdmittedBundle:
    """Admit one hash-bound bundle; every forged schema, join, token or count refuses."""
    refuse(type(provenance) is ExpectedProvenance, "analysis:provenance_required")
    refuse(
        type(raw) is bytes and digest_bytes(raw) == provenance.expected_raw_sha256,
        "analysis:raw_digest_mismatch",
    )
    descriptors = tuple(tasks)
    refuse(
        bool(descriptors) and all(type(t) is TaskDescriptor for t in descriptors),
        "analysis:tasks_required",
    )
    refuse(len({t.task_id for t in descriptors}) == len(descriptors), "analysis:duplicate_task")
    native = provenance.origin == "native"
    if native:
        refuse(len(descriptors) == NATIVE_TASK_COUNT, "analysis:native_task_count")
        refuse(
            all(t.task_id.startswith(REGISTERED_PREFIX) for t in descriptors),
            "analysis:native_task_prefix",
        )
    else:
        refuse(
            all(not t.task_id.startswith(REGISTERED_PREFIX) for t in descriptors),
            "analysis:synthetic_reserved_task_prefix",
        )

    parsed = parse_bounded_json(raw)
    if isinstance(parsed, dict):
        top_schema = parsed.get("schema")
        if top_schema in {PUBLIC_OBSERVATION_SCHEMA, PUBLIC_COST_CENSUS_SCHEMA}:
            fail("analysis:public_projection_not_native_bundle")
        if top_schema == FAILURE_RECEIPT_SCHEMA:
            fail("analysis:failure_receipt_not_completed_bundle")
        pa_raw = parsed.get("paired_analysis")
        if isinstance(pa_raw, dict) and isinstance(pa_raw.get("cost_summary"), dict):
            if pa_raw["cost_summary"].get("schema") == PUBLIC_COST_CENSUS_SCHEMA:
                fail("analysis:public_projection_not_native_bundle")

    value = subset_keys(parsed, _TOP_KNOWN, _TOP_REQUIRED, "bundle")
    refuse(
        value["schema"] == (NATIVE_SCHEMA if native else SYNTHETIC_SCHEMA),
        "analysis:bundle_schema_or_origin",
    )
    decl_closure = as_digest(value["declaration_source_closure_sha256"], "declaration_closure")
    if provenance.declaration_source_closure_sha256 is not None:
        refuse(
            decl_closure == provenance.declaration_source_closure_sha256,
            "analysis:declaration_closure_mismatch",
        )
    refuse(
        value["attempt_id"] == provenance.attempt_id
        and value["input_pin_sha256"] == provenance.input_pin_sha256
        and value["evaluator_closure_sha256"] == provenance.evaluator_closure_sha256
        and value["worker_closure_sha256"] == provenance.worker_closure_sha256,
        "analysis:provenance_identity",
    )
    refuse(
        as_bool(value["functional_only"], "functional_only") is (not native),
        "analysis:functional_only_origin",
    )
    for flag in (
        "scientific_acceptance_claimed",
        "held_out_acceptance_claimed",
        "production_acceptance_claimed",
    ):
        refuse(value[flag] is False, "analysis:acceptance_claim")
    as_int(value["candidate_bytes"], "candidate_bytes", 0, 1024**3)
    refuse(
        isinstance(value["byte_forecast"], dict) and isinstance(value["inner_evidence"], dict),
        "analysis:forecast_or_inner_evidence_shape",
    )
    _check_native_workers_and_cleanup(value, native=native)

    slot_total = len(descriptors) * len(ARMS) * 3
    if native:
        refuse(slot_total == NATIVE_SLOT_COUNT, "analysis:native_slot_count")
    refuse(
        as_int(value["observed_slot_count"], "observed_slot_count", 0, 100000) == slot_total,
        "analysis:observed_slot_count",
    )
    token_count = as_int(value["token_record_count"], "token_record_count", 0, 100000)
    if native:
        refuse(token_count == NATIVE_TOKEN_RECORDS, "analysis:native_token_records")
    token_map = (
        None
        if counted_worker_tokens is None
        else parse_counted_token_evidence(
            counted_worker_tokens, native=native, expected_total=token_count
        )
    )

    report = subset_keys(value["supplement_report"], _REPORT_KNOWN, _REPORT_REQUIRED, "report")
    paired = subset_keys(value["paired_analysis"], _PAIRED_KNOWN, _PAIRED_REQUIRED, "paired")
    refuse(report["schema"] == SUPPLEMENT_REPORT_SCHEMA, "analysis:report_schema")
    refuse(paired["schema"] == PAIRED_ANALYSIS_SCHEMA, "analysis:paired_schema")
    refuse(report["input_pin_sha256"] == provenance.input_pin_sha256, "analysis:report_pin")
    if "source_closure_sha256" in report:
        refuse(report["source_closure_sha256"] == decl_closure, "analysis:report_closure_join")
    refuse(
        as_int(report["slot_count"], "report_slot_count", 0, 100000) == slot_total
        and as_int(paired["slot_count"], "paired_slot_count", 0, 100000) == slot_total
        and as_int(paired["task_count"], "paired_task_count", 0, 100000) == len(descriptors)
        and as_int(paired["token_evidence_count"], "token_evidence_count", 0, 100000) == token_count,
        "analysis:section_counts",
    )
    for opt_key, expected_int in (
        ("arms", len(ARMS)),
        ("repetitions", 3),
        ("positive_floor", 8),
        ("family_floor", 2),
        ("expected_full_study_token_records", NATIVE_TOKEN_RECORDS),
    ):
        if opt_key in paired:
            refuse(
                as_int(paired[opt_key], opt_key, 0, 100000) == expected_int,
                f"analysis:paired_{opt_key}",
            )
    for flag in (
        "native_acceptance", "scientific_acceptance", "held_out_acceptance",
        "production_acceptance", "repetitions_are_independent_samples", "predecessor_pooling",
    ):
        if flag in report:
            refuse(report[flag] is False, "analysis:report_acceptance")
    for flag in (
        "scientific_acceptance_claimed", "heldout_acceptance_claimed",
        "full_w5_acceptance_claimed", "production_acceptance_claimed",
    ):
        refuse(paired[flag] is False, "analysis:paired_acceptance")
    refuse(
        report["phase_presence_is_not_per_slot_cost_coverage"] is True,
        "analysis:cost_presence_flag",
    )

    grid = validate_slot_grid(
        descriptors=descriptors,
        report=report,
        paired=paired,
        native=native,
        token_map=token_map,
    )
    costs = validate_bundle_costs(
        report=report,
        paired=paired,
        provenance=provenance,
        declared_slots=grid.declared_slot_keys,
    )
    return AdmittedBundle(
        origin=provenance.origin,
        native_qualified=native,
        raw_sha256=provenance.expected_raw_sha256,
        declaration_source_closure_sha256=decl_closure,
        observed_namespaces=grid.observed_namespaces,
        tasks=descriptors,
        slots=grid.slots,
        state_counts=grid.state_counts,
        task_truncated=grid.task_truncated,
        task_completed_all_arms=grid.task_completed_all_arms,
        comparable_positive_tasks=grid.comparable_positive_tasks,
        family_counts=grid.family_counts,
        floor_met=grid.floor_met,
        costs=costs,
        cost_record_count=len(costs),
        cost_completeness=str(report["cost_completeness"]),
        counted_worker_tokens_verified=token_map is not None,
    )


__all__ = [
    "AdmittedBundle", "CostObservation", "SlotRecord", "TokenRecordEvidence",
    "admit_bundle", "parse_bounded_json",
]
