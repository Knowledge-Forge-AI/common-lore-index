"""Fail-closed W5 captured-text input pin custody and registered façade."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from retrieval_body_catalog import (
    BodyCatalog,
    _parse_registered_catalog,
    parse_functional_catalog,
)
from retrieval_body_corpus import BodyCorpus, check_body_catalog_corpus, parse_body_corpus
from retrieval_body_input_metadata import (
    validate_namespace,
    validate_preauthoring_gate,
    validate_static_inventory,
)
from retrieval_body_input_records import (
    MAX_JSON_BYTES,
    build_body_input_pin,
    parse_body_input_pin,
)
from retrieval_body_predecessors import PredecessorSnapshot, verify_predecessor_manifest
from retrieval_body_values import (
    INPUT_NAMES,
    FIRST_INPUT_PIN_SHA256,
    REGISTERED_IDENTITY,
    REGISTERED_INPUT_PIN_SHA256,
    BodyIdentity,
    canonical_bytes,
    digest_bytes,
    require,
    strict_json,
)
from retrieval_supplement_files import file_inventory, read_member
from retrieval_supplement_pin import policy_aliases, policy_from_bytes
from retrieval_supplement_primitives import mapping_copy
from retrieval_supplement_tasks import task_digests


PIN_FILE_NAME = "input-pin-v1.json"
_EXPECTED_MEMBERS = tuple(sorted((*INPUT_NAMES, PIN_FILE_NAME)))
_TOKEN = object()


@dataclass(frozen=True)
class _BodySnapshot:
    pin_raw: bytes
    pin_sha256: str
    catalog: BodyCatalog
    corpus: BodyCorpus
    policy: BodyProjectionPolicy
    predecessor: PredecessorSnapshot


def _bindings(raw: bytes) -> dict[str, str]:
    value = strict_json(raw)
    require(isinstance(value, dict), "body.pin:bindings_object")
    return mapping_copy(value, "body.pin.bindings")


def _check_policy(catalog: BodyCatalog, corpus: BodyCorpus, policy: BodyProjectionPolicy,
                  bindings: Mapping[str, str]) -> None:
    policy.validate()
    require(policy.graph_id == catalog.identity.graph_id, "body.pin:policy_graph")
    derived = policy_aliases(policy)
    require(dict(bindings) == derived, "body.pin:bindings_drift")
    known = set(policy.bindings)
    denied = corpus.denied_binding_ids
    for case in catalog.cases:
        selected = set(case.task.selected_bindings)
        require(selected <= known and selected <= set(policy.selected_binding_ids), "body.pin:task_policy_binding")
        if case.kind == "privacy":
            require(selected & denied, "body.pin:privacy_binding")
            continue
        for binding_id in selected:
            resolved = policy.bindings[binding_id]
            require(resolved.resolved_graph_privacy == "public-dev", "body.pin:graph_privacy")
            require(resolved.resolved_binding_privacy == "public-dev", "body.pin:binding_privacy")
            require(resolved.resolved_evidence_retention == "full-authorized", "body.pin:retention")
            require(resolved.original_graph_privacy != "inherit" and resolved.original_binding_privacy != "inherit",
                    "body.pin:unresolved_privacy")
            require(resolved.original_evidence_retention != "inherit", "body.pin:unresolved_retention")


def _read_current(root: Path) -> tuple[bytes, dict[str, bytes], dict[str, Any]]:
    require(isinstance(root, Path), "body.pin:root")
    require(file_inventory(root) == _EXPECTED_MEMBERS, "body.pin:tree_membership")
    pin_raw = read_member(
        root, PIN_FILE_NAME, max_bytes=MAX_JSON_BYTES,
        expected_sha256=REGISTERED_INPUT_PIN_SHA256,
    )
    pin = parse_body_input_pin(pin_raw)
    files: dict[str, bytes] = {}
    for name in INPUT_NAMES:
        entry = pin["input_files"][name]
        data = read_member(
            root, name, max_bytes=entry["bytes"], expected_sha256=entry["sha256"],
        )
        require(len(data) == entry["bytes"], "body.pin:member_size")
        files[name] = data
    return pin_raw, files, pin


def _parse_catalog(
    raw: bytes, *, identity: BodyIdentity, binding_ids: tuple[str, ...], pin_raw: bytes,
    registered: bool,
) -> BodyCatalog:
    if registered:
        catalog = _parse_registered_catalog(raw, pin_raw=pin_raw, binding_ids=binding_ids)
    else:
        catalog = parse_functional_catalog(raw, identity=identity, binding_ids=binding_ids)
    require(BodyCatalog.is_factory_issued(catalog), "body.pin:catalog_factory")
    require(catalog.identity == identity, "body.pin:catalog_identity")
    if registered:
        require(catalog.registered and identity == REGISTERED_IDENTITY, "body.pin:catalog_registered")
    else:
        require(not identity.reserved and not catalog.registered, "body.pin:functional_registered")
    return catalog


def _validate_snapshot(
    pin_raw: bytes,
    files: Mapping[str, bytes],
    *,
    identity: BodyIdentity,
    predecessor_root: Path,
    expected_pin_sha256: str | None,
    registered: bool,
) -> _BodySnapshot:
    require(set(files) == set(INPUT_NAMES), "body.pin:input_membership")
    pin = parse_body_input_pin(pin_raw)
    for name in INPUT_NAMES:
        entry = pin["input_files"][name]
        data = files[name]
        require(type(data) is bytes and len(data) == entry["bytes"], "body.pin:member_size")
        require(digest_bytes(data) == entry["sha256"], "body.pin:member_digest")
    pin_sha256 = digest_bytes(pin_raw)
    if expected_pin_sha256 is not None:
        require(pin_sha256 == expected_pin_sha256, "body.pin:pin_digest")
    if registered:
        require(identity == REGISTERED_IDENTITY and pin_sha256 == REGISTERED_INPUT_PIN_SHA256,
                "body.pin:registered_identity")
    else:
        require(not identity.reserved and pin_sha256 != REGISTERED_INPUT_PIN_SHA256,
                "body.pin:functional_identity")
    require(pin["class_id"] == identity.class_id, "body.pin:class_id")
    require(pin["corpus_id"] == identity.corpus_id, "body.pin:corpus_id")
    require(pin["phase"] == identity.phase, "body.pin:phase")
    policy = policy_from_bytes(files["policy-v1.json"])
    require(policy.policy_digest() == pin["policy_digest"], "body.pin:policy_digest")
    bindings = _bindings(files["bindings-v1.json"])
    require(bindings == policy_aliases(policy), "body.pin:bindings")
    catalog = _parse_catalog(
        files["catalog-v1.json"], identity=identity,
        binding_ids=tuple(bindings.values()), pin_raw=pin_raw, registered=registered,
    )
    corpus = parse_body_corpus(files["corpus-v1.json"], identity=identity, bindings=bindings)
    check_body_catalog_corpus(catalog, corpus)
    _check_policy(catalog, corpus, policy, bindings)
    task_map = {case.task.task_id: task_digests(case.task) for case in catalog.cases}
    require(pin["task_digests"] == task_map, "body.pin:task_digests")
    predecessor_raw = files["predecessor-manifest.json"]
    require(pin["predecessor_manifest_sha256"] == digest_bytes(predecessor_raw),
            "body.pin:predecessor_pin")
    predecessor = verify_predecessor_manifest(
        predecessor_raw, predecessor_root=predecessor_root, identity=identity,
    )
    if registered:
        from retrieval_body_revision import verify_body_input_revision
        prefix = "src/test/fixtures/retrieval_evaluation/body-v1/"
        first_members = {entry.path: entry for entry in predecessor.files if entry.path.startswith(prefix)}
        require(set(first_members) == {prefix + name for name in (*INPUT_NAMES, PIN_FILE_NAME)},
                "body.pin:first_freeze_membership")
        first_member = prefix + PIN_FILE_NAME
        if predecessor_root.name == "retrieval_evaluation":
            first_member = "body-v1/" + PIN_FILE_NAME
        first_raw = read_member(predecessor_root, first_member, max_bytes=MAX_JSON_BYTES,
                                expected_sha256=FIRST_INPUT_PIN_SHA256)
        first = parse_body_input_pin(first_raw)
        for name in INPUT_NAMES:
            entry = first_members[prefix + name]
            expected = first["input_files"][name]
            require(entry.bytes == expected["bytes"] and "sha256:" + entry.sha256 == expected["sha256"],
                    "body.pin:first_freeze_member_drift")
        verify_body_input_revision(first_raw, pin_raw, expected_first_sha256=FIRST_INPUT_PIN_SHA256,
                                   expected_current_sha256=REGISTERED_INPUT_PIN_SHA256)
    validate_preauthoring_gate(files["preauthoring-gate-v1.json"], identity=identity)
    validate_namespace(
        files["namespace-v1.json"], identity=identity, predecessor=predecessor,
        task_ids=tuple(task_map),
    )
    validate_static_inventory(files["static-inventory-v1.json"], catalog=catalog, corpus=corpus)
    rebuilt = build_body_input_pin(catalog, input_files=files, policy=policy)
    require(canonical_bytes(rebuilt) == pin_raw, "body.pin:reproduction")
    return _BodySnapshot(pin_raw, pin_sha256, catalog, corpus, policy, predecessor)


class RegisteredBodyInputs:
    """Factory-issued body inputs whose downstream properties revalidate custody."""

    __slots__ = ("_root", "_predecessor_root", "_pin_raw", "_pin_sha256", "_issued")
    _root: Path
    _predecessor_root: Path
    _pin_raw: bytes
    _pin_sha256: str
    _issued: tuple[Any, ...]

    def __init__(self, root: Path, predecessor_root: Path, pin_raw: bytes, *, _token: object = None) -> None:
        require(_token is _TOKEN, "body.inputs:factory_required")
        require(isinstance(root, Path) and isinstance(predecessor_root, Path), "body.inputs:roots")
        require(type(pin_raw) is bytes, "body.inputs:pin_bytes")
        require(digest_bytes(pin_raw) == REGISTERED_INPUT_PIN_SHA256, "body.inputs:registered_pin")
        for name, value in (("_root", root), ("_predecessor_root", predecessor_root),
                            ("_pin_raw", bytes(pin_raw)), ("_pin_sha256", REGISTERED_INPUT_PIN_SHA256),
                            ("_issued", (_TOKEN, id(self), root, predecessor_root, digest_bytes(pin_raw)))):
            object.__setattr__(self, name, value)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("RegisteredBodyInputs is immutable")

    @staticmethod
    def is_factory_issued(value: object) -> bool:
        try:
            return type(value) is RegisteredBodyInputs and value._issued == (
                _TOKEN, id(value), value._root, value._predecessor_root, digest_bytes(value._pin_raw),
            ) and value._pin_sha256 == REGISTERED_INPUT_PIN_SHA256
        except (AttributeError, TypeError, ValueError):
            return False

    def _snapshot(self) -> _BodySnapshot:
        require(self.is_factory_issued(self), "body.inputs:registered_factory_required")
        pin_raw, files, _pin = _read_current(self._root)
        return _validate_snapshot(
            pin_raw, files, identity=REGISTERED_IDENTITY, predecessor_root=self._predecessor_root,
            expected_pin_sha256=REGISTERED_INPUT_PIN_SHA256, registered=True,
        )

    @property
    def root(self) -> Path:
        return self._root

    @property
    def predecessor_root(self) -> Path:
        return self._predecessor_root

    @property
    def pin_raw(self) -> bytes:
        return bytes(self._snapshot().pin_raw)

    @property
    def pin_sha256(self) -> str:
        self._snapshot()
        return self._pin_sha256

    @property
    def catalog(self) -> BodyCatalog:
        return self._snapshot().catalog

    @property
    def corpus(self) -> BodyCorpus:
        return self._snapshot().corpus

    @property
    def policy(self) -> BodyProjectionPolicy:
        return self._snapshot().policy

    @property
    def predecessor(self) -> PredecessorSnapshot:
        return self._snapshot().predecessor


class FunctionalBodyInputs:
    """Unregistered functional result; it can never satisfy registered entrypoints."""

    __slots__ = ("_snapshot",)
    _snapshot: _BodySnapshot

    def __init__(self, snapshot: _BodySnapshot, *, _token: object = None) -> None:
        require(_token is _TOKEN, "body.functional:factory_required")
        object.__setattr__(self, "_snapshot", snapshot)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("FunctionalBodyInputs is immutable")

    @property
    def pin_raw(self) -> bytes:
        return bytes(self._snapshot.pin_raw)

    @property
    def pin_sha256(self) -> str:
        return self._snapshot.pin_sha256

    @property
    def catalog(self) -> BodyCatalog:
        return self._snapshot.catalog

    @property
    def corpus(self) -> BodyCorpus:
        return self._snapshot.corpus

    @property
    def policy(self) -> BodyProjectionPolicy:
        return self._snapshot.policy

    @property
    def predecessor(self) -> PredecessorSnapshot:
        return self._snapshot.predecessor


def load_registered_body_inputs(root: Path, *, predecessor_root: Path) -> RegisteredBodyInputs:
    """Admit only the exact registered body tree and constant data pin."""
    require(isinstance(root, Path) and isinstance(predecessor_root, Path), "body.inputs:roots")
    pin_raw = read_member(
        root, PIN_FILE_NAME, max_bytes=MAX_JSON_BYTES,
        expected_sha256=REGISTERED_INPUT_PIN_SHA256,
    )
    result = RegisteredBodyInputs(root, predecessor_root, pin_raw, _token=_TOKEN)
    result._snapshot()
    return result


def require_registered_body_inputs(value: object) -> RegisteredBodyInputs:
    """Require the exact factory type and revalidate the current custody boundary."""
    require(RegisteredBodyInputs.is_factory_issued(value), "body.inputs:registered_factory_required")
    registered = cast(RegisteredBodyInputs, value)
    registered._snapshot()
    return registered


def validate_functional_body_inputs(
    input_files: Mapping[str, bytes], *, identity: BodyIdentity, policy: BodyProjectionPolicy,
    predecessor_root: Path, expected_pin_sha256: str | None = None,
) -> FunctionalBodyInputs:
    """Validate unrelated functional bytes without issuing registered admission."""
    require(type(identity) is BodyIdentity and not identity.reserved, "body.functional:identity")
    require(isinstance(input_files, Mapping), "body.functional:input_mapping")
    files = dict(input_files)
    require(set(files) == set((*INPUT_NAMES, PIN_FILE_NAME)), "body.functional:input_membership")
    pin_raw = files.pop(PIN_FILE_NAME, None)
    require(pin_raw is not None, "body.functional:pin_missing")
    require(type(pin_raw) is bytes, "body.functional:pin_bytes")
    assert isinstance(pin_raw, bytes)
    snapshot = _validate_snapshot(
        pin_raw, files, identity=identity, predecessor_root=predecessor_root,
        expected_pin_sha256=expected_pin_sha256, registered=False,
    )
    require(type(policy) is BodyProjectionPolicy and snapshot.policy.to_dict() == policy.to_dict()
            and snapshot.policy.max_projected_files == policy.max_projected_files,
            "body.functional:policy_argument")
    return FunctionalBodyInputs(snapshot, _token=_TOKEN)


__all__ = [
    "FunctionalBodyInputs", "RegisteredBodyInputs", "load_registered_body_inputs",
    "require_registered_body_inputs", "validate_functional_body_inputs",
]
