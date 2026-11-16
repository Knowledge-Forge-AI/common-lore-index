"""Frozen-grid target/token joins and truncation exclusion remain independent gates."""
from __future__ import annotations
import pytest
from retrieval_supplement_study_contracts import StudyRefusal
from retrieval_supplement_study_preflight import SlotOutcome,TokenEvidence,analyze_slots,parse_token_evidence,require_store_joins
from retrieval_supplement_values import ARMS,POSITIVE_FAMILIES

def _outcomes(*, truncated_task: str | None = None):
    outcomes, evidence = [], {}
    for index in range(24):
        task = f"task-{index}"
        kind = "positive" if index < 16 else "absent"
        family = POSITIVE_FAMILIES[index % 4] if kind == "positive" else "absent_ambiguous_changed_deleted_stale"
        for arm in ARMS:
            for rep in (1, 2, 3):
                outcomes.append(SlotOutcome(
                    task, family, kind, arm, rep, "completed", f"q:{task}", (f"d:{task}",),
                ))
        evidence[f"q:{task}"] = TokenEvidence(f"q:{task}", 10, task == truncated_task, "v")
        evidence[f"d:{task}"] = TokenEvidence(f"d:{task}", 10, False, "v")
    return outcomes, evidence


def test_paired_floor_joins_and_truncation_exclusion() -> None:
    tasks = [f"task-{index}" for index in range(24)]
    outcomes, evidence = _outcomes()
    result = analyze_slots(outcomes, evidence=evidence, ordered_task_ids=tasks)
    assert result["slot_count"] == 432 and result["floor_met"] is True
    assert len(result["raw_slots"]) == 432 and len(result["paired_tasks"]) == 24
    assert result["scientific_acceptance_claimed"] is False

    outcomes_trunc, evidence_trunc = _outcomes(truncated_task="task-0")
    res_trunc = analyze_slots(outcomes_trunc, evidence=evidence_trunc, ordered_task_ids=tasks)
    assert "task-0" not in res_trunc["comparable_positive_tasks"]

    # None query_record_id on a completed vector slot refuses (A4).
    bad = list(outcomes)
    idx_vec = next(i for i, o in enumerate(bad) if o.arm == ARMS[3])
    bad[idx_vec] = SlotOutcome(
        bad[idx_vec].task_id, bad[idx_vec].family, bad[idx_vec].kind,
        bad[idx_vec].arm, bad[idx_vec].repetition, "completed", None, (),
    )
    with pytest.raises(StudyRefusal, match="missing_query_record_id"):
        analyze_slots(bad, evidence=evidence, ordered_task_ids=tasks)


def test_store_joins_require_all_positives_and_token_identities() -> None:
    tasks = [f"task-{index}" for index in range(24)]
    positives = tasks[:16]
    nodes = {f"gold-{i}": {"publication_identity": "pub", "span_start": 1, "span_end": 2}
             for i in range(16)}
    golds = {f"task-{i}": [f"gold-{i}"] for i in range(16)}
    tok = parse_token_evidence([{"representation": "v1", "token_evidence": [
        {"record_id": "r1", "token_count": 3, "truncated": False},
    ]}], expected_total=1)
    require_store_joins(
        ordered_task_ids=tasks, gold_targets=golds, accepted_nodes=nodes,
        publication_identity="pub", expected_publication_identity="pub",
        positive_task_ids=positives, expected_token_record_ids=["r1"], token_evidence=tok,
    )
    missing_pos = dict(golds)
    missing_pos.pop("task-0")
    with pytest.raises(StudyRefusal, match="positive_task_missing_gold"):
        require_store_joins(
            ordered_task_ids=tasks, gold_targets=missing_pos, accepted_nodes=nodes,
            publication_identity="pub", expected_publication_identity="pub",
            positive_task_ids=positives,
        )
    with pytest.raises(StudyRefusal, match="token_identity_join_mismatch"):
        require_store_joins(
            ordered_task_ids=tasks, gold_targets=golds, accepted_nodes=nodes,
            publication_identity="pub", expected_publication_identity="pub",
            positive_task_ids=positives, expected_token_record_ids=["r1", "r2"],
            token_evidence=tok,
        )
