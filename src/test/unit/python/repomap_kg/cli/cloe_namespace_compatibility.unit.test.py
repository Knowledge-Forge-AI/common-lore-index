"""Explicit entrypoint branding and finite canonical object reexports."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import sys
from pathlib import Path
import tomllib
from unittest.mock import patch

import pytest

import common_lore_index
from common_lore_index import cli, entrypoints
from common_lore_index.cli.main import main as canonical_main
from common_lore_index.cli.parser import build_parser
from common_lore_index.runtime.project_identity import PROJECT_IDENTITY
BRANDS = ((entrypoints.cloe_main, "cloe"), (entrypoints.long_main, "common-lore-index"))


def capture(entry, *args: str):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = entry(list(args))
        except SystemExit as exited:
            code = exited.code
    return code, stdout.getvalue(), stderr.getvalue()


def test_each_entrypoint_delegates_to_the_one_canonical_main():
    calls = []
    def canonical(argv=None, *, prog):
        calls.append((argv, prog))
        return 7
    for entry, prog in BRANDS:
        calls.clear()
        with patch.object(cli, "main", side_effect=canonical):
            code, stdout, stderr = capture(entry, "identity", "--json")
        assert (code, stdout) == (7, "")
        assert calls == [(["identity", "--json"], prog)]
        assert stderr == ""
    assert cli.main is canonical_main


@pytest.mark.parametrize("argv0", ["repomap-kg", "cloe", "/opt/env/bin/common-lore-index", "-c"])
def test_brand_is_explicit_and_never_inferred_from_argv0(argv0):
    with patch.object(sys, "argv", [argv0, "--help"]):
        for entry, prog in BRANDS:
            code, stdout, stderr = capture(entry, "--help")
            assert code == 0 and stdout.startswith(f"usage: {prog} ") and "RepoMap" not in stdout
            assert stderr == ""
    assert build_parser().prog == "cloe"


def test_cli_keeps_no_argument_build_parser_seam(monkeypatch):
    seen = []
    def parser():
        seen.append(True)
        return build_parser(prog="seam")
    monkeypatch.setitem(canonical_main.__globals__, "build_parser", parser)
    code, stdout, _stderr = capture(canonical_main, "--help")
    assert code == 0 and stdout.startswith("usage: seam ") and seen == [True]


def test_version_brands_and_identity_stdout_preserve_exact_contracts():
    for entry, prog in BRANDS:
        assert capture(entry, "--version") == (
            0, f"{prog} {common_lore_index.__version__}\n", "")
        code, stdout, stderr = capture(entry, "identity", "--json")
        assert code == 0 and json.loads(stdout) == PROJECT_IDENTITY.as_dict()
        assert len(json.loads(stdout)) == 6
        assert stderr == ""


def test_installable_entrypoints_publish_only_canonical_commands() -> None:
    root = Path(__file__).resolve().parents[6]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert project["project"]["scripts"] == {
        "cloe": "common_lore_index.entrypoints:cloe_main",
        "common-lore-index": "common_lore_index.entrypoints:long_main",
    }
    assert project["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "common_lore_index", "common_lore_index.*",
    ]
    assert not hasattr(entrypoints, "legacy_main")


def test_preview004_identity_matches_packaging_and_root_lock_entry() -> None:
    root = Path(__file__).resolve().parents[6]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((root / "uv.lock").read_text())
    editable = [row for row in lock["package"] if row.get("source") == {"editable": "."}]
    assert common_lore_index.__version__ == "0.0.4"
    assert project["name"] == common_lore_index.DISTRIBUTION_NAME == "common-lore-index"
    assert project["version"] == common_lore_index.__version__
    assert [(row["name"], row["version"]) for row in editable] == [(project["name"], project["version"])]
    assert PROJECT_IDENTITY.name == "Common Lore Index"
    assert PROJECT_IDENTITY.cli == "cloe"
    assert PROJECT_IDENTITY.license == "AGPL-3.0-or-later"
