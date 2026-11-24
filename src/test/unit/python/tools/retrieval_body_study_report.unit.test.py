"""Unit ownership for actual report identity and scientific/non-scientific flags."""
from __future__ import annotations

import pytest

from retrieval_body_study_report import validate_actual_body_report


def test_report_rejects_inert_origin_claim() -> None:
    with pytest.raises(ValueError, match="body.report:origin"):
        validate_actual_body_report({
            "schema": "cloe-body-study-actual-report-v1",
            "phase": "CLOE-W5-BODY-STUDY1",
            "origin_proven": False,
            "scientific_acceptance": False,
            "slot_count": 432,
            "slots": (),
            "evaluation": {"outcome": "signal_not_demonstrated"},
        })


def test_report_requires_all_slots_even_for_a_negative_result() -> None:
    report = {
        "schema": "cloe-body-study-actual-report-v1",
        "phase": "CLOE-W5-BODY-STUDY1",
        "origin_proven": True,
        "scientific_acceptance": False,
        "slot_count": 432,
        "slots": (),
        "evaluation": {"outcome": "signal_not_demonstrated"},
    }
    with pytest.raises(ValueError, match="body.report:slots"):
        validate_actual_body_report(report)
