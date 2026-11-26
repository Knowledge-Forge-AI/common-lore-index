"""Inert declaration identity, slot completeness and authority-refusal checks."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from common_lore_index_test_support.retrieval_supplement_fixtures import (
    make_test_bindings, make_test_catalog_payload, make_test_identity,
)
from retrieval_supplement_catalog import SupplementCatalog, parse_catalog
from retrieval_supplement_declaration import (
    InertDeclaration, JournalBinding, capture_arm, create_declaration, ordered_slots,
    representation_for, validate_declaration,
)
from retrieval_supplement_values import ARMS, canonical_bytes

PIN, FREEZE, SOURCE, PROFILE = ("sha256:" + str(number) * 64 for number in (1, 2, 3, 4))
EMBEDDING_PROFILES = {"canonical-node-v1": "sha256:" + "5" * 64,
                      "canonical-node-public-text-v2": "sha256:" + "6" * 64}


def _catalog() -> SupplementCatalog:
    return parse_catalog(canonical_bytes(make_test_catalog_payload()), identity=make_test_identity(),
                         binding_ids=tuple(make_test_bindings().values()))


def _journals() -> dict[str, JournalBinding]:
    result = {}
    for index, arm in enumerate(ARMS):
        name = "baseline" if index < 3 else "v1" if index == 3 else "public-text"
        result[arm] = JournalBinding("unit-journal-" + name, "unit-attempt-" + name,
                                     representation_for(arm), EMBEDDING_PROFILES.get(representation_for(arm)))
    return result


def _create(catalog: SupplementCatalog | None = None, **changes: Any) -> InertDeclaration:
    arguments: dict[str, Any] = dict(input_pin_sha256=PIN, freeze_sha256=FREEZE, source_closure_sha256=SOURCE,
                     artifact_profile_sha256=PROFILE, embedding_profiles=EMBEDDING_PROFILES,
                     attempt_id="unit-outer-attempt", journals=_journals())
    arguments.update(changes)
    return create_declaration(_catalog() if catalog is None else catalog, **arguments)


def _check(raw: bytes, *, catalog: SupplementCatalog | None = None, **changes: Any) -> InertDeclaration:
    arguments: dict[str, Any] = dict(expected_input_pin_sha256=PIN, expected_freeze_sha256=FREEZE,
                     expected_source_closure_sha256=SOURCE, expected_artifact_profile_sha256=PROFILE,
                     expected_embedding_profiles=EMBEDDING_PROFILES)
    arguments.update(changes)
    return validate_declaration(raw, _catalog() if catalog is None else catalog, **arguments)


def test_declares_complete_ordered_grid_without_any_execution_authority() -> None:
    catalog = _catalog()
    declaration = _create(catalog)
    value = declaration.to_dict()
    slots = ordered_slots(catalog)
    assert value["ordered_task_ids"] == [task.task_id for task in catalog.tasks]
    assert value["arms"] == list(ARMS)
    assert len(slots) == len(catalog.tasks) * 6 * 3 == value["slot_count"]
    assert len({slot.key for slot in slots}) == len(slots)
    assert [slot.repetition for slot in slots[:3]] == [1, 2, 3]
    assert [slot.arm for slot in slots[::3]][:6] == list(ARMS)
    assert value["status"] == "inert"
    assert value["queries_authorized"] == value["model_workers_authorized"] == 0
    assert value["publication_authorized"] is False
    assert value["scientific_criteria_accepted"] is False and value["held_out_authorized"] is False
    assert _check(declaration.raw, catalog=catalog).raw == declaration.raw


def test_qualified_arms_map_without_collapsing_representation_journals() -> None:
    value = _create().to_dict()
    assert capture_arm(ARMS[3]) == capture_arm(ARMS[4]) == "exact_vector"
    assert capture_arm(ARMS[5]) == "hybrid_rrf"
    first, second = value["journals"][ARMS[3]], value["journals"][ARMS[4]]
    assert first["journal_id"] != second["journal_id"]
    assert first["attempt_id"] != second["attempt_id"]
    assert first["representation"] != second["representation"]
    assert value["journals"][ARMS[5]] == second
    with pytest.raises(ValueError):
        capture_arm("hnsw_vector:canonical-node-public-text-v2")


@pytest.mark.parametrize("field", ["journal_id", "attempt_id"])
def test_same_representation_journal_or_attempt_cannot_be_reused(field: str) -> None:
    journals = _journals()
    journals[ARMS[4]] = replace(journals[ARMS[4]], **{field: getattr(journals[ARMS[3]], field)})
    journals[ARMS[5]] = journals[ARMS[4]]
    with pytest.raises(ValueError, match="share_journal"):
        _create(journals=journals)


def test_fusion_cannot_substitute_a_different_exact_exchange() -> None:
    journals = _journals()
    journals[ARMS[5]] = replace(journals[ARMS[5]], journal_id="another-exact")
    with pytest.raises(ValueError, match="fusion_requires"):
        _create(journals=journals)
    del journals[ARMS[4]]
    with pytest.raises(ValueError, match="journals"):
        _create(journals=journals)


@pytest.mark.parametrize("field,value", [
    ("repetitions", 2), ("repetitions", True), ("generation", True), ("slot_count", 1),
    ("queries_authorized", 1), ("queries_authorized", False), ("publication_authorized", True),
    ("held_out_authorized", True), ("scientific_criteria_accepted", True), ("status", "approved"),
    ("unexpected", "field"),
])
def test_parser_rejects_changed_denominator_or_forged_authority(field: str, value: object) -> None:
    changed = _create().to_dict()
    changed[field] = value
    with pytest.raises(ValueError):
        _check(canonical_bytes(changed))


@pytest.mark.parametrize("field", [
    "expected_input_pin_sha256", "expected_freeze_sha256", "expected_source_closure_sha256", "expected_artifact_profile_sha256",
])
def test_parser_binds_external_expectations(field: str) -> None:
    changes: dict[str, Any] = {field: "sha256:" + "a" * 64}
    with pytest.raises(ValueError, match="external_pin"):
        _check(_create().raw, **changes)


def test_task_order_and_task_content_cannot_drift_after_declaration() -> None:
    changed = _create().to_dict()
    changed["ordered_task_ids"].reverse()
    with pytest.raises(ValueError):
        _check(canonical_bytes(changed))
    payload = make_test_catalog_payload()
    payload["cases"][0]["task"]["question"] += " Additional synthetic context."
    changed_catalog = parse_catalog(canonical_bytes(payload), identity=make_test_identity(),
                                   binding_ids=tuple(make_test_bindings().values()))
    with pytest.raises(ValueError):
        _check(_create().raw, catalog=changed_catalog)


def test_registered_and_functional_identity_are_never_interchangeable() -> None:
    declaration = _create()
    changed = declaration.to_dict()
    changed["supplement_id"] = "sup1-calibration-v1"
    with pytest.raises(ValueError):
        _check(canonical_bytes(changed))
    copy = declaration.to_dict()
    copy["journals"].clear()
    assert len(declaration.to_dict()["journals"]) == 6
    with pytest.raises(ValueError, match="factory_required"):
        InertDeclaration(changed)


@pytest.mark.parametrize("profiles", [
    {"canonical-node-v1": PROFILE, "canonical-node-public-text-v2": "sha256:" + "6" * 64},
    {"canonical-node-v1": "sha256:" + "5" * 64, "canonical-node-public-text-v2": "sha256:" + "5" * 64},
    {"canonical-node-v1": "sha256:" + "5" * 64},
])
def test_artifact_and_two_embedding_profile_identities_cannot_be_conflated(profiles: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        _create(embedding_profiles=profiles)


def test_embedding_profile_expectations_are_external_bindings() -> None:
    changed = dict(EMBEDDING_PROFILES, **{"canonical-node-v1": "sha256:" + "a" * 64})
    with pytest.raises(ValueError, match="external_embedding_profiles"):
        _check(_create().raw, expected_embedding_profiles=changed)
