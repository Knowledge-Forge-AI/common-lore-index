"""Negative ownership for retained BODY failure evidence."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from retrieval_body_study_observation import (
    FAILURE_REPORT_NAME,
    build_body_failure_envelope,
    observation_record,
    validate_body_failure_envelope,
    write_body_failure_envelope,
)
from retrieval_body_study_values import BodyStudyError, digest_value


def _record():
    return observation_record({
        "task_id": "body-01",
        "arm": "v1",
        "repetition": 1,
        "generation": 1,
        "execution_state": "completed",
        "ranked_files": ["binding:doc.py"],
        "snapshot_hits": [{"source_binding_id": "binding", "source_relative_path": "doc.py"}],
        "origin_proven": True,
        "preparation_only": False,
    }, index=0)


def test_nonfunctional_record_without_generation_is_rejected() -> None:
    with pytest.raises(BodyStudyError, match="slot_generation"):
        observation_record({
            "task_id": "body-01", "arm": "v1", "repetition": 1,
            "execution_state": "completed", "ranked_files": (),
            "snapshot_hits": (), "origin_proven": True, "preparation_only": False,
        }, index=0)


def _envelope():
    record = _record()
    journal = SimpleNamespace(
        retained_observations=lambda: (record,),
        sealed_observation_identity=digest_value([record["observation_sha256"]]),
    )
    failed = {"task_id": "body-000", "arm": "v1", "repetition": 1,
              "generation": 1, "execution_state": "failed", "ranked_files": (),
              "returned_hit_count": 0, "error_code": "transport_lost",
              "origin_proven": False, "preparation_only": False}
    unstarted = tuple({"task_id": f"body-{index:03d}", "arm": "v1",
                       "repetition": index % 3 + 1, "generation": 1,
                       "execution_state": "unstarted", "ranked_files": (),
                       "returned_hit_count": 0, "origin_proven": False,
                       "preparation_only": False} for index in range(1, 432))
    return build_body_failure_envelope(
        attempt_id="body-attempt-1",
        failure_code="transport_lost",
        failure_stage="worker_exchange_v2",
        cleanup={"cleanup_ok": None, "relations_clean": False, "container_absent": False},
        journal=journal,
        worker_receipts=(SimpleNamespace(
            identity="sha256:" + "a" * 64,
            representation="canonical-node-v1",
            response_identity="sha256:" + "b" * 64,
            admission_identity="sha256:" + "c" * 64,
            reachability_identity="sha256:" + "d" * 64,
        ),),
        slots=SimpleNamespace(actual_slots=(failed, *unstarted), unstarted_slots=431),
    )


def test_failure_envelope_retains_original_record_but_has_no_primary() -> None:
    envelope = _envelope()
    checked = validate_body_failure_envelope(envelope)
    assert checked["primary"] is None
    assert checked["origin_proven"] is False
    assert checked["worker_count"] == 1
    assert checked["observed_slot_count"] == 1
    assert checked["slot_record_count"] == 432
    assert checked["slot_state_counts"]["failed"] == 1
    assert checked["slot_state_counts"]["unstarted"] == 431
    assert checked["sealed_observation_manifest"][0]["observation_sha256"] == _record()["observation_sha256"]
    assert checked["sealed_observation_manifest"][0]["raw_sha256"].startswith("sha256:")


def test_failure_envelope_is_immutable_and_bound_to_written_identity(tmp_path: Path) -> None:
    envelope = _envelope()
    identity = write_body_failure_envelope(tmp_path, envelope)
    assert identity == envelope["report_identity"]
    path = tmp_path / FAILURE_REPORT_NAME
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="destination_exists"):
        write_body_failure_envelope(tmp_path, envelope)


def test_primary_or_acceptance_claim_cannot_enter_failure_envelope() -> None:
    forged = _envelope() | {"primary": {"attempt_id": "other"}}
    forged["report_identity"] = digest_value({
        key: value for key, value in forged.items() if key != "report_identity"
    })
    with pytest.raises(BodyStudyError, match="acceptance"):
        validate_body_failure_envelope(forged)
