"""Actual body-study publication/admission preparation.

Preparation retains the two complete source reports but does not mark a slot
as observed.  A publication pin and every row are recomputed from the current
registered façade; caller mappings cannot be promoted to admissions.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from retrieval_body_admission import BodyTaskAdmission, admit_body_task
from retrieval_body_admission_rules import evaluate_body_case
from retrieval_body_catalog import BodyCatalog
from retrieval_body_pin import RegisteredBodyInputs
from retrieval_body_study_join import prepare_bound_body_study
from retrieval_body_study_values import (
    ARMS,
    REGISTERED_INPUT_PIN_SHA256,
    ROW_COUNT,
    SLOT_COUNT,
    STUDY_PHASE,
    V2_ARM,
    digest_value,
    require,
)
from retrieval_semantic_provenance import VerifiedPublicationPin


@dataclass(frozen=True, slots=True)
class BodyPublicationPreparation:
    """Both publication reports and the selected row/slot matrix."""

    inputs: RegisteredBodyInputs
    catalog: BodyCatalog
    policy: Any
    frozen_policy: Any
    sqlite_pin: VerifiedPublicationPin
    postgres_pin: VerifiedPublicationPin
    # These are deliberately inert recomputations.  They are the maintained
    # prepare_bound_body_study row mappings, not execution admissions.  The
    # only live BodyTaskAdmission objects are created at the v2 query boundary
    # immediately before build_body_query_records.
    sqlite_decisions: tuple[Mapping[str, Any], ...]
    postgres_decisions: tuple[Mapping[str, Any], ...]
    matrix: dict[str, Any]
    sqlite_report: dict[str, Any]
    postgres_report: dict[str, Any]
    phase: str = STUDY_PHASE

    def require_current(self) -> None:
        from retrieval_body_pin import require_registered_body_inputs

        require(self.phase == STUDY_PHASE, "body.prepare:study_phase")
        current = require_registered_body_inputs(self.inputs)
        require(current.pin_sha256 == REGISTERED_INPUT_PIN_SHA256, "body.prepare:input_pin")
        require(BodyCatalog.is_factory_issued(self.catalog), "body.prepare:catalog")
        require(current.catalog.digest == self.catalog.digest, "body.prepare:catalog_drift")
        require(self.sqlite_pin.is_factory_issued(), "body.prepare:sqlite_pin")
        require(self.postgres_pin.is_factory_issued(), "body.prepare:postgres_pin")
        self.sqlite_pin.revalidate_current()
        self.postgres_pin.revalidate_current()
        require(self.sqlite_pin.backend == "sqlite", "body.prepare:sqlite_backend")
        require(self.postgres_pin.backend == "postgresql", "body.prepare:postgres_backend")
        require(self.sqlite_pin.generation == self.postgres_pin.generation, "body.prepare:generation")
        require(self.sqlite_pin.snapshot_vector == self.postgres_pin.snapshot_vector, "body.prepare:snapshots")
        require(self.matrix.get("planned_slot_count") == SLOT_COUNT, "body.prepare:slots")
        rebuilt = prepare_bound_body_study(
            current.catalog,
            sqlite_decisions=self.sqlite_decisions,
            postgres_decisions=self.postgres_decisions,
            sqlite_pin=self.sqlite_pin,
            postgres_pin=self.postgres_pin,
            policy=self.policy,
            frozen_policy=self.frozen_policy,
        )
        require(digest_value(rebuilt) == digest_value(self.matrix), "body.prepare:matrix_drift")

    @property
    def identity(self) -> str:
        self.require_current()
        return digest_value({
            "phase": self.phase,
            "catalog_sha256": self.catalog.digest,
            "input_pin_sha256": self.inputs.pin_sha256,
            "sqlite_pin": self.sqlite_pin.to_dict(),
            "postgres_pin": self.postgres_pin.to_dict(),
            "matrix": self.matrix.get("selected_rows"),
        })


def _inert_decisions(
    current: Any,
    catalog: BodyCatalog,
    *,
    policy: Any,
    frozen_policy: Any,
    pin: VerifiedPublicationPin,
    backend: str,
) -> tuple[Mapping[str, Any], ...]:
    """Recompute the maintained preparation rows without issuing admissions."""
    require(current.catalog.digest == catalog.digest, "body.prepare:catalog_snapshot")
    require(current.pin_sha256 == REGISTERED_INPUT_PIN_SHA256, "body.prepare:input_snapshot")
    require(current.policy.to_dict() == frozen_policy.to_dict(), "body.prepare:policy_snapshot")
    result: list[Mapping[str, Any]] = []
    for case in catalog.cases:
        for arm in ARMS:
            row = evaluate_body_case(
                catalog, case, arm=arm, policy=policy,
                frozen_policy=frozen_policy, publication_pin=pin,
                observed_backend=backend,
            )
            row["input_pin_sha256"] = current.pin_sha256
            result.append(row)
    require(len(result) == ROW_COUNT, f"body.prepare:{backend}_row_count")
    return tuple(result)


def prepare_body_publications(
    inputs: RegisteredBodyInputs,
    *,
    sqlite_pin: VerifiedPublicationPin,
    postgres_pin: VerifiedPublicationPin,
    policy: Any | None = None,
    frozen_policy: Any | None = None,
    validate: bool = True,
) -> BodyPublicationPreparation:
    """Retain 288 maintained inert rows and both complete source reports."""
    from retrieval_body_pin import RegisteredBodyInputs

    require(RegisteredBodyInputs.is_factory_issued(inputs), "body.prepare:registered_factory")
    current = inputs
    snapshot = inputs._snapshot()
    catalog = snapshot.catalog
    chosen_policy = snapshot.policy if policy is None else policy
    chosen_frozen = chosen_policy if frozen_policy is None else frozen_policy
    require(catalog.registered, "body.prepare:registered_catalog_required")
    require(sqlite_pin.backend == "sqlite", "body.prepare:sqlite_backend")
    require(postgres_pin.backend == "postgresql", "body.prepare:postgres_backend")
    require(sqlite_pin.is_factory_issued() and postgres_pin.is_factory_issued(),
            "body.prepare:publication_origin")
    sqlite_pin.revalidate_current()
    postgres_pin.revalidate_current()
    sqlite = _inert_decisions(
        snapshot, catalog, policy=chosen_policy, frozen_policy=chosen_frozen,
        pin=sqlite_pin, backend="sqlite",
    )
    postgres = _inert_decisions(
        snapshot, catalog, policy=chosen_policy, frozen_policy=chosen_frozen,
        pin=postgres_pin, backend="postgresql",
    )
    matrix = prepare_bound_body_study(
        catalog,
        sqlite_decisions=sqlite,
        postgres_decisions=postgres,
        sqlite_pin=sqlite_pin,
        postgres_pin=postgres_pin,
        policy=chosen_policy,
        frozen_policy=chosen_frozen,
    )
    require(matrix.get("selected_row_count") == ROW_COUNT, "body.prepare:selected_rows")
    require(matrix.get("planned_slot_count") == SLOT_COUNT, "body.prepare:selected_slots")
    result = BodyPublicationPreparation(
        current, catalog, chosen_policy, chosen_frozen, sqlite_pin, postgres_pin,
        sqlite, postgres, matrix,
        dict(matrix["sqlite_report"]), dict(matrix["postgres_report"]),
    )
    require(type(validate) is bool, "body.prepare:validate_flag")
    if validate:
        result.require_current()
    return result


def selected_admissions(
    preparation: BodyPublicationPreparation, arm: str, *, validate: bool = True,
) -> tuple[BodyTaskAdmission, ...]:
    """Issue only the live current PostgreSQL v2 query admissions.

    Preparation rows are inert mappings.  Query-record construction is the
    sole boundary that receives BodyTaskAdmission objects, and it uses the
    maintained factory admission API rather than a raw query-row helper.
    """
    require(type(validate) is bool, "body.prepare:validate_flag")
    if validate:
        preparation.require_current()
    require(arm == V2_ARM, "body.prepare:query_admission_arm")
    require(RegisteredBodyInputs.is_factory_issued(preparation.inputs),
            "body.prepare:query_admission_inputs")
    current = preparation.inputs._snapshot()
    require(current.catalog.digest == preparation.catalog.digest, "body.prepare:query_catalog_drift")
    require(current.pin_sha256 == REGISTERED_INPUT_PIN_SHA256, "body.prepare:query_input_pin")
    pin = preparation.postgres_pin
    require(pin.is_factory_issued() and pin.backend == "postgresql", "body.prepare:query_pin")
    result = tuple(
        admit_body_task(
            preparation.inputs, case.task, arm=V2_ARM, policy=current.policy,
            publication_pin=pin, observed_backend="postgresql",
        )
        for case in current.catalog.cases
    )
    require(len(result) == len(preparation.catalog.cases), "body.prepare:arm_admission_count")
    require(all(BodyTaskAdmission.is_factory_issued(item) for item in result),
            "body.prepare:query_admission_origin")
    return result


__all__ = [
    "BodyPublicationPreparation",
    "prepare_body_publications",
    "selected_admissions",
]
