"""Open the committed grading envelope only after verified journal closure."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common_lore_index.retrieval.query_contracts import EvaluationLocator
from retrieval_body_study_journal_io import JournalRefusal
from retrieval_evaluation_schema import GoldTarget
from retrieval_semantic_file_projection import compute_file_level_ranking_metrics
from retrieval_semantic_representation import RepresentationValidationError
from retrieval_supplement_primitives import check_keys
from retrieval_w5_matched_archive import read_matched_archive
from retrieval_w5_matched_attempt import ClosedRanking, RankCoverage, RankingEvent
from retrieval_w5_matched_design import Absence, Assessment, FileMetrics, Observation, ObservationState, Outcome, Postflight, Preflight, Slot, Stratum, assess
from retrieval_w5_matched_inputs import exact_rows, read_json, require_match
from retrieval_w5_matched_publications import MatchedPublications, refusal_for

_MEMBERS = {"cohort.json", "golds.json", "literal-expectations.json"}


@dataclass(frozen=True, slots=True)
class GradingEnvelope:
    targets: tuple[tuple[GoldTarget, ...], ...] = field(repr=False)
    literal_files: Mapping[int, tuple[tuple[str, str], ...]] = field(repr=False)


def _locator(
    row: dict[str, Any], publications: MatchedPublications, ordinal: int,
) -> tuple[str, str]:
    require_match(isinstance(row["binding"], str) and isinstance(row["path"], str), "gold_locator")
    alias, path = row["binding"], row["path"]
    task = publications.inputs.tasks[ordinal - 1]
    require_match(alias in task.bindings and alias in publications.binding_ids, "gold_binding")
    key = (publications.binding_ids[alias], path)
    require_match(key in publications.inventory, "gold_inventory")
    return key


def _control(ordinal: int) -> str:
    return (
        "none" if ordinal <= 16 else "answer_absent" if ordinal <= 18 else "ambiguous" if ordinal <= 20
        else "binding_privacy_not_authorized" if ordinal <= 22
        else "literal_scoped_match" if ordinal == 23 else "literal_scoped_complete_empty"
    )


def _load_envelope(
    root: Path, name: str, publications: MatchedPublications,
) -> GradingEnvelope:
    commitment = publications.inputs.commitment
    members = read_matched_archive(root, name, expected_sha256=commitment.grading_sha256)
    require_match(set(members) == _MEMBERS, "grade_inventory")
    cohort = check_keys(read_json(members, "cohort.json"), ("cohort", "frame_sha256", "cases_sha256"), "matched_grade")
    require_match(cohort == {"cohort": commitment.cohort, "frame_sha256": commitment.frame_sha256,
                             "cases_sha256": commitment.cases_sha256}, "grade_binding")
    parsed = []
    for ordinal, audit in enumerate(exact_rows(read_json(members, "golds.json"), 24), 1):
        fields = {"ordinal", "golds", "control"}
        require_match(type(audit) is dict and fields <= set(audit) <= fields | {"justification"}, "gold_shape")
        require_match(type(audit["ordinal"]) is int and audit["ordinal"] == ordinal
                      and audit["control"] == _control(ordinal), "gold_control")
        count = 1 if ordinal<=16 else 2 if ordinal in (19, 20) else 0
        targets = {}
        grade = 3 if ordinal<=16 else 2
        for item in exact_rows(audit["golds"], count):
            row = check_keys(item, ("binding", "path", "grade"), "matched_gold")
            key = _locator(row, publications, ordinal)
            require_match(type(row["grade"]) is int and row["grade"] == grade and key not in targets, "gold_relevance")
            targets[key] = GoldTarget(EvaluationLocator(*key), grade, "committed matched-study gold")
        parsed.append(tuple(targets.values()))

    literal = {}
    for ordinal, item in enumerate(exact_rows(read_json(members, "literal-expectations.json"), 2), 23):
        row = check_keys(item, ("ordinal", "expected_outcome", "expected_files"), "matched_literal")
        require_match(type(row["ordinal"]) is int and row["ordinal"] == ordinal
                      and row["expected_outcome"] == _control(ordinal), "literal_expectation")
        literal[ordinal] = tuple(
            _locator(check_keys(value, ("binding", "path"), "matched_literal_file"), publications, ordinal)
            for value in exact_rows(row["expected_files"], 1 if ordinal == 23 else 0)
        )
    return GradingEnvelope(tuple(parsed), literal)


def _ungraded(event: RankingEvent) -> Observation:
    return Observation(event.state, control_correct=event.refusal_correct)


def _eligible(publications: MatchedPublications, event: RankingEvent) -> bool:
    task = publications.inputs.tasks[event.slot.ordinal - 1]
    if refusal_for(task, event.slot.arm) is not None:
        return event.state is ObservationState.CONTROL_CHECKED
    if event.state is not ObservationState.RANKED:
        return False
    stratum = task.case.stratum
    if stratum is Stratum.ABSENT:
        return bool(event.hits) or event.coverage is RankCoverage.COMPLETE
    if stratum is Stratum.UNSUPPORTED:
        return event.coverage is RankCoverage.COMPLETE
    return True


def _grade(
    event: RankingEvent, envelope: GradingEnvelope, publications: MatchedPublications,
) -> Observation:
    ordinal = event.slot.ordinal
    task = publications.inputs.tasks[ordinal - 1]
    if event.state is ObservationState.CONTROL_CHECKED:
        return _ungraded(event)
    if task.case.stratum is Stratum.ABSENT:
        return Observation(
            ObservationState.RANKED, absence=Absence.OTHER_RETURNS if event.hits else Absence.SCOPED_COMPLETE_EMPTY,
        )
    if task.case.stratum is Stratum.UNSUPPORTED:
        files = tuple(dict.fromkeys(hit.file_key() for hit in event.hits))
        return Observation(ObservationState.RANKED, control_correct=files == envelope.literal_files[ordinal])
    # The committed matched journal already binds each hit to this task's exact
    # public scope. Use the generic file scorer, without changing calibration's
    # guarded task-aware entry point.
    selected = publications.selected_ids(task)
    require_match(all(hit.binding_id in selected and hit.file_key() in publications.inventory
                      for hit in event.hits), "grade_hit_scope")
    metrics = compute_file_level_ranking_metrics(
        event.hits, envelope.targets[ordinal - 1], verified_pin=publications.pin_for(event.slot.arm), cutoffs=(10,),
    )
    grade = FileMetrics(metrics["file_ndcg@10"], metrics["file_mrr@10"], metrics["file_recall@10"])
    return Observation(ObservationState.RANKED, metrics=grade)


def grade_closed_ranking(
    journal: ClosedRanking, publications: MatchedPublications, *, grade_root: Path, archive_name: str,
) -> Assessment:
    """Read grading bytes only after the fixed, admissible ranked ledger closes."""
    require_match(type(journal) is ClosedRanking, "unclosed_grade_request")
    cases = tuple(task.case for task in publications.inputs.tasks)
    try:
        journal.revalidate(publications)
        require_match(tuple(event.slot for event in journal.events) == publications.inputs.slots, "grade_slot_census")
        ungraded = assess(cases, journal.preflight, {row.slot: _ungraded(row) for row in journal.events},
                          postflight=journal.postflight)
        publications.revalidate()
    except (JournalRefusal, ValueError):
        return Assessment(Outcome.INADMISSIBLE)
    except OSError:
        return Assessment(Outcome.INADMISSIBLE if journal.postflight is Postflight.INADMISSIBLE else Outcome.INCOMPLETE)
    if (ungraded.outcome is Outcome.INADMISSIBLE or journal.preflight is not Preflight.READY
            or journal.postflight is not Postflight.VERIFIED):
        return ungraded
    if not all(_eligible(publications, event) for event in journal.events):
        return Assessment(Outcome.INCOMPLETE)
    envelope = _load_envelope(grade_root, archive_name, publications)
    observations: dict[Slot, Observation] = {}
    try:
        for event in journal.events:
            observations[event.slot] = _grade(event, envelope, publications)
        journal.revalidate(publications)
        publications.revalidate()
    except (JournalRefusal, RepresentationValidationError, ValueError):
        return Assessment(Outcome.INADMISSIBLE)
    except OSError:
        return Assessment(Outcome.INCOMPLETE)
    return assess(cases, journal.preflight, observations, postflight=journal.postflight)
