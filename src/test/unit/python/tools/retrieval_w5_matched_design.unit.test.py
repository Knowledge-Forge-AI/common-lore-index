"""Synthetic matched-W5 census and outcome proof."""

from dataclasses import replace
from fractions import Fraction
from itertools import chain, permutations
from typing import cast

import pytest

from retrieval_w5_matched_design import (
    Absence,
    Arm,
    Case,
    FileMetrics,
    Observation,
    ObservationState,
    Outcome,
    Postflight,
    Preflight,
    Slot,
    Stratum,
    Wording,
    assess,
    plan_slots,
)


@pytest.fixture
def cases() -> tuple[Case, ...]:
    body = (
        (family, wording)
        for family in (Stratum.BEHAVIOR, Stratum.CONTEXT, Stratum.TESTS)
        for wording in Wording
        for _ in range(2)
    )
    controls = (
        (kind, None)
        for kind, count in (
            (Stratum.ANCHOR, 4),
            (Stratum.ABSENT, 2),
            (Stratum.AMBIGUOUS, 2),
            (Stratum.PRIVACY, 2),
            (Stratum.UNSUPPORTED, 2),
        )
        for _ in range(count)
    )
    return tuple(
        Case(ordinal, family, wording)
        for ordinal, (family, wording) in enumerate(chain(body, controls), 1)
    )


def test_canonical_order_balances_every_position(cases: tuple[Case, ...]) -> None:
    slots = plan_slots(cases[::-1])
    assert len(slots) == len(set(slots)) == 72
    assert slots[:3] == (Slot(1, Arm.NATIVE), Slot(1, Arm.LEXICAL), Slot(1, Arm.FUSION))
    orders = tuple(tuple(slot.arm for slot in slots[i:i + 3]) for i in range(0, 72, 3))
    assert orders[:6] == tuple(permutations(Arm))
    assert all(orders[:12].count(order) == 2 for order in set(orders))
    for position in range(3):
        for arm in Arm:
            assert sum(slot.arm is arm for slot in slots[position::3]) == 8


def test_census_refuses_substitution_and_repeated_ordinals(cases: tuple[Case, ...]) -> None:
    with pytest.raises(ValueError, match="body_census"):
        plan_slots((replace(cases[0], wording=Wording.PARAPHRASED), *cases[1:]))
    with pytest.raises(ValueError, match="control_order"):
        plan_slots((*cases[:-1], replace(cases[-1], stratum=Stratum.ABSENT)))
    with pytest.raises(ValueError, match="case_ordinals"):
        plan_slots((cases[1], *cases[1:]))
    with pytest.raises(ValueError, match="case_ordinals"):
        plan_slots(cases[:-1])


def _disposition(
    cases: tuple[Case, ...], preflight: Preflight, rows: dict[Slot, Observation],
) -> Outcome:
    return assess(cases, preflight, rows, postflight=Postflight.VERIFIED).outcome


def _complete_row(key: Slot) -> Observation:
    if key.ordinal <= 12:
        return Observation(ObservationState.RANKED, FileMetrics(
            {Arm.FUSION: 0.55, Arm.LEXICAL: 0.50, Arm.NATIVE: 0.45}[key.arm], 0.8, 0.9,
        ))
    if key.ordinal in (17, 18):
        return Observation(ObservationState.RANKED, absence=Absence.SCOPED_COMPLETE_EMPTY)
    if key.ordinal <= 20:
        return Observation(ObservationState.RANKED, FileMetrics(1, 1, 1))
    if key.ordinal in (21, 22) or key.arm is Arm.FUSION:
        return Observation(ObservationState.CONTROL_CHECKED, control_correct=True)
    return Observation(ObservationState.RANKED, control_correct=True)


@pytest.fixture
def complete(cases: tuple[Case, ...]) -> dict[Slot, Observation]:
    return {key: _complete_row(key) for key in plan_slots(cases)}


def test_exact_gain_floor_and_equal_secondary_metrics_pass(
    cases: tuple[Case, ...], complete: dict[Slot, Observation],
) -> None:
    assert _disposition(cases, Preflight.READY, complete) is Outcome.PRESENT


@pytest.mark.parametrize("metric", (
    FileMetrics(0.549999, 0.8, 0.9),
    FileMetrics(0.55, 0.799999, 0.9),
    FileMetrics(0.55, 0.8, 0.899999),
))
@pytest.mark.parametrize("baseline", (Arm.NATIVE, Arm.LEXICAL))
def test_loss_against_either_comparator_fails_even_at_smallest_recorded_unit(
    cases: tuple[Case, ...], complete: dict[Slot, Observation],
    metric: FileMetrics, baseline: Arm,
) -> None:
    for ordinal in range(1, 13):
        complete[Slot(ordinal, baseline)] = Observation(ObservationState.RANKED, FileMetrics(0.5, 0.8, 0.9))
        complete[Slot(ordinal, Arm.FUSION)] = Observation(ObservationState.RANKED, metric)
    assert _disposition(cases, Preflight.READY, complete) is Outcome.NOT_DEMONSTRATED


@pytest.mark.parametrize(
    ("family_ndcg", "expected"),
    ((0.45, Outcome.PRESENT), (0.449999, Outcome.NOT_DEMONSTRATED)),
)
def test_family_regression_boundary_is_separate_from_global_gain(
    cases: tuple[Case, ...], complete: dict[Slot, Observation],
    family_ndcg: float, expected: Outcome,
) -> None:
    for ordinal in range(1, 13):
        complete[Slot(ordinal, Arm.FUSION)] = Observation(
            ObservationState.RANKED, FileMetrics(family_ndcg if ordinal <= 4 else 0.7, 0.8, 0.9),
        )
    assert _disposition(cases, Preflight.READY, complete) is expected


@pytest.mark.parametrize("grade", (float("nan"), float("inf"), -0.01, 1.01, True))
def test_invalid_numeric_evidence_is_rejected(grade: float) -> None:
    with pytest.raises(ValueError, match="metric_range"):
        FileMetrics(grade, 0, 0)
    with pytest.raises(ValueError, match="metric_precision"):
        FileMetrics(0.1234567, 0, 0)


def test_preflight_stops_ranking_and_preserves_technical_failure(
    cases: tuple[Case, ...],
) -> None:
    key = Slot(1, Arm.FUSION)
    pending = {slot: Observation(ObservationState.UNSTARTED) for slot in plan_slots(cases)}
    assert _disposition(cases, Preflight.BELOW_FLOOR, {}) is Outcome.INCOMPLETE
    assert _disposition(cases, Preflight.BELOW_FLOOR, pending) is Outcome.BELOW_FLOOR
    pending[Slot(21, Arm.NATIVE)] = Observation(ObservationState.CONTROL_CHECKED, control_correct=True)
    assert _disposition(cases, Preflight.BELOW_FLOOR, pending) is Outcome.BELOW_FLOOR
    pending[key] = Observation(ObservationState.CONTROL_CHECKED, control_correct=True)
    assert _disposition(cases, Preflight.BELOW_FLOOR, pending) is Outcome.INADMISSIBLE
    assert _disposition(cases, Preflight.BELOW_FLOOR, {
        key: Observation(ObservationState.TECHNICAL_FAILURE),
    }) is Outcome.INADMISSIBLE
    assert _disposition(cases, Preflight.BELOW_FLOOR, {
        key: Observation(ObservationState.RANKED, FileMetrics(0, 0, 0)),
    }) is Outcome.INADMISSIBLE
    assert _disposition(cases, Preflight.TECHNICAL_FAILURE, {}) is Outcome.INCOMPLETE
    assert _disposition(cases, Preflight.INADMISSIBLE, {}) is Outcome.INADMISSIBLE


def test_missing_grade_and_unstarted_rank_never_score_as_zero(
    cases: tuple[Case, ...], complete: dict[Slot, Observation],
) -> None:
    key = Slot(1, Arm.LEXICAL)
    for row in (Observation(ObservationState.UNSTARTED), Observation(ObservationState.RANKED)):
        complete[key] = row
        assert _disposition(cases, Preflight.READY, complete) is Outcome.INCOMPLETE
    complete.pop(key)
    assert _disposition(cases, Preflight.READY, complete) is Outcome.INCOMPLETE
    complete[key] = Observation(ObservationState.RANKED, FileMetrics(0, 0, 0))
    assert _disposition(cases, Preflight.READY, complete) is Outcome.PRESENT


@pytest.mark.parametrize("ordinal", (21, 24))
def test_refusal_can_be_incorrect_incomplete_or_inadmissible(
    cases: tuple[Case, ...], complete: dict[Slot, Observation], ordinal: int,
) -> None:
    key = Slot(ordinal, Arm.FUSION)
    for row, outcome in (
        (Observation(ObservationState.CONTROL_CHECKED, control_correct=False), Outcome.NOT_DEMONSTRATED),
        (Observation(ObservationState.RANKED), Outcome.INADMISSIBLE),
        (Observation(ObservationState.UNAVAILABLE), Outcome.INADMISSIBLE),
        (Observation(ObservationState.TECHNICAL_FAILURE), Outcome.INADMISSIBLE),
        (Observation(ObservationState.FORBIDDEN_WORK), Outcome.INADMISSIBLE),
    ):
        complete[key] = row
        assert _disposition(cases, Preflight.READY, complete) is outcome
    assert _disposition(cases, Preflight.TECHNICAL_FAILURE, complete) is Outcome.INADMISSIBLE


def test_malformed_evidence_refuses_before_screening(cases: tuple[Case, ...]) -> None:
    with pytest.raises(ValueError, match="ledger_shape"):
        _disposition(cases, Preflight.READY, cast(dict[Slot, Observation], {True: "unstarted"}))
    with pytest.raises(ValueError, match="flight_state"):
        _disposition(cases, cast(Preflight, "ready"), {})
    with pytest.raises(ValueError, match="case_sequence"):
        plan_slots(cast(tuple[Case, ...], iter(cases)))
    with pytest.raises(ValueError, match="case_sequence"):
        plan_slots(cast(tuple[Case, ...], ["body_positive"]))
    with pytest.raises(ValueError, match="metric_state"):
        Observation(ObservationState.UNSTARTED, FileMetrics(0, 0, 0))
    with pytest.raises(ValueError, match="metric_state"):
        Observation(ObservationState.RANKED, cast(FileMetrics, "missing"))
    with pytest.raises(ValueError, match="missing_control"):
        Observation(ObservationState.CONTROL_CHECKED)
    with pytest.raises(ValueError, match="control_state"):
        Observation(ObservationState.UNSTARTED, control_correct=True)
    with pytest.raises(ValueError, match="observation_state"):
        Observation(cast(ObservationState, "unknown"))
    with pytest.raises(ValueError, match="conflicting_evidence"):
        Observation(ObservationState.RANKED, FileMetrics(0, 0, 0), control_correct=True)


def test_slot_identity_cannot_alias_canonical_evidence() -> None:
    with pytest.raises(ValueError, match="slot_identity"):
        Slot(True, Arm.NATIVE)
    with pytest.raises(ValueError, match="slot_identity"):
        Slot(1, cast(Arm, Arm.NATIVE.value))
    with pytest.raises(ValueError, match="slot_identity"):
        Slot(25, Arm.NATIVE)


def test_out_of_range_integer_grades_fail_with_a_stable_code() -> None:
    with pytest.raises(ValueError, match="metric_range"):
        FileMetrics(10 ** 400, 0, 0)



@pytest.mark.parametrize("metrics", ((1, 0, 0), (0.1, 0.099999, 1)))
def test_impossible_quality_rows_refuse(metrics: tuple[float, float, float]) -> None:
    with pytest.raises(ValueError, match="metric_consistency"):
        FileMetrics(*metrics)


def test_missing_anchor_or_ambiguity_grade_blocks_complete_signal(
    cases: tuple[Case, ...], complete: dict[Slot, Observation],
) -> None:
    for ordinal in (13, 19):
        key = Slot(ordinal, Arm.FUSION)
        original = complete[key]
        complete[key] = Observation(ObservationState.RANKED)
        assert _disposition(cases, Preflight.READY, complete) is Outcome.INCOMPLETE
        complete[key] = original


def test_literal_control_executes_native_and_lexical_and_refuses_fusion(
    cases: tuple[Case, ...], complete: dict[Slot, Observation],
) -> None:
    key = Slot(23, Arm.LEXICAL)
    assert _disposition(cases, Preflight.READY, complete) is Outcome.PRESENT
    complete[key] = Observation(ObservationState.RANKED, control_correct=False)
    assert _disposition(cases, Preflight.READY, complete) is Outcome.NOT_DEMONSTRATED
    complete[key] = Observation(ObservationState.CONTROL_CHECKED, control_correct=True)
    assert _disposition(cases, Preflight.READY, complete) is Outcome.INADMISSIBLE


def test_absence_remains_separate_from_the_body_signal(
    cases: tuple[Case, ...], complete: dict[Slot, Observation],
) -> None:
    key = Slot(17, Arm.FUSION)
    complete[key] = Observation(ObservationState.RANKED, absence=Absence.OTHER_RETURNS)
    result = assess(cases, Preflight.READY, complete, postflight=Postflight.VERIFIED)
    assert result.outcome is Outcome.PRESENT
    assert result.absence_gaps == (key,)
    complete[key] = Observation(ObservationState.UNAVAILABLE)
    result = assess(cases, Preflight.READY, complete, postflight=Postflight.VERIFIED)
    assert result.outcome is Outcome.INCOMPLETE
    assert not result.contrasts


def test_postflight_falsity_precedes_positive_grades(
    cases: tuple[Case, ...], complete: dict[Slot, Observation],
) -> None:
    for postflight, outcome in (
        (Postflight.TECHNICAL_FAILURE, Outcome.INCOMPLETE),
        (Postflight.INADMISSIBLE, Outcome.INADMISSIBLE),
    ):
        result = assess(cases, Preflight.READY, complete, postflight=postflight)
        assert result.outcome is outcome
        assert not result.contrasts
    assert assess(cases, Preflight.TECHNICAL_FAILURE, complete,
                  postflight=Postflight.INADMISSIBLE).outcome is Outcome.INADMISSIBLE


def test_exact_contrasts_include_each_pair_family_and_wording(
    cases: tuple[Case, ...], complete: dict[Slot, Observation],
) -> None:
    complete[Slot(1, Arm.FUSION)] = Observation(ObservationState.RANKED, FileMetrics(0.67, 0.8, 0.9))
    result = assess(cases, Preflight.READY, complete, postflight=Postflight.VERIFIED)
    native, lexical = result.contrasts
    assert native.baseline is Arm.NATIVE
    assert lexical.baseline is Arm.LEXICAL
    assert lexical.mean.ndcg == Fraction(6, 100)
    assert lexical.fusion_mean.ndcg == Fraction(56, 100)
    assert lexical.baseline_mean.ndcg == Fraction(50, 100)
    assert dict(lexical.by_ordinal)[1].ndcg == Fraction(17, 100)
    assert dict(lexical.by_family)[Stratum.BEHAVIOR].ndcg == Fraction(8, 100)
    assert dict(lexical.by_wording)[Wording.LITERAL].ndcg == Fraction(7, 100)
    assert dict(lexical.by_wording)[Wording.PARAPHRASED].ndcg == Fraction(5, 100)



def test_ranked_work_requires_ready_preflight(
    cases: tuple[Case, ...], complete: dict[Slot, Observation],
) -> None:
    assert _disposition(cases, Preflight.TECHNICAL_FAILURE, complete) is Outcome.INADMISSIBLE
