"""Exact-address, preflight, refusal and grading proof over synthetic archives."""

from dataclasses import replace
import gzip
from hashlib import sha256
import json
from pathlib import Path

import pytest

from retrieval_body_study_journal_io import JournalRefusal

from common_lore_index.retrieval.query_contracts import (
    QueryMode, QueryScope, refused_envelope, stale_envelope, unavailable_envelope,
)

from common_lore_index_test_support.retrieval_body_admission_fixtures import make_body_publication
from common_lore_index_test_support.retrieval_w5_matched_fixture import (
    EVALUATOR, GRADE, captured_fixture, make_synthetic_archives, ready_fit, synthetic_policy, synthetic_result, synthetic_tar,
)
from retrieval_supplement_primitives import canonical_bytes
from retrieval_w5_matched_archive import read_matched_archive
from retrieval_w5_matched_attempt import MatchedAttempt, RankCoverage
from retrieval_w5_matched_baselines import _lexical_rank
from retrieval_w5_matched_design import (
    Arm, Observation, ObservationState, Outcome, Postflight, Preflight, Slot, assess,
)
from retrieval_w5_matched_grading import grade_closed_ranking
from retrieval_w5_matched_inputs import load_matched_input
from retrieval_w5_matched_publications import MissingObservedIdentity, bind_matched_publications, full_policy_digest


def _bind(tmp_path: Path):
    root = tmp_path / "custody"
    c = make_synthetic_archives().write(root)
    inputs = load_matched_input(root, EVALUATOR, c)
    policy, ids = synthetic_policy()
    sqlite, sqlite_live = make_body_publication(policy=policy)
    postgres, postgres_live = make_body_publication(policy=policy, backend="postgresql")
    captures = [captured_fixture(inputs, policy, pin, ids) for pin in (sqlite, postgres)]
    pair = bind_matched_publications(
        inputs, sqlite=sqlite, postgres=postgres, sqlite_policy=policy, postgres_policy=policy,
        expected_policy_sha256=full_policy_digest(policy),
        read_sqlite_capture=lambda: captures[0], read_postgres_capture=lambda: captures[1],
    )
    ledger = tmp_path / "ledger"
    ledger.mkdir(mode=0o700)
    return pair, root, ledger, captures, sqlite_live, postgres_live


@pytest.mark.parametrize("entry", ("../escape", "/absolute", "bad/./member", "back\\slash"))
def test_archive_refuses_noncanonical_paths(tmp_path: Path, entry: str) -> None:
    raw = synthetic_tar({entry: b"body"})
    (tmp_path / EVALUATOR).write_bytes(raw)
    with pytest.raises(ValueError):
        read_matched_archive(tmp_path, EVALUATOR, expected_sha256=sha256(raw).hexdigest())


@pytest.mark.parametrize("damage", ("gzip", "padding", "wrong_digest", "symlink"))
def test_archive_refuses_changed_container(tmp_path: Path, damage: str) -> None:
    raw = synthetic_tar({"safe": b"body"})
    path = tmp_path / EVALUATOR
    if damage == "symlink":
        (tmp_path / "target").write_bytes(raw)
        path.symlink_to("target")
    else:
        path.write_bytes({"gzip": gzip.compress(raw), "padding": raw + b"\0" * 512}.get(damage, raw))
    expected = "1" * 64 if damage == "wrong_digest" else sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        read_matched_archive(tmp_path, EVALUATOR, expected_sha256=expected)


@pytest.mark.parametrize("mutation,reason", (
    ("schedule", "schedule_identity"), ("lexical", "lexical_plan"), ("private_plan", "native_refusal"),
    ("file", "file_digest"), ("extra_member", "archive_inventory"),
))
def test_exact_input_rechecks_archived_members(tmp_path: Path, mutation: str, reason: str) -> None:
    archive = make_synthetic_archives()
    if mutation == "file":
        archive.evaluator["population/elm/specimen_0.py"] += b"x"
    elif mutation == "extra_member":
        archive.evaluator["evaluator-extra.json"] = b"{}"
    else:
        name, row, updates = {
            "schedule": ("schedule.json", 0, {"ordinal": 2}),
            "lexical": ("lexical-plans.json", 0, {"query": "changed"}),
            "private_plan": ("native-plans.json", 20, {"patterns": ["canopy"]}),
        }[mutation]
        rows = json.loads(archive.evaluator[name])
        rows[row].update(updates)
        archive.evaluator[name] = canonical_bytes(rows)
    root = tmp_path / "eval"
    commitment = archive.write(root, include_grade=False)
    with pytest.raises(ValueError, match=reason):
        load_matched_input(root, EVALUATOR, commitment)


@pytest.mark.parametrize("mutation,reason", (
    ("max_files", "full_policy_binding"), ("copied_input", "unbound_evaluator_input"),
    ("copied_pin", "publication_factory"), ("witness_count", "capture_file_census"),
    ("witness_bytes", "capture_bytes"), ("vector_missing", "fusion_component_readiness"),
))
def test_pair_refuses_invalid_current_policy_and_capture(tmp_path: Path, mutation: str, reason: str) -> None:
    pair, _, _, captures, _, _ = _bind(tmp_path)
    policy, _ = synthetic_policy()
    postgres_policy = replace(policy, max_projected_files=25) if mutation == "max_files" else policy
    assert postgres_policy.policy_digest() == policy.policy_digest()
    if mutation.startswith("witness"):
        rows = captures[1].files
        bad = replace(rows[0], lexical_entries=-1) if mutation == "witness_count" else replace(rows[0], sha256="b" * 64)
        captures[1] = replace(captures[1], files=(bad, *rows[1:]))
    elif mutation == "vector_missing":
        captures[1] = replace(captures[1], vector_generation_id=None)
    with pytest.raises(ValueError, match=reason):
        bind_matched_publications(
            replace(pair.inputs) if mutation == "copied_input" else pair.inputs,
            sqlite=pair.sqlite, postgres=replace(pair.postgres) if mutation == "copied_pin" else pair.postgres,
            sqlite_policy=policy, postgres_policy=postgres_policy, expected_policy_sha256=full_policy_digest(policy),
            read_sqlite_capture=lambda: captures[0], read_postgres_capture=lambda: captures[1],
        )


@pytest.mark.parametrize("mutation,expected", (
    ("missing_fit", Preflight.TECHNICAL_FAILURE), ("missing_baseline", Preflight.TECHNICAL_FAILURE),
    ("missing_query", Preflight.TECHNICAL_FAILURE), ("unreachable", Preflight.BELOW_FLOOR),
    ("oversize", Preflight.BELOW_FLOOR), ("below_census", Preflight.BELOW_FLOOR),
))
def test_preflight_retains_all_unstarted_without_opening_grade(
    tmp_path: Path, mutation: str, expected: Preflight,
) -> None:
    pair, _, ledger, _, _, _ = _bind(tmp_path)
    fit = ready_fit(pair)
    if mutation == "missing_baseline":
        fit = replace(fit, reachable_baselines={})
    elif mutation == "unreachable":
        fit = replace(fit, reachable_baselines=dict.fromkeys(fit.reachable_baselines, False))
    elif mutation == "missing_query":
        fit = replace(fit, query_tokens={})
    elif mutation == "oversize":
        fit = replace(fit, document_tokens=dict.fromkeys(fit.document_tokens, (513,)))
    elif mutation == "below_census":
        fit = replace(fit, document_tokens=dict.fromkeys(fit.document_tokens, ()))
    attempt = MatchedAttempt(pair, None if mutation == "missing_fit" else fit, journal_root=ledger)
    assert attempt.preflight is expected
    assert not attempt.run_next(lambda _: pytest.fail("rank invoked"), check_owner=lambda: None)
    journal = attempt.close(postflight=Postflight.VERIFIED)
    assert len(journal.events) == 72
    assert all(row.state is ObservationState.UNSTARTED for row in journal.events)
    assessment = grade_closed_ranking(journal, pair, grade_root=tmp_path / "absent", archive_name=GRADE)
    assert assessment.outcome is (Outcome.BELOW_FLOOR if expected is Preflight.BELOW_FLOOR else Outcome.INCOMPLETE)


@pytest.mark.parametrize("case,code,postflight", (
    ("missing", "lexical_generation_missing", Postflight.TECHNICAL_FAILURE),
    ("wrong", "observation_identity_drift", Postflight.INADMISSIBLE),
    ("mixed_wrong_missing", "observation_identity_drift", Postflight.INADMISSIBLE),
    ("extra_component", "vector_generation_drift", Postflight.INADMISSIBLE),
    ("missing_with_denied_hit", "hit_scope", Postflight.INADMISSIBLE),
    ("unavailable_drift", "postgres_capture_drift", Postflight.INADMISSIBLE),
    ("drift", "postgres_capture_drift", Postflight.INADMISSIBLE),
    ("pin", "stale_publication_receipt", Postflight.INADMISSIBLE),
    ("owner", "lease lost", Postflight.TECHNICAL_FAILURE),
))
def test_pg_slot_stops_at_missing_or_contradictory_proof(
    tmp_path: Path, case: str, code: str, postflight: Postflight,
) -> None:
    pair, _, ledger, captures, _, pg = _bind(tmp_path)
    attempt = MatchedAttempt(pair, ready_fit(pair), journal_root=ledger)
    assert attempt.run_next(lambda plan: synthetic_result(pair, plan.slot), check_owner=lambda: None)
    seen: list[Slot] = []

    def owner() -> None:
        if case == "owner" and seen:
            raise OSError("lease lost")

    def read(plan):
        seen.append(plan.slot)
        assert plan.slot.arm is Arm.LEXICAL
        result = synthetic_result(pair, plan.slot)
        if case == "missing":
            return replace(result, publication=replace(result.publication, lexical_generation_id=None))
        if case == "wrong":
            return replace(result, publication=replace(result.publication, accepted_run_id=2))
        if case == "mixed_wrong_missing":
            return replace(result, publication=replace(result.publication, publication_bundle_id=None, accepted_run_id=2))
        if case == "extra_component":
            return replace(result, publication=replace(result.publication, vector_generation_id="unexpected"))
        if case == "missing_with_denied_hit":
            return replace(result, publication=replace(result.publication, lexical_generation_id=None),
                           hits=(replace(result.hits[0], binding_id=pair.binding_ids["barred-a"]),))
        if case == "unavailable_drift":
            captures[1] = replace(captures[1], lexical_generation_id="changed")
            return None
        if case == "drift":
            captures[1] = replace(captures[1], lexical_generation_id="changed")
        elif case == "pin":
            pg.advance()
        return result

    error = MissingObservedIdentity if case == "missing" else OSError if case == "owner" else ValueError
    with pytest.raises(error, match=code):
        attempt.run_next(read, check_owner=owner)
    assert len(seen) == 1 and attempt.next_slot() is None
    journal = attempt.close(postflight=Postflight.VERIFIED)
    assert journal.postflight is postflight
    assert journal.events[0].state is ObservationState.RANKED
    assert all(row.state is ObservationState.UNSTARTED for row in journal.events[1:])
    chain = sorted((ledger / "events").glob("*.json"))
    closed = json.loads(chain[-1].read_bytes())
    assert closed["kind"] == "closed" and closed["payload"]["pending_slot"] is True
    assert json.loads(chain[-2].read_bytes())["kind"] == "admitted"
    result = grade_closed_ranking(journal, pair, grade_root=tmp_path / "absent", archive_name=GRADE)
    assert result.outcome is (Outcome.INADMISSIBLE if postflight is Postflight.INADMISSIBLE else Outcome.INCOMPLETE)


@pytest.mark.parametrize(
    "finish", ("normal", "signal", "wrong_literal", "absence_other_returns", "bounded_literal", "copied_journal",
               "changed_journal", "changed_event", "forbidden_rank"),
)
def test_grading_opens_only_after_admissible_journal(tmp_path: Path, finish: str) -> None:
    pair, root, ledger, _, _, _ = _bind(tmp_path)
    attempt = MatchedAttempt(pair, ready_fit(pair), journal_root=ledger)
    executed = []

    def execute(plan):
        executed.append(plan.slot)
        assert plan.task is not None and plan.refusal_code is None
        assert plan.slot.ordinal not in (21, 22)
        assert (plan.lexical_request is not None) is (plan.slot.arm is Arm.LEXICAL)
        result = synthetic_result(pair, plan.slot)
        if finish in ("signal", "wrong_literal", "absence_other_returns") and plan.slot.ordinal <= 12 and plan.slot.arm is not Arm.FUSION:
            return replace(result, hits=())
        if finish == "wrong_literal" and plan.slot.ordinal == 23 and plan.slot.arm is Arm.NATIVE:
            return replace(result, hits=())
        if finish == "absence_other_returns" and plan.slot.ordinal == 17:
            return replace(result, hits=synthetic_result(pair, Slot(1, plan.slot.arm)).hits)
        if finish == "bounded_literal" and plan.slot.ordinal == 24:
            return replace(result, coverage=RankCoverage.BOUNDED_PAGE)
        return result

    if finish == "forbidden_rank":
        while (plan := attempt.next_slot()) is not None and plan.refusal_code is None:
            assert attempt.run_next(execute, check_owner=lambda: None)
        assert plan is not None and plan.task is None and plan.slot.ordinal == 21
        assert plan.lexical_request is None and plan.selected_binding_ids == ()
        with pytest.raises(ValueError, match="forbidden_ranked_slot"):
            attempt.record_ranked(plan.slot, synthetic_result(pair, plan.slot))
        assert not attempt.run_next(execute, check_owner=lambda: None)
    else:
        while attempt.run_next(execute, check_owner=lambda: None):
            pass
        assert len(executed) == 64
    journal = attempt.close(postflight=Postflight.VERIFIED)
    if finish in {"copied_journal", "changed_journal", "changed_event"}:
        if finish == "changed_journal":
            (ledger / "matched-ranking.json").write_bytes(b"{}")
        if finish == "changed_event":
            event = ledger / "events/00000.json"
            content = json.loads(event.read_bytes())
            content["payload"]["planned_slots"] = 73
            event.write_bytes(canonical_bytes(content, ascii_only=True) + b"\n")
        result = grade_closed_ranking(replace(journal) if finish == "copied_journal" else journal,
                                      pair, grade_root=tmp_path / "absent", archive_name=GRADE)
        assert result.outcome is Outcome.INADMISSIBLE
        return
    if finish in ("bounded_literal", "forbidden_rank"):
        root = tmp_path / "absent"
    result = grade_closed_ranking(journal, pair, grade_root=root, archive_name=GRADE)
    if finish == "normal":
        assert sum(row.observed is not None for row in journal.events) == 64
    assert result.outcome is {
        "normal": Outcome.NOT_DEMONSTRATED,
        "signal": Outcome.PRESENT,
        "wrong_literal": Outcome.NOT_DEMONSTRATED,
        "absence_other_returns": Outcome.PRESENT,
        "bounded_literal": Outcome.INCOMPLETE,
        "forbidden_rank": Outcome.INADMISSIBLE,
    }[finish]
    if finish == "absence_other_returns":
        assert set(result.absence_gaps) == {Slot(17, arm) for arm in Arm}
    with pytest.raises(ValueError, match="journal_close_state"):
        attempt.close(postflight=Postflight.VERIFIED)


def test_valid_multichunk_projection_stops_at_floor_without_rank(tmp_path: Path) -> None:
    original, _, ledger, captures, _, _ = _bind(tmp_path)
    first, *rest = captures[1].files
    capture = replace(captures[1], files=(replace(first, lexical_entries=2), *rest))
    pair = bind_matched_publications(
        original.inputs, sqlite=original.sqlite, postgres=original.postgres,
        sqlite_policy=original.native_policy, postgres_policy=original.native_policy,
        expected_policy_sha256=original.policy_sha256,
        read_sqlite_capture=lambda: captures[0], read_postgres_capture=lambda: capture,
    )
    attempt = MatchedAttempt(pair, ready_fit(pair), journal_root=ledger)
    assert attempt.preflight is Preflight.BELOW_FLOOR
    assert not attempt.run_next(lambda _: pytest.fail("rank invoked"), check_owner=lambda: None)
    assert attempt.close(postflight=Postflight.VERIFIED).preflight is Preflight.BELOW_FLOOR


@pytest.mark.parametrize("failure", ("custody", "reentrant_close"))
def test_owned_attempt_rejects_custody_loss_and_cannot_close_during_rank(tmp_path: Path, failure: str) -> None:
    pair, _, ledger, _, _, _ = _bind(tmp_path)
    attempt = MatchedAttempt(pair, ready_fit(pair), journal_root=ledger)
    lock = ledger / "events/.lock"
    if failure == "custody":
        lock.chmod(0o644)
        with pytest.raises(JournalRefusal, match="journal_custody"):
            attempt.run_next(lambda _: pytest.fail("rank executed"), check_owner=lambda: None)
        lock.chmod(0o600)
    else:
        def execute(_plan):
            attempt.close(postflight=Postflight.VERIFIED)
            pytest.fail("reentrant close succeeded")

        with pytest.raises(ValueError, match="journal_slot_in_progress"):
            attempt.run_next(execute, check_owner=lambda: None)
    assert attempt.next_slot() is None
    assert attempt.close(postflight=Postflight.VERIFIED).postflight is Postflight.INADMISSIBLE


@pytest.mark.parametrize("terminal,reason", (
    ("refused", "baseline_refused_or_stale"), ("stale", "baseline_refused_or_stale"),
    ("unavailable_with_drift", "observation_identity_drift"),
))
def test_lexical_terminal_envelope_preserves_integrity_classification(
    tmp_path: Path, terminal: str, reason: str,
) -> None:
    pair, _, ledger, _, _, _ = _bind(tmp_path)
    attempt = MatchedAttempt(pair, ready_fit(pair), journal_root=ledger)
    assert attempt.run_next(lambda plan: synthetic_result(pair, plan.slot), check_owner=lambda: None)
    plan = attempt.next_slot()
    assert plan is not None and plan.slot.arm is Arm.LEXICAL
    scope = QueryScope(graph_id=pair.postgres.graph_id, backend="postgresql", selected_binding_ids=plan.selected_binding_ids)
    mode = QueryMode.RANKED_DISCOVERY
    if terminal == "refused":
        envelope = refused_envelope(requested_mode=mode, scope=scope, refusal_code="binding_privacy_not_authorized")
    elif terminal == "stale":
        envelope = stale_envelope(requested_mode=mode, scope=scope, detail="lexical_policy_digest_changed")
    else:
        envelope = unavailable_envelope(requested_mode=mode, scope=scope, reason="projection_absent",
                                        provenance={"accepted_run_id": pair.postgres.run_id + 1})
    with pytest.raises(ValueError, match=reason):
        attempt.run_next(lambda active: _lexical_rank(active, pair, envelope), check_owner=lambda: None)
    assert attempt.close(postflight=Postflight.VERIFIED).postflight is Postflight.INADMISSIBLE


def test_below_floor_requires_correct_disposition_if_refusal_completed(tmp_path: Path) -> None:
    pair, _, _, _, _, _ = _bind(tmp_path)
    observations = dict.fromkeys(pair.inputs.slots, Observation(ObservationState.UNSTARTED))
    observations[Slot(21, Arm.NATIVE)] = Observation(ObservationState.CONTROL_CHECKED, control_correct=False)
    result = assess(tuple(task.case for task in pair.inputs.tasks), Preflight.BELOW_FLOOR, observations,
                    postflight=Postflight.VERIFIED)
    assert result.outcome is Outcome.INCOMPLETE


def test_interrupted_reservation_cannot_retry_and_custody_loss_latches(tmp_path: Path) -> None:
    pair, _, ledger, _, _, _ = _bind(tmp_path)
    attempt = MatchedAttempt(pair, ready_fit(pair), journal_root=ledger)

    def interrupted(_plan):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        attempt.run_next(interrupted, check_owner=lambda: None)
    assert attempt.next_slot() is None
    assert not attempt.run_next(lambda _: pytest.fail("pending slot retried"), check_owner=lambda: None)
    lock = ledger / "events/.lock"
    lock.chmod(0o644)
    with pytest.raises(JournalRefusal, match="journal_custody"):
        attempt.close(postflight=Postflight.VERIFIED)
    lock.chmod(0o600)
    assert attempt.close(postflight=Postflight.VERIFIED).postflight is Postflight.INADMISSIBLE


def test_grade_preserves_capture_drift_even_below_floor(tmp_path: Path) -> None:
    pair, _, ledger, captures, _, _ = _bind(tmp_path)
    attempt = MatchedAttempt(pair, None, journal_root=ledger)
    journal = attempt.close(postflight=Postflight.VERIFIED)
    captures[1] = replace(captures[1], lexical_generation_id="post-close-drift")
    assert grade_closed_ranking(journal, pair, grade_root=tmp_path / "absent", archive_name=GRADE).outcome is Outcome.INADMISSIBLE
