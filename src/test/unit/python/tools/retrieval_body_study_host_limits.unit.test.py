"""Finite BODY host cadence and Docker reservation regressions."""
from __future__ import annotations

import pytest

from retrieval_body_study_contracts import MAX_DOCKER_STREAM_FORECAST_BYTES
from retrieval_body_study_host_limits import (
    direct_fixed_stream_terms,
    derive_body_host_limits,
)


def test_direct_fixed_stream_terms_remain_explicit() -> None:
    terms = direct_fixed_stream_terms()
    assert sum(item["reserved_bytes"] for item in terms.values()) == 43_868_482
    assert terms["exchange.request_read"]["limit_bytes"] == 8 * 1024**2 + 20 * 1024
    assert terms["settlement.marker"]["limit_bytes"] == 128


def test_body_thirty_second_cadence_fits_source_and_docker_bounds() -> None:
    limits = derive_body_host_limits()
    assert limits["model_poll_count"] == 19
    assert limits["source_guard_calls"] == 352
    assert limits["docker_operation_calls"] == 715
    assert limits["direct_fixed_stream_bytes"] == 43_868_482
    assert limits["docker_stream_bytes"] == 507_029_654
    assert limits["docker_stream_bytes"] <= MAX_DOCKER_STREAM_FORECAST_BYTES
    assert "report_receiver.allowance" not in limits["docker_stream_terms"]


def test_runtime_and_sealed_schedule_share_body_only_model_cadence() -> None:
    from retrieval_body_study_contracts import BODY_MODEL_POLL_SECONDS
    from retrieval_body_study_model_host import MODEL_LEASE_POLL_SECONDS
    from retrieval_body_study_schedule import build_schedule
    from retrieval_supplement_model_host import MODEL_LEASE_POLL_SECONDS as old_model_poll

    assert MODEL_LEASE_POLL_SECONDS == BODY_MODEL_POLL_SECONDS == 30.0
    assert old_model_poll == 5.0
    schedule = build_schedule()
    assert schedule["model_poll_seconds"] == MODEL_LEASE_POLL_SECONDS
    assert schedule["poll_seconds"] == 5.0
    assert schedule["source_rechecks"] == 400


def test_old_five_second_cadence_cannot_claim_cap_four_hundred() -> None:
    with pytest.raises(ValueError, match="source guard"):
        derive_body_host_limits(model_poll_seconds=5.0)


def test_stream_and_operation_underreservation_are_rejected() -> None:
    with pytest.raises(ValueError, match="Docker stream"):
        derive_body_host_limits(stream_limit=507_029_653)
    with pytest.raises(ValueError, match="Docker operation"):
        derive_body_host_limits(operation_limit=714)
