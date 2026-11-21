"""Registered input frame used by the actual W5 body-study inner driver.

The frame is deliberately small.  It is a custody handle, not a copied JSON
manifest: every property is obtained from ``RegisteredBodyInputs`` and the
caller must revalidate the handle before crossing an authority boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from retrieval_body_pin import (
    RegisteredBodyInputs,
    load_registered_body_inputs,
)
from retrieval_body_study_values import (
    REGISTERED_INPUT_PIN_SHA256,
    STUDY_PHASE,
    digest_value,
    require,
)


@dataclass(frozen=True, slots=True)
class BodyStudyInputFrame:
    """Current registered body inputs bound to one execution attempt."""

    inputs: RegisteredBodyInputs
    phase: str = STUDY_PHASE

    def require_current(self) -> Any:
        require(self.phase == STUDY_PHASE, "body.inputs:study_phase")
        require(RegisteredBodyInputs.is_factory_issued(self.inputs), "body.inputs:registered_factory")
        # The maintained façade exposes one complete immutable snapshot through
        # its custody method.  Bind that result locally so this boundary does
        # not traverse the same 11 MiB input tree once per property.
        current = self.inputs._snapshot()
        require(current.pin_sha256 == REGISTERED_INPUT_PIN_SHA256, "body.inputs:registered_pin")
        require(current.catalog.registered, "body.inputs:registered_catalog")
        require(current.catalog.identity.reserved, "body.inputs:reserved_identity")
        return current

    @property
    def catalog(self) -> Any:
        return self.require_current().catalog

    @property
    def corpus(self) -> Any:
        return self.require_current().corpus

    @property
    def policy(self) -> Any:
        return self.require_current().policy

    @property
    def pin_sha256(self) -> str:
        return self.require_current().pin_sha256

    @property
    def identity(self) -> str:
        current = self.require_current()
        return digest_value({
            "phase": self.phase,
            "input_pin_sha256": current.pin_sha256,
            "catalog_sha256": current.catalog.digest,
            "class_id": current.catalog.identity.class_id,
            "corpus_id": current.catalog.identity.corpus_id,
        })


def load_body_study_inputs(
    root: Path, *, predecessor_root: Path, validate: bool = True,
) -> BodyStudyInputFrame:
    """Load the exact registered tree, optionally checking it immediately."""
    registered = load_registered_body_inputs(root, predecessor_root=predecessor_root)
    frame = BodyStudyInputFrame(registered)
    require(type(validate) is bool, "body.inputs:validate_flag")
    if validate:
        frame.require_current()
    return frame


def require_body_study_inputs(value: object) -> BodyStudyInputFrame:
    """Accept only a frame backed by the factory-issued registered façade."""
    require(type(value) is BodyStudyInputFrame, "body.inputs:frame_required")
    assert isinstance(value, BodyStudyInputFrame)
    value.require_current()
    return value


__all__ = [
    "BodyStudyInputFrame",
    "load_body_study_inputs",
    "require_body_study_inputs",
]
