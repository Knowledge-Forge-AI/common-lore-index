"""Offline v0.0.4-to-candidate Local upgrade and pre-upgrade recovery."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Literal

import pytest

from common_lore_index.server._mcp_dispatch import MCP_PROTOCOL_VERSION
from installed_experience_pins import (
    CODE_KIND, GRAPH_ID, PREVIEW005_BASE_CONFIG, PREVIEW005_GRAPH_PARSER_SHA256,
    PREVIEW005_PACKAGE_INPUTS, RELEASED_CLOE_COMMIT,
)
from common_lore_index_test_support.installed_candidate import (
    ROOT, BuiltWheel, candidate_wheel, installed_graph_parser_digest, preview005_predecessor_wheel,
    released_cloe_wheel, wheel_inventory,
)
from common_lore_index_test_support.installed_environment import InstalledEnvironment, run_child
from common_lore_index_test_support.installed_experience_driver import json_object, pure_environment
from common_lore_index_test_support.installed_experience_fixture import (
    current_config_name, expectations, legacy_tiny_home, tiny_home, write_tiny_material,
)
from common_lore_index_test_support.installed_mcp_session import useful_question
from common_lore_index_test_support.installed_origin import closure, observe_origin

def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _installed_digest(env: InstalledEnvironment, wheel: BuiltWheel, *, legacy_facade: bool = False) -> str:
    normal = observe_origin(env, ROOT, isolated=False)
    isolated = observe_origin(env, ROOT, isolated=True)
    assert closure("pure-local", normal, isolated, legacy_facade=legacy_facade)
    return installed_graph_parser_digest(wheel, env.purelib)


def _cli(env: InstalledEnvironment, home: Path, command: str, *args: str) -> dict[str, Any]:
    result = run_child(env, (str(env.wrapper("cloe")), "ops", command, "--repo-map-home",
                             str(home), "--graph", GRAPH_ID, *args, "--json"))
    assert result.exit == 0, (command, result.exit)
    parsed = json_object(result.stdout)
    assert parsed is not None, command
    return parsed


def _answer(
    env: InstalledEnvironment, home: Path, code_query: str | None = None, *,
    tool_prefix: Literal["cloe", "repomap"] = "cloe",
    extra: dict[str, str] | None = None,
) -> tuple[str, str, int, str]:
    expected = expectations()
    answer = useful_question(env, (str(env.wrapper("cloe")), "mcp", "serve", "--repo-map-home", str(home)),
                             graph_id=GRAPH_ID, code_query=code_query or expected.code_query,
                             code_kind=CODE_KIND, docs_query=expected.docs_query, protocol=MCP_PROTOCOL_VERSION,
                             tool_prefix=tool_prefix, extra=extra)
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
    assert wheel_inventory(candidate.wheel).record_valid
    assert _installed_digest(previous, released, legacy_facade=True) != _installed_digest(current, candidate)
    assert released.input_sha256 != candidate.input_sha256

    material = write_tiny_material(tmp_path / "public material")
    primary = tiny_home(tmp_path / "existing local home", material, config_name=current_config_name())
    recovery = tiny_home(tmp_path / "candidate recovery home", material, config_name=current_config_name())
    rollback = tiny_home(tmp_path / "released recovery home", material, config_name=current_config_name())
    backup = tmp_path / "before upgrade backup"
    _cli(previous, primary, "sqlite-init")
    first = _cli(previous, primary, "refresh-graph")["accepted_generation"]
    assert first == 1
    original = _answer(previous, primary, tool_prefix="repomap")
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
    assert _answer(previous, rollback, tool_prefix="repomap") == original
    assert _files_digest(bundle) == pre_upgrade
    print(json.dumps({"event": "released-local-upgrade-proof", "baseline_commit": RELEASED_CLOE_COMMIT,
                      "baseline_generation": first, "candidate_generation": next_generation,
                      "candidate_source_blind_readback": True, "candidate_restored_old_backup": True,
                      "released_restored_old_backup": True}, sort_keys=True))


def test_preview005_predecessor_preserves_source_blind_local_recovery(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path,
) -> None:
    preview = preview005_predecessor_wheel(tmp_path_factory)
    assert (preview.input_sha256, preview.input_count) == PREVIEW005_PACKAGE_INPUTS
    assert wheel_inventory(preview.wheel).record_valid
    installed = pure_environment(tmp_path / "preview005 environment", preview)
    assert _installed_digest(installed, preview, legacy_facade=True) == PREVIEW005_GRAPH_PARSER_SHA256

    material = write_tiny_material(tmp_path / "public material")
    home = tiny_home(tmp_path / "preview005 home", material, config_name=PREVIEW005_BASE_CONFIG)
    recovered = tiny_home(tmp_path / "preview005 recovery", material, config_name=PREVIEW005_BASE_CONFIG)
    backup = tmp_path / "preview005 backup"
    _cli(installed, home, "sqlite-init")
    assert _cli(installed, home, "refresh-graph")["accepted_generation"] == 1
    before = _answer(installed, home, tool_prefix="repomap")
    assert _cli(installed, home, "sqlite-backup", "--output", str(backup))["result"] == "created"
    frozen = _files_digest((backup / "graph.sqlite3", backup / "manifest.json"))
    material.rename(material.with_name("hidden public material"))
    assert _cli(installed, recovered, "sqlite-restore", "--backup", str(backup))["result"] == "restored"
    assert _answer(installed, recovered, tool_prefix="repomap") == before
    assert _files_digest((backup / "graph.sqlite3", backup / "manifest.json")) == frozen

def _migration(env: InstalledEnvironment, home: Path, action: str) -> dict[str, Any]:
    result = run_child(env, (str(env.wrapper("cloe")), "ops", f"config-migration-{action}",
                             "--repo-map-home", str(home), "--yes", "--json"))
    assert result.exit == 0 and result.stderr == "", (action, result.exit)
    assert str(home) not in result.stdout
    payload = json_object(result.stdout)
    assert payload is not None
    return payload


def _owned_home_snapshot(home: Path) -> dict[str, tuple[int, str | None]]:
    snapshot: dict[str, tuple[int, str | None]] = {}
    for path in (home, *sorted(home.rglob("*"))):
        info = path.lstat()
        assert info.st_uid == os.getuid()
        assert stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        snapshot[path.relative_to(home).as_posix()] = (
            stat.S_IMODE(info.st_mode), _digest(path.read_bytes()) if stat.S_ISREG(info.st_mode) else None,
        )
    return snapshot


def test_preview005_local_conversion_and_coherent_rollback(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path,
) -> None:
    preview = preview005_predecessor_wheel(tmp_path_factory)
    candidate = candidate_wheel(tmp_path_factory)
    child_cwd = tmp_path / "shared work directory"
    child_cwd.mkdir(mode=0o700)
    previous = replace(pure_environment(tmp_path / "preview predecessor", preview), cwd=child_cwd)
    current = replace(pure_environment(tmp_path / "canonical candidate", candidate), cwd=child_cwd)
    assert (preview.input_sha256, preview.input_count) == PREVIEW005_PACKAGE_INPUTS
    assert _installed_digest(previous, preview, legacy_facade=True) == PREVIEW005_GRAPH_PARSER_SHA256
    version = run_child(previous, (str(previous.wrapper("cloe")), "--version"))
    assert (version.exit, version.stdout, version.stderr) == (0, "cloe 0.0.4\n", "")
    assert preview.input_sha256 != candidate.input_sha256
    assert wheel_inventory(candidate.wheel).record_valid
    parser_path = "src/main/python/common_lore_index/cli/_parser_ops_graph.py"
    assert _installed_digest(current, candidate) == _digest((ROOT / parser_path).read_bytes())

    relative_source = Path("public source")
    material = write_tiny_material(child_cwd / relative_source)
    assert material == child_cwd / relative_source and material.is_dir()
    hidden = material.with_name("public source offline")
    home = legacy_tiny_home(tmp_path / "existing local graph", relative_source)
    recovery = tmp_path / "canonical recovery graph"
    recovery.mkdir(mode=0o700)
    old_recovery = legacy_tiny_home(tmp_path / "predecessor recovery graph", relative_source)
    decoy = tiny_home(tmp_path / "unselected graph home", relative_source, config_name=current_config_name())
    decoy_config = decoy / current_config_name()
    original_decoy = decoy_config.read_text()
    assert GRAPH_ID in original_decoy
    decoy_config.write_text(original_decoy.replace(GRAPH_ID, "decoy-graph"))
    decoy_snapshot = _owned_home_snapshot(decoy)
    choose_explicit = {"CLOE_HOME": str(decoy)}

    backup = tmp_path / "preconversion backup"
    _cli(previous, home, "sqlite-init")
    assert _cli(previous, home, "refresh-graph")["accepted_generation"] == 1
    expected = _answer(previous, home, tool_prefix="repomap")
    assert _cli(previous, home, "sqlite-backup", "--output", str(backup))["result"] == "created"
    retained = _owned_home_snapshot(backup)
    assert set(retained) == {".", "graph.sqlite3", "manifest.json"}
    before = _owned_home_snapshot(home)
    assert before["."][0] == 0o700
    old_config = {name: row for name, row in before.items() if name.endswith(".toml")}
    assert len(old_config) == 2 and all(mode == 0o600 for mode, _ in old_config.values())

    material.rename(hidden)
    converted = _migration(current, home, "apply")
    assert converted["result"] == "applied" and converted["rollback_available"] is True
    after = _owned_home_snapshot(home)
    assert after[".cloe-config-migration"][0] == 0o700
    assert all(mode in (0o600, 0o700) for name, (mode, _) in after.items()
               if name.startswith(".cloe-config-migration/"))
    new_config = {name: row for name, row in after.items() if "/" not in name and name.endswith(".toml")}
    assert set(new_config) == {current_config_name(), "zz-local.local.cloe.toml"}
    assert sorted(new_config.values()) == sorted(old_config.values())
    assert _answer(current, home, extra=choose_explicit) == expected
    assert _owned_home_snapshot(home) == after
    assert {k: v for k, v in before.items() if not k.endswith(".toml")} == {
        k: v for k, v in after.items() if not (k.endswith(".toml") or k.startswith(".cloe-config-migration"))
    }

    for name, (mode, _) in new_config.items():
        copied = recovery / name
        copied.write_bytes((home / name).read_bytes())
        copied.chmod(mode)
    assert _owned_home_snapshot(recovery) == {".": (0o700, None), **new_config}
    assert _cli(current, recovery, "sqlite-restore", "--backup", str(backup))["result"] == "restored"
    assert _answer(current, recovery, extra=choose_explicit) == expected
    hidden.rename(material)
    assert _cli(current, recovery, "refresh-graph")["accepted_generation"] == 2
    assert _answer(current, recovery, extra=choose_explicit) == expected
    material.rename(hidden)

    assert _migration(current, home, "rollback")["result"] == "rolled-back"
    assert _migration(current, home, "rollback")["result"] == "nothing-to-recover"
    assert _owned_home_snapshot(home) == before
    assert _answer(previous, home, tool_prefix="repomap") == expected
    assert _owned_home_snapshot(home) == before
    assert _cli(previous, old_recovery, "sqlite-restore", "--backup", str(backup))["result"] == "restored"
    assert _answer(previous, old_recovery, tool_prefix="repomap") == expected
    assert _owned_home_snapshot(backup) == retained
    assert _owned_home_snapshot(decoy) == decoy_snapshot
