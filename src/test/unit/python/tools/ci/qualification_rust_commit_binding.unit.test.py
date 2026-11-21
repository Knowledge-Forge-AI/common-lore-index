"""Candidate binding uses actual Git blobs, not only a matching HEAD string."""

from pathlib import Path
import json
import os
import subprocess
from typing import Any

import pytest

from ci.rust_pilot_qualification import PrerequisiteRefused, _bind_candidate


def _git(root: Path, *args: str) -> str:
    env = os.environ | {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                        "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                        "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
    result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                             "-c", "init.templateDir=", *args], cwd=root, env=env,
                            text=True, capture_output=True, check=True, timeout=10)
    return result.stdout.strip()


@pytest.fixture
def candidate(tmp_path: Path) -> tuple[Path, dict[str, Any], str, str]:
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "source.py").write_text("VALUE = 1\n")
    _git(tmp_path, "add", "source.py")
    _git(tmp_path, "commit", "-m", "Pre-manifest fixture checkpoint")
    checkpoint = {"sha": _git(tmp_path, "rev-parse", "HEAD"),
                  "tree": _git(tmp_path, "rev-parse", "HEAD^{tree}")}
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures/sample.rs").write_text("fn sample() {}\n")
    (tmp_path / "incumbent.json").write_text("{}\n")
    (tmp_path / "recipe.txt").write_text("pinned recipe fixture\n")
    document = {"source_checkpoint": checkpoint, "closure": {"python_paths": ["source.py"]},
                "fixture": {"root": "fixtures", "files": {"sample.rs": {}}},
                "incumbent": {"report_path": "incumbent.json"},
                "sdk": {"recipe_files": {"recipe.txt": "unused-by-blob-binding"}}}
    (tmp_path / "manifest.json").write_text(json.dumps(document) + "\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Candidate fixture bytes")
    return tmp_path, document, _git(tmp_path, "rev-parse", "HEAD"), _git(tmp_path, "rev-parse", "HEAD^{tree}")


def test_exact_candidate_regular_blobs_bind(candidate: tuple[Path, dict[str, Any], str, str]) -> None:
    root, manifest, head, tree = candidate
    _bind_candidate(root, manifest, head, tree, root / "manifest.json")


@pytest.mark.parametrize("path", ["source.py", "manifest.json", "fixtures/sample.rs", "incumbent.json", "recipe.txt"])
def test_uncommitted_bound_bytes_refuse(
        candidate: tuple[Path, dict[str, Any], str, str], path: str) -> None:
    root, manifest, head, tree = candidate
    target = root / path
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(PrerequisiteRefused, match="committed_input_bytes_mismatch"):
        _bind_candidate(root, manifest, head, tree, root / "manifest.json")


def test_untracked_bound_source_refuses(candidate: tuple[Path, dict[str, Any], str, str]) -> None:
    root, manifest, head, tree = candidate
    (root / "extra.py").write_text("VALUE = 2\n")
    manifest["closure"]["python_paths"].append("extra.py")
    with pytest.raises(PrerequisiteRefused, match="committed_input_path_set_mismatch"):
        _bind_candidate(root, manifest, head, tree, root / "manifest.json")


def test_sealed_candidate_tree_mismatch_refuses(candidate: tuple[Path, dict[str, Any], str, str]) -> None:
    root, manifest, head, _tree = candidate
    with pytest.raises(PrerequisiteRefused, match="candidate_binding_mismatch"):
        _bind_candidate(root, manifest, head, "0" * 40, root / "manifest.json")


def test_older_checkpoint_refuses_shallow_history_and_binds_after_restoration(
        candidate: tuple[Path, dict[str, Any], str, str], tmp_path: Path) -> None:
    root, manifest, _head, _tree = candidate
    for index in range(3):
        _git(root, "commit", "--allow-empty", "-m", f"Advance fixture {index}")
    head, tree = _git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "HEAD^{tree}")
    clone = tmp_path / "shallow"
    _git(root, "clone", "--depth=2", root.as_uri(), str(clone))
    assert _git(clone, "rev-parse", "--is-shallow-repository") == "true"
    with pytest.raises(PrerequisiteRefused, match="git_evidence_unavailable"):
        _bind_candidate(clone, manifest, head, tree, clone / "manifest.json")
    _git(clone, "fetch", "--unshallow", "origin")
    assert _git(clone, "rev-parse", "--is-shallow-repository") == "false"
    _bind_candidate(clone, manifest, head, tree, clone / "manifest.json")


def test_successor_refuses_checkpoint_with_an_older_manifest(candidate) -> None:
    root, manifest, old_head, old_tree = candidate
    manifest["source_checkpoint"] = {"sha": old_head, "tree": old_tree}
    (root / "manifest.json").write_text(json.dumps(manifest) + "\n")
    _git(root, "add", "manifest.json")
    _git(root, "commit", "-m", "Successor still points at a manifest-bearing checkpoint")
    head, tree = _git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "HEAD^{tree}")
    with pytest.raises(PrerequisiteRefused, match="checkpoint_contains_manifest"):
        _bind_candidate(root, manifest, head, tree, root / "manifest.json")


def test_successor_binds_after_manifest_free_source_checkpoint(candidate) -> None:
    root, manifest, old_head, _old_tree = candidate
    (root / "source.py").write_text("VALUE = 2\n")
    (root / "manifest.json").unlink()
    _git(root, "add", "-u")
    _git(root, "commit", "-m", "Commit successor source without its manifest")
    checkpoint = {"sha": _git(root, "rev-parse", "HEAD"),
                  "tree": _git(root, "rev-parse", "HEAD^{tree}")}
    assert _git(root, "show", checkpoint["sha"] + ":source.py") == "VALUE = 2"
    absent = subprocess.run(["git", "cat-file", "-e", checkpoint["sha"] + ":manifest.json"],
                            cwd=root, capture_output=True, check=False)
    assert absent.returncode != 0
    manifest["source_checkpoint"] = checkpoint
    (root / "manifest.json").write_text(json.dumps(manifest) + "\n")
    _git(root, "add", "manifest.json")
    _git(root, "commit", "-m", "Restore successor manifest with exact source checkpoint")
    head, tree = _git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "HEAD^{tree}")
    assert _git(root, "merge-base", old_head, head) == old_head
    _bind_candidate(root, manifest, head, tree, root / "manifest.json")
