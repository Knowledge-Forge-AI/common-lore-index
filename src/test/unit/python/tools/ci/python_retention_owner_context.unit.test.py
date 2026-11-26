"""Negative controls for globals, caller arguments and measurement lifetime."""

import ast
from pathlib import Path

import pytest

from ci.python_retention_owner_contract import CONTRACTS, validate_owner_context

ROOT = Path(__file__).resolve().parents[6]


def _owner_id(owner: str) -> str:
    # Keep the six existing parametrized node IDs selectable after the support move.
    if owner == "src/test/support/python/common_lore_index_test_support/pgvector_prebuilt_discovery_cases.py":
        return "src/test/support/python/repomap_test_support/pgvector_prebuilt_discovery_cases.py"
    return owner


@pytest.mark.parametrize("owner", sorted(CONTRACTS), ids=_owner_id)
def test_complete_current_owner_context(owner: str) -> None:
    validate_owner_context(owner, ast.parse((ROOT / owner).read_text()))


@pytest.mark.parametrize("owner", sorted(CONTRACTS), ids=_owner_id)
@pytest.mark.parametrize("extra", [
    "REPO_ROOT = Path('/foreign')",
    "sys.path.append('/foreign')",
    "importlib = foreign_loader",
    "sys.modules.clear()",
    "exec_module(unregistered_module)",
])
def test_refreshed_source_cannot_authorize_changed_owner_context(owner: str, extra: str) -> None:
    # Deliberately bypass stale-source checks: semantic context must reject even
    # when a caller recomputes its source SHA and operation fingerprints.
    tree = ast.parse((ROOT / owner).read_text() + "\n" + extra + "\n")
    with pytest.raises(ValueError, match="owner context differs"):
        validate_owner_context(owner, tree)


def test_loader_callback_arguments_are_part_of_contract() -> None:
    owner = next(path for path in CONTRACTS if "helper_branches" in path)
    source = (ROOT / owner).read_text()
    assert '"probe.py"' in source
    changed = source.replace('"probe.py"', '"foreign.py"')
    with pytest.raises(ValueError, match="owner context differs"):
        validate_owner_context(owner, ast.parse(changed))
