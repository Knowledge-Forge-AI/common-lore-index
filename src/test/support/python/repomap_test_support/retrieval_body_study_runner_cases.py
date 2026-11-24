"""Adverse body-allocation checks shared by exact runner unit owners."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from offline_qualification_values import BODY_CAPS
from runner_pgvector_budget import Allocation, CAPS, STUDY_CAPS


def body_ledger_case(root: Path) -> None:
    path = root / "body-allocation.json"
    original_caps = dict(BODY_CAPS)
    path.write_text(json.dumps({"schema": "cloe-pgvector-allocation-v1", "caps": BODY_CAPS,
        "used": {key: 0 for key in BODY_CAPS}, "records": {}}))
    path.chmod(0o600)
    allocation = Allocation(path, BODY_CAPS)
    with allocation.session() as meter:
        meter.charge("sdk_body_study", 1)
        for key in ("builds", "pulls", "materializations", "runtime_exports"):
            with pytest.raises(RuntimeError, match="exhausted"):
                meter.charge(key, 1)
        for record in ("core_result", "native_result", "study_result"):
            with pytest.raises(RuntimeError, match="allocation record"):
                meter.bind(record, {"exit": 0})
        meter.bind("body_study_result", {"exit": 1, "cleanup_proven": False})
    with allocation.session() as meter:
        assert meter.used["sdk_body_study"] == 1
        assert meter.records["body_study_result"]["cleanup_proven"] is False
        with pytest.raises(RuntimeError, match="exhausted"):
            meter.charge("sdk_body_study", 1)
    for caps in (CAPS, STUDY_CAPS):
        with pytest.raises(RuntimeError, match="allocation shape"):
            with Allocation(path, caps).session():
                raise AssertionError("body allocation cannot become an old cohort")
    assert BODY_CAPS == original_caps
