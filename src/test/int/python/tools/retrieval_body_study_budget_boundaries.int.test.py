"""Core budget, source-read, SDK-registry, and archive-identity joins."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from offline_qualification_values import BODY_CAPS
from runner_pgvector_budget import Allocation
from runner_body_study import validate_body_cohort
from retrieval_body_study_contracts import BODY_SELECTOR, BodyStudyRefusal
from retrieval_body_study_host_meter import HostMeter
from retrieval_body_study_inner_reads import (
    InnerReadRefusal,
    READ_LIMIT,
    read_receipt,
    reserve_inner_source_verification,
)
from retrieval_body_study_schedule import (
    build_byte_forecast,
    build_schedule,
    validate_schedule,
)


def _allocation(root: Path) -> Allocation:
    path = root / "body-allocation.json"
    path.write_text(
        json.dumps(
            {
                "schema": "cloe-pgvector-allocation-v1",
                "caps": BODY_CAPS,
                "used": {key: 0 for key in BODY_CAPS},
                "records": {},
            },
            sort_keys=True,
        )
    )
    path.chmod(0o600)
    return Allocation(path, BODY_CAPS)


def _source_plan() -> dict[str, object]:
    return {
        "schema": "cloe-body-study-inner-plan-v1",
        "role": "body-study",
        "candidate_head": "b" * 40,
        "candidate_snapshot": {
            "candidate.py": {"sha256": "a" * 64, "mode": 0o644}
        },
    }


def test_budget_forecast_registry_and_source_reads_are_failure_persistent(
    tmp_path: Path,
) -> None:
    schedule = build_schedule()
    validate_schedule(schedule)
    validate_body_cohort(
        [
            "--suite", "int", "--jobs", "1", "--no-coverage",
            "--pg-container-port", "55433", "--pg-test-profile", "pgvector",
            "--report-dir", str(tmp_path / "report"), "--", "-s", BODY_SELECTOR,
        ]
    )
    assert schedule["source_memoization"] is False
    assert schedule["source_rechecks"] > 0
    forecast = build_byte_forecast(
        source_bytes=7,
        treatment_bytes=11,
        worker_bytes=13,
        request_bytes=(17, 19),
        provider_bytes=23,
        candidate_bytes=29,
    )
    assert forecast["physical_os_pg_costs"] == "unknown_not_measured_by_controlled_counters"
    assert forecast["source_memoization"] is False
    assert forecast["runtime_imports"] == 1 and forecast["runtime_exports"] == 0

    allocation = _allocation(tmp_path)
    meter = HostMeter(allocation, started=100.0, clock=lambda: 100.0)
    meter.read(7)
    meter.transfer(11)
    meter.operation("docker.inspect")
    meter.worker()
    meter.record_result({"status": "incomplete_technical"})
    with allocation.session() as ledger:
        assert ledger.used["read_bytes"] == 18
        assert ledger.used["transfer_bytes"] == 11
        assert ledger.used["body_study_workers"] == 1
        assert ledger.used["sdk_body_study"] == 0
        assert ledger.used["builds"] == ledger.used["pulls"] == 0
        assert ledger.records["body_study_result"]["status"] == "incomplete_technical"

    repo = tmp_path / "candidate"
    ledger_root = tmp_path / "source-ledger"
    repo.mkdir(mode=0o700)
    ledger_root.mkdir(mode=0o700)
    source = repo / "candidate.py"
    source.write_bytes(b"source\n")
    source.chmod(0o644)
    plan = _source_plan()
    first = reserve_inner_source_verification(plan, repo, ledger_root)
    source.write_bytes(b"source drift\n")
    with pytest.raises(InnerReadRefusal, match="source_size_changed"):
        reserve_inner_source_verification(plan, repo, ledger_root)
    assert read_receipt(plan, ledger_root=ledger_root, receipt=first)["consumed_reads"] == 1

    source.write_bytes(b"source\n")
    receipts = [first]
    receipts.extend(
        reserve_inner_source_verification(plan, repo, ledger_root)
        for _ in range(READ_LIMIT - 1)
    )
    assert [item["consumed_reads"] for item in receipts] == list(range(1, READ_LIMIT + 1))
    with pytest.raises(InnerReadRefusal, match="read_budget_exhausted"):
        reserve_inner_source_verification(plan, repo, ledger_root)


def test_original_runtime_archive_hash_is_checked_before_controller_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A compact deterministic archive substitutes only for the pinned asset."""
    import retrieval_body_study_host_controller as controller

    allocation = _allocation(tmp_path)
    meter = HostMeter(allocation, started=100.0, clock=lambda: 100.0)
    archive = tmp_path / "runtime.tar"
    original = b"original-runtime-archive"
    archive.write_bytes(original)
    archive.chmod(0o600)
    monkeypatch.setattr(controller, "BODY_RUNTIME_ARCHIVE_BYTES", len(original))
    monkeypatch.setattr(
        controller,
        "BODY_RUNTIME_ARCHIVE_SHA256",
        "sha256:" + hashlib.sha256(original).hexdigest(),
    )
    plan = {"archive": {"path": str(archive)}}
    controller._verify_archive(plan, meter)

    archive.write_bytes(b"tampered-runtime-archive")
    archive.chmod(0o600)
    with pytest.raises(BodyStudyRefusal, match="body runtime archive identity"):
        controller._verify_archive(plan, meter)
