from __future__ import annotations

import importlib.metadata
from pathlib import Path
import subprocess
import sys

import psycopg
from psycopg import pq
import psycopg_c
import pytest

from ci.bootstrap_pre_review import verify_python_distribution


def test_sandbox_c_psycopg_distribution_attestation_succeeds_in_fresh_child_python() -> None:
    python_bin = Path(sys.executable)
    owner_root = Path(sys.prefix).resolve()

    assert importlib.metadata.version("psycopg") == "3.3.6"
    assert importlib.metadata.version("psycopg-c") == "3.3.6"
    assert pq.__impl__ == "c"
    assert psycopg.__file__ is not None
    assert psycopg_c.__file__ is not None
    assert Path(psycopg.__file__).resolve().is_relative_to(owner_root)
    assert Path(psycopg_c.__file__).resolve().is_relative_to(owner_root)

    verify_python_distribution(python_bin, "psycopg", "psycopg", "3.3.6", owner_root)
    verify_python_distribution(python_bin, "psycopg_c", "psycopg-c", "3.3.6", owner_root)


def test_sandbox_c_psycopg_distribution_attestation_refuses_wrong_version() -> None:
    python_bin = Path(sys.executable)
    owner_root = Path(sys.prefix).resolve()

    with pytest.raises(
        RuntimeError,
        match="sealed Python dependency attestation failed for psycopg-c",
    ):
        verify_python_distribution(python_bin, "psycopg_c", "psycopg-c", "3.3.5", owner_root)

    with pytest.raises(
        RuntimeError,
        match="sealed Python dependency attestation failed for psycopg",
    ):
        verify_python_distribution(python_bin, "psycopg", "psycopg", "3.3.5", owner_root)


def test_sandbox_c_psycopg_distribution_attestation_refuses_wrong_owner_root(
    tmp_path: Path,
) -> None:
    python_bin = Path(sys.executable)
    wrong_root = tmp_path / "wrong-owner-root"
    wrong_root.mkdir()

    with pytest.raises(
        RuntimeError,
        match="sealed Python dependency attestation failed for psycopg-c",
    ):
        verify_python_distribution(python_bin, "psycopg_c", "psycopg-c", "3.3.6", wrong_root)

    with pytest.raises(
        RuntimeError,
        match="sealed Python dependency attestation failed for psycopg",
    ):
        verify_python_distribution(python_bin, "psycopg", "psycopg", "3.3.6", wrong_root)


def test_sandbox_c_psycopg_distribution_attestation_refuses_missing_module_or_distribution() -> None:
    python_bin = Path(sys.executable)
    owner_root = Path(sys.prefix).resolve()

    with pytest.raises(subprocess.CalledProcessError):
        verify_python_distribution(
            python_bin,
            "repomap_nonexistent_module_ci65",
            "psycopg-c",
            "3.3.6",
            owner_root,
        )

    with pytest.raises(subprocess.CalledProcessError):
        verify_python_distribution(
            python_bin,
            "psycopg_c",
            "repomap-nonexistent-dist-ci65",
            "3.3.6",
            owner_root,
        )



def test_current_loader_declarations_close_real_graph_and_retain_history() -> None:
    import json
    from ci.python_retention_dependencies import build_dependency_graph
    from ci.python_retention_dynamic import apply_dynamic_boundaries
    from ci.python_retention_dynamic_history import active_boundaries
    root = Path(__file__).resolve().parents[6]
    document = json.loads((root / "tools/ci/python_retention_inventory.json").read_bytes())
    candidates = sorted(row["path"] for row in document["files"])
    graph = apply_dynamic_boundaries(root, candidates, build_dependency_graph(root, candidates))
    records = [row for row in active_boundaries(document)
               if row["id"].startswith("cloe.ci149repair1.")]
    assert len(records) == 15
    for row in records:
        owner = row["owner"]
        assert owner not in graph["errors"]
        assert not graph["dynamic_uncertainty"].get(owner)
        assert row["targets"][0]["path"] in graph["dependencies"][owner]
        assert row["id"] in graph["resolved_dynamic_dependencies"]
    retired = {row["supersedes"] for row in records if row["supersedes"] is not None}
    assert len(retired) == 6
    assert retired <= {row["id"] for row in document["dynamic_boundaries"]}
    assert not retired & {row["id"] for row in active_boundaries(document)}
