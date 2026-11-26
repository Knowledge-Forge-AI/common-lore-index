"""Exact actual-study owner (ADR 0203 Route A).

Ordinary populations visibly deselect this node in ``src/test/conftest.py``.
It runs only as the sole collected node inside the canonical study SDK when
``CLOE_PGVECTOR_OFFLINE_PLAN`` points to the parent-projected ``0600`` inner
study plan (``/sandbox-scratch/pgvector/plan.json``) carrying a verified
``StudyAdmission``. Without the authenticated sandbox, verified inner plan,
fresh closures, disposable socket-only PostgreSQL runtime and host sibling
worker bridge it refuses; it never skips, xfails, or falls back to a synthetic
double.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from common_lore_index_test_support.retrieval_supplement_study import (
    MARKER,
    require_study_admission,
)
from common_lore_index_test_support.retrieval_supplement_study_driver import (
    run_supplement_study_inner,
)
from retrieval_supplement_study_contracts import (
    INNER_STUDY_REPORT_DIR,
    SLOT_COUNT,
    TOTAL_RECORDS,
)
from runner_pgvector_prebuilt import INNER_PLAN
from test_sandbox import active_sandbox

pytestmark = [
    pytest.mark.requires_pgvector,
    getattr(pytest.mark, MARKER),
]


def test_supplement_study_actual_attempt(tmp_path: Path) -> None:
    assert active_sandbox(os.environ), "canonical study SDK sandbox required"
    admission = require_study_admission(os.environ)
    inner_plan_path = Path(INNER_PLAN)
    assert inner_plan_path.is_file(), "projected inner study plan required"
    repo_root = Path("/workspace")
    report_root = Path(INNER_STUDY_REPORT_DIR)
    outcome = run_supplement_study_inner(
        inner_plan_path=inner_plan_path,
        repo_root=repo_root,
        workspace=tmp_path / "study-workspace",
        report_root=report_root,
        functional_only=False,
    )
    assert outcome.status == "completed"
    assert outcome.attempt_id == admission.attempt_id
    assert outcome.observed_slots == SLOT_COUNT and outcome.unstarted_slots == 0
    assert outcome.token_records == TOTAL_RECORDS
    assert outcome.cleanup_proven is True
    assert outcome.report_sha256 is not None
    assert outcome.paired_analysis is not None
    assert outcome.paired_analysis["scientific_acceptance_claimed"] is False
