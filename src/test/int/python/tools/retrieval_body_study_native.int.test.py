"""Authenticated one-node owner for the complete actual BODY study."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from common_lore_index_test_support.retrieval_body_study import MARKER, require_body_admission
from common_lore_index_test_support.retrieval_body_study_driver import run_authenticated_body_study
from retrieval_body_study_contracts import INNER_BODY_REPORT_DIR, TOTAL_RECORDS
from retrieval_body_study_values import SLOT_COUNT

pytestmark = [pytest.mark.requires_body_study_admission, getattr(pytest.mark, MARKER)]


def test_body_study_actual_attempt() -> None:
    """Run only after the host has consumed the exact BODY inner admission."""
    admission = require_body_admission()
    outcome = run_authenticated_body_study(admission)
    assert outcome.status == "completed"
    assert outcome.token_records == TOTAL_RECORDS == 134
    assert outcome.observed_slots + outcome.unstarted_slots == SLOT_COUNT == 432
    assert outcome.unstarted_slots in {0, SLOT_COUNT}
    assert outcome.cleanup_proven is True
    assert outcome.report_sha256.startswith("sha256:")
    assert outcome.evaluation["outcome"] in {
        "inconclusive_floor", "signal_not_demonstrated", "calibration_signal_present",
    }
    assert outcome.evaluation.get("signal") is not True or outcome.evaluation["outcome"] == "calibration_signal_present"

    report_root = Path(INNER_BODY_REPORT_DIR).parent
    budget_path = report_root / "body-study-source-read-budget.json"
    receipt_path = report_root / "body-study-snapshot-counter.json"
    assert (report_root / "body-study/body-study-report-v1.json").is_file()
    assert (report_root / "attempt-journal").is_dir()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    assert receipt["valid"] is True
    assert receipt["observed_count"] == receipt["reserved_count"] == 201
    assert budget["snapshot_counter_receipt_file"] == receipt_path.name
    assert budget["snapshot_counter_receipt"]["valid"] is True
    assert budget["snapshot_counter_receipt_sha256"] == (
        "sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    )
    assert budget["registered_snapshot_count"] == 201
