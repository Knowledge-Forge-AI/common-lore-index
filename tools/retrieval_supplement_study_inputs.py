"""Bounded, source-backed inputs for the retained supplement study report."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from retrieval_supplement_catalog import SupplementCatalog, load_registered_catalog
from retrieval_supplement_declaration import SlotIdentity, ordered_slots
from retrieval_supplement_files import read_member
from retrieval_supplement_study_contracts import INPUT_PIN_SHA256
from retrieval_supplement_tasks import task_digests
from retrieval_supplement_primitives import (
    MAX_JSON_BYTES,
    SupplementIdentity,
    digest_value,
    integer,
    mapping_copy,
    require,
    strict_json,
)

_INPUT_ROOT = "src/test/fixtures/retrieval_evaluation/supplement-v1"
_IDENTITY = SupplementIdentity(
    "sup1-calibration-v1",
    "sup1-corpus-v1",
    "CLOE-W5-SUPPLEMENT1",
    "sup1-calibration",
    "sup1-",
)


@dataclass(frozen=True)
class FrozenTaskMetadata:
    """The catalog case kind and task family bound to one task identifier."""

    kind: str
    family: str


@dataclass(frozen=True)
class QueryTargetJoin:
    """Source-backed query IDs and gold locator joins for one catalog task."""

    v1_query_record_id: str
    v2_query_record_id: str
    target_locator_keys: tuple[str, ...]


@dataclass(frozen=True)
class FrozenStudyInputs:
    """Immutable catalog metadata used to admit and verify the retained report."""

    catalog: SupplementCatalog
    input_pin_sha256: str
    catalog_sha256: str
    task_digests: Mapping[str, Mapping[str, str]]
    task_metadata: Mapping[str, FrozenTaskMetadata]
    query_target_joins: Mapping[str, QueryTargetJoin]

    def __post_init__(self) -> None:
        digests = {
            task_id: MappingProxyType(dict(value))
            for task_id, value in self.task_digests.items()
        }
        metadata = dict(self.task_metadata)
        joins = dict(self.query_target_joins)
        object.__setattr__(self, "task_digests", MappingProxyType(digests))
        object.__setattr__(self, "task_metadata", MappingProxyType(metadata))
        object.__setattr__(self, "query_target_joins", MappingProxyType(joins))


def _pin_entry(pin: Mapping[str, Any], name: str) -> tuple[int, str]:
    files = pin.get("input_files")
    require(isinstance(files, dict), "study_inputs:pin_input_files")
    assert isinstance(files, dict)
    entry = files.get(name)
    require(isinstance(entry, dict), "study_inputs:pin_entry")
    assert isinstance(entry, dict)
    size = integer(entry.get("bytes"), "study_inputs:pin_bytes", 0, MAX_JSON_BYTES)
    return size, digest_value(entry.get("sha256"), "study_inputs:pin_digest")


def _read_pinned(
    root: Path,
    relative: str,
    *,
    size: int,
    digest: str,
    charge_bytes: Callable[[int], None],
    expected_size: int | None = None,
) -> bytes:
    charge_bytes(size)
    data = read_member(root, relative, max_bytes=size, expected_sha256=digest)
    require(expected_size is None or len(data) == expected_size, "study_inputs:pin_size_mismatch")
    return data


def _load_bindings(raw: bytes) -> dict[str, str]:
    value = strict_json(raw)
    require(isinstance(value, dict), "study_inputs:bindings_object")
    require(
        value.get("supplement_id") == _IDENTITY.supplement_id
        and value.get("corpus_id") == _IDENTITY.corpus_id
        and value.get("phase") == _IDENTITY.phase
        and value.get("graph_id") == _IDENTITY.graph_id,
        "study_inputs:bindings_identity",
    )
    bindings = value.get("bindings")
    require(isinstance(bindings, Mapping), "study_inputs:bindings_mapping")
    result = mapping_copy(bindings, "study_inputs:bindings")
    require(result and len(set(result.values())) == len(result), "study_inputs:bindings_unique")
    return result


def _frozen_metadata(
    catalog: SupplementCatalog,
) -> tuple[dict[str, dict[str, str]], dict[str, FrozenTaskMetadata], dict[str, QueryTargetJoin]]:
    digests: dict[str, dict[str, str]] = {}
    metadata: dict[str, FrozenTaskMetadata] = {}
    joins: dict[str, QueryTargetJoin] = {}
    for case, task in zip(catalog.cases, catalog.tasks, strict=True):
        digests[task.task_id] = task_digests(task)
        metadata[task.task_id] = FrozenTaskMetadata(case.kind, task.family)
        targets = tuple(
            f"{gold.locator.binding_id}:{gold.locator.source_relative_path}"
            for gold in task.gold_targets if gold.relevance_grade > 0
        )
        joins[task.task_id] = QueryTargetJoin(
            f"v1.query:{task.task_id}", f"ptx.q.{task.task_id}", targets,
        )
    return digests, metadata, joins


def load_study_inputs(
    root: Path,
    charge_bytes: Callable[[int], None],
) -> FrozenStudyInputs:
    """Read the exact registered catalog and its pin-bound task metadata."""
    require(isinstance(root, Path) and callable(charge_bytes), "study_inputs:arguments")
    input_root = root / _INPUT_ROOT
    pin_raw = _read_pinned(
        input_root,
        "input-pin-v1.json",
        size=MAX_JSON_BYTES,
        digest=INPUT_PIN_SHA256,
        charge_bytes=charge_bytes,
    )
    pin = strict_json(pin_raw)
    require(isinstance(pin, dict), "study_inputs:pin_object")
    require(
        pin.get("supplement_id") == _IDENTITY.supplement_id
        and pin.get("corpus_id") == _IDENTITY.corpus_id
        and pin.get("phase") == _IDENTITY.phase,
        "study_inputs:pin_identity",
    )
    binding_size, binding_digest = _pin_entry(pin, "bindings-v1.json")
    bindings_raw = _read_pinned(
        input_root,
        "bindings-v1.json",
        size=binding_size,
        digest=binding_digest,
        charge_bytes=charge_bytes,
        expected_size=binding_size,
    )
    bindings = _load_bindings(bindings_raw)
    catalog_size, catalog_digest = _pin_entry(pin, "catalog-v1.json")
    catalog_raw = _read_pinned(
        input_root,
        "catalog-v1.json",
        size=catalog_size,
        digest=catalog_digest,
        charge_bytes=charge_bytes,
        expected_size=catalog_size,
    )
    catalog = load_registered_catalog(
        catalog_raw,
        identity=_IDENTITY,
        binding_ids=tuple(bindings.values()),
        pin_raw=pin_raw,
    )
    digests, metadata, joins = _frozen_metadata(catalog)
    require(pin.get("task_digests") == digests, "study_inputs:task_digest_pin_mismatch")
    return FrozenStudyInputs(
        catalog,
        INPUT_PIN_SHA256,
        catalog.digest,
        digests,
        metadata,
        joins,
    )


def validate_manifest(
    manifest: object,
    inputs: FrozenStudyInputs,
) -> tuple[SlotIdentity, ...]:
    """Require an exact ordered declaration manifest for the frozen catalog."""
    require(isinstance(inputs, FrozenStudyInputs), "study_inputs:inputs_required")
    expected = ordered_slots(inputs.catalog)
    require(isinstance(manifest, list), "study_inputs:manifest_list")
    require(manifest == [slot.to_dict() for slot in expected], "study_inputs:manifest_mismatch")
    return expected


def require_study_manifest(
    plan: Mapping[str, Any],
    catalog: SupplementCatalog,
) -> tuple[SlotIdentity, ...]:
    """Require a plan declaration to use the exact ordered catalog slot set."""
    require(isinstance(plan, Mapping), "study_inputs:plan_required")
    require(isinstance(catalog, SupplementCatalog), "study_inputs:catalog_required")
    declaration = plan.get("declaration")
    require(isinstance(declaration, Mapping), "study_inputs:declaration_required")
    assert isinstance(declaration, Mapping)
    expected = ordered_slots(catalog)
    require(
        declaration.get("slot_manifest") == [slot.to_dict() for slot in expected],
        "study_inputs:manifest_mismatch",
    )
    return expected


__all__ = [
    "FrozenStudyInputs", "FrozenTaskMetadata", "QueryTargetJoin",
    "load_study_inputs", "require_study_manifest", "validate_manifest",
]
