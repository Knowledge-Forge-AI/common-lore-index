"""Closed BODY plan, authority, and reservation admission guards."""
from __future__ import annotations

import pytest

from retrieval_body_study_contracts import BodyStudyRefusal
from retrieval_body_study_host_admission import validate_authority_record
from retrieval_body_study_plan import validate_body_plan
from retrieval_body_study_reservation import validate_reservation
from repomap_test_support.retrieval_body_study_host_cases import (
    body_plan, copy_case, mutate, reservation_record,
)


def test_complete_inert_body_case_and_authority_records_are_closed() -> None:
    plan, records = copy_case()
    checked = validate_body_plan(plan)
    for role, record in records.items():
        validate_authority_record(role, record, checked)
    validate_reservation(reservation_record(checked))


@pytest.mark.parametrize(("label", "change"), (
    ("schema", lambda value: value.update(schema="cloe-study-plan-v0")),
    ("old-role", lambda value: value.update(role="study")),
    ("selector", lambda value: value.update(selector="old/selector.py")),
    ("snapshot-escape", lambda value: value.update(candidate_snapshot={"../escape.py": {"sha256": "a" * 64, "mode": 0o644}})),
    ("internal-symlink", lambda value: value.update(candidate_snapshot={"pkg/link": {"symlink": "../../outside"}})),
    ("recipe", lambda value: value.update(sdk_recipe_sha256="b" * 64)),
    ("sdk-image", lambda value: value.update(sdk_image="sha256:" + "b" * 64)),
    ("provider", lambda value: value.update(provider_sha256="b" * 64)),
    ("archive", lambda value: value["archive"].update(sha256="b" * 64)),
    ("input-pin", lambda value: value.update(input_pin_sha256="sha256:" + "b" * 64)),
    ("protocol", lambda value: value.update(protocol_sha256="sha256:" + "b" * 64)),
    ("source-scope", lambda value: value["source_members"].append("tools/not-registered.py")),
    ("treatment-scope", lambda value: value["treatment_members"].append("tools/not-registered.py")),
    ("query-text", lambda value: value["declaration"]["query_manifest"]["canonical-node-v1"][0].update(text_sha256="sha256:" + "b" * 64)),
    ("census", lambda value: value["declaration"]["slot_manifest"].pop()),
    ("current-flags", lambda value: value["declaration"].update(execution_authorized=True)),
))
def test_independent_plan_corruptions_refuse_before_actions(label: str, change) -> None:
    plan = mutate(body_plan(), change)
    with pytest.raises(BodyStudyRefusal):
        validate_body_plan(plan)


@pytest.mark.parametrize(("label", "change"), (
    ("receipt-hash", lambda value: value["source_review"].update(independent_review_sha256="bad")),
    ("operator-receipt-join", lambda value: value["operator_selection"].update(decision_packet_sha256="sha256:" + "a" * 64)),
    ("authority-phase", lambda value: value["source_review"].update(phase="OLD")),
    ("operator-current-flag", lambda value: value["operator_selection"].update(
        scientific_heldout_production_main_release_acceptance=True)),
))
def test_authority_corruptions_refuse_before_admission(label: str, change) -> None:
    plan, records = copy_case()
    change(records)
    checked = validate_body_plan(plan)
    with pytest.raises(BodyStudyRefusal):
        operator_case = label in {"operator-receipt-join", "operator-current-flag"}
        role = "operator_selection" if operator_case else "source_review"
        validate_authority_record(role, records[role], checked)


def test_reservation_reuse_and_state_mismatch_refuse() -> None:
    value = reservation_record()
    value["consumed"] = True
    with pytest.raises(BodyStudyRefusal):
        validate_reservation(value)


def test_file_backed_operator_receipt_hash_is_distinct_from_decision_hash(tmp_path):
    from retrieval_body_study_host_admission import read_parent_admission
    from repomap_test_support.retrieval_body_study_host_cases import write_physical_authority_case

    plan, path, digest = write_physical_authority_case(tmp_path / "authority")
    admitted = read_parent_admission(path, expected_sha256=digest, plan=plan)
    assert len(admitted.pins) == 5
    assert admitted.record["operator_selection"]["sha256"] != plan["operator_decision_sha256"]
    charged: list[int] = []
    admitted.require_current(charged.append)
    assert len(charged) == 5 and all(amount > 0 for amount in charged)


def test_file_backed_wrong_decision_is_refused_despite_valid_receipt_bytes(tmp_path):
    from retrieval_body_study_host_admission import read_parent_admission
    from repomap_test_support.retrieval_body_study_host_cases import write_physical_authority_case

    plan, path, digest = write_physical_authority_case(tmp_path / "wrong", wrong_decision=True)
    with pytest.raises(BodyStudyRefusal, match="body operator decision"):
        read_parent_admission(path, expected_sha256=digest, plan=plan)
