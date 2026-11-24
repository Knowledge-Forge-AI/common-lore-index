"""Chronological body exposure records; journal contents do not mint admission."""
from __future__ import annotations
from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import re
from typing import Any
from retrieval_body_study_common import CommonPositiveSet
from retrieval_body_study_journal_io import (
    MAX_EVENTS, digest, locked, members, metadata, publish, read_event, require, root_identity,
)
from retrieval_body_study_observation import (
    MAX_SEALED_OBSERVATIONS, observation_bytes, observation_hash,
    observation_identity, publish_observation, read_body_failure_envelope,
    read_observation, read_observations,
)
from retrieval_body_study_outcomes import _validate_optional_slot_fields, slot_exposes_quality
from retrieval_body_study_recovery import VALID_RESULT_OUTCOMES
from retrieval_body_study_values import ARMS, REGISTERED_INPUT_PIN_SHA256, SLOT_STATES, is_digest
SCHEMA = "cloe-body-study-attempt-journal-v1"
IDENTITY_FIELDS = frozenset({"attempt_id", "input_pin_sha256", "source_closure_sha256",
    "treatment_sha256", "protocol_sha256", "candidate_snapshot_sha256", "plan_sha256"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_EVENT_FIELDS = frozenset({"schema", "index", "identity_sha256", "previous_sha256",
                          "event", "payload", "event_sha256"})
_OUTCOMES = VALID_RESULT_OUTCOMES | {"incomplete_technical", "inadmissible"}
_ISSUED = object()
def _identity(value: Mapping[str, Any]) -> dict[str, Any]:
    require(isinstance(value, Mapping) and set(value) == IDENTITY_FIELDS, "journal_identity_fields")
    require(type(value["attempt_id"]) is str and _ID.fullmatch(value["attempt_id"]), "journal_attempt_id")
    require(all(is_digest(value[key]) for key in IDENTITY_FIELDS - {"attempt_id"}),
            "journal_identity_digests")
    return dict(value)
def _slot(task_id: Any, arm: Any, repetition: Any) -> tuple[str, str, int]:
    require(type(task_id) is str and _ID.fullmatch(task_id), "journal_task_id")
    require(type(arm) is str and arm in ARMS, "journal_arm")
    require(type(repetition) is int and 1 <= repetition <= 3, "journal_repetition")
    return task_id, arm, repetition
def _tasks(task_ids: Sequence[str]) -> tuple[str, ...]:
    require(isinstance(task_ids, (list, tuple)) and len(task_ids) == 24 and all(type(task) is str and _ID.fullmatch(task) for task in task_ids) and len(set(task_ids)) == 24, "journal_task_population")
    return tuple(sorted(task_ids))
class BodyAttemptJournal:
    def __init__(self, root: Path, identity: Mapping[str, Any], task_ids: Sequence[str],
                 *, functional_only: bool, observation_root: Path, _issued: object = None) -> None:
        require(_issued is _ISSUED, "journal_factory_required")
        self.root = root
        self.identity = _identity(identity)
        self._tasks = _tasks(task_ids)
        self._functional = functional_only
        self._observation_root = observation_root
        self._root = root_identity(root)
        self._identity_sha256 = digest(self.identity)
        self._metadata: dict[str, tuple[int, ...]] = {}
        self._last = "sha256:" + "0" * 64
        self._common: bool | None = None
        self._common_identity: str | None = None
        self._started: set[tuple[str, str, int]] = set()
        self._observed: set[tuple[str, str, int]] = set()
        self._observation_hashes: list[str] = []
        self._retained: dict[tuple[str, str, int], str] = {}
        self._retained_indexes: dict[tuple[str, str, int], int] = {}
        self._sealed_bytes = 0
        self._exposed = False
        self._terminal = False
    @classmethod
    def create(cls, root: Path, *, identity: Mapping[str, Any],
               task_ids: Sequence[str], functional_only: bool = False) -> BodyAttemptJournal:
        checked = _identity(identity)
        tasks = _tasks(task_ids)
        require(type(functional_only) is bool, "journal_functional_mode")
        require((checked["input_pin_sha256"] != REGISTERED_INPUT_PIN_SHA256) == functional_only,
                "journal_input_class_mode")
        require(isinstance(root, Path) and root.is_absolute() and root.resolve() == root,
                "journal_root_path")
        require(not root.exists() and not root.is_symlink(), "journal_root_used")
        observation_root = root.with_name(root.name + "-observations")
        require(isinstance(observation_root, Path) and observation_root.is_absolute()
                and observation_root.resolve() == observation_root
                and not observation_root.exists() and not observation_root.is_symlink(),
                "journal_observation_root")
        root.mkdir(mode=0o700)
        descriptor = os.open(root / ".lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        journal = cls(root, checked, tasks, functional_only=functional_only,
                      observation_root=observation_root, _issued=_ISSUED)
        journal._append("attempt_created", {"identity": checked, "task_ids": list(tasks),
                                            "functional_only": functional_only})
        return journal
    def _current(self) -> None:
        require(members(self.root) == tuple(self._metadata), "journal_history_changed")
        require(all(metadata(self.root / name) == expected for name, expected in self._metadata.items()), "journal_prior_event_changed")
        if self._metadata:
            last = read_event(self.root / next(reversed(self._metadata)))
            claimed = last.pop("event_sha256", None)
            require(claimed == digest(last) == self._last, "journal_head_changed")
    def _append(self, event: str, payload: Mapping[str, Any]) -> None:
        require(not self._terminal, "journal_already_terminal")
        with locked(self.root, self._root):
            self._current()
            index = len(self._metadata)
            require(index < MAX_EVENTS, "journal_event_limit")
            value = {"schema": SCHEMA, "index": index, "identity_sha256": self._identity_sha256, "previous_sha256": self._last, "event": event, "payload": dict(payload)}
            current = digest(value)
            value["event_sha256"] = current
            require(root_identity(self.root) == self._root, "journal_root_replaced")
            meta = publish(self.root, index, value)
            require(root_identity(self.root) == self._root, "journal_root_replaced")
            self._metadata[f"{index:05d}.json"] = meta
            self._last = current
    def freeze_common(self, common: CommonPositiveSet) -> None:
        require(CommonPositiveSet.is_factory_issued(common), "journal_common_factory_required")
        require(self._common is None and not self._started, "journal_common_not_prospective")
        planned = {row["task_id"] for row in common.planned}
        require(len(planned) == 12 and planned <= set(self._tasks) and set(common.task_ids) <= planned,
                "journal_common_population_join")
        self._append("common_set_frozen", {"common_set_sha256": common.identity, "floor_passed": common.floor_passed})
        self._common = common.floor_passed
        self._common_identity = common.identity
    def before_slot(self, task_id: str, arm: str, repetition: int) -> None:
        key = _slot(task_id, arm, repetition)
        require(self._common is True, "journal_query_before_common_floor")
        require(task_id in self._tasks, "journal_foreign_task")
        require(key not in self._started, "journal_duplicate_slot_start")
        self._append("slot_started", {"task_id": key[0], "arm": key[1], "repetition": key[2], "quality_exposure_possible": True, "quality_observation_claimed": False})
        self._started.add(key)
    def retain_observation(self, observation: Any, *, case_kind: str,
                           record_kind: str = "observation") -> Mapping[str, Any]:
        if self._functional:
            slot = observation if isinstance(observation, Mapping) else getattr(observation, "actual", None)
        elif record_kind == "failure" and isinstance(observation, Mapping):
            slot = observation
        else:
            from retrieval_body_study_capture import BodySlotObservation
            require(BodySlotObservation.is_factory_issued(observation),
                    "journal_observation_factory_required")
            slot = observation.policy
        require(isinstance(slot, Mapping), "journal_observation_mapping")
        assert isinstance(slot, Mapping)
        key = _slot(slot.get("task_id"), slot.get("arm"), slot.get("repetition"))
        existing = self._retained.get(key)
        if existing is not None:
            match = read_observation(self._observation_root, self._retained_indexes[key])
            require(match["observation_sha256"] == existing, "journal_observation_hash_join")
            return match
        require(len(self._observation_hashes) < MAX_SEALED_OBSERVATIONS, "journal_observation_limit")
        record = publish_observation(self._observation_root, observation, index=len(self._observation_hashes),
                                     record_kind=record_kind, aggregate_bytes=self._sealed_bytes,
                                     functional_only=self._functional)
        claimed = observation_hash(record)
        self._observation_hashes.append(claimed)
        self._retained[key] = claimed
        self._retained_indexes[key] = len(self._observation_hashes) - 1
        self._sealed_bytes += observation_bytes(record)
        return record
    def retain_failure(self, identity: Mapping[str, Any], *, error_code: str) -> Mapping[str, Any]:
        require(isinstance(identity, Mapping), "journal_failure_identity")
        row = dict(identity)
        row.update({"execution_state": "failed", "ranked_files": (),
                    "returned_hit_count": 0, "error_code": str(error_code),
                    "origin_proven": False, "preparation_only": False,
                    "generation": int(row.get("generation", 1))})
        return self.retain_observation(row, case_kind="failure", record_kind="failure")
    def retained_observations(self) -> tuple[dict[str, Any], ...]:
        if not self._observation_root.exists():
            require(not self._observation_hashes, "journal_observation_root_lost")
            return ()
        rows = read_observations(self._observation_root)
        require(len(rows) == len(self._observation_hashes), "journal_observation_history")
        require(tuple(observation_hash(row) for row in rows) == tuple(self._observation_hashes), "journal_observation_hash_join")
        require(sum(observation_bytes(row) for row in rows) == self._sealed_bytes,
                "journal_observation_size_join")
        return rows
    @property
    def sealed_observation_identity(self) -> str:
        return observation_identity(self.retained_observations())
    def observe(self, observation: Any, *, case_kind: str) -> None:
        if self._functional:
            slot = observation if isinstance(observation, Mapping) else getattr(observation, "policy", None)
        else:
            from retrieval_body_study_capture import BodySlotObservation
            require(BodySlotObservation.is_factory_issued(observation), "journal_observation_factory_required")
            slot = observation.policy
        require(isinstance(slot, Mapping), "journal_observation_mapping")
        assert isinstance(slot, Mapping)
        require({"task_id", "arm", "repetition", "execution_state", "ranked_files"} <= set(slot)
                and type(slot["execution_state"]) is str and slot["execution_state"] in SLOT_STATES,
                "journal_observation_fields")
        try:
            _validate_optional_slot_fields(slot)
        except ValueError:
            require(False, "journal_observation_fields")
        key = _slot(slot.get("task_id"), slot.get("arm"), slot.get("repetition"))
        require(key in self._started and key not in self._observed, "journal_observation_order")
        record = self.retain_observation(observation, case_kind=case_kind)
        exposed = slot_exposes_quality(slot, case_kind)
        self._append("slot_observed", {"task_id": key[0], "arm": key[1], "repetition": key[2],
            "observation_sha256": observation_hash(record), "quality_observed": exposed})
        self._observed.add(key)
        self._exposed = self._exposed or exposed
    def _require_failure_envelope(self, identity: str, stage: str | None,
                                  sealed_identity: str) -> None:
        value = read_body_failure_envelope(self.root.parent)
        require(value.get("report_identity") == identity, "journal_failure_envelope_hash_join")
        require(value.get("sealed_observation_identity") == sealed_identity
                and value.get("journal_observation_identity") == sealed_identity
                and value.get("sealed_observation_count") == len(self._observation_hashes),
                "journal_failure_observation_join")
        require(stage is None or value.get("failure_stage") == stage,
                "journal_failure_stage_join")
    def terminal(self, *, outcome: str, report_sha256: str,
                 integrity_ok: bool | None, ownership_ok: bool | None,
                 cleanup_ok: bool | None, expected_common_identity: str | None = None,
                 failure_envelope_sha256: str | None = None,
                 failure_stage: str | None = None) -> dict[str, Any]:
        require(type(outcome) is str and outcome in _OUTCOMES and is_digest(report_sha256),
                "journal_terminal_identity")
        require(failure_envelope_sha256 is None or is_digest(failure_envelope_sha256),
                "journal_failure_envelope_identity")
        require(failure_stage is None or type(failure_stage) is str, "journal_failure_stage")
        sealed_identity = self.sealed_observation_identity
        if outcome in {"incomplete_technical", "inadmissible"}:
            require(is_digest(failure_envelope_sha256) and type(failure_stage) is str
                    and bool(failure_stage), "journal_failure_evidence_required")
            assert isinstance(failure_envelope_sha256, str) and isinstance(failure_stage, str)
            self._require_failure_envelope(failure_envelope_sha256, failure_stage, sealed_identity)
        else:
            require(failure_envelope_sha256 is None and failure_stage is None,
                    "journal_unexpected_failure_envelope")
        require(all(value is None or type(value) is bool for value in (integrity_ok, ownership_ok, cleanup_ok)), "journal_terminal_flags")
        require(expected_common_identity is None or (
            is_digest(expected_common_identity) and expected_common_identity == self._common_identity),
            "journal_terminal_common_mismatch")
        pending = len(self._started - self._observed)
        if outcome in VALID_RESULT_OUTCOMES:
            require(integrity_ok is True and ownership_ok is True and cleanup_ok is True and pending == 0
                    and self._common is not None, "journal_valid_result_unproved")
            require((outcome == "inconclusive_floor") == (self._common is False),
                    "journal_floor_outcome_mismatch")
            if outcome == "inconclusive_floor":
                require(not self._started and not self._observed and not self._exposed
                        and not self._observation_hashes,
                        "journal_low_floor_has_observations")
            if outcome != "inconclusive_floor":
                expected = {(task, arm, rep) for task in self._tasks for arm in ARMS for rep in (1, 2, 3)}
                require(self._observed == expected
                        and len(self._observation_hashes) == len(self._observed),
                        "journal_valid_result_population_incomplete")
        payload = {"outcome": outcome, "report_sha256": report_sha256,
            "functional_only": self._functional,
            "quality_observed": self._exposed, "possible_unknown_exposure": pending > 0,
            "pending_slots": pending, "observed_slots": len(self._observed),
            "integrity_ok": integrity_ok, "ownership_ok": ownership_ok, "cleanup_ok": cleanup_ok,
            "sealed_observation_count": len(self._observation_hashes),
            "sealed_observation_identity": sealed_identity,
            "failure_envelope_sha256": failure_envelope_sha256,
            "failure_stage": failure_stage}
        self._append("attempt_terminal", payload)
        self._terminal = True
        return {**payload, "journal_sha256": self._last, "identity_sha256": self._identity_sha256,
                "execution_authority_granted": False, "origin_proven": False}
def read_attempt_journal(root: Path, *, identity: Mapping[str, Any],
                         task_ids: Sequence[str], expected_common_identity: str | None = None,
                         functional_only: bool = False) -> tuple[dict[str, Any], ...]:
    expected_identity = _identity(identity)
    require(type(functional_only) is bool
            and (expected_identity["input_pin_sha256"] != REGISTERED_INPUT_PIN_SHA256) == functional_only,
            "journal_input_class_mode")
    tasks = _tasks(task_ids)
    expected_digest = digest(expected_identity)
    root_id = root_identity(root)
    with locked(root, root_id):
        names = members(root)
        require(bool(names), "journal_empty")
        rows = []
        previous = "sha256:" + "0" * 64
        for index, name in enumerate(names):
            row = read_event(root / name)
            require(set(row) == _EVENT_FIELDS and row["schema"] == SCHEMA
                    and type(row["index"]) is int and row["index"] == index
                    and row["identity_sha256"] == expected_digest
                    and row["previous_sha256"] == previous, "journal_chain_join")
            value = dict(row)
            claimed = value.pop("event_sha256")
            require(claimed == digest(value), "journal_chain_digest")
            previous = claimed
            rows.append(row)
        require(rows[0]["event"] == "attempt_created" and rows[0]["payload"] == {
                    "identity": expected_identity, "task_ids": list(tasks),
                    "functional_only": functional_only},
                "journal_creation_join")
        require(rows[-1]["event"] == "attempt_terminal", "journal_incomplete_terminal")
        require(all(row["event"] != "attempt_terminal" for row in rows[:-1]),
                "journal_events_after_terminal")
        observation_root = root.with_name(root.name + "-observations")
        retained = read_observations(observation_root) if observation_root.exists() else ()
        terminal_payload = rows[-1]["payload"]
        failure_envelope = (
            read_body_failure_envelope(root.parent)
            if is_digest(terminal_payload.get("failure_envelope_sha256")) else None
        )
        _validate_replay(rows, tasks, functional_only, retained, failure_envelope)
        common = [row["payload"]["common_set_sha256"] for row in rows if row["event"] == "common_set_frozen"]
        require(len(common) <= 1 and (expected_common_identity is None or common == [expected_common_identity]),
                "journal_expected_common_join")
        return tuple(rows)
def _validate_replay(rows: Sequence[Mapping[str, Any]], tasks: tuple[str, ...],
                    functional_only: bool, retained: Sequence[Mapping[str, Any]],
                    failure_envelope: Mapping[str, Any] | None) -> None:
    common: bool | None = None
    started: set[tuple[str, str, int]] = set()
    observed: set[tuple[str, str, int]] = set()
    exposed = False
    for row in rows[1:]:
        event, value = row["event"], row["payload"]
        require(isinstance(value, dict), "journal_payload_object")
        if event == "common_set_frozen":
            require(set(value) == {"common_set_sha256", "floor_passed"}
                    and common is None and not started
                    and is_digest(value["common_set_sha256"])
                    and type(value["floor_passed"]) is bool, "journal_common_history")
            common = value["floor_passed"]
        elif event in {"slot_started", "slot_observed"}:
            extra = ({"quality_exposure_possible", "quality_observation_claimed"}
                     if event == "slot_started" else {"observation_sha256", "quality_observed"})
            require(set(value) == {"task_id", "arm", "repetition"} | extra,
                    "journal_slot_payload_fields")
            key = _slot(value["task_id"], value["arm"], value["repetition"])
            require(common is True and key[0] in tasks, "journal_slot_history")
            if event == "slot_started":
                require(key not in started and value["quality_exposure_possible"] is True
                        and value["quality_observation_claimed"] is False, "journal_slot_history")
                started.add(key)
            else:
                require(key in started and key not in observed
                        and is_digest(value["observation_sha256"])
                        and type(value["quality_observed"]) is bool, "journal_observation_history")
                observed.add(key)
                exposed = exposed or value["quality_observed"]
        elif event == "attempt_terminal":
            require(set(value) == {"outcome", "report_sha256", "quality_observed",
                "possible_unknown_exposure", "pending_slots", "observed_slots",
                "integrity_ok", "ownership_ok", "cleanup_ok", "functional_only",
                "sealed_observation_count", "sealed_observation_identity",
                "failure_envelope_sha256", "failure_stage"}, "journal_terminal_fields")
            pending = len(started - observed)
            retained_hashes = tuple(observation_hash(row) for row in retained
                                    if row["record_kind"] == "observation")
            require(type(value["outcome"]) is str and value["outcome"] in _OUTCOMES
                    and is_digest(value["report_sha256"])
                    and type(value["pending_slots"]) is int and value["pending_slots"] == pending
                    and type(value["observed_slots"]) is int and value["observed_slots"] == len(observed)
                    and value["quality_observed"] is exposed
                    and value["possible_unknown_exposure"] is (pending > 0)
                    and value["functional_only"] is functional_only
                    and type(value["sealed_observation_count"]) is int
                    and value["sealed_observation_count"] == len(retained)
                    and value["sealed_observation_identity"] == observation_identity(retained)
                    and (value["failure_envelope_sha256"] is None
                         or is_digest(value["failure_envelope_sha256"]))
                    and (value["failure_stage"] is None
                         or type(value["failure_stage"]) is str)
                    and all(value[key] is None or type(value[key]) is bool
                            for key in ("integrity_ok", "ownership_ok", "cleanup_ok")),
                    "journal_terminal_history")
            require(retained_hashes == tuple(
                row["payload"]["observation_sha256"] for row in rows
                if row["event"] == "slot_observed"), "journal_observation_hash_history")
            failure_hash = value["failure_envelope_sha256"]
            if failure_hash is None:
                require(failure_envelope is None and value["failure_stage"] is None,
                        "journal_unexpected_failure_envelope")
            else:
                require(failure_envelope is not None
                        and failure_envelope.get("report_identity") == failure_hash
                        and failure_envelope.get("sealed_observation_identity")
                        == value["sealed_observation_identity"]
                        and failure_envelope.get("journal_observation_identity")
                        == value["sealed_observation_identity"]
                        and failure_envelope.get("sealed_observation_count")
                        == value["sealed_observation_count"]
                        and failure_envelope.get("failure_stage") == value["failure_stage"],
                        "journal_failure_envelope_history")
            if value["outcome"] in VALID_RESULT_OUTCOMES:
                require(all(value[key] is True for key in ("integrity_ok", "ownership_ok", "cleanup_ok"))
                        and pending == 0 and common is not None
                        and (value["outcome"] == "inconclusive_floor") == (common is False),
                        "journal_valid_result_history")
                if not common:
                    require(not started and not observed and not exposed,
                            "journal_low_floor_has_observations")
                if common:
                    require(observed == {(task, arm, rep) for task in tasks for arm in ARMS for rep in (1, 2, 3)},
                            "journal_valid_result_population_incomplete")
                    require(len(retained) == len(observed), "journal_valid_result_observations")
            elif value["outcome"] in {"incomplete_technical", "inadmissible"}:
                require(is_digest(value["failure_envelope_sha256"])
                        and type(value["failure_stage"]) is str and value["failure_stage"],
                        "journal_failure_evidence_required")
        else:
            require(False, "journal_unknown_event")
