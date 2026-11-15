"""The single allocated real native extension lifecycle; no model or study."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from runner_pgvector_context import load_inner_plan
from runner_pgvector_qualification import bounded_native_run, qualify
from runner_sandbox import _SetupCommands


@pytest.mark.requires_pgvector
def test_prebuilt_runtime_native_extension_index_rollback_and_cleanup() -> None:
    # Admission already validated the real rewritten argv; recheck sealed custody.
    plan = load_inner_plan(os.environ)
    assert plan["role"] == "native"
    image = plan["derived"]["runtime_config"]
    assert os.environ["REPOMAP_TEST_PG_IMAGE"] == image
    setup = _SetupCommands(dict(os.environ), bounded_native_run)
    report = qualify(setup.execute, setup.event, image)
    assert report["native_qualification"] == "passed" and report["cleanup_proven"]
    assert report["native_lifecycles"] == 1 and report["jit"] == "off"
    assert report["production_or_scientific_admission"] is False
    # Canonical report export retains this bounded diagnostic receipt.
    path = Path("/sandbox-report/report/pgvector-native-qualification.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as output:
        json.dump(report, output, sort_keys=True)
        output.write("\n")
