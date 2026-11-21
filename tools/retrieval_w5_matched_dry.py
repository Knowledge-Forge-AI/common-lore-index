"""One-use synthetic qualification of the matched W5 controller boundary."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import monotonic_ns

from retrieval_body_study_files import publish_member
from retrieval_body_study_journal_io import root_identity
from retrieval_supplement_primitives import canonical_bytes, digest_json
from retrieval_w5_matched_attempt import ClosedRanking, FitObservation, MatchedAttempt, RankedObservation, SlotPlan
from retrieval_w5_matched_design import ObservationState, Outcome, Postflight
from retrieval_w5_matched_grading import grade_closed_ranking
from retrieval_w5_matched_inputs import InputCommitment, MatchedInput, digest_value, load_matched_input, require_match
from retrieval_w5_matched_publications import MatchedPublications

DRY_COHORT = "synthetic-matched"
EVALUATOR_MEMBER = "dry-evaluator.tar"
GRADING_MEMBER = "dry-grading.tar"
_RESERVATION = "dry-reservation.json"
_TERMINAL = "dry-terminal.json"


@dataclass(frozen=True, slots=True)
class OfflineClosure:
    """Hashes attested by the dry-run launcher before entering the sandbox."""

    worker_source_sha256: str
    artifact_profile_sha256: str
    requirements_sha256: str
    runtime_archive_sha256: str

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            digest_value(value)


@dataclass(frozen=True, slots=True)
class DryRunPlan:
    commitment: InputCommitment
    source_sha256: str
    protocol_sha256: str
    closure: OfflineClosure
    attempt_owner: tuple[int, int]

    def identity(self) -> str:
        require_match(type(self.commitment) is InputCommitment and type(self.closure) is OfflineClosure, "dry_plan_shape")
        require_match(self.commitment.cohort == DRY_COHORT, "dry_cohort")
        digest_value(self.source_sha256)
        digest_value(self.protocol_sha256)
        device, inode = self.attempt_owner
        require_match(type(self.attempt_owner) is tuple and type(device) is int and type(inode) is int
                      and device >= 0 and inode > 0, "dry_plan_owner")
        return digest_json({
            "schema": "cloe-w5-matched-dry-run-v1", "plan": asdict(self),
            "slots": 72, "mode": "synthetic", "runtime_activation": False,
        }).removeprefix("sha256:")


@dataclass(frozen=True, slots=True)
class DryRuntime:
    """An owned, opened fixture runtime with live pins and an inert vector route."""

    publications: MatchedPublications
    fit: FitObservation | None
    execute: Callable[[SlotPlan], RankedObservation | None] = field(repr=False)
    check_owner: Callable[[], None] = field(repr=False)


RuntimeFactory = Callable[[MatchedInput], AbstractContextManager[DryRuntime]]


@dataclass(frozen=True, slots=True)
class DryRunReport:
    plan_sha256: str
    elapsed_ns: int
    status: str
    cleanup: str
    planned_slots: int
    ranked_slots: int
    refused_slots: int
    journal_sha256: str | None

    def document(self, stage: str) -> bytes:
        return canonical_bytes({
            "schema": "cloe-w5-matched-dry-result-v1", "mode": "synthetic", "stage": stage, "report": asdict(self),
        })


def _counts(journal: ClosedRanking | None) -> tuple[int, int]:
    if journal is None:
        return 0, 0
    return (sum(row.state is ObservationState.RANKED for row in journal.events),
            sum(row.state is ObservationState.CONTROL_CHECKED for row in journal.events))


def run_synthetic_dry(
    plan: DryRunPlan, *, expected_plan_sha256: str, observed_closure: OfflineClosure,
    evaluator_root: Path, grade_root: Path, attempt_root: Path,
    open_runtime: RuntimeFactory, cleanup_verified: Callable[[], bool],
) -> DryRunReport:
    """Run one bound synthetic attempt and retain its outcome after cleanup.

    The launcher gives the selected backend an evaluator-only input and owns
    the disposable runtime. The controller reserves before reading the
    evaluator and invokes the grader only from a closed ranking journal.
    """
    require_match(type(plan) is DryRunPlan and plan.identity() == digest_value(expected_plan_sha256), "dry_plan_binding")
    require_match(type(observed_closure) is OfflineClosure and observed_closure == plan.closure, "dry_offline_closure")
    start = monotonic_ns()
    owner = root_identity(attempt_root)
    require_match(owner == plan.attempt_owner, "dry_attempt_owner")
    evaluator_owner = root_identity(evaluator_root)
    require_match(grade_root.is_absolute() and ".." not in grade_root.parts, "dry_grade_path")
    paths = (attempt_root, evaluator_root, grade_root)
    require_match(all(not first.is_relative_to(second) and not second.is_relative_to(first)
                      for index, first in enumerate(paths) for second in paths[index + 1:]), "dry_custody_separation")
    publish_member(attempt_root, _RESERVATION, canonical_bytes({
        "plan_sha256": expected_plan_sha256, "mode": "synthetic", "slots": 72,
    }))
    journal: ClosedRanking | None = None
    result_status = "technical_failure"
    cleanup = "unverified"
    runtime_entered = False
    stage = "evaluator"
    try:
        inputs = load_matched_input(evaluator_root, EVALUATOR_MEMBER, plan.commitment)
        require_match(root_identity(evaluator_root) == evaluator_owner and root_identity(attempt_root) == owner,
                      "dry_custody_drift")
        stage = "runtime"
        runtime_entered = True
        with open_runtime(inputs) as runtime:
            require_match(type(runtime) is DryRuntime and runtime.publications.inputs is inputs, "dry_runtime_binding")
            runtime.check_owner()
            ledger_root = attempt_root / "ranking"
            ledger_root.mkdir(mode=0o700)
            attempt = MatchedAttempt(runtime.publications, runtime.fit, journal_root=ledger_root)
            stage = "ranking"
            try:
                while attempt.run_next(runtime.execute, check_owner=runtime.check_owner):
                    pass
                runtime.check_owner()
            except BaseException:
                journal = attempt.close(postflight=Postflight.TECHNICAL_FAILURE)
                raise
            journal = attempt.close(postflight=Postflight.VERIFIED)
            stage = "grading"
            runtime.check_owner()
            result = grade_closed_ranking(journal, runtime.publications, grade_root=grade_root, archive_name=GRADING_MEMBER)
            runtime.check_owner()
            stage = "cleanup"
        if result.outcome in (Outcome.PRESENT, Outcome.NOT_DEMONSTRATED):
            result_status = "qualified"
        else:
            result_status = result.outcome.value
    finally:
        if runtime_entered:
            try:
                cleanup = "proven" if cleanup_verified() is True else "unverified"
            except Exception:
                cleanup = "unverified"
        else:
            cleanup = "unstarted"
        if runtime_entered and cleanup != "proven":
            result_status = "cleanup_unverified"
        ranked, refused = _counts(journal)
        report = DryRunReport(
            expected_plan_sha256, monotonic_ns() - start, result_status, cleanup, 72, ranked, refused,
            journal.journal_sha256 if journal is not None else None,
        )
        require_match(root_identity(attempt_root) == owner, "dry_custody_drift")
        publish_member(attempt_root, _TERMINAL, report.document(stage))
    return report
