"""Join maintained file grading to the prospective W5 screen on synthetic ranks."""

from retrieval_body_study_metrics import file_metrics_at_10
from retrieval_supplement_primitives import FUSION_ARM, NATIVE_ARM
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


def _cases() -> tuple[Case, ...]:
    strata: list[tuple[Stratum, Wording | None]] = [
        (family, wording)
        for family in (Stratum.BEHAVIOR, Stratum.CONTEXT, Stratum.TESTS)
        for wording in Wording
        for _ in range(2)
    ]
    strata += [
        (stratum, None)
        for stratum in (Stratum.ANCHOR, Stratum.ANCHOR, Stratum.ABSENT,
                        Stratum.AMBIGUOUS, Stratum.PRIVACY, Stratum.UNSUPPORTED)
        for _ in range(2)
    ]
    return tuple(
        Case(ordinal, stratum, wording)
        for ordinal, (stratum, wording) in enumerate(strata, 1)
    )


def _grade(ranked: tuple[tuple[str, str], ...]) -> FileMetrics:
    metrics = file_metrics_at_10(ranked, (("core", "src/relay.py", 3),))
    return FileMetrics(metrics["file_ndcg@10"], metrics["file_mrr@10"], metrics["file_recall@10"])


def test_shared_file_grading_can_distinguish_matched_treatments() -> None:
    assert Arm.NATIVE.value == NATIVE_ARM
    assert Arm.FUSION.value == FUSION_ARM
    gold = ("core", "src/relay.py")
    same_path_in_other_binding = ("companion", "src/relay.py")
    baseline = _grade((same_path_in_other_binding, same_path_in_other_binding, gold))
    fusion = _grade((gold, same_path_in_other_binding, gold))
    assert baseline == FileMetrics(0.63093, 0.5, 1)
    assert fusion == FileMetrics(1, 1, 1)

    cases = _cases()
    observations: dict[Slot, Observation] = {}
    for slot in plan_slots(cases):
        if slot.ordinal <= 12:
            observations[slot] = Observation(
                ObservationState.RANKED, fusion if slot.arm is Arm.FUSION else baseline,
            )
        elif slot.ordinal in (17, 18):
            observations[slot] = Observation(ObservationState.RANKED, absence=Absence.SCOPED_COMPLETE_EMPTY)
        elif slot.ordinal <= 20:
            observations[slot] = Observation(ObservationState.RANKED, fusion)
        elif slot.ordinal <= 22 or slot.arm is Arm.FUSION:
            observations[slot] = Observation(ObservationState.CONTROL_CHECKED, control_correct=True)
        else:
            observations[slot] = Observation(ObservationState.RANKED, control_correct=True)
    result = assess(cases, Preflight.READY, observations, postflight=Postflight.VERIFIED)
    assert result.outcome is Outcome.PRESENT

    key = Slot(1, Arm.FUSION)
    observations[key] = Observation(ObservationState.UNSTARTED)
    assert assess(cases, Preflight.READY, observations, postflight=Postflight.VERIFIED).outcome is Outcome.INCOMPLETE
