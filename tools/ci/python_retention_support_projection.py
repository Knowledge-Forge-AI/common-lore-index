"""Verify one forward test-support package namespace projection."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

from ci.retained_python_contract import RatchetContractError
from ci.retained_python_git import blob, git, git_required
from ci.retained_python_projection_evidence import read_file
from ci.python_retention_support_dynamic import derive_support_dynamic_records, project_source

LEGACY_MODULE = "repomap_test_support"
CANONICAL_MODULE = "common_lore_index_test_support"
PARENT = "src/test/support/python"
OLD_PACKAGE = f"{PARENT}/{LEGACY_MODULE}"
NEW_PACKAGE = f"{PARENT}/{CANONICAL_MODULE}"
INVENTORY = "tools/ci/python_retention_inventory.json"
RECEIPT = "tools/ci/cloe-test-support-namespace-lineage.json"
SCHEMA = "cloe-test-support-namespace-lineage-v1"
IDENTITY = "Sealed ADR0223 test-support namespace projection"
AUTHORITY = "ADR0223"
PROJECTED_MEMBER_COUNT = 464
PROJECTED_DYNAMIC_COUNT = 35


def _decode(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate test-support namespace field")
            result[key] = value
        return result

    doc = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(doc, dict):
        raise ValueError("test-support namespace evidence must be an object")
    return doc


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _tree_sources(root: Path, revision: str) -> dict[str, tuple[bytes, bytes]]:
    result = git(root, ("ls-tree", "-r", "-z", revision, "--", OLD_PACKAGE, NEW_PACKAGE), text=False)
    if result.returncode:
        raise ValueError("test-support predecessor tree cannot be read")
    files = {}
    for record in result.stdout.split(b"\x00"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, oid = metadata.split(b" ")
        path = raw_path.decode("utf-8")
        if kind != b"blob" or mode not in (b"100644", b"100755") or path in files:
            raise ValueError("test-support predecessor includes a non-regular entry")
        files[path] = (mode, oid)
    return files


def _project_member(path: str) -> str:
    parts = PurePosixPath(path).parts
    if (
        not path.startswith(OLD_PACKAGE + "/")
        or "\\" in path
        or ".." in parts
        or str(PurePosixPath(path)) != path
        or not path.endswith(".py")
    ):
        raise ValueError("test-support predecessor is outside the sealed Python package")
    return NEW_PACKAGE + path[len(OLD_PACKAGE):]


def _package_blobs(root: Path, tree: dict[str, tuple[bytes, bytes]]) -> dict[bytes, bytes]:
    """Batch immutable package objects by object ID, never by an input path."""
    oids = sorted({oid for _, oid in tree.values()})
    try:
        result = subprocess.run(
            ["git", "cat-file", "--batch"], cwd=root, input=b"\n".join(oids) + b"\n",
            capture_output=True, env=os.environ | {"GIT_NO_REPLACE_OBJECTS": "1"}, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RatchetContractError("test-support package object inspection failed") from error
    if result.returncode:
        raise ValueError("test-support package objects could not be read")
    stream = BytesIO(result.stdout)
    loaded = {}
    for oid in oids:
        header = stream.readline().rstrip(b"\n").split(b" ")
        if len(header) != 3 or header[:2] != [oid, b"blob"] or not header[2].isdigit():
            raise ValueError("test-support package object header differs")
        count = int(header[2])
        data = stream.read(count)
        if len(data) != count or stream.read(1) != b"\n":
            raise ValueError("test-support package object content is truncated")
        loaded[oid] = data
    if stream.read(1):
        raise ValueError("test-support package object response has trailing bytes")
    return loaded


def _verify_current_source(root: Path, path: str, expected_mode: bytes, expected_sha: str) -> None:
    data = read_file(root, path)
    if data is None or _hash(data) != expected_sha:
        raise ValueError(f"test-support successor content differs: {path}")
    observed = b"100755" if (root / path).stat().st_mode & 0o100 else b"100644"
    if observed != expected_mode:
        raise ValueError(f"test-support successor mode differs: {path}")


@dataclass(frozen=True)
class SupportNamespaceProjection:
    introduction: str | None
    paths: dict[str, str]
    dynamic_by_id: dict[str, dict[str, Any]]


def _receipt_introduction(root: Path, candidate: str, receipt: bytes) -> str | None:
    history = git_required(root, "log", "--full-history", "--format=%H", candidate, "--", RECEIPT).splitlines()
    introductions = []
    for revision in history:
        if blob(root, revision, RECEIPT) != receipt:
            raise ValueError("test-support namespace evidence was modified after introduction")
        parents = git_required(root, "show", "-s", "--format=%P", revision).split()
        if len(parents) == 1 and blob(root, parents[0], RECEIPT) is None:
            introductions.append(revision)
    if history and len(introductions) != 1:
        raise ValueError("test-support namespace evidence requires one introduction")
    return introductions[0] if introductions else None


def verify_support_namespace(root: Path, *, exact_candidate: str | None = None) -> SupportNamespaceProjection | None:
    """Return validated current paths. The committed check additionally binds the receipt and tree."""
    path = root / RECEIPT
    raw = read_file(root, RECEIPT) if path.exists() or path.is_symlink() else None
    new_present = (root / NEW_PACKAGE).exists() or (root / NEW_PACKAGE).is_symlink()
    if raw is None and not new_present and not (root / ".git").exists():
        return None
    head = git(root, ("rev-parse", "--verify", "--quiet", "HEAD"))
    if head.returncode == 1 and raw is None and not new_present:
        return None
    if head.returncode:
        raise RatchetContractError("Git history inspection failed")
    candidate = head.stdout.strip()
    if raw is None:
        if new_present or git_required(root, "log", "-1", "--full-history", "--format=%H", candidate, "--", RECEIPT):
            raise ValueError("test-support namespace receipt is missing")
        return None
    document = _decode(raw)
    if git(root, ("ls-files", "--error-unmatch", "--", RECEIPT)).returncode:
        raise ValueError("test-support namespace receipt is untracked")
    fields = {"schema", "authority", "predecessor", "inventory_sha256", "members", "dynamic_successor_sha256"}
    if set(document) != fields or document["schema"] != SCHEMA or document["authority"] != AUTHORITY:
        raise ValueError("test-support namespace receipt has invalid fields")
    revision = document["predecessor"]
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("test-support namespace predecessor is not a commit ID")
    if git_required(root, "rev-parse", "--verify", f"{revision}^{{commit}}") != revision:
        raise ValueError("test-support namespace predecessor is not the pinned commit itself")
    sealed = _receipt_introduction(root, candidate, raw)
    if exact_candidate is not None and (candidate != exact_candidate or sealed is None):
        raise ValueError("test-support namespace evidence does not belong to the exact commit")
    if sealed is None:
        parent = candidate
    else:
        parents = git_required(root, "show", "-s", "--format=%P", sealed).split()
        if len(parents) != 1:
            raise ValueError("test-support namespace introduction must have one parent")
        parent = parents[0]
    if revision != parent:
        raise ValueError("test-support namespace predecessor is not the introduction parent")
    previous = blob(root, revision, INVENTORY)
    if previous is None or _hash(previous) != document["inventory_sha256"]:
        raise ValueError("test-support predecessor inventory differs or is absent")
    expected = _tree_sources(root, revision)
    members = document["members"]
    if not isinstance(members, list) or len(members) != PROJECTED_MEMBER_COUNT or len(expected) != len(members):
        raise ValueError("test-support predecessor census differs from approval")
    older_document = _decode(previous)
    rows = older_document.get("files")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("path"), str) for row in rows
    ):
        raise ValueError("test-support predecessor inventory files are invalid")
    before_inventory = {item["path"] for item in rows if item["path"].startswith(OLD_PACKAGE + "/")}
    if before_inventory != set(expected):
        raise ValueError("test-support predecessor inventory does not cover every tracked member")
    before_blobs = _package_blobs(root, expected)
    after_tree = _tree_sources(root, sealed) if sealed is not None else {}
    after_blobs = _package_blobs(root, after_tree) if after_tree else {}
    derived: dict[str, str] = {}
    for row in members:
        if not isinstance(row, dict) or set(row) != {"before", "after", "before_oid", "after_sha256"}:
            raise ValueError("test-support namespace member fields are invalid")
        before, after = row["before"], row["after"]
        if not isinstance(before, str) or before in derived or after != _project_member(before):
            raise ValueError("test-support namespace member mapping differs")
        if before not in expected:
            raise ValueError("test-support namespace member is absent from predecessor")
        mode, oid = expected[before]
        if oid.decode("ascii") != row["before_oid"]:
            raise ValueError("test-support predecessor Git blob differs")
        if _hash(project_source(before_blobs[oid])) != row["after_sha256"]:
            raise ValueError("test-support successor exceeds the sealed import and path projection")
        if sealed is None:
            _verify_current_source(root, after, mode, row["after_sha256"])
        else:
            observed = after_tree.get(after)
            if observed is None or observed[0] != mode or _hash(after_blobs[observed[1]]) != row["after_sha256"]:
                raise ValueError("test-support successor differs from its introduction commit")
        derived[before] = after
    if sealed is not None:
        if set(after_tree) != set(derived.values()):
            raise ValueError("test-support introduction tree census or modes differ")
    current = git(root, ("ls-files", "-z", "--", OLD_PACKAGE, NEW_PACKAGE), text=False)
    if current.returncode:
        raise ValueError("test-support current tracked census could not be read")
    current_paths = {p.decode("utf-8") for p in current.stdout.split(b"\x00") if p}
    if any(name.startswith(OLD_PACKAGE + "/") for name in current_paths):
        raise ValueError("legacy test-support files are still tracked")
    if sealed is None and current_paths != set(derived.values()):
        raise ValueError("test-support current tracked census differs from projection")
    if (root / OLD_PACKAGE).exists() or (root / OLD_PACKAGE).is_symlink():
        raise ValueError("legacy test-support namespace remains present")
    # Resolve actual active predecessors with the historical schema before deriving
    # projected declarations. This local import keeps the early source parser independent.
    from ci.python_retention_dynamic_history import active_boundaries

    @cache
    def prior(path: str) -> bytes:
        value = blob(root, revision, path)
        if value is None:
            raise ValueError("test-support dynamic predecessor source is absent")
        return value

    @cache
    def successor(path: str) -> bytes:
        value = blob(root, sealed, path) if sealed is not None else read_file(root, path)
        if value is None:
            raise ValueError("test-support dynamic successor source is absent")
        return value

    older_rows = active_boundaries(older_document)
    try:
        dynamic = derive_support_dynamic_records(older_rows, derived, prior, successor)
    except (KeyError, TypeError, AttributeError, SyntaxError) as error:
        raise ValueError("test-support dynamic witness is malformed") from error
    rendered = json.dumps(dynamic, sort_keys=True, separators=(",", ":")).encode()
    if len(dynamic) != PROJECTED_DYNAMIC_COUNT or _hash(rendered) != document["dynamic_successor_sha256"]:
        raise ValueError("test-support dynamic successor proof differs")
    by_id = {row["id"]: row for row in dynamic}
    if len(by_id) != len(dynamic):
        raise ValueError("test-support dynamic successor IDs collide")
    return SupportNamespaceProjection(sealed, derived, by_id)
