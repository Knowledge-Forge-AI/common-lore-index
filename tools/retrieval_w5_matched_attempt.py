"""Prospective admission and close-only ranking journal for the matched cohort."""

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from common_lore_index.retrieval.query_contracts import QueryMode
from retrieval_body_study_files import publish_member, read_member
from retrieval_body_study_journal_io import JournalRefusal, root_identity
from retrieval_semantic_fusion import FileIdentityTuple
from retrieval_semantic_representation import RepresentationValidationError
from retrieval_supplement_primitives import canonical_bytes, digest_json
from retrieval_w5_matched_design import Arm, ObservationState, Postflight, Preflight, Slot
from retrieval_w5_matched_inputs import TaskInput, require_match
from retrieval_w5_matched_journal import MatchedJournal, revalidate_event_seal
from retrieval_w5_matched_publications import MatchedPublications, MissingObservedIdentity, ObservedPublication, refusal_for

_JOURNAL_MEMBER = "matched-ranking.json"
_JOURNAL_BYTES = 1024 * 1024


class RankCoverage(StrEnum):
    COMPLETE = "complete"
    BOUNDED_PAGE = "bounded_page"


@dataclass(frozen=True, slots=True)
class FitObservation:
    """Already-observed token counts, supplied by the future authorized worker."""

    evaluation_sha256: str
    publication_pair_sha256: str
    document_tokens: Mapping[tuple[str, str], tuple[int, ...]]
    query_tokens: Mapping[int, int]
    model_text_bytes: int
    reachable_baselines: Mapping[Slot, bool | None]


def publication_pair_sha256(publications: MatchedPublications) -> str:
    return digest_json({
        "sqlite": publications.sqlite.to_dict(), "postgres": publications.postgres.to_dict(),
        "full_policy_sha256": publications.policy_sha256,
        "captures": [asdict(publications.sqlite_capture), asdict(publications.postgres_capture)],
    }).removeprefix("sha256:")


def classify_preflight(publications: MatchedPublications, fit: FitObservation | None) -> Preflight:
    """Require an exhaustive document and scoped fusion-query census before ranking."""
    if fit is None:
        return Preflight.TECHNICAL_FAILURE
    require_match(type(fit) is FitObservation
                  and fit.evaluation_sha256 == publications.inputs.commitment.evaluation_sha256
                  and fit.publication_pair_sha256 == publication_pair_sha256(publications), "fit_binding")
    documents = {(file.binding, file.path) for file in publications.inputs.files}
    queries = {task.case.ordinal for task in publications.inputs.tasks if refusal_for(task, Arm.FUSION) is None}
    baselines = {
        slot for slot in publications.inputs.slots
        if slot.arm is not Arm.FUSION
        and refusal_for(publications.inputs.tasks[slot.ordinal - 1], slot.arm) is None
    }
    if (fit.document_tokens.keys() != documents or fit.query_tokens.keys() != queries
            or fit.reachable_baselines.keys() != baselines
            or any(value is None for value in fit.reachable_baselines.values())):
        return Preflight.TECHNICAL_FAILURE
    require_match(all(type(value) is bool for value in fit.reachable_baselines.values()), "fit_baseline_shape")
    counts = tuple(fit.document_tokens.values())
    require_match(all(type(values) is tuple for values in counts), "fit_document_shape")
    require_match(all(type(key) is int for key in fit.query_tokens), "fit_query_identity")
    document_tokens = tuple(value for values in counts for value in values)
    all_tokens = document_tokens + tuple(fit.query_tokens.values())
    require_match(all(type(value) is int and value >= 0 for value in all_tokens)
                  and type(fit.model_text_bytes) is int and fit.model_text_bytes > 0, "fit_measurement")
    eligible = {key for key, counts in fit.document_tokens.items() if counts}
    if (len(eligible) < 22 or {alias for alias, _ in eligible} != set(publications.inputs.public_bindings)
            or not all(fit.reachable_baselines.values())
            or any(row.lexical_entries != 1 for row in publications.postgres_capture.files)
            or len(document_tokens) > 80 or len(queries) > 24
            or len(all_tokens) > 500 or min(all_tokens) == 0 or max(all_tokens) > 512 or fit.model_text_bytes > 8 * 1024 * 1024):
        return Preflight.BELOW_FLOOR
    return Preflight.READY


@dataclass(frozen=True, slots=True)
class SlotPlan:
    slot: Slot
    task: TaskInput | None = field(repr=False)
    refusal_code: str | None
    selected_binding_ids: tuple[str, ...] = ()
    lexical_request: LexicalQueryRequest | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class RankedObservation:
    publication: ObservedPublication
    hits: tuple[FileIdentityTuple, ...] = field(repr=False)
    coverage: RankCoverage
    effective_mode: QueryMode
    fallback: bool = False


@dataclass(frozen=True, slots=True)
class RankingEvent:
    slot: Slot
    state: ObservationState
    hits: tuple[FileIdentityTuple, ...] = field(default=(), repr=False)
    coverage: RankCoverage | None = None
    refusal_correct: bool | None = None
    effective_mode: QueryMode | None = None
    fallback: bool = False
    observed: ObservedPublication | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ordinal": self.slot.ordinal, "arm": self.slot.arm.value, "state": self.state.value,
            "hits": [hit.to_dict() for hit in self.hits],
            "coverage": None if self.coverage is None else self.coverage.value,
            "refusal_correct": self.refusal_correct,
            "effective_mode": None if self.effective_mode is None else self.effective_mode.value,
            "fallback": self.fallback,
            "observed_publication": None if self.observed is None else asdict(self.observed),
        }


@dataclass(frozen=True, slots=True)
class ClosedRanking:
    """Receipt of an exclusively published, exact-byte-read-back ranking journal."""

    root: Path
    owner_identity: tuple[int, int] = field(repr=False)
    evaluation_sha256: str
    cases_sha256: str
    grading_sha256: str
    pair_sha256: str
    journal_sha256: str
    event_seal: str
    preflight: Preflight
    postflight: Postflight
    events: tuple[RankingEvent, ...] = field(repr=False)
    _issued_identity: int | None = field(default=None, init=False, repr=False, compare=False)

    def document(self) -> bytes:
        return canonical_bytes({
            "schema": "cloe-w5-matched-ranking-v1", "evaluation_sha256": self.evaluation_sha256,
            "cases_sha256": self.cases_sha256, "grading_sha256": self.grading_sha256,
            "publication_pair_sha256": self.pair_sha256, "event_seal": self.event_seal, "preflight": self.preflight.value,
            "postflight": self.postflight.value, "slots": [event.to_dict() for event in self.events],
        })

    def revalidate(self, publications: MatchedPublications) -> None:
        require_match(self._issued_identity == id(self) and publications.is_factory_issued(), "unclosed_journal")
        require_match(root_identity(self.root) == self.owner_identity, "journal_parent_replaced")
        commitment = publications.inputs.commitment
        require_match(
            (self.evaluation_sha256, self.cases_sha256, self.grading_sha256, self.pair_sha256)
            == (commitment.evaluation_sha256, commitment.cases_sha256, commitment.grading_sha256,
                publication_pair_sha256(publications)), "journal_binding",
        )
        raw = read_member(self.root, _JOURNAL_MEMBER, max_bytes=_JOURNAL_BYTES,
                          expected_sha256="sha256:" + self.journal_sha256)
        require_match(raw == self.document(), "journal_readback")
        revalidate_event_seal(self.root, self.event_seal)
        require_match(root_identity(self.root) == self.owner_identity, "journal_parent_replaced")


class MatchedAttempt:
    """Single-threaded source adapter for an authorized runner's owned attempt.

    The adapter hands back bound plans and validates supplied observations. It
    contains no query, filesystem search or model invocation.
    """

    def __init__(self, publications: MatchedPublications, fit: FitObservation | None, *, journal_root: Path) -> None:
        self._publications = publications
        publications.revalidate()
        self._preflight = classify_preflight(publications, fit)
        self._journal = MatchedJournal(journal_root, {
            "evaluation_sha256": publications.inputs.commitment.evaluation_sha256,
            "publication_pair_sha256": publication_pair_sha256(publications),
            "sqlite_preflight": publications.sqlite.to_dict(),
            "postgres_preflight": publications.postgres.to_dict(),
            "preflight": self._preflight.value,
            "planned_slots": len(publications.inputs.slots),
        })
        self._pending: Slot | None = None
        self._executing = False
        self._events: list[RankingEvent] = []
        self._closed = False
        self._fault = Postflight.VERIFIED

    @property
    def preflight(self) -> Preflight:
        return self._preflight

    def next_slot(self) -> SlotPlan | None:
        return None if self._pending is not None else self._plan_next()

    def _plan_next(self) -> SlotPlan | None:
        if self._closed or self._fault is not Postflight.VERIFIED or self._preflight is not Preflight.READY:
            return None
        inputs = self._publications.inputs
        if len(self._events) == len(inputs.slots):
            return None
        slot = inputs.slots[len(self._events)]
        task = inputs.tasks[slot.ordinal - 1]
        refusal = refusal_for(task, slot.arm)
        if refusal is not None:
            return SlotPlan(slot, None, refusal)
        selected = tuple(self._publications.binding_ids[alias] for alias in task.bindings)
        lexical = None
        if slot.arm is Arm.LEXICAL:
            require_match(task.lexical is not None, "missing_lexical_plan")
            assert task.lexical is not None
            lexical = replace(task.lexical, selected_binding_ids=selected)
        return SlotPlan(slot, task, None, selected, lexical)

    def run_next(
        self, execute: Callable[[SlotPlan], RankedObservation | None], *, check_owner: Callable[[], None],
    ) -> bool:
        """Dispatch a single rank only after admission and a live ownership check."""
        self._fault_if_executing()
        plan = self.next_slot()
        if plan is None:
            return False
        self._executing = True
        try:
            check_owner()
            if plan.refusal_code is not None:
                self._begin(plan)
                self._retain(RankingEvent(
                    plan.slot, ObservationState.CONTROL_CHECKED, refusal_correct=True,
                ))
                return True
            self._publications.revalidate(plan.slot.arm)
            self._begin(plan)
            try:
                result = execute(plan)
            except BaseException as error:
                self._latch(error)
                raise
            finally:
                try:
                    check_owner()
                except BaseException as error:
                    self._latch(error)
                    raise
                finally:
                    self._publications.revalidate(plan.slot.arm)
            require_match(self._fault is Postflight.VERIFIED, "journal_reentrant_work")
            if result is None:
                self._retain(RankingEvent(plan.slot, ObservationState.UNAVAILABLE))
            else:
                require_match(type(result) is RankedObservation, "rank_observation")
                self._record_ranked(plan.slot, result)
        except BaseException as error:
            self._latch(error)
            raise
        finally:
            self._executing = False
        return True

    def _latch(self, error: BaseException) -> None:
        if isinstance(error, (ValueError, JournalRefusal)) and not isinstance(error, MissingObservedIdentity):
            self._fault = Postflight.INADMISSIBLE
        elif self._fault is Postflight.VERIFIED:
            self._fault = Postflight.TECHNICAL_FAILURE

    def _fault_if_executing(self) -> None:
        if self._executing:
            self._fault = Postflight.INADMISSIBLE
            raise ValueError("matched_input:journal_slot_in_progress")

    def _begin(self, plan: SlotPlan) -> None:
        self._journal.append("admitted", {"ordinal": plan.slot.ordinal, "arm": plan.slot.arm.value,
                                          "disposition": plan.refusal_code or "ranked"})
        self._pending = plan.slot

    def _retain(self, event: RankingEvent) -> None:
        require_match(self._pending == event.slot, "journal_slot_unreserved")
        self._journal.append("observed", event.to_dict())
        self._events.append(event)
        self._pending = None

    def _expect(self, slot: Slot) -> SlotPlan:
        expected = self._plan_next()
        require_match(expected is not None and expected.slot == slot, "unadmitted_slot_order")
        assert expected is not None
        return expected

    def record_ranked(self, slot: Slot, result: RankedObservation) -> None:
        """Reject an external rank that has no owned dispatch."""
        self._fault_if_executing()
        self._record_ranked(slot, result)

    def _record_ranked(
        self, slot: Slot, result: RankedObservation,
    ) -> None:
        try:
            expected = self._expect(slot)
            require_match(expected.refusal_code is None, "forbidden_ranked_slot")
            require_match(self._pending == slot, "journal_slot_unreserved")
            require_match(type(result) is RankedObservation, "rank_evidence")
            hits, coverage = result.hits, result.coverage
            require_match(type(coverage) is RankCoverage and type(hits) is tuple and len(hits) <= 10, "rank_evidence")
            assert expected.task is not None
            requested = QueryMode(expected.task.mode)
            effective = QueryMode.LITERAL_CAPTURED_TEXT if slot.arm is Arm.NATIVE else requested
            require_match(result.effective_mode is effective and result.fallback is (requested != effective),
                          "rank_treatment")
            pair = self._publications
            pair.revalidate(slot.arm)
            pin = pair.pin_for(slot.arm)
            bindings = frozenset(expected.selected_binding_ids)
            snapshots = pin.snapshot_by_binding()
            for item in hits:
                require_match(type(item) is FileIdentityTuple, "hit_type")
                hit = item.validate()
                snapshot = snapshots.get(hit.binding_id)
                require_match(
                    hit.binding_id in bindings and hit.file_key() in pair.inventory
                    and snapshot is not None and snapshot[1] == hit.snapshot_id, "hit_scope",
                )
            pair.check_observed(slot, result.publication)
        except BaseException as error:
            self._latch(error)
            raise
        self._retain(RankingEvent(
            slot, ObservationState.RANKED, hits, coverage, effective_mode=result.effective_mode,
            fallback=result.fallback, observed=result.publication,
        ))

    def close(self, *, postflight: Postflight) -> ClosedRanking:
        """Publish the fixed 72-slot retained journal and verify its exact bytes."""
        self._fault_if_executing()
        require_match(not self._closed and type(postflight) is Postflight, "journal_close_state")
        pair = self._publications
        if self._fault is Postflight.INADMISSIBLE:
            postflight = Postflight.INADMISSIBLE
        elif self._fault is Postflight.TECHNICAL_FAILURE and postflight is Postflight.VERIFIED:
            postflight = Postflight.TECHNICAL_FAILURE
        try:
            pair.revalidate()
        except (JournalRefusal, RepresentationValidationError, ValueError):
            postflight = Postflight.INADMISSIBLE
        except OSError:
            if postflight is Postflight.VERIFIED:
                postflight = Postflight.TECHNICAL_FAILURE
        try:
            return self._seal_ranking(postflight)
        except BaseException as error:
            self._latch(error)
            raise

    def _seal_ranking(self, postflight: Postflight) -> ClosedRanking:
        pair = self._publications
        inputs = pair.inputs
        c = inputs.commitment
        tail = tuple(RankingEvent(slot, ObservationState.UNSTARTED) for slot in inputs.slots[len(self._events):])
        events = tuple(self._events) + tail
        require_match(self._pending is None or postflight is not Postflight.VERIFIED, "journal_pending_work")
        self._journal.append("closed", {
            "postflight": postflight.value, "observed_slots": len(self._events), "pending_slot": self._pending is not None,
            "sqlite_postflight": pair.sqlite.to_dict() if postflight is Postflight.VERIFIED else None,
            "postgres_postflight": pair.postgres.to_dict() if postflight is Postflight.VERIFIED else None,
        })
        self._closed = True
        event_seal = self._journal.seal()
        root = self._journal.root
        args = (root, root_identity(root), c.evaluation_sha256, c.cases_sha256, c.grading_sha256, publication_pair_sha256(pair))
        provisional = ClosedRanking(*args, "", event_seal, self.preflight, postflight, events)
        raw = provisional.document()
        require_match(len(raw) <= _JOURNAL_BYTES, "journal_bytes")
        sealed = ClosedRanking(*args, sha256(raw).hexdigest(), event_seal, self.preflight, postflight, events)
        publish_member(root, _JOURNAL_MEMBER, raw)
        object.__setattr__(sealed, "_issued_identity", id(sealed))
        sealed.revalidate(pair)
        return sealed
