"""Unit ownership for the fixed body slot grid."""
from __future__ import annotations

import pytest
from dataclasses import replace

from retrieval_body_study_capture import BodySlotObservation, _ISSUED, _observation_identity
from retrieval_body_study_slots import _grid


def test_slot_grid_refuses_subsets_instead_of_padding_quality_rows() -> None:
    with pytest.raises(ValueError, match="body.slots:selected_row_count"):
        _grid({"selected_rows": ()})


def test_slot_grid_retains_three_repetitions_for_every_selected_row() -> None:
    rows = [
        {"task_id": f"body1-{index:02d}", "arm": "native_filesystem", "preparation": "ready",
         "generation": 1}
        for index in range(1, 145)
    ]
    grid = _grid({"selected_rows": rows})
    assert len(grid) == 432
    assert {row["repetition"] for row in grid} == {1, 2, 3}


def test_slot_observation_cannot_be_forged_outside_capture_factory() -> None:
    with pytest.raises(ValueError, match="body.capture:factory_required"):
        BodySlotObservation({}, {}, None)


def test_slot_observation_replacement_loses_origin_seal() -> None:
    issued = BodySlotObservation(
        {"execution_state": "failed"}, {"execution_state": "failed"}, None,
        _origin=_ISSUED,
    )
    object.__setattr__(issued, "_seal", _observation_identity(issued))
    assert BodySlotObservation.is_factory_issued(issued)
    replaced = replace(issued, policy={"execution_state": "completed"})
    assert not BodySlotObservation.is_factory_issued(replaced)


def test_failed_rich_observation_keeps_generation_out_of_policy_projection(monkeypatch):
    from types import SimpleNamespace
    from retrieval_body_study_capture import capture_body_slot
    from retrieval_body_study_outcomes import _validate_optional_slot_fields
    from retrieval_body_study_values import NATIVE_ARM
    from retrieval_semantic_provenance import VerifiedPublicationPin

    # An isolated failed-row seam: no publication/model/quality origin is claimed.
    pin = object.__new__(VerifiedPublicationPin)
    monkeypatch.setattr(VerifiedPublicationPin, "is_factory_issued", lambda self: True)
    monkeypatch.setattr(VerifiedPublicationPin, "revalidate_current", lambda self: None)
    monkeypatch.setattr(VerifiedPublicationPin, "to_dict", lambda self: {"functional_pin": True})
    captured = capture_body_slot(task=SimpleNamespace(generation=1), task_id="functional-task",
        arm=NATIVE_ARM, repetition=1, result={"execution_state": "failed", "envelope": None},
        verified_pin=pin)
    assert captured.actual["generation"] == 1
    assert captured.actual["origin_proven"] is False
    assert "generation" not in captured.policy
    _validate_optional_slot_fields(captured.policy)


@pytest.mark.parametrize("state", ["refused", "unavailable"])
def test_preparation_terminal_preserves_state_without_claiming_query_origin(monkeypatch, state):
    from types import SimpleNamespace
    from common_lore_index.retrieval.query_contracts import (
        QueryMode, QueryScope, refused_envelope, unavailable_envelope,
    )
    from retrieval_body_study_capture import capture_body_slot
    from retrieval_body_study_values import NATIVE_ARM
    from retrieval_semantic_provenance import VerifiedPublicationPin

    pin = object.__new__(VerifiedPublicationPin)
    monkeypatch.setattr(VerifiedPublicationPin, "is_factory_issued", lambda self: True)
    monkeypatch.setattr(VerifiedPublicationPin, "revalidate_current", lambda self: None)
    monkeypatch.setattr(VerifiedPublicationPin, "to_dict", lambda self: {"functional_pin": True})
    scope = QueryScope(graph_id="functional-body", backend="sqlite")
    if state == "refused":
        envelope = refused_envelope(requested_mode=QueryMode.RANKED_DISCOVERY, scope=scope, refusal_code="privacy_not_authorized")
        reason = {"refusal_code": "privacy_not_authorized"}
    else:
        envelope = unavailable_envelope(requested_mode=QueryMode.RANKED_DISCOVERY, scope=scope, reason="body_unavailable")
        reason = {"unavailable_reason": "body_unavailable"}
    observed = capture_body_slot(
        task=SimpleNamespace(generation=1, comparability_stratum="body"),
        task_id="functional-body-1", arm=NATIVE_ARM, repetition=1, verified_pin=pin,
        result={"execution_state": state, "envelope": envelope,
                "observation_origin": "preparation_terminal", **reason},
    )
    assert observed.actual["execution_state"] == state
    assert observed.actual["observation_origin"] == "preparation_terminal"
    assert observed.actual["origin_proven"] is False
    assert observed.actual["query_execution_proven"] is False
    assert not observed.actual["snapshot_hits"]
    assert observed.grade is None
    assert BodySlotObservation.is_factory_issued(observed)
