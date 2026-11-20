"""Offline v0.0.4-to-candidate Local upgrade and pre-upgrade recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from common_lore_index.server._mcp_dispatch import MCP_PROTOCOL_VERSION
from installed_experience_pins import CODE_KIND, GRAPH_ID, RELEASED_CLOE_COMMIT
from repomap_test_support.installed_candidate import (
    ROOT, BuiltWheel, candidate_wheel, installed_graph_parser_digest, released_cloe_wheel, wheel_inventory,
)
from repomap_test_support.installed_environment import InstalledEnvironment, run_child
from repomap_test_support.installed_experience_driver import json_object, pure_environment
from repomap_test_support.installed_experience_fixture import (
    current_config_name, expectations, tiny_home, write_tiny_material,
)
from repomap_test_support.installed_mcp_session import useful_question
from repomap_test_support.installed_origin import closure, observe_origin

def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _installed_digest(env: InstalledEnvironment, wheel: BuiltWheel) -> str:
    normal = observe_origin(env, ROOT, isolated=False)
    isolated = observe_origin(env, ROOT, isolated=True)
    assert closure("pure-local", normal, isolated)
    assert wheel_inventory(wheel.wheel).record_valid
    return installed_graph_parser_digest(wheel, env.purelib)


def _cli(env: InstalledEnvironment, home: Path, command: str, *args: str) -> dict[str, Any]:
    result = run_child(env, (str(env.wrapper("cloe")), "ops", command, "--repo-map-home",
                             str(home), "--graph", GRAPH_ID, *args, "--json"))
    assert result.exit == 0, (command, result.exit)
    parsed = json_object(result.stdout)
    assert parsed is not None, command
    return parsed


def _answer(
    env: InstalledEnvironment, home: Path, code_query: str | None = None,
) -> tuple[str, str, int, str]:
    expected = expectations()
    answer = useful_question(env, (str(env.wrapper("cloe")), "mcp", "serve", "--repo-map-home", str(home)),
                             graph_id=GRAPH_ID, code_query=code_query or expected.code_query,
                             code_kind=CODE_KIND, docs_query=expected.docs_query, protocol=MCP_PROTOCOL_VERSION)
    assert answer.complete and answer.protocol_version == MCP_PROTOCOL_VERSION
    facts = {key: item[0] for key, item in answer.facts.items()}
    assert facts["code_kind"] == CODE_KIND and facts["code_path"] == expected.code_path
    assert facts["docs_path"] == expected.docs_path
    assert isinstance(facts["code_line"], int)
    return facts["code_kind"], facts["code_path"], facts["code_line"], facts["docs_path"]


def _files_digest(paths: tuple[Path, ...]) -> tuple[str, ...]:
    return tuple(_digest(path.read_bytes()) for path in paths)


def test_released_local_home_upgrade_and_backup_recovery(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path,
) -> None:
    released = released_cloe_wheel(tmp_path_factory)
    candidate = candidate_wheel(tmp_path_factory)
    previous = pure_environment(tmp_path / "released environment", released)
    current = pure_environment(tmp_path / "candidate environment", candidate)
    assert _installed_digest(previous, released) != _installed_digest(current, candidate)
    assert released.input_sha256 != candidate.input_sha256

    material = write_tiny_material(tmp_path / "public material")
    primary = tiny_home(tmp_path / "existing local home", material, config_name=current_config_name())
    recovery = tiny_home(tmp_path / "candidate recovery home", material, config_name=current_config_name())
    rollback = tiny_home(tmp_path / "released recovery home", material, config_name=current_config_name())
    backup = tmp_path / "before upgrade backup"
    _cli(previous, primary, "sqlite-init")
    first = _cli(previous, primary, "refresh-graph")["accepted_generation"]
    assert first == 1
    original = _answer(previous, primary)
    assert original[2] == expectations().code_line
    assert _cli(previous, primary, "sqlite-backup", "--output", str(backup))["result"] == "created"
    assert {path.name for path in backup.iterdir()} == {"graph.sqlite3", "manifest.json"}
    bundle = backup / "graph.sqlite3", backup / "manifest.json"
    pre_upgrade = _files_digest(bundle)
    database = primary / "state/sqlite-local/graphs" / f"{GRAPH_ID}.sqlite3"
    watched = primary / current_config_name(), database
    before_readback = _files_digest(watched)

    hidden = material.with_name("material offline")
    material.rename(hidden)
    assert _answer(current, primary) == original
    assert _files_digest(watched) == before_readback
    hidden.rename(material)

    code_file = material / "code" / expectations().code_path
    with code_file.open("a", encoding="utf-8") as stream:
        stream.write("\n\ndef released_upgrade_probe():\n    return 7\n")
    next_generation = _cli(current, primary, "refresh-graph")["accepted_generation"]
    assert next_generation == first + 1
    assert _answer(current, primary, "released_upgrade_probe")[2] > original[2]
    assert _files_digest(bundle) == pre_upgrade

    material.rename(hidden)
    assert _cli(current, recovery, "sqlite-restore", "--backup", str(backup))["result"] == "restored"
    assert _answer(current, recovery) == original
    assert _cli(previous, rollback, "sqlite-restore", "--backup", str(backup))["result"] == "restored"
    assert _answer(previous, rollback) == original
    assert _files_digest(bundle) == pre_upgrade
    print(json.dumps({"event": "released-local-upgrade-proof", "baseline_commit": RELEASED_CLOE_COMMIT,
                      "baseline_generation": first, "candidate_generation": next_generation,
                      "candidate_source_blind_readback": True, "candidate_restored_old_backup": True,
                      "released_restored_old_backup": True}, sort_keys=True))
