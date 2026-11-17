"""Preparation-only body report formatting and complete planned-slot joins."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any

from retrieval_body_admission import BodyTaskAdmission
from retrieval_body_catalog import BodyCatalog
from retrieval_body_record_types import BodyPreparationDecision
from retrieval_body_values import ARMS, MAX_CASES
from retrieval_semantic_generator_inputs import INPUT_POLICY
from retrieval_supplement_tasks import task_digests

REPETITIONS = 3
PREPARATION_ROW_COUNT = MAX_CASES * len(ARMS)
PLANNED_SLOT_COUNT = PREPARATION_ROW_COUNT * REPETITIONS
_REPRESENTATIONS = {
    ARMS[0]: "native-tools-baseline", ARMS[1]: "current-graph-baseline:postgresql",
    ARMS[2]: "postgres-native-fts:postgresql", ARMS[3]: "canonical-node-v1",
    ARMS[4]: "canonical-node-public-text-v2", ARMS[5]: "canonical-node-public-text-v2",
}
_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_FALSE_FLAGS = {
    "model_accepted": False, "study_accepted": False, "tokenizer_observed": False,
    "tokenizer_accepted": False, "scientific_acceptance": False, "heldout_acceptance": False,
    "held_out_acceptance": False, "production_acceptance": False, "model_executed": False,
}


class BodyReportError(ValueError):
    """A public-safe report validation refusal."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise BodyReportError(code)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(v) for v in value]
    return value


def _digest(value: Any) -> str:
    try:
        raw = json.dumps(_thaw(value), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as exc:
        raise BodyReportError("decision_identity") from exc
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _state(row: Mapping[str, Any]) -> tuple[str, str | None, str, str]:
    state, reason = row.get("preparation"), row.get("reason")
    surface, fallback = row.get("actual_surface"), row.get("fallback_policy")
    _require(type(state) is str and state in {"ready", "refused", "unavailable"}, "preparation_state")
    if state == "ready":
        _require(reason is None and type(surface) is str and surface != "none", "ready_fields")
        _require(fallback in {"none", "required", "record_actual"}, "ready_fallback")
    else:
        _require(type(reason) is str and bool(reason) and surface == "none" and fallback == "none", "nonready_fields")
    assert isinstance(state, str) and isinstance(surface, str) and isinstance(fallback, str)
    assert reason is None or isinstance(reason, str)
    return state, reason, surface, fallback


def _reject_execution_claims(row: Mapping[str, Any]) -> None:
    for name in (*_FALSE_FLAGS, "candidate_access_performed", "execution_authority_granted", "model_executed",
                 "acceptance_claimed", "native_acceptance", "held_out_acceptance"):
        if name in row:
            _require(row[name] is False, "execution_claim")
    if "execution_state" in row:
        _require(row["execution_state"] == "not_executed", "execution_state")


def _decision_mapping(value: object) -> tuple[Mapping[str, Any], str]:
    if type(value) is BodyTaskAdmission:
        row = value.to_dict()
        return row, value.identity
    if type(value) is BodyPreparationDecision:
        row = value.to_dict()
        return row, str(getattr(value, "admission_identity", "supplied-decision"))
    _require(isinstance(value, Mapping), "decision_mapping")
    assert isinstance(value, Mapping)
    row = dict(value)
    identity = row.get("admission_identity")
    return row, identity if type(identity) is str and identity else "mapping:" + _digest(row)


def _catalog_manifest(catalog: BodyCatalog) -> tuple[dict[str, Any], ...]:
    _require(type(catalog) is BodyCatalog, "catalog_type")
    cases = tuple(catalog.cases)
    _require(len(cases) == MAX_CASES, "catalog_task_count")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for case in cases:
        task = case.task
        _require(task.task_id not in seen, "catalog_duplicate_task")
        seen.add(task.task_id)
        digests = task_digests(task)
        raw = case.to_dict()
        result.append({"case": case, "task": task, "case_kind": case.kind,
                       "intent_id": raw["intent_id"], "wording_stratum": raw["wording_stratum"],
                       "family": task.family, "case_sha256": case.digest,
                       "task_sha256": digests["task_sha256"],
                       "retained_task_sha256": digests["retained_task_sha256"]})
    _require(Counter(item["case_kind"] for item in result) ==
             Counter({"body_positive": 12, "metadata_anchor": 4, "absent": 2,
                      "ambiguous": 2, "privacy": 2, "unsupported": 2}), "catalog_role_matrix")
    body = [item for item in result if item["case_kind"] == "body_positive"]
    _require(all(Counter(item["wording_stratum"] for item in body if item["family"] == family) ==
                 Counter({"literal_clue": 2, "paraphrased_intent": 2})
                 for family in ("behavior_without_exact_symbol", "code_plus_design_or_message_context",
                                "relevant_tests_for_behavior")), "catalog_wording_matrix")
    return tuple(result)


def _normal_row(row: Mapping[str, Any], meta: Mapping[str, Any], catalog: BodyCatalog,
                identity: str, frame: dict[str, Any]) -> dict[str, Any]:
    _reject_execution_claims(row)
    _require(row.get("class_id") == frame["class_id"], "row_class")
    _require(row.get("catalog_sha256") == catalog.digest, "row_catalog_digest")
    _require(row.get("case_sha256") == meta["case_sha256"], "row_case_digest")
    _require(row.get("task_id") == meta["task"].task_id, "row_task_id")
    _require(row.get("task_sha256") == meta["task_sha256"] and
             row.get("retained_task_sha256") == meta["retained_task_sha256"], "row_task_digest")
    _require(type(row.get("input_pin_sha256")) is str and _DIGEST.fullmatch(row["input_pin_sha256"]) is not None,
             "row_input_pin")
    pin = row["input_pin_sha256"]
    if frame.get("input_pin_sha256") is None:
        frame["input_pin_sha256"] = pin
    _require(pin == frame["input_pin_sha256"], "mixed_input_pin")
    task = meta["task"]
    generation = row.get("generation", task.generation)
    _require(type(generation) is int and generation == task.generation, "row_generation")
    arm = row.get("arm")
    _require(type(arm) is str and arm in ARMS, "row_arm")
    assert isinstance(arm, str)
    state, reason, surface, fallback = _state(row)
    publication = row.get("publication_identity")
    if publication is not None:
        _require(isinstance(publication, Mapping), "row_publication")
        publication = _thaw(publication)
        digest = _digest(publication)
        if frame.get("publication_digest") is None:
            frame["publication_digest"], frame["publication_identity"] = digest, publication
        _require(digest == frame["publication_digest"], "mixed_publication")
    else:
        _require(state == "refused", "row_publication_required")
    representation = row.get("representation_identity", _REPRESENTATIONS[arm])
    _require(representation == _REPRESENTATIONS[arm], "row_representation")
    return {"class_id": frame["class_id"], "catalog_sha256": catalog.digest,
            "input_pin_sha256": pin, "case_sha256": meta["case_sha256"],
            "task_id": task.task_id, "task_sha256": meta["task_sha256"],
            "retained_task_sha256": meta["retained_task_sha256"], "generation": generation,
            "arm": arm, "case_kind": meta["case_kind"], "family": meta["family"],
            "intent_id": meta["intent_id"], "wording_stratum": meta["wording_stratum"],
            "stratum": "body" if meta["case_kind"] == "body_positive" else
            "anchor" if meta["case_kind"] == "metadata_anchor" else "control",
            "preparation": state, "reason": reason, "actual_surface": surface,
            "fallback_policy": fallback, "admission_identity": identity,
            "publication_identity": publication, "representation_identity": representation}


def _preparation_rows(
    catalog: BodyCatalog, decisions: Sequence[object]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = _catalog_manifest(catalog)
    _require(isinstance(decisions, Sequence) and not isinstance(decisions, (str, bytes, bytearray)),
             "decisions_sequence")
    _require(len(decisions) == PREPARATION_ROW_COUNT, "preparation_row_count")
    frame: dict[str, Any] = {"class_id": catalog.identity.class_id, "input_pin_sha256": None,
                             "publication_digest": None, "publication_identity": None}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    by_task = {item["task"].task_id: item for item in manifest}
    for value in decisions:
        row, identity = _decision_mapping(value)
        task_id, arm = row.get("task_id"), row.get("arm")
        _require(type(task_id) is str and type(arm) is str, "row_identity_types")
        assert isinstance(task_id, str) and isinstance(arm, str)
        meta = by_task.get(task_id)
        _require(meta is not None, "row_task_membership")
        assert meta is not None
        _require((task_id, arm) not in seen, "duplicate_preparation_row")
        seen.add((task_id, arm))
        rows.append(_normal_row(row, meta, catalog, identity, frame))
    expected = {(item["task"].task_id, arm) for item in manifest for arm in ARMS}
    _require(seen == expected, "preparation_row_completeness")
    rows.sort(key=lambda row: (row["task_id"].encode("utf-8"), ARMS.index(row["arm"])))
    return rows, frame


def build_body_report_rows(catalog: BodyCatalog, decisions: Sequence[object]) -> list[dict[str, Any]]:
    """Validate and return the complete 24-by-6 preparation row set."""
    return _preparation_rows(catalog, decisions)[0]


def _summary(rows: Sequence[Mapping[str, Any]], kind: str) -> dict[str, Any]:
    selected = [row for row in rows if row["stratum"] == kind]
    tasks = {row["task_id"]: row for row in selected}.values()
    return {"task_count": len(tasks), "slot_count": len(selected) * REPETITIONS,
            "family_counts": dict(Counter(row["family"] for row in tasks)),
            "wording_counts": dict(Counter(row["wording_stratum"] for row in tasks))}


def build_body_report(catalog: BodyCatalog, decisions: Sequence[object]) -> dict[str, Any]:
    """Build preparation rows and every planned repetition slot, never execution evidence."""
    rows, frame = _preparation_rows(catalog, decisions)
    slots: list[dict[str, Any]] = []
    for row in rows:
        for repetition in range(1, REPETITIONS + 1):
            slot = dict(row)
            slot.update({"repetition": repetition, "execution_state": "not_executed",
                         "refused_preparation_is_execution_evidence": False,
                         "execution_refusal_evidence": False, **_FALSE_FLAGS})
            slots.append(slot)
    _require(len(rows) == PREPARATION_ROW_COUNT and len(slots) == PLANNED_SLOT_COUNT, "report_counts")
    body, anchor, control = _summary(rows, "body"), _summary(rows, "anchor"), _summary(rows, "control")
    body_floor = {"planned_task_count": body["task_count"], "minimum_distinct_intents": 8,
                  "minimum_per_family": 2, "family_counts": body["family_counts"], "pooled_with_anchors": False}
    anchor_floor = {"planned_task_count": anchor["task_count"], "pooled_with_body": False}
    return {"schema": "cloe-body-report-v1", "class_id": frame["class_id"],
            "catalog_sha256": catalog.digest, "input_pin_sha256": frame["input_pin_sha256"],
            "publication_identity": frame["publication_identity"], "input_policy": INPUT_POLICY,
            "preparation_rows": rows, "rows": rows, "planned_slots": slots, "slots": slots,
            "preparation_row_count": len(rows), "planned_slot_count": len(slots), "task_count": MAX_CASES,
            "arm_count": len(ARMS), "repetition_count": REPETITIONS, "body": body,
            "metadata_anchors": anchor, "controls": control, "body_floor": body_floor,
            "anchor_floor": anchor_floor, "body_anchor_pooling": False,
            "repetition_pooling": False, "embedding_count": {"value": None, "status": "PLANNED",
            "basis": "record partitions not supplied; repetitions reuse query embeddings"},
            "embedding_count_planned": None,
            "tokenization_observed": False, "token_counts": "unobserved", "truncation": "unobserved",
            "runtime_costs": "unobserved", "origin_proven": False,
            "origin_note": "raw decisions do not prove factory origin; controller join required",
            "controller_admission_join_required": True, "execution_state": "not_executed",
            "refused_preparation_is_execution_evidence": False,
            "execution_refusal_evidence": False,
            **_FALSE_FLAGS}


build_body_report_bundle = build_body_report

__all__ = ["BodyReportError", "PREPARATION_ROW_COUNT", "PLANNED_SLOT_COUNT", "build_body_report",
           "build_body_report_bundle", "build_body_report_rows"]
