"""Owners for BODY plan identity, projection, and admission joins."""
from __future__ import annotations

import pytest

from retrieval_body_study_contracts import BodyStudyRefusal, BODY_PLAN_SCHEMA, BODY_ROLE
from retrieval_body_study_plan import (
    _validate_snapshot, admission_from_inner_plan, is_inner_body_plan, validate_body_plan,
)


def test_old_role_and_schema_never_enter_body_plan() -> None:
    with pytest.raises(BodyStudyRefusal):
        validate_body_plan({"schema": BODY_PLAN_SCHEMA, "role": "study"})
    assert BODY_ROLE == "body-study"


def test_inner_plan_surface_forbids_private_paths() -> None:
    # The inner validator is intentionally exercised with a malformed object:
    # a real projected plan is produced only after a consumed admission.
    assert is_inner_body_plan({"schema": "cloe-body-study-inner-plan-v1"})
    with pytest.raises(BodyStudyRefusal):
        admission_from_inner_plan({"schema": "cloe-body-study-inner-plan-v1"})


def test_candidate_snapshot_accepts_repo_contained_skill_projection() -> None:
    snapshot = _validate_snapshot({
        ".agents/skills/repo-map-development-standards": {
            "symlink": "../../docs/contrib/skills/repo-map-development-standards"
        }
    })
    assert snapshot[".agents/skills/repo-map-development-standards"]["symlink"].startswith("../")


def test_candidate_snapshot_rejects_symlink_escape() -> None:
    with pytest.raises(BodyStudyRefusal):
        _validate_snapshot({"one/file": {"symlink": "../../outside"}})


def test_body_plan_byte_cap_is_checked_on_mapping_before_admission() -> None:
    from retrieval_body_study_contracts import MAX_PLAN_BYTES
    from common_lore_index_test_support.retrieval_body_study_host_cases import body_plan

    plan = body_plan()
    validate_body_plan(plan)
    plan["derived"]["runtime_config_document"]["oversized"] = "x" * MAX_PLAN_BYTES
    with pytest.raises(BodyStudyRefusal, match="body plan serialized byte cap"):
        validate_body_plan(plan)


def test_tighter_body_plan_cap_does_not_change_existing_offline_cohorts() -> None:
    from offline_qualification_checkout import MAX_PLAN
    from retrieval_body_study_contracts import MAX_PLAN_BYTES

    assert MAX_PLAN_BYTES == 1024**2
    assert MAX_PLAN == 2 * 1024**2
