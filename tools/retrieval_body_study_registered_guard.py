"""Registered BODY input custody without re-reading historical payload bytes."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import stat
from collections.abc import Callable
from typing import Any

from retrieval_body_pin import PIN_FILE_NAME, RegisteredBodyInputs, _read_current
from retrieval_body_predecessors import _read_relative
from retrieval_body_values import (
    INPUT_NAMES, REGISTERED_INPUT_PIN_SHA256, digest_bytes, require,
)
from retrieval_body_study_values import digest_value
from retrieval_supplement_files import file_inventory

_ISSUED = object()
_Fingerprint = tuple[int, int, int, int, int, int, int]
_HISTORICAL_ROOTS = ("body-v1", "context-v1", "mixed-v1", "supplement-v1")


def _member(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    require(parts and all(part not in {"", ".", ".."} for part in parts),
            "body.guard:relative_member")
    return root.joinpath(*parts)


def _fingerprint(path: Path) -> _Fingerprint:
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode), "body.guard:symlink")
    require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
            "body.guard:file_type")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _metadata(
    root: Path, members: tuple[str, ...], label: str,
    *, owners: dict[tuple[int, int], str] | None = None,
) -> tuple[tuple[str, _Fingerprint], ...]:
    values: dict[str, _Fingerprint] = {}
    inode_owners = {} if owners is None else owners
    for relative in members:
        path = _member(root, relative)
        while True:
            key = f"{label}:{'.' if path == root else path.relative_to(root)}"
            value = _fingerprint(path)
            if stat.S_ISREG(value[2]):
                owner = inode_owners.get((value[0], value[1]))
                require(owner is None or owner == key, "body.guard:hardlink")
                inode_owners[(value[0], value[1])] = key
            previous = values.get(key)
            require(previous is None or previous == value, "body.guard:metadata_alias")
            values[key] = value
            if path == root:
                break
            parent = path.parent
            require(parent != path, "body.guard:metadata_root")
            path = parent
    return tuple(sorted(values.items()))


def _historical_members(snapshot: Any, predecessor_root: Path) -> tuple[str, ...]:
    values = tuple(_read_relative(predecessor_root, item.path)
                   for item in snapshot.predecessor.files)
    require(len(values) == 73 and len(set(values)) == len(values),
            "body.guard:historical_members")
    return tuple(sorted(values))


def _historical_inventory(root: Path) -> tuple[str, ...]:
    values = tuple(
        f"{name}/{relative}"
        for name in _HISTORICAL_ROOTS
        for relative in file_inventory(root / name)
    )
    require(len(values) == 73 and len(set(values)) == len(values),
            "body.guard:historical_inventory")
    return tuple(sorted(values))


def _identity(value: "BodyRegisteredDataGuard") -> str:
    return digest_value({
        "inputs_object_id": id(value.inputs),
        "active_root": str(value.active_root),
        "predecessor_root": str(value.predecessor_root),
        "input_pin_sha256": value.input_pin_sha256,
        "catalog_sha256": value.catalog_sha256,
        "policy_sha256": value.policy_sha256,
        "active_file_bytes": value.active_file_bytes,
        "registered_snapshot_file_bytes": value.registered_snapshot_file_bytes,
        "active_members": value.active_members,
        "historical_members": value.historical_members,
        "metadata": value.frozen_metadata,
    })


@dataclass(frozen=True, slots=True)
class BodyRegisteredDataGuard:
    """Factory-sealed active bytes plus historical custody metadata."""

    inputs: RegisteredBodyInputs
    active_root: Path
    predecessor_root: Path
    active_members: tuple[str, ...]
    historical_members: tuple[str, ...]
    frozen_metadata: tuple[tuple[str, _Fingerprint], ...]
    input_pin_sha256: str
    catalog_sha256: str
    policy_sha256: str
    active_file_bytes: int
    registered_snapshot_file_bytes: int
    _origin: object = field(default=None, repr=False, compare=False)
    _seal: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        require(self._origin is _ISSUED, "body.guard:factory_required")
        require(RegisteredBodyInputs.is_factory_issued(self.inputs),
                "body.guard:inputs_factory")
        require(self.inputs._root == self.active_root
                and self.inputs._predecessor_root == self.predecessor_root,
                "body.guard:input_roots")
        require(self.input_pin_sha256 == REGISTERED_INPUT_PIN_SHA256,
                "body.guard:input_pin")
        require(type(self.active_file_bytes) is int and self.active_file_bytes >= 0,
                "body.guard:active_bytes")
        require(type(self.registered_snapshot_file_bytes) is int
                and self.registered_snapshot_file_bytes >= self.active_file_bytes,
                "body.guard:snapshot_bytes")

    @staticmethod
    def is_factory_issued(value: object) -> bool:
        return (type(value) is BodyRegisteredDataGuard and value._origin is _ISSUED
                and value._seal == _identity(value)
                and RegisteredBodyInputs.is_factory_issued(value.inputs)
                and value.inputs._root == value.active_root
                and value.inputs._predecessor_root == value.predecessor_root)

    @property
    def identity(self) -> str:
        require(self.is_factory_issued(self), "body.guard:identity")
        return self._seal

    @classmethod
    def from_registered_inputs(cls, inputs: RegisteredBodyInputs) -> "BodyRegisteredDataGuard":
        require(type(inputs) is RegisteredBodyInputs
                and RegisteredBodyInputs.is_factory_issued(inputs),
                "body.guard:inputs_factory")
        active_root = inputs._root
        predecessor_root = inputs._predecessor_root
        active_members = tuple(sorted((*INPUT_NAMES, PIN_FILE_NAME)))
        historical_members = _historical_inventory(predecessor_root)
        before_owners: dict[tuple[int, int], str] = {}
        before = tuple(sorted((
            *_metadata(active_root, active_members, "active", owners=before_owners),
            *_metadata(predecessor_root, historical_members, "historical", owners=before_owners),
        )))
        snapshot = inputs._snapshot()
        require(_historical_members(snapshot, predecessor_root) == historical_members,
                "body.guard:historical_manifest")
        require(_historical_inventory(predecessor_root) == historical_members,
                "body.guard:historical_manifest_after")
        after_owners: dict[tuple[int, int], str] = {}
        after = tuple(sorted((
            *_metadata(active_root, active_members, "active", owners=after_owners),
            *_metadata(predecessor_root, historical_members, "historical", owners=after_owners),
        )))
        require(before == after,
                "body.guard:construction_toctou")
        frozen = after
        by_name = dict(after)
        active_file_bytes = sum(by_name[f"active:{name}"][4] for name in active_members)
        snapshot_file_bytes = (
            sum(by_name[f"active:{name}"][4] for name in active_members)
            + sum(by_name[f"historical:{name}"][4] for name in historical_members)
        )
        result = cls(
            inputs, active_root, predecessor_root, active_members, historical_members,
            frozen, snapshot.pin_sha256, snapshot.catalog.digest,
            snapshot.policy.policy_digest(), active_file_bytes, snapshot_file_bytes,
            _origin=_ISSUED,
        )
        object.__setattr__(result, "_seal", _identity(result))
        require(cls.is_factory_issued(result), "body.guard:seal")
        return result

    def _current_metadata(self) -> tuple[tuple[str, _Fingerprint], ...]:
        # Inventory is a custody join, not a payload read.  Requiring the
        # frozen historical member set on every hop catches additions as well
        # as deletions; the metadata walk below then catches replacement,
        # ownership, mode, link, size, mtime, and ctime changes.
        require(_historical_inventory(self.predecessor_root) == self.historical_members,
                "body.guard:historical_inventory_drift")
        owners: dict[tuple[int, int], str] = {}
        return tuple(sorted((
            *_metadata(self.active_root, self.active_members, "active", owners=owners),
            *_metadata(self.predecessor_root, self.historical_members, "historical", owners=owners),
        )))

    def require_current(self, code_guard: Callable[[], None]) -> None:
        """Read active bytes once; historical members receive custody-only checks."""
        require(self.is_factory_issued(self), "body.guard:seal_current")
        require(callable(code_guard), "body.guard:code_guard")
        before = self._current_metadata()
        require(before == self.frozen_metadata, "body.guard:metadata_before")
        code_guard()
        pin_raw, _files, _pin = _read_current(self.active_root)
        require(digest_bytes(pin_raw) == REGISTERED_INPUT_PIN_SHA256,
                "body.guard:active_pin")
        after = self._current_metadata()
        require(after == before == self.frozen_metadata, "body.guard:metadata_after")

    def require_semantic_snapshot(self, code_guard: Callable[[], None]) -> Any:
        """Perform a full current façade validation only at a semantic boundary."""
        require(self.is_factory_issued(self), "body.guard:seal_semantic")
        require(callable(code_guard), "body.guard:code_guard")
        before = self._current_metadata()
        snapshot = self.inputs._snapshot()
        code_guard()
        after = self._current_metadata()
        require(before == after == self.frozen_metadata, "body.guard:semantic_toctou")
        require(snapshot.pin_sha256 == self.input_pin_sha256
                and snapshot.catalog.digest == self.catalog_sha256
                and snapshot.policy.policy_digest() == self.policy_sha256,
                "body.guard:semantic_identity")
        return snapshot


def registered_body_stat_footprint(source_root: Path) -> dict[str, int]:
    """Return a stable lstat-only footprint for the registered BODY tree."""
    require(isinstance(source_root, Path) and source_root.is_absolute()
            and not source_root.is_symlink(), "body.guard:stat_root")
    predecessor_root = source_root.parent
    active_members = tuple(sorted((*INPUT_NAMES, PIN_FILE_NAME)))
    historical_members = _historical_inventory(predecessor_root)
    before_owners: dict[tuple[int, int], str] = {}
    before = tuple(sorted((
        *_metadata(source_root, active_members, "active", owners=before_owners),
        *_metadata(predecessor_root, historical_members, "historical", owners=before_owners),
    )))
    after_owners: dict[tuple[int, int], str] = {}
    after = tuple(sorted((
        *_metadata(source_root, active_members, "active", owners=after_owners),
        *_metadata(predecessor_root, historical_members, "historical", owners=after_owners),
    )))
    require(before == after, "body.guard:stat_toctou")
    indexed = dict(after)
    active = sum(indexed[f"active:{name}"][4] for name in active_members)
    historical = sum(indexed[f"historical:{name}"][4] for name in historical_members)
    return {"active_file_bytes": active,
            "registered_snapshot_file_bytes": active + historical}


__all__ = ["BodyRegisteredDataGuard", "registered_body_stat_footprint"]
