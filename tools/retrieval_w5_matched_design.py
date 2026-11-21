"""Model-independent census, schedule and screen for the prospective W5 study."""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from itertools import permutations


class Arm(StrEnum):
    NATIVE = "native_tools_baseline"
    LEXICAL = "public-lexical-comparator-v1"
    FUSION = "hybrid_rrf:canonical-node-public-text-v2"


class Stratum(StrEnum):
    BEHAVIOR = "behavior_without_exact_symbol"
    CONTEXT = "code_plus_design_or_message_context"
    TESTS = "relevant_tests_for_behavior"
    ANCHOR = "metadata_anchor"
    ABSENT = "absent"
    AMBIGUOUS = "ambiguous"
    PRIVACY = "privacy"
    UNSUPPORTED = "unsupported"


class Wording(StrEnum):
    LITERAL = "literal_clue"
    PARAPHRASED = "paraphrased_intent"


class Preflight(StrEnum):
    READY = "ready"
    BELOW_FLOOR = "below_floor"
    TECHNICAL_FAILURE = "technical_failure"
    INADMISSIBLE = "inadmissible"


class Postflight(StrEnum):
    VERIFIED = "verified"
    TECHNICAL_FAILURE = "technical_failure"
    INADMISSIBLE = "inadmissible"


class ObservationState(StrEnum):
    RANKED = "ranked"
    CONTROL_CHECKED = "control_checked"
    UNSTARTED = "unstarted"
    UNAVAILABLE = "unavailable"
    TECHNICAL_FAILURE = "technical_failure"
    FORBIDDEN_WORK = "forbidden_work"


class Absence(StrEnum):
    SCOPED_COMPLETE_EMPTY = "scoped_complete_empty"
    OTHER_RETURNS = "other_returns"


class Outcome(StrEnum):
    INADMISSIBLE = "inadmissible"
    INCOMPLETE = "incomplete_technical"
    BELOW_FLOOR = "inconclusive_floor"
    PRESENT = "matched_signal_present"
    NOT_DEMONSTRATED = "signal_not_demonstrated"


_BODY = (Stratum.BEHAVIOR, Stratum.CONTEXT, Stratum.TESTS)
_ARMS = (Arm.NATIVE, Arm.LEXICAL, Arm.FUSION)
_ORDERS = tuple(permutations(_ARMS))
_CONTROL_ORDER = (Stratum.ANCHOR,) * 4 + tuple(
    kind for kind in (Stratum.ABSENT, Stratum.AMBIGUOUS, Stratum.PRIVACY, Stratum.UNSUPPORTED)
    for _ in range(2)
)
_BODY_COUNTS = Counter((family, wording) for family in _BODY for wording in tuple(Wording) * 2)
_EFFECT = Fraction(1, 20)
_METRIC_SCALE = 1_000_000
_CASE_COUNT = 24
_BODY_COUNT = 12


@dataclass(frozen=True, slots=True)
class Case:
    ordinal: int
    stratum: Stratum
    wording: Wording | None = None

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or not 1 <= self.ordinal <= _CASE_COUNT:
            raise ValueError("matched_design:case_identity")
        if not isinstance(self.stratum, Stratum):
            raise ValueError("matched_design:case_identity")
        if self.stratum in _BODY:
            if not isinstance(self.wording, Wording):
                raise ValueError("matched_design:wording")
        elif self.wording is not None:
            raise ValueError("matched_design:wording")


@dataclass(frozen=True, slots=True)
class Slot:
    ordinal: int
    arm: Arm

    def __post_init__(self) -> None:
        if (
            type(self.ordinal) is not int
            or not 1 <= self.ordinal <= _CASE_COUNT
            or not isinstance(self.arm, Arm)
        ):
            raise ValueError("matched_design:slot_identity")


def _ordered_cases(cases: Sequence[Case]) -> tuple[Case, ...]:
    if not isinstance(cases, Sequence) or any(not isinstance(case, Case) for case in cases):
        raise ValueError("matched_design:case_sequence")
    ordered = tuple(sorted(cases, key=lambda case: case.ordinal))
    if tuple(case.ordinal for case in ordered) != tuple(range(1, _CASE_COUNT + 1)):
        raise ValueError("matched_design:case_ordinals")
    if Counter((case.stratum, case.wording) for case in ordered[:_BODY_COUNT]) != _BODY_COUNTS:
        raise ValueError("matched_design:body_census")
    if tuple(case.stratum for case in ordered[_BODY_COUNT:]) != _CONTROL_ORDER:
        raise ValueError("matched_design:control_order")
    return ordered


def plan_slots(cases: Sequence[Case]) -> tuple[Slot, ...]:
    """Validate the fixed census and return the answer-blind balanced schedule."""
    return tuple(
        Slot(case.ordinal, arm)
        for case in _ordered_cases(cases)
        for arm in _ORDERS[(case.ordinal - 1) % len(_ORDERS)]
    )


@dataclass(frozen=True, slots=True)
class FileMetrics:
    ndcg: float
    mrr: float
    recall: float

    def __post_init__(self) -> None:
        for value in (self.ndcg, self.mrr, self.recall):
            if type(value) not in (int, float) or not 0 <= value <= 1:
                raise ValueError("matched_design:metric_range")
            if (_METRIC_SCALE * Fraction(str(value))).denominator != 1:
                raise ValueError("matched_design:metric_precision")
        if not (bool(self.ndcg) == bool(self.mrr) == bool(self.recall)) or 0 < self.mrr < 0.1:
            raise ValueError("matched_design:metric_consistency")


@dataclass(frozen=True, slots=True)
class Observation:
    state: ObservationState
    metrics: FileMetrics | None = None
    control_correct: bool | None = None
    absence: Absence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, ObservationState):
            raise ValueError("matched_design:observation_state")
        if self.metrics is not None and (
            not isinstance(self.metrics, FileMetrics) or self.state is not ObservationState.RANKED
        ):
            raise ValueError("matched_design:metric_state")
        if self.absence is not None and (
            not isinstance(self.absence, Absence) or self.state is not ObservationState.RANKED
        ):
            raise ValueError("matched_design:absence_state")
        if self.state is ObservationState.CONTROL_CHECKED and self.control_correct is None:
            raise ValueError("matched_design:missing_control")
        if self.control_correct is not None and (
            type(self.control_correct) is not bool
            or self.state not in (ObservationState.CONTROL_CHECKED, ObservationState.RANKED)
        ):
            raise ValueError("matched_design:control_state")
        if sum(value is not None for value in (self.metrics, self.absence, self.control_correct)) > 1:
            raise ValueError("matched_design:conflicting_evidence")


@dataclass(frozen=True, slots=True)
class MetricDelta:
    ndcg: Fraction
    mrr: Fraction
    recall: Fraction


@dataclass(frozen=True, slots=True)
class Contrast:
    baseline: Arm
    mean: MetricDelta
    fusion_mean: MetricDelta
    baseline_mean: MetricDelta
    by_ordinal: tuple[tuple[int, MetricDelta], ...]
    by_family: tuple[tuple[Stratum, MetricDelta], ...]
    by_wording: tuple[tuple[Wording, MetricDelta], ...]


@dataclass(frozen=True, slots=True)
class Assessment:
    outcome: Outcome
    contrasts: tuple[Contrast, ...] = ()
    absence_gaps: tuple[Slot, ...] = ()


def _exact(metrics: FileMetrics) -> MetricDelta:
    return MetricDelta(Fraction(str(metrics.ndcg)), Fraction(str(metrics.mrr)), Fraction(str(metrics.recall)))


def _difference(left: MetricDelta, right: MetricDelta) -> MetricDelta:
    return MetricDelta(left.ndcg - right.ndcg, left.mrr - right.mrr, left.recall - right.recall)


def _mean(rows: Sequence[MetricDelta]) -> MetricDelta:
    count = len(rows)
    return MetricDelta(
        sum((row.ndcg for row in rows), Fraction()) / count,
        sum((row.mrr for row in rows), Fraction()) / count,
        sum((row.recall for row in rows), Fraction()) / count,
    )


def _contrast(
    baseline: Arm, positives: Sequence[Case], observations: Mapping[Slot, Observation],
) -> Contrast:
    fusion_rows = []
    baseline_rows = []
    differences = {}
    for case in positives:
        fusion = observations[Slot(case.ordinal, Arm.FUSION)].metrics
        comparator = observations[Slot(case.ordinal, baseline)].metrics
        assert fusion is not None and comparator is not None
        left, right = _exact(fusion), _exact(comparator)
        fusion_rows.append(left)
        baseline_rows.append(right)
        differences[case.ordinal] = _difference(left, right)
    return Contrast(
        baseline,
        _mean(tuple(differences.values())),
        _mean(fusion_rows),
        _mean(baseline_rows),
        tuple(differences.items()),
        tuple(
            (family, _mean([differences[case.ordinal] for case in positives if case.stratum is family]))
            for family in _BODY
        ),
        tuple(
            (wording, _mean([differences[case.ordinal] for case in positives if case.wording is wording]))
            for wording in Wording
        ),
    )


def _passes_quality(contrast: Contrast) -> bool:
    return (
        contrast.mean.ndcg >= _EFFECT
        and contrast.mean.mrr >= 0
        and contrast.mean.recall >= 0
        and all(delta.ndcg >= -_EFFECT for _, delta in contrast.by_family)
    )


def _planned_refusal(case: Case, slot: Slot) -> bool:
    return case.stratum is Stratum.PRIVACY or (
        case.stratum is Stratum.UNSUPPORTED and slot.arm is Arm.FUSION
    )


def _ready_observation(case: Case, slot: Slot, row: Observation) -> bool:
    if _planned_refusal(case, slot):
        return row.state is ObservationState.CONTROL_CHECKED
    if row.state is not ObservationState.RANKED:
        return False
    if case.stratum is Stratum.ABSENT:
        return row.absence is not None
    if case.stratum is Stratum.UNSUPPORTED:
        return row.control_correct is not None
    return row.metrics is not None


def _below_floor_observation(case: Case, slot: Slot, row: Observation) -> bool:
    return row.state is ObservationState.UNSTARTED or (
        _planned_refusal(case, slot) and row.state is ObservationState.CONTROL_CHECKED and row.control_correct is True
    )


def _validate_evidence(
    preflight: Preflight, postflight: Postflight, observations: Mapping[Slot, Observation],
) -> None:
    if not isinstance(preflight, Preflight) or not isinstance(postflight, Postflight):
        raise ValueError("matched_design:flight_state")
    if not isinstance(observations, Mapping) or any(
        not isinstance(key, Slot) or not isinstance(row, Observation)
        for key, row in observations.items()
    ):
        raise ValueError("matched_design:ledger_shape")


def assess(
    cases: Sequence[Case],
    preflight: Preflight,
    observations: Mapping[Slot, Observation],
    *,
    postflight: Postflight,
) -> Assessment:
    """Screen a closed, already graded ledger and independent integrity dispositions."""
    _validate_evidence(preflight, postflight, observations)
    slots = plan_slots(cases)
    by_ordinal = {case.ordinal: case for case in cases}
    if (
        preflight is Preflight.INADMISSIBLE
        or postflight is Postflight.INADMISSIBLE
        or any(
            row.state is ObservationState.FORBIDDEN_WORK
            or (row.state is ObservationState.CONTROL_CHECKED and not _planned_refusal(by_ordinal[key.ordinal], key))
            or (row.state not in (ObservationState.UNSTARTED, ObservationState.CONTROL_CHECKED)
                and (preflight is not Preflight.READY or _planned_refusal(by_ordinal[key.ordinal], key)))
            for key, row in observations.items()
        )
    ):
        return Assessment(Outcome.INADMISSIBLE)
    if (
        preflight is Preflight.TECHNICAL_FAILURE
        or postflight is Postflight.TECHNICAL_FAILURE
        or observations.keys() != set(slots)
    ):
        return Assessment(Outcome.INCOMPLETE)
    if preflight is Preflight.BELOW_FLOOR:
        if all(_below_floor_observation(by_ordinal[key.ordinal], key, row)
               for key, row in observations.items()):
            return Assessment(Outcome.BELOW_FLOOR)
        return Assessment(Outcome.INCOMPLETE)
    if not all(_ready_observation(by_ordinal[key.ordinal], key, row)
               for key, row in observations.items()):
        return Assessment(Outcome.INCOMPLETE)

    positives = tuple(by_ordinal[ordinal] for ordinal in range(1, _BODY_COUNT + 1))
    contrasts = tuple(_contrast(arm, positives, observations) for arm in (Arm.NATIVE, Arm.LEXICAL))
    gaps = tuple(key for key in slots if observations[key].absence is Absence.OTHER_RETURNS)
    correct_controls = all(row.control_correct is not False for row in observations.values())
    outcome = (
        Outcome.PRESENT if correct_controls and all(map(_passes_quality, contrasts))
        else Outcome.NOT_DEMONSTRATED
    )
    return Assessment(outcome, contrasts, gaps)
