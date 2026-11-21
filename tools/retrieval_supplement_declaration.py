"""Inert six-arm supplement declarations; creating one grants no execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from retrieval_supplement_catalog import SupplementCatalog
from retrieval_supplement_operation_values import digest, positive_int, token
from retrieval_supplement_tasks import task_digests
from retrieval_supplement_values import ARMS, canonical_bytes, digest_bytes, digest_json, strict_json

SCHEMA = "cloe-calibration-supplement-declaration-v1"
V1 = "canonical-node-v1"
PTX = "canonical-node-public-text-v2"
_JOURNAL_FIELDS = frozenset({"journal_id", "attempt_id", "representation", "profile_sha256"})
_TOKEN = object()
_FIELDS = frozenset({
    "schema", "supplement_id", "corpus_id", "phase", "purpose", "attempt_id",
    "input_pin_sha256", "freeze_sha256", "source_closure_sha256", "artifact_profile_sha256", "embedding_profiles",
    "catalog_sha256", "task_digests", "ordered_task_ids", "arms", "generation",
    "repetitions", "slot_order", "slot_count", "slot_set_sha256", "journals",
    "status", "queries_authorized", "model_workers_authorized", "publication_authorized",
    "scientific_criteria_accepted", "held_out_authorized",
})


def capture_arm(arm: str) -> str:
    """Map qualified identities without changing an old operation-record schema."""
    if arm not in ARMS:
        raise ValueError("supplement_declaration_arm")
    return arm.split(":", 1)[0] if arm.startswith(("exact_vector:", "hybrid_rrf:")) else arm


def representation_for(arm: str) -> str:
    if arm not in ARMS:
        raise ValueError("supplement_declaration_arm")
    return arm.split(":", 1)[1] if arm.startswith(("exact_vector:", "hybrid_rrf:")) else "baseline"


@dataclass(frozen=True)
class JournalBinding:
    journal_id: str
    attempt_id: str
    representation: str
    profile_sha256: str | None

    def validate(self) -> JournalBinding:
        token(self.journal_id)
        token(self.attempt_id)
        if type(self.representation) is not str or self.representation not in {"baseline", V1, PTX}:
            raise ValueError("supplement_declaration_representation")
        if self.representation == "baseline":
            if self.profile_sha256 is not None:
                raise ValueError("supplement_baseline_embedding_profile")
        else:
            digest(self.profile_sha256)
        return self

    def to_dict(self) -> dict[str, str | None]:
        self.validate()
        return {
            "journal_id": self.journal_id, "attempt_id": self.attempt_id,
            "representation": self.representation, "profile_sha256": self.profile_sha256,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> JournalBinding:
        if not isinstance(value, Mapping) or set(value) != _JOURNAL_FIELDS:
            raise ValueError("supplement_declaration_journal_fields")
        return cls(**value).validate()


@dataclass(frozen=True)
class SlotIdentity:
    task_id: str
    arm: str
    repetition: int
    generation: int = 1

    def validate(self) -> SlotIdentity:
        token(self.task_id)
        positive_int(self.repetition, 3)
        positive_int(self.generation, 1)
        if self.arm not in ARMS:
            raise ValueError("supplement_slot_arm")
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"task_id": self.task_id, "arm": self.arm, "repetition": self.repetition, "generation": self.generation}

    @property
    def key(self) -> tuple[str, str, int, int]:
        self.validate()
        return self.task_id, self.arm, self.repetition, self.generation


def ordered_slots(catalog: SupplementCatalog) -> tuple[SlotIdentity, ...]:
    if not isinstance(catalog, SupplementCatalog) or not 1 <= len(catalog.tasks) <= 24:
        raise ValueError("supplement_declaration_preparation_task_cap")
    return tuple(SlotIdentity(task.task_id, arm, repetition) for task in catalog.tasks
                 for arm in ARMS for repetition in (1, 2, 3))


class InertDeclaration:
    """A frozen proposal cannot reserve a study or attest native execution."""

    __slots__ = ("_raw",)
    _raw: bytes

    def __init__(self, value: Mapping[str, Any], *, _token: object = None) -> None:
        if _token is not _TOKEN:
            raise ValueError("supplement_declaration_factory_required")
        object.__setattr__(self, "_raw", canonical_bytes(value))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("InertDeclaration is immutable")

    @property
    def raw(self) -> bytes:
        return self._raw

    @property
    def digest(self) -> str:
        return digest_bytes(self._raw)

    def to_dict(self) -> dict[str, Any]:
        return dict(strict_json(self._raw))


def create_declaration(
    catalog: SupplementCatalog, *, input_pin_sha256: str, freeze_sha256: str,
    source_closure_sha256: str, artifact_profile_sha256: str,
    embedding_profiles: Mapping[str, str], attempt_id: str,
    journals: Mapping[str, JournalBinding],
) -> InertDeclaration:
    """Declare the complete ordered slot set while keeping every budget ungranted."""
    slots = ordered_slots(catalog)
    token(attempt_id)
    for value in (input_pin_sha256, freeze_sha256, source_closure_sha256, artifact_profile_sha256):
        digest(value)
    if not isinstance(embedding_profiles, Mapping) or set(embedding_profiles) != {V1, PTX}:
        raise ValueError("supplement_declaration_embedding_profiles")
    for identity in embedding_profiles.values():
        digest(identity)
    if len(set(embedding_profiles.values())) != 2 or artifact_profile_sha256 in embedding_profiles.values():
        raise ValueError("supplement_declaration_profile_identity_domains")
    if not isinstance(journals, Mapping) or set(journals) != set(ARMS):
        raise ValueError("supplement_declaration_journals")
    for arm, binding in journals.items():
        if type(binding) is not JournalBinding:
            raise ValueError("supplement_declaration_journal_type")
        binding.validate()
        if binding.representation != representation_for(arm) or binding.profile_sha256 != embedding_profiles.get(binding.representation):
            raise ValueError("supplement_declaration_journal_scope")
    v1, ptx, fusion = (journals[ARMS[index]] for index in (3, 4, 5))
    if v1.journal_id == ptx.journal_id or v1.attempt_id == ptx.attempt_id:
        raise ValueError("supplement_declaration_representations_share_journal")
    if fusion != ptx:
        raise ValueError("supplement_declaration_fusion_requires_public_text_exact")
    return InertDeclaration({
        "schema": SCHEMA, "supplement_id": catalog.identity.supplement_id,
        "corpus_id": catalog.identity.corpus_id, "phase": catalog.identity.phase,
        "purpose": "calibration", "attempt_id": attempt_id,
        "input_pin_sha256": input_pin_sha256, "freeze_sha256": freeze_sha256,
        "source_closure_sha256": source_closure_sha256, "artifact_profile_sha256": artifact_profile_sha256,
        "embedding_profiles": dict(embedding_profiles),
        "catalog_sha256": catalog.digest, "task_digests": {task.task_id: task_digests(task) for task in catalog.tasks},
        "ordered_task_ids": [task.task_id for task in catalog.tasks], "arms": list(ARMS),
        "generation": 1, "repetitions": 3, "slot_order": "task_arm_repetition",
        "slot_count": len(slots), "slot_set_sha256": digest_json([slot.to_dict() for slot in slots]),
        "journals": {arm: journals[arm].to_dict() for arm in ARMS},
        "status": "inert", "queries_authorized": 0, "model_workers_authorized": 0,
        "publication_authorized": False, "scientific_criteria_accepted": False, "held_out_authorized": False,
    }, _token=_TOKEN)


def validate_declaration(
    raw: bytes, catalog: SupplementCatalog, *, expected_input_pin_sha256: str,
    expected_freeze_sha256: str, expected_source_closure_sha256: str,
    expected_artifact_profile_sha256: str, expected_embedding_profiles: Mapping[str, str],
) -> InertDeclaration:
    """Require external pin expectations; self-rehashed foreign declarations fail."""
    value = strict_json(raw)
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError("supplement_declaration_fields")
    for field, expected in (
        ("input_pin_sha256", expected_input_pin_sha256), ("freeze_sha256", expected_freeze_sha256),
        ("source_closure_sha256", expected_source_closure_sha256), ("artifact_profile_sha256", expected_artifact_profile_sha256),
    ):
        if value[field] != digest(expected):
            raise ValueError("supplement_declaration_external_pin")
    if value["embedding_profiles"] != dict(expected_embedding_profiles):
        raise ValueError("supplement_declaration_external_embedding_profiles")
    raw_journals = value["journals"]
    if type(raw_journals) is not dict:
        raise ValueError("supplement_declaration_journals")
    expected_declaration = create_declaration(
        catalog, input_pin_sha256=expected_input_pin_sha256, freeze_sha256=expected_freeze_sha256,
        source_closure_sha256=expected_source_closure_sha256, artifact_profile_sha256=expected_artifact_profile_sha256,
        embedding_profiles=expected_embedding_profiles,
        attempt_id=value["attempt_id"], journals={arm: JournalBinding.from_mapping(row) for arm, row in raw_journals.items()},
    )
    # JSON equality conflates bool/int, so compare canonical serialized types.
    if canonical_bytes(value) != expected_declaration.raw:
        raise ValueError("supplement_declaration_changed_slots_or_authority")
    return expected_declaration
