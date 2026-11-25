"""ADR 0222 clean-process command and module launches, stdout integrity and read-only MCP stdio."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from common_lore_index import PREFERRED_CLI, __version__
from common_lore_index.runtime.project_identity import PROJECT_IDENTITY
from common_lore_index.server.mcp_schemas import tool_definitions
from repomap_test_support.cli_in_process import REPO_ROOT, module_process_environment
from repomap_test_support.sqlite_local_fixtures import graph_toml, write_sqlite_home
from repomap_test_support.sqlite_local_harness import initialize
from runner_coverage_observer import launch_observed_process

CANONICAL_MODULE = "python -m common_lore_index"


def console_script(entry: str) -> list[str]:
    """The installed wrapper's exact call shape, launched without an installer."""
    return [sys.executable, "-c", f"import sys\nfrom common_lore_index.entrypoints import {entry}\nsys.exit({entry}())"]


LAUNCHES: dict[str, tuple[list[str], str]] = {
    CANONICAL_MODULE: ([sys.executable, "-m", "common_lore_index"], PREFERRED_CLI),
    "cloe": (console_script("cloe_main"), "cloe"),
    "common-lore-index": (console_script("long_main"), "common-lore-index"),
}
MCP_REQUESTS = "".join(json.dumps(item) + "\n" for item in (
    initialize(1),
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "cloe_list_graphs", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "repomap_status", "arguments": {}}},
))
DRIVER_IMPORT_PROBE = """
import importlib.abc, json, sys
attempts = []
class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in ('psycopg', 'psycopg_binary', 'psycopg_c', 'psycopg_pool'):
            attempts.append(fullname)
            raise ImportError('fixture blocks optional driver')
        return None
sys.meta_path.insert(0, Blocker())
hooks = (list(sys.meta_path), list(sys.path_hooks))
import common_lore_index
import common_lore_index.entrypoints
import common_lore_index.ops.config_migration as migration
names = ('apply_cloe_config_migration', 'rollback_cloe_config_migration', 'finalize_cloe_config_migration')
watched = ('common_lore_index.cli', 'common_lore_index.cli.main', 'common_lore_index.server.mcp')
print(json.dumps({
    'attempts': attempts,
    'hooks_unchanged': hooks == (list(sys.meta_path), list(sys.path_hooks)),
    'legacy_loaded': any(name.split('.')[0] == 'repomap_kg' for name in sys.modules),
    'canonical_loaded': sorted(name for name in watched if name in sys.modules),
    'drivers_loaded': sorted(name for name in sys.modules if name.split('.')[0].startswith('psycopg')),
    'migration_routes': [callable(getattr(migration, name)) for name in names],
}, sort_keys=True))
"""


def disposable_environment(user_home: Path) -> dict[str, str]:
    """A public-safe child environment whose only home is a disposable directory."""
    user_home.mkdir(mode=0o700)
    env = module_process_environment()
    for name in ("CLOE_HOME", "REPOMAP_HOME", "CLOE_OPS_CONFIG", "CLOE_MCP_CONFIG",
                 "REPOMAP_OPS_CONFIG", "REPOMAP_MCP_CONFIG"):
        env.pop(name, None)
    env["HOME"] = str(user_home)
    return env


def launch(
    argv: list[str], env: dict[str, str], *args: str, input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return launch_observed_process(
        [*argv, *args], family="cli_module", cwd=REPO_ROOT, env=env, input_text=input_text,
        pid_namespace_relation="shared", timeout=120,
    )


def snapshot(root: Path) -> dict[str, bytes | None]:
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
            for path in sorted(root.rglob("*"))}


def mcp_responses(stdout: str) -> dict[int, dict[str, Any]]:
    responses = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    return {int(response["id"]): response for response in responses}


def assert_mcp_wire(stdout: str) -> dict[str, Any]:
    responses = mcp_responses(stdout)
    assert sorted(responses) == [1, 2, 3, 4]
    retired = responses[4]["result"]
    assert retired["isError"] is True
    assert "unknown RepoMap MCP tool" in retired["structuredContent"]["error"]
    initialized = responses[1]["result"]
    assert isinstance(initialized, dict) and initialized["serverInfo"] == {"name": "common-lore-index", "version": __version__}
    listed = responses[2]["result"]
    assert isinstance(listed, dict)
    assert [tool["name"] for tool in listed["tools"]] == [tool["name"] for tool in tool_definitions()]
    return responses[3]["result"]


@pytest.mark.parametrize("label", sorted(LAUNCHES))
def test_clean_process_help_is_explicitly_branded(tmp_path: Path, label: str) -> None:
    argv, prog = LAUNCHES[label]
    env = disposable_environment(tmp_path / "user home")
    result = launch(argv, env, "--help")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"usage: {prog} ") and "RepoMap" not in result.stdout
    assert result.stderr == ""
    assert "deprecated" not in result.stdout and snapshot(tmp_path / "user home") == {}


def test_clean_process_version_and_identity_stdout_are_identical_for_every_launch(tmp_path: Path) -> None:
    env = disposable_environment(tmp_path / "user home")
    observed: dict[str, dict[str, tuple[int, str, str]]] = {}
    for label, (argv, _prog) in LAUNCHES.items():
        observed[label] = {}
        for args in (("--version",), ("identity", "--json"), ("identity",)):
            result = launch(argv, env, *args)
            observed[label][" ".join(args)] = (result.returncode, result.stdout, result.stderr)
    canonical = observed[CANONICAL_MODULE]
    for label, (_argv, prog) in LAUNCHES.items():
        for command, (code, stdout, stderr) in observed[label].items():
            expected = (0, f"{prog} {__version__}\n") if command == "--version" else canonical[command][:2]
            assert (code, stdout) == expected, (label, command, stderr)
            assert stderr == "", (label, command)
    _code, version, _stderr = canonical["--version"]
    assert version.count("\n") == 1 and version.split()[-1] == __version__
    identity = json.loads(canonical["identity --json"][1])
    assert identity == PROJECT_IDENTITY.as_dict() and len(identity) == 6
    assert snapshot(tmp_path / "user home") == {}


def test_canonical_imports_defer_drivers_and_have_no_legacy_alias(tmp_path: Path) -> None:
    env = disposable_environment(tmp_path / "user home")
    result = launch([sys.executable, "-c", DRIVER_IMPORT_PROBE], env)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "attempts": [], "canonical_loaded": [], "drivers_loaded": [],
        "hooks_unchanged": True, "legacy_loaded": False,
        "migration_routes": [True, True, True],
    }


def test_canonical_mcp_module_can_start_without_a_graph_home(tmp_path: Path) -> None:
    home = tmp_path / "user home"
    env = disposable_environment(home)
    result = launch([sys.executable, "-m", "common_lore_index.server.mcp"], env, input_text=MCP_REQUESTS)
    assert result.returncode == 0 and result.stderr == "", result.stderr
    assert_mcp_wire(result.stdout)
    assert snapshot(home) == {}


def test_canonical_module_keeps_the_service_environment_boundary(tmp_path: Path) -> None:
    env = disposable_environment(tmp_path / "user home")
    code = """import json,os,runpy,sys
import common_lore_index.cli as cli
def record(argv=None, *, prog=None):
    print(json.dumps({'keys':sorted(os.environ), 'argv':argv, 'prog':prog}))
    return 0
cli.main=record
os.environ.clear()
os.environ.update(LANG='C.UTF-8', DATABASE_PASSWORD='xxxxxxxxxxxxxxxx')
sys.argv=['common_lore_index','ops','coordinator-serve','--repo-map-home','/placeholder/home','--service-package-environment','--json']
runpy.run_module('common_lore_index',run_name='__main__')
"""
    result = launch([sys.executable, "-c", code], env)
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout) == {"keys": ["LANG"], "argv": None, "prog": None}
    assert "xxxxxxxxxxxxxxxx" not in result.stdout + result.stderr


def test_mcp_module_and_console_commands_share_read_only_stdio(tmp_path: Path) -> None:
    env = disposable_environment(tmp_path / "user home")
    home = tmp_path / "explicit home"
    write_sqlite_home(home, graph_toml("one", "/not-read/source"))
    home.chmod(0o700)
    env["CLOE_HOME"] = str(home)
    before = snapshot(tmp_path)
    entrances = (
        ([sys.executable, "-m", "common_lore_index.server.mcp"], ()),
        (console_script("cloe_main"), ("mcp", "serve", "--repo-map-home", str(home))),
        (console_script("long_main"), ("mcp", "serve", "--repo-map-home", str(home))),
    )
    responses = []
    for argv, args in entrances:
        result = launch(argv, env, *args, input_text=MCP_REQUESTS)
        assert result.returncode == 0 and result.stderr == "", result.stderr
        graph_list = assert_mcp_wire(result.stdout)
        assert graph_list.get("isError", False) is False
        assert [graph["graph_id"] for graph in graph_list["structuredContent"]["graphs"]] == ["one"]
        assert str(home) not in result.stdout
        responses.append(result.stdout)
    assert len(set(responses)) == 1
    assert snapshot(tmp_path) == before


def test_system_consumer_accepts_real_canonical_mcp_across_recreated_processes(tmp_path: Path) -> None:
    from repomap_test_support.sqlite_local_harness import LocalHarness
    from tools.system.scenario import MonotonicTimer
    from tools.system.scenario_mcp import run_mcp_public_readback
    from unittest.mock import MagicMock

    source = tmp_path / "source"
    source.mkdir()
    (source / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    home = write_sqlite_home(tmp_path / "explicit home", graph_toml("fixture", source))
    harness = LocalHarness(tmp_path / "guarded processes", block_psycopg=True)
    initialized = harness.cli_json("ops", "sqlite-init", "--repo-map-home", str(home), "--graph", "fixture")
    assert initialized["result"] == "initialized"
    harness.cli_json("ops", "refresh-graph", "--repo-map-home", str(home), "--graph", "fixture")
    before = snapshot(home)
    sessions: list[dict[int, dict[str, Any]]] = []

    def real_stdio_transport(compose_dir: Path, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv == ["--profile", "integration", "run", "--rm", "-i", "mcp"]
        requests = [json.loads(line) for line in kwargs["stdin_input"].splitlines()]
        replies = harness.mcp(home, requests)
        assert replies[1]["result"]["serverInfo"] == {"name": "common-lore-index", "version": __version__}
        sessions.append(replies)
        output = "\n".join(json.dumps(value) for value in replies.values()) + "\n"
        return subprocess.CompletedProcess(argv, 0, output, "")

    result, digest = run_mcp_public_readback(
        tmp_path, MagicMock(),  # Only the transport is bridged; the product and graph are real.
        MonotonicTimer(total_budget_seconds=120, cleanup_reserve_seconds=10),
        load_plan_env=lambda _plan: {}, run_compose=real_stdio_transport,
    )
    assert result.status == "passed" and len(digest) == 64 and len(sessions) == 2
    projection = result.details["semantic_projection"]
    assert projection["fixture_search_add_keys"] == ["python.function:calc:add"]
    assert projection["fixture_status"]["canonical_nodes"] > 0
    assert projection["fixture_status"]["canonical_edges"] > 0
    after = snapshot(home)
    # SQLite query-only connections may create their WAL-index cache. Durable
    # graph/configuration bytes must remain exact, and the WAL must stay empty.
    sidecars = "state/sqlite-local/graphs/fixture.sqlite3"
    assert after.get(sidecars + "-wal") in (None, b"")
    for suffix in ("-wal", "-shm"):
        before.pop(sidecars + suffix, None)
        after.pop(sidecars + suffix, None)
    assert after == before, sorted(key for key in after.keys() | before.keys() if after.get(key) != before.get(key))
    assert harness.forbidden_events() == []
