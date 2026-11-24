"""Fixed 432-slot actual body-study executor."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from retrieval_body_study_capture import capture_body_slot
from retrieval_body_study_preflight import CommonFreezeReceipt
from retrieval_body_study_values import (
    ARMS, REPETITIONS, SLOT_COUNT, V2_ARM, V2_REPRESENTATION, digest_value, require,
)
from retrieval_evaluation_admission_w5 import _pin_identity
from retrieval_semantic_provenance import VerifiedPublicationPin


@dataclass(frozen=True, slots=True)
class BodySlotBatch:
    actual_slots: tuple[Mapping[str, Any], ...]
    policy_slots: tuple[Mapping[str, Any], ...]
    unstarted_slots: int
    exposure_count: int

    def require_current(self) -> None:
        require(len(self.actual_slots) == SLOT_COUNT, "body.slots:actual_count")
        require(len(self.policy_slots) == SLOT_COUNT, "body.slots:policy_count")
        require(self.unstarted_slots == sum(
            row["execution_state"] == "unstarted" for row in self.policy_slots
        ), "body.slots:unstarted_count")
        require(self.exposure_count == sum(
            row["execution_state"] in {"completed", "refused"} for row in self.policy_slots
        ), "body.slots:exposure_count")


def captured_v2_slot_matches(row: Mapping[str, Any], task: Any, pin: VerifiedPublicationPin) -> bool:
    """Check the ready BODY row against the live PostgreSQL publication."""
    publication = row.get("publication_identity")
    return (
        row.get("arm") == V2_ARM and row.get("preparation") == "ready"
        and row.get("task_id") == task.task_id and row.get("generation") == task.generation
        and row.get("selected_backend") == row.get("source_publication") == pin.backend == "postgresql"
        and row.get("representation_identity") == V2_REPRESENTATION
        and row.get("actual_surface") == task.intended_match_surface == "captured_text"
        and task.comparability_stratum == "body" and task.requested_mode == "ranked_discovery"
        and task.grading_rule in {"ranked_positive", "absent_answer"}
        and isinstance(publication, Mapping)
        and digest_value(publication) == digest_value(_pin_identity(pin))
    )


def _grid(matrix: Mapping[str, Any]) -> list[dict[str, Any]]:
    selected_rows = matrix.get("selected_rows")
    require(isinstance(selected_rows, Sequence), "body.slots:selected_rows")
    assert isinstance(selected_rows, Sequence)
    raw_rows = cast(Sequence[object], selected_rows)
    require(len(raw_rows) == 144, "body.slots:selected_row_count")
    selected: list[Mapping[str, Any]] = []
    for raw_row in raw_rows:
        require(isinstance(raw_row, Mapping), "body.slots:selected_row")
        assert isinstance(raw_row, Mapping)
        selected.append(cast(Mapping[str, Any], raw_row))
    slots: list[dict[str, Any]] = []
    for row in selected:
        for repetition in range(1, REPETITIONS + 1):
            slots.append({
                **dict(row), "task_id": row["task_id"], "arm": row["arm"],
                "repetition": repetition, "generation": row["generation"],
                "execution_state": "unstarted", "ranked_files": (),
            })
    require(len(slots) == SLOT_COUNT, "body.slots:grid_count")
    return slots


def _failed_slot_row(identity: Mapping[str, Any], error: BaseException) -> dict[str, Any]:
    """Retain an operational failure when authority is already lost.

    This row carries no quality, hit, or absence claim.  It is deliberately
    not passed to the journal: a failed pin revalidation means the journal
    remains pending/unknown rather than accepting a caller-created observation.
    """
    return {
        "task_id": identity["task_id"],
        "arm": identity["arm"],
        "repetition": identity["repetition"],
        "generation": identity["generation"],
        "execution_state": "failed",
        "ranked_files": (),
        "returned_hit_count": 0,
        "error_code": type(error).__name__,
        "origin_proven": False,
        "preparation_only": False,
    }


def _retain(operation: Any, observation: Any, *, case_kind: str,
            record_kind: str = "observation") -> None:
    saver = getattr(operation, "retain_observation", None)
    if callable(saver):
        saver(observation, case_kind=case_kind, record_kind=record_kind)


def _retain_failure(operation: Any, identity: Mapping[str, Any], error: BaseException) -> None:
    saver = getattr(operation, "retain_failure", None)
    if callable(saver):
        saver(identity, error_code=type(error).__name__)


def execute_body_slots(
    *,
    preparation: Any,
    common_freeze: CommonFreezeReceipt,
    pins: Mapping[str, VerifiedPublicationPin],
    operation: Any,
    execute: Callable[[Mapping[str, Any], Any], Mapping[str, Any]],
    private_markers: Sequence[str] = (),
    source_guard: Callable[[], None] | None = None,
) -> BodySlotBatch:
    """Execute only after the actual common set has been durably frozen."""
    if source_guard is None:
        preparation.require_current()
    else:
        source_guard()
    common_freeze.require_current()
    require(common_freeze.frozen_before_ranking is True, "body.slots:freeze_required")
    require(callable(execute), "body.slots:executor")
    require(source_guard is None or callable(source_guard), "body.slots:source_guard")
    require(operation is not None and callable(getattr(operation, "before_slot", None))
            and callable(getattr(operation, "observe", None)),
            "body.slots:operation_journal")
    cases = {case.task.task_id: case for case in preparation.catalog.cases}
    skeleton = _grid(preparation.matrix)
    if not common_freeze.common.floor_passed:
        policy_skeleton = tuple({
            "task_id": row["task_id"], "arm": row["arm"], "repetition": row["repetition"],
            "generation": row["generation"],
            "execution_state": "unstarted", "ranked_files": (),
        } for row in skeleton)
        batch = BodySlotBatch(tuple(skeleton), policy_skeleton, SLOT_COUNT, 0)
        batch.require_current()
        return batch

    actual: list[Mapping[str, Any]] = []
    policy: list[Mapping[str, Any]] = []
    exposed = 0
    for index, identity in enumerate(skeleton):
        task_id, arm, repetition = identity["task_id"], identity["arm"], identity["repetition"]
        case = cases[task_id]
        backend = "sqlite" if arm == ARMS[0] else "postgresql"
        pin = pins.get(backend)
        require(type(pin) is VerifiedPublicationPin, "body.slots:pin")
        assert isinstance(pin, VerifiedPublicationPin)
        try:
            if source_guard is not None:
                source_guard()
            operation.before_slot(task_id, arm, repetition)
            result = execute(identity, case)
            captured = capture_body_slot(
                task=case.task, task_id=task_id, arm=arm, repetition=repetition,
                result=result, verified_pin=pin, private_markers=private_markers,
            )
            _retain(operation, captured, case_kind=case.kind)
            operation.observe(captured, case_kind=case.kind)
        except Exception as error:
            try:
                captured = capture_body_slot(
                    task=case.task, task_id=task_id, arm=arm, repetition=repetition,
                    result={"execution_state": "failed", "envelope": None,
                            "error_code": type(error).__name__}, verified_pin=pin,
                    private_markers=private_markers,
                )
                _retain(operation, captured, case_kind=case.kind, record_kind="failure")
            except Exception as authority_error:
                failed = _failed_slot_row(identity, authority_error)
                _retain_failure(operation, failed, authority_error)
                actual.append(failed)
                policy.append({
                    "task_id": task_id, "arm": arm, "repetition": repetition,
                    "generation": identity["generation"], "execution_state": "failed", "ranked_files": (),
                    "returned_hit_count": 0,
                })
                for remaining in skeleton[index + 1:]:
                    actual.append(dict(remaining))
                    policy.append({
                        "task_id": remaining["task_id"], "arm": remaining["arm"],
                        "repetition": remaining["repetition"], "generation": remaining["generation"],
                        "execution_state": "unstarted",
                        "ranked_files": (),
                    })
                break
        actual.append(dict(captured.actual))
        policy.append(dict(captured.policy))
        if captured.exposed:
            exposed += 1
        if captured.actual.get("execution_state") == "failed":
            for remaining in skeleton[index + 1:]:
                actual.append(dict(remaining))
                policy.append({
                    "task_id": remaining["task_id"], "arm": remaining["arm"],
                    "repetition": remaining["repetition"], "generation": remaining["generation"],
                    "execution_state": "unstarted",
                    "ranked_files": (),
                })
            break
    batch = BodySlotBatch(
        tuple(actual), tuple(policy),
        sum(row["execution_state"] == "unstarted" for row in policy), exposed,
    )
    batch.require_current()
    return batch


__all__ = ["BodySlotBatch", "execute_body_slots"]
