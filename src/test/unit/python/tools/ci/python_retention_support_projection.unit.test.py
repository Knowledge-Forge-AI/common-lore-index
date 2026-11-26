"""Prove test-support source lineage against an owned synthetic Git history."""
from __future__ import annotations

import hashlib
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import pytest

from ci import python_retention_support_projection as projection
from ci.python_retention_cohorts import _validate_admitted_replacement
from ci.python_retention_dynamic_history import SCHEMA, active_boundaries
from ci.python_retention_history import _resolve_chain
from ci.python_retention_history_git import HistoryValidationError
from ci.python_retention_operations import dynamic_operations
from ci.python_retention_support_dynamic import derive_support_dynamic_records, project_source


def _git(root: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")} | {
        "GIT_AUTHOR_NAME": "CLOE test",
        "GIT_AUTHOR_EMAIL": "cloe-test@example.invalid",
        "GIT_COMMITTER_NAME": "CLOE test",
        "GIT_COMMITTER_EMAIL": "cloe-test@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *args],
        capture_output=True, text=True, check=True, env=env,
    )
    return result.stdout.strip()


def _bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _json(path: Path, value: Any) -> bytes:
    encoded = json.dumps(value, sort_keys=True).encode()
    _bytes(path, encoded)
    return encoded


@pytest.fixture
def transition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any]]:
    monkeypatch.setattr(projection, "PROJECTED_MEMBER_COUNT", 1)
    monkeypatch.setattr(projection, "PROJECTED_DYNAMIC_COUNT", 0)
    old = f"{projection.OLD_PACKAGE}/owner.py"
    new = f"{projection.NEW_PACKAGE}/owner.py"
    source = b"from repomap_test_support import helpers\n"
    _git(tmp_path, "init", "-q")
    _bytes(tmp_path / old, source)
    inventory = _json(tmp_path / projection.INVENTORY, {"files": [{"path": old}]})
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "Predecessor support package")
    predecessor = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "rm", old)
    successor = b"from common_lore_index_test_support import helpers\n"
    _bytes(tmp_path / new, successor)
    receipt = {
        "schema": projection.SCHEMA,
        "authority": projection.AUTHORITY,
        "predecessor": predecessor,
        "inventory_sha256": hashlib.sha256(inventory).hexdigest(),
        "dynamic_successor_sha256": hashlib.sha256(b"[]").hexdigest(),
        "members": [{
            "before": old,
            "after": new,
            "before_oid": _git(tmp_path, "rev-parse", f"{predecessor}:{old}"),
            "after_sha256": hashlib.sha256(successor).hexdigest(),
        }],
    }
    _json(tmp_path / projection.RECEIPT, receipt)
    _git(tmp_path, "add", ".")
    return tmp_path, receipt


def test_projection_preserves_stable_test_paths() -> None:
    before = (b"from repomap_test_support import helpers\n"
              b"selector = 'src/test/int/python/repomap_test_support/owner.int.test.py'\n"
              b"def test_repomap_test_support_snapshot(): pass\n")
    assert project_source(before) == (
        b"from common_lore_index_test_support import helpers\n"
        b"selector = 'src/test/int/python/repomap_test_support/owner.int.test.py'\n"
        b"def test_repomap_test_support_snapshot(): pass\n"
    )


def test_staged_and_introduced_projection_with_later_source_change(
    transition: tuple[Path, dict[str, Any]],
) -> None:
    root, receipt = transition
    entry, = receipt["members"]
    staged = projection.verify_support_namespace(root)
    assert staged is not None
    assert staged.introduction is None
    assert staged.paths == {entry["before"]: entry["after"]}
    _git(root, "commit", "-qm", "Introduce support projection")
    introduction = _git(root, "rev-parse", "HEAD")
    sealed = projection.verify_support_namespace(root, exact_candidate=introduction)
    assert sealed is not None and sealed.introduction == introduction
    _bytes(root / entry["after"], b"value = 'ordinary later change'\n")
    assert projection.verify_support_namespace(root) == sealed


def test_merge_keeps_the_single_introduction(transition: tuple[Path, dict[str, Any]]) -> None:
    root, receipt = transition
    _git(root, "branch", "other", receipt["predecessor"])
    introducing_branch = _git(root, "branch", "--show-current")
    _git(root, "commit", "-qm", "Introduce support projection")
    introduction = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "other")
    _git(root, "commit", "--allow-empty", "-qm", "Independent checkpoint")
    _git(root, "merge", "--no-ff", "--no-edit", introducing_branch)
    head = _git(root, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="does not belong to the exact commit"):
        projection.verify_support_namespace(root, exact_candidate=introduction)
    sealed = projection.verify_support_namespace(root, exact_candidate=head)
    assert sealed is not None and sealed.introduction == introduction


def test_receipt_cannot_be_rewritten_after_introduction(transition: tuple[Path, dict[str, Any]]) -> None:
    root, receipt = transition
    _git(root, "commit", "-qm", "Introduce support projection")
    receipt["inventory_sha256"] = "0" * 64
    _json(root / projection.RECEIPT, receipt)
    _git(root, "commit", "-qam", "Rewrite support projection")
    with pytest.raises(ValueError, match="modified after introduction"):
        projection.verify_support_namespace(root)


def test_preserves_historical_children_and_later_canonical_successors() -> None:
    old = f"{projection.OLD_PACKAGE}/owner.py"
    new = f"{projection.NEW_PACKAGE}/owner.py"
    child = f"{projection.NEW_PACKAGE}/child.py"
    successor = f"{projection.NEW_PACKAGE}/successor.py"
    targets = {old: [child], new: [successor]}
    support = projection.SupportNamespaceProjection(None, {old: new}, {})
    assert _resolve_chain(old, {child, successor}, targets, support.paths) == {child, successor}
    current = {
        "files": [{"path": child}, {"path": successor}],
        "path_transitions": [{"from": new, "to": successor, "rationale": "Later move"}],
    }
    _validate_admitted_replacement(old, {"members": [successor]}, current, support)
    with pytest.raises(HistoryValidationError, match="remain in original cohort"):
        _validate_admitted_replacement(old, {"members": [child]}, current, support)


def test_dynamic_ledger_accepts_only_the_derived_successor() -> None:
    old = f"{projection.OLD_PACKAGE}/owner.py"
    new = f"{projection.NEW_PACKAGE}/owner.py"
    owner, witness = "tools/entry.py", "src/test/unit/python/tools/entry.unit.test.py"
    prior = {
        owner: b'import runpy\ndef entry():\n    runpy.run_module("repomap_test_support.owner", run_name="__main__")\n',
        witness: b"from repomap_test_support import owner\n",
        old: b"value = 1\n",
    }
    updated = {(new if path == old else path): project_source(raw) for path, raw in prior.items()}
    operation, = dynamic_operations(ast.parse(prior[owner]))
    base = {
        "id": "fixture.entry", "owner": owner, "test": witness, "recipe": "literal_module_entry",
        "owner_sha256": hashlib.sha256(prior[owner]).hexdigest(),
        "test_sha256": hashlib.sha256(prior[witness]).hexdigest(),
        "kind": operation["kind"], "symbol": operation["symbol"], "fingerprint": operation["fingerprint"],
        "parameters": {"module": "repomap_test_support.owner", "run_name": "__main__"},
        "targets": [{"path": old, "module": "repomap_test_support.owner",
                     "sha256": hashlib.sha256(prior[old]).hexdigest()}],
        "supersedes": None,
    }
    exact, = derive_support_dynamic_records([base], {old: new}, prior.__getitem__, updated.__getitem__)
    assert exact["parameters"]["module"] == "common_lore_index_test_support.owner"
    assert exact["targets"][0]["path"] == new
    assert exact["fingerprint"] != base["fingerprint"]
    document: dict[str, Any] = {"dynamic_schema": SCHEMA, "dynamic_boundaries": [base, exact]}
    authority = projection.SupportNamespaceProjection(None, {old: new}, {exact["id"]: exact})
    assert active_boundaries(document, support=authority) == [exact]
    with pytest.raises(ValueError, match="requires verified lineage"):
        active_boundaries(document)
    forged = deepcopy(document)
    forged["dynamic_boundaries"][1]["parameters"]["module"] = "unrelated"
    with pytest.raises(ValueError, match="differs from the verified projection"):
        active_boundaries(forged, support=authority)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing", "receipt is missing"),
        ("mixed", "legacy test-support namespace remains present"),
        ("source", "successor content differs"),
        ("digest", "dynamic successor proof differs"),
        ("late", "predecessor is not the introduction parent"),
    ],
)
def test_refuses_invalid_source_lineage(
    transition: tuple[Path, dict[str, Any]], mutation: str, expected: str,
) -> None:
    root, receipt = transition
    entry, = receipt["members"]
    if mutation == "missing":
        (root / projection.RECEIPT).unlink()
    elif mutation == "mixed":
        _bytes(root / entry["before"], b"")
    elif mutation == "source":
        _bytes(root / entry["after"], b"value = 'unrelated change'\n")
    elif mutation == "digest":
        receipt["dynamic_successor_sha256"] = "0" * 64
        _json(root / projection.RECEIPT, receipt)
    else:
        _git(root, "reset", "-q")
        _git(root, "commit", "--allow-empty", "-qm", "Different intermediate predecessor")
        _git(root, "add", "-A")
    with pytest.raises(ValueError, match=expected):
        projection.verify_support_namespace(root)
