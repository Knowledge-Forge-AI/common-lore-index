"""Owners for retained negative/low-floor outcomes and complete slot joins."""
from __future__ import annotations

import inspect

import pytest

from retrieval_body_study_contracts import BodyStudyRefusal
from retrieval_body_study_host_report import _validate_slots, validate_actual_body_envelope


def test_actual_report_requires_plan_binding() -> None:
    assert "plan" in inspect.signature(validate_actual_body_envelope).parameters
    assert "cleanup" in inspect.signature(validate_actual_body_envelope).parameters


def test_actual_report_rejects_duplicate_slot_identity() -> None:
    rows = [{"task_id": f"task-{index}", "arm": "native_tools_baseline",
             "repetition": 1, "generation": 1, "state": "failed"}
            for index in range(432)]
    rows[-1] = dict(rows[0])
    with pytest.raises(BodyStudyRefusal):
        _validate_slots(rows)
