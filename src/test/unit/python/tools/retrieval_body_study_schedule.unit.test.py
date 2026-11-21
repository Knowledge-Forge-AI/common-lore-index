"""Owners for the finite BODY lifetime and byte forecast."""
from __future__ import annotations

import pytest

from retrieval_body_study_contracts import (BodyStudyRefusal, BODY_CONTROLLER_SECONDS,
                                            BODY_SDK_SECONDS, INNER_SOURCE_READ_LIMIT)
from retrieval_body_study_schedule import (SDK_PHASES, SchedulePhase, build_byte_forecast,
                                           build_schedule, derive_worker_windows, validate_schedule)


def test_schedule_has_body_lifetime_and_sequential_workers() -> None:
    schedule = build_schedule()
    assert schedule["sdk_total_seconds"] == BODY_SDK_SECONDS == 4_800
    assert schedule["controller_total_seconds"] <= BODY_CONTROLLER_SECONDS
    assert [item["representation"] for item in schedule["worker_windows"]] == [
        "canonical-node-v1", "canonical-node-public-text-v2"]
    assert [item["aggregate"] for item in schedule["worker_windows"]] == [1_700, 1_700]
    assert schedule["source_memoization"] is False
    assert schedule["registered_input_checks"] == 4
    assert schedule["parent_admission_checks"] == 5
    assert schedule["parent_admission_pins"] == 5


def test_worker_windows_reject_overlap_or_phase_growth() -> None:
    windows = derive_worker_windows()
    assert all(item.pre_request_sdk_seconds <= 120 for item in windows)
    altered = list(SDK_PHASES)
    phase = altered[4]
    altered[4] = SchedulePhase(phase.name, phase.budget_seconds + 1,
                                phase.window, phase.overlaps_worker_init,
                                phase.pre_request_for, phase.worker_representation)
    with pytest.raises(BodyStudyRefusal):
        derive_worker_windows(tuple(altered))


def test_forecast_retains_inner_whole_candidate_reservations() -> None:
    forecast = build_byte_forecast(source_bytes=1_024, treatment_bytes=2_048, worker_bytes=128, candidate_bytes=2_048,
                                   request_bytes=(8 * 1024**2, 8 * 1024**2))
    assert forecast["read_items"]["inner_candidate_verifications_x32"] > 32 * 2_048
    assert forecast["source_memoization"] is False


def test_schedule_digest_cannot_cover_altered_payload() -> None:
    altered = build_schedule()
    altered["sdk_phases"] = list(altered["sdk_phases"])
    altered["sdk_phases"][0] = dict(altered["sdk_phases"][0], budget_seconds=181)
    with pytest.raises(BodyStudyRefusal):
        validate_schedule(altered)


@pytest.mark.parametrize("field,value", (
    ("guard_limit", 512.0), ("guard_limit", True),
    ("whole_read_limit", float(INNER_SOURCE_READ_LIMIT)), ("whole_read_limit", True),
))
def test_equal_float_or_boolean_cannot_authorize_read_counts(field, value) -> None:
    with pytest.raises(BodyStudyRefusal, match="inner read limits"):
        build_byte_forecast(source_bytes=1024, request_bytes=(1, 1), **{field: value})
