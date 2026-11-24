import json
from pathlib import Path
from typing import TypedDict

import pytest

from retrieval_supplement_declaration import ordered_slots
from retrieval_supplement_host_report import validate_exported_study
from retrieval_supplement_study_contracts import INPUT_PIN_SHA256, WORKER_CLOSURE_SHA256
from retrieval_supplement_study_inputs import FrozenStudyInputs, load_study_inputs
from retrieval_supplement_study_preflight import SlotOutcome, analyze_slots, parse_token_evidence
from retrieval_supplement_values import ARMS


class TokenEvidenceRow(TypedDict):
    record_id: str
    token_count: int
    truncated: bool


class WorkerTokenEvidence(TypedDict):
    representation: str
    record_count: int
    token_evidence: list[TokenEvidenceRow]


def worker_tokens(inputs: FrozenStudyInputs) -> list[WorkerTokenEvidence]:
    admitted = [
        task.task_id for task in inputs.catalog.tasks
        if inputs.task_metadata[task.task_id].kind not in {"privacy", "unsupported"}
    ]
    assert len(admitted) == 20
    v1_documents = [f"v1.test-document-{index:03d}" for index in range(66)]
    ptx_documents = [f"ptx.d.{index:04d}" for index in range(1, 41)]
    v1_queries = [inputs.query_target_joins[task_id].v1_query_record_id for task_id in admitted]
    ptx_queries = [inputs.query_target_joins[task_id].v2_query_record_id for task_id in admitted]
    return [
        {"representation": "canonical-node-v1", "record_count": 86, "token_evidence": [
            {"record_id": record_id, "token_count": 4, "truncated": False}
            for record_id in (*v1_documents, *v1_queries)]},
        {"representation": "canonical-node-public-text-v2", "record_count": 60, "token_evidence": [
            {"record_id": record_id, "token_count": 4, "truncated": False}
            for record_id in (*ptx_documents, *ptx_queries)]},
    ]


def study_inputs(charges=None) -> FrozenStudyInputs:
    root = Path(__file__).parents[5]
    return load_study_inputs(root, (lambda count: None) if charges is None else charges)


def bundle(inputs: FrozenStudyInputs, *, comparable=False):
    slots = ordered_slots(inputs.catalog)
    task_ids = [task.task_id for task in inputs.catalog.tasks]
    by_family: dict[str, list[str]] = {}
    for task_id in task_ids:
        metadata = inputs.task_metadata[task_id]
        if metadata.kind == "positive":
            by_family.setdefault(metadata.family, []).append(task_id)
    comparable_ids = {task_id for values in by_family.values() for task_id in values[:2]}
    tokens = worker_tokens(inputs)
    v1_documents = [row["record_id"] for row in tokens[0]["token_evidence"][:66]]
    ptx_documents = [row["record_id"] for row in tokens[1]["token_evidence"][:40]]
    rows = []
    for slot in slots:
        metadata = inputs.task_metadata[slot.task_id]
        state = "completed" if comparable and slot.task_id in comparable_ids else "not_run"
        join = inputs.query_target_joins[slot.task_id]
        if state == "completed" and slot.arm in ARMS[3:]:
            query_id = join.v1_query_record_id if slot.arm == ARMS[3] else join.v2_query_record_id
            targets: tuple[str, ...] = (v1_documents[0],) if slot.arm == ARMS[3] else (ptx_documents[0],)
        else:
            query_id, targets = None, ()
        rows.append(SlotOutcome(
            slot.task_id, metadata.family, metadata.kind, slot.arm, slot.repetition,
            state, query_id, targets,
        ).to_dict())
    paired = analyze_slots([SlotOutcome(**row) for row in rows],
        evidence=parse_token_evidence(tokens, expected_total=146),
        ordered_task_ids=task_ids)
    nested = []
    for row, slot in zip(rows, slots, strict=True):
        metadata = inputs.task_metadata[slot.task_id]
        task = next(task for task in inputs.catalog.tasks if task.task_id == slot.task_id)
        nested.append({
            "slot": slot.to_dict(),
            "task_digests": dict(inputs.task_digests[slot.task_id]),
            "delivery_kind": "external_unqualified",
            "execution_state": row["state"], "reason": row["reason"],
            "expected_execution_state": row["state"], "surface_observation": {},
            "expected_surface": None, "expected_fallback_policy": "none",
            "surface_expectation_matches": True, "fallback_expectation_matches": True,
            "capability_expectation_matches": True,
            "grading": {"task_id": slot.task_id, "family": metadata.family,
                        "grading_rule": task.grading_rule},
            "capture_reference_sha256": "sha256:" + "a" * 64,
        })
    report = {"slot_count": 432, "input_pin_sha256": INPUT_PIN_SHA256,
        "slots": nested, "native_acceptance": False, "scientific_acceptance": False,
        "held_out_acceptance": False, "production_acceptance": False}
    manifest = [slot.to_dict() for slot in slots]
    return {"schema": "cloe-supplement-study-bundle-v1", "attempt_id": "public-study-1",
        "functional_only": False, "input_pin_sha256": INPUT_PIN_SHA256,
        "evaluator_closure_sha256": "sha256:" + "a" * 64,
        "worker_closure_sha256": WORKER_CLOSURE_SHA256,
        "scientific_acceptance_claimed": False, "paired_analysis": paired,
        "supplement_report": report, "plan_manifest": manifest}


def write_bundle(root: Path, value):
    destination = root / "supplement-study"
    destination.mkdir(mode=0o700, exist_ok=True)
    (destination / "supplement-study-report-v1.json").write_text(json.dumps(value), encoding="utf-8")


def validate(root: Path, value, inputs: FrozenStudyInputs):
    write_bundle(root, value)
    charges: list[int] = []
    result = validate_exported_study(root, plan={"attempt_id": "public-study-1",
        "evaluator_closure_sha256": "sha256:" + "a" * 64,
        "declaration": {"slot_manifest": value["plan_manifest"]}}, charge_bytes=charges.append,
        counted_worker_tokens=worker_tokens(inputs), frozen_inputs=inputs)
    assert charges and charges[0] > 0
    return result


def test_complete_retained_grid_below_usefulness_floor_does_not_request_retry(tmp_path):
    inputs = study_inputs()
    result = validate(tmp_path, bundle(inputs), inputs)
    assert result["slots"] == 432 and result["token_records"] == 146
    assert result["floor_met"] is False and result["scientific_acceptance_claimed"] is False
    assert result["state_counts"]["not_run"] == 432
    assert "retry" not in result


@pytest.mark.parametrize("field", ["attempt_id", "input_pin_sha256", "evaluator_closure_sha256", "worker_closure_sha256"])
def test_foreign_identity_cannot_join_current_attempt(tmp_path, field):
    inputs = study_inputs()
    value = bundle(inputs)
    value[field] = "foreign"
    with pytest.raises(ValueError, match="identity_or_origin"):
        validate(tmp_path, value, inputs)


@pytest.mark.parametrize("mutation", ["synthetic", "missing", "duplicate", "state_count", "token_count",
    "scientific", "heldout", "false_floor", "duplicate_positive", "foreign_family"])
def test_missing_false_or_foreign_evidence_is_rejected(tmp_path, mutation):
    inputs = study_inputs()
    value = bundle(inputs)
    paired = value["paired_analysis"]
    if mutation == "synthetic": value["functional_only"] = True
    elif mutation == "missing": paired["raw_slots"].pop()
    elif mutation == "duplicate": paired["raw_slots"][-1] = paired["raw_slots"][0]
    elif mutation == "state_count": paired["state_counts"]["not_run"] = 431
    elif mutation == "token_count": paired["token_evidence_count"] = 145
    elif mutation == "scientific": value["scientific_acceptance_claimed"] = True
    elif mutation == "heldout": paired["heldout_acceptance_claimed"] = True
    elif mutation == "false_floor": paired.update(floor_met=True, criteria_proposal_allowed=True)
    elif mutation == "duplicate_positive": paired["comparable_positive_tasks"] = ["same", "same"]
    else: paired["family_counts"]["foreign"] = 1
    with pytest.raises((ValueError, KeyError)):
        validate(tmp_path, value, inputs)


@pytest.mark.parametrize("mutation", ["wrong_task", "generation", "digest", "family", "state"])
def test_catalog_slot_and_nested_state_joins_are_exact(tmp_path, mutation):
    inputs = study_inputs()
    value = bundle(inputs, comparable=True)
    if mutation == "wrong_task":
        value["paired_analysis"]["raw_slots"][0]["task_id"] = "sup1-24"
    elif mutation == "generation":
        value["supplement_report"]["slots"][0]["slot"]["generation"] = 2
    elif mutation == "digest":
        value["supplement_report"]["slots"][0]["task_digests"]["task_sha256"] = "sha256:" + "b" * 64
    elif mutation == "family":
        value["paired_analysis"]["raw_slots"][0]["family"] = "foreign"
    else:
        value["supplement_report"]["slots"][0]["execution_state"] = "not_run"
    with pytest.raises(ValueError):
        validate(tmp_path, value, inputs)


def test_counted_family_floor_only_enables_prospective_operator_review(tmp_path):
    inputs = study_inputs()
    result = validate(tmp_path, bundle(inputs, comparable=True), inputs)
    assert result["floor_met"] is True
    assert result["scientific_acceptance_claimed"] is False


def test_symlink_report_member_is_never_consumed(tmp_path):
    inputs = study_inputs()
    write_bundle(tmp_path, bundle(inputs))
    path = tmp_path / "supplement-study/supplement-study-report-v1.json"
    retained = path.with_suffix(".retained")
    path.replace(retained)
    path.symlink_to(retained)
    with pytest.raises(ValueError):
        validate_exported_study(tmp_path, plan={}, charge_bytes=lambda count: None,
            counted_worker_tokens=worker_tokens(inputs), frozen_inputs=inputs)


def test_false_floor_over_unstarted_rows_and_truncated_workers_is_blocked(tmp_path):
    inputs = study_inputs()
    value = bundle(inputs)
    paired = value["paired_analysis"]
    paired.update(comparable_positive_tasks=[task.task_id for task in inputs.catalog.tasks[:8]],
        comparable_positive_count=8, family_counts=dict.fromkeys(inputs.task_metadata[task.task_id].family
            for task in inputs.catalog.tasks if inputs.task_metadata[task.task_id].kind == "positive"),
        floor_met=True, criteria_proposal_allowed=True)
    with pytest.raises((ValueError, KeyError)):
        validate(tmp_path, value, inputs)
    value = bundle(inputs, comparable=True)
    write_bundle(tmp_path, value)
    tokens = worker_tokens(inputs)
    tokens[0]["token_evidence"][0]["truncated"] = True
    with pytest.raises(ValueError, match="counted_token_analysis_join"):
        validate_exported_study(tmp_path, plan={"attempt_id": "public-study-1",
            "evaluator_closure_sha256": "sha256:" + "a" * 64,
            "declaration": {"slot_manifest": value["plan_manifest"]}}, charge_bytes=lambda count: None,
            counted_worker_tokens=tokens, frozen_inputs=inputs)
