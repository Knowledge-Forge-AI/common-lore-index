"""Inner SDK entrypoint for the actual BODY study.

The host owns plan/admission and publications; this entrypoint owns only the
ordered model epochs, common freeze, slot observations, and report handoff.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from retrieval_body_study_driver import BodyStudyOutcome, run_actual_body_study
from retrieval_body_study_values import STUDY_PHASE, require


@dataclass(frozen=True, slots=True)
class BodyInnerContext:
    preparation: Any
    requests: Any
    worker_exchange: Any
    slot_executor: Any
    journal: Any
    sqlite_pin: Any
    postgres_pin: Any
    cleanup: Mapping[str, Any]
    private_markers: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> BodyInnerContext:
        required = {
            "preparation", "requests", "worker_exchange", "slot_executor", "journal",
            "sqlite_pin", "postgres_pin", "cleanup",
        }
        allowed = required | {"private_markers"}
        keys = set(value) if isinstance(value, Mapping) else set()
        require(isinstance(value, Mapping) and (keys == required or keys == allowed),
                "body.inner:context_fields")
        return cls(
            value["preparation"], value["requests"], value["worker_exchange"],
            value["slot_executor"], value["journal"], value["sqlite_pin"],
            value["postgres_pin"], value["cleanup"], tuple(value.get("private_markers", ())),
        )


def run_inner_body_study(context: BodyInnerContext) -> BodyStudyOutcome:
    """Execute an already-authenticated inner context with no fallback path."""
    require(type(context) is BodyInnerContext, "body.inner:context_type")
    require(context.preparation.phase == STUDY_PHASE, "body.inner:phase")
    return run_actual_body_study(
        preparation=context.preparation,
        requests=context.requests,
        exchange=context.worker_exchange,
        pins={"sqlite": context.sqlite_pin, "postgresql": context.postgres_pin},
        execute_slot=context.slot_executor,
        journal=context.journal,
        cleanup=context.cleanup,
        private_markers=context.private_markers,
    )


__all__ = ["BodyInnerContext", "run_inner_body_study"]
