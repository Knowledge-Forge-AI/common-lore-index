"""Closed identities and explicitly scoped samples for supplement phase costs."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from retrieval_supplement_values import ARMS

PHASES = frozenset({
    "setup", "input_preparation", "inference", "import", "index", "query",
    "footprint", "cleanup", "teardown",
})
SAMPLE_STATES = frozenset({"observed", "unknown", "coarse", "shared", "unattributed"})
UNITS = frozenset({"ns", "bytes", "rows", "statements", "count"})
LAYERS = frozenset({"caller", "adapter", "database", "transport", "model_worker", "container"})
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def token(value: object) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise ValueError("supplement_operation_token")
    return value


def digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError("supplement_operation_digest")
    return value


def positive_int(value: object, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("supplement_operation_integer")
    return value


@dataclass(frozen=True)
class PhaseIdentity:
    """Identity binds a phase to one arm/task or one explicitly shared owner."""

    supplement_id: str
    input_pin_sha256: str
    freeze_sha256: str
    source_closure_sha256: str
    attempt_id: str
    operation_id: str
    phase: str
    generation: int = 1
    task_id: str | None = None
    arm: str | None = None
    repetition: int | None = None
    shared_owner: str | None = None

    def validate(self) -> PhaseIdentity:
        for value in (self.supplement_id, self.attempt_id, self.operation_id):
            token(value)
        for value in (self.input_pin_sha256, self.freeze_sha256, self.source_closure_sha256):
            digest(value)
        positive_int(self.generation, 1)
        if type(self.phase) is not str or self.phase not in PHASES:
            raise ValueError("supplement_operation_phase")
        shared = self.shared_owner is not None
        if shared:
            token(self.shared_owner)
            if any(value is not None for value in (self.task_id, self.arm, self.repetition)):
                raise ValueError("supplement_operation_mixed_owner")
        else:
            token(self.task_id)
            if type(self.arm) is not str or self.arm not in ARMS:
                raise ValueError("supplement_operation_arm")
            positive_int(self.repetition, 3)
        if self.phase == "inference" and not shared:
            raise ValueError("supplement_whole_worker_requires_shared_owner")
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "supplement_id": self.supplement_id, "input_pin_sha256": self.input_pin_sha256,
            "freeze_sha256": self.freeze_sha256, "source_closure_sha256": self.source_closure_sha256,
            "attempt_id": self.attempt_id, "operation_id": self.operation_id,
            "phase": self.phase, "generation": self.generation, "task_id": self.task_id,
            "arm": self.arm, "repetition": self.repetition, "shared_owner": self.shared_owner,
        }


@dataclass(frozen=True)
class FieldSample:
    """An explicit observation never inferred from a generation default or delta."""

    name: str
    units: str
    value: int | float | None
    state: str
    layer: str
    shared_owner: str | None = None
    additive: bool = False

    def validate(self) -> FieldSample:
        token(self.name)
        if (
            type(self.units) is not str or type(self.state) is not str or type(self.layer) is not str
            or self.units not in UNITS or self.state not in SAMPLE_STATES or self.layer not in LAYERS
        ):
            raise ValueError("supplement_sample_vocabulary")
        if self.additive is not False:
            raise ValueError("supplement_sample_nonadditive")
        if self.state in {"unknown", "unattributed"}:
            if self.value is not None:
                raise ValueError("supplement_unknown_sample_has_value")
        else:
            number = self.value
            if (
                not isinstance(number, (int, float)) or isinstance(number, bool)
                or not 0 <= number <= 2**63 - 1 or not math.isfinite(number)
            ):
                raise ValueError("supplement_sample_number")
        if self.state == "shared":
            token(self.shared_owner)
        elif self.shared_owner is not None:
            raise ValueError("supplement_sample_unexpected_owner")
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "name": self.name, "units": self.units, "value": self.value,
            "state": self.state, "layer": self.layer, "shared_owner": self.shared_owner,
            "additive": False,
        }


def public_error_type(error: BaseException | None) -> str | None:
    """Never retain exception text, arguments, traceback or private payloads."""
    if error is None:
        return None
    name = type(error).__name__
    return name if name.isidentifier() and len(name) <= 64 else "OperationError"
