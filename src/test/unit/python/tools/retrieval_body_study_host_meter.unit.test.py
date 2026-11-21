"""Owners for BODY ledger keys and bounded Docker operations."""
from __future__ import annotations

from retrieval_body_study_host_meter import HostMeter


def test_meter_exposes_body_result_and_worker_accounting() -> None:
    assert "record_result" in HostMeter.__dict__
    assert "worker" in HostMeter.__dict__
    assert "docker_read" in HostMeter.__dict__
