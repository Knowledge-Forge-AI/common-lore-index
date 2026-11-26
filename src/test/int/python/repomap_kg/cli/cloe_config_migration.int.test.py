"""ADR 0114 real CLI/API configuration migration apply, rollback and finalize in the disposable sandbox."""
from __future__ import annotations

import builtins
from collections.abc import Callable
import io
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

import pytest

from common_lore_index.ops.config_migration import (
    apply_cloe_config_migration, finalize_cloe_config_migration, rollback_cloe_config_migration,
)
from common_lore_index.ops.config_migration_fs import Home, MAX_CONFIG_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES
from common_lore_index_test_support.cli_integration import OPS_CONFIG_TEMPLATE, run_cli_module
from common_lore_index_test_support.cli_in_process import REPO_ROOT, module_process_environment
from runner_coverage_observer import launch_observed_process
from common_lore_index_test_support.sqlite_local_fixtures import graph_toml, sqlite_home_toml
from common_lore_index_test_support.sqlite_local_harness import LocalHarness

SECRET = "migration-int-secret-sentinel"
OVERLAY = 'schema_version = 1\n[service]\nlog_level = "debug"\n'
OPERATION = ".cloe-config-migration"


def test_cli_import_defers_fcntl_and_migration_refuses_before_home_writes(tmp_path: Path) -> None:
    program = """
import importlib.abc, json, sys
original_fcntl = sys.modules.pop('fcntl', None)
attempts = []
class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == 'fcntl':
            frame = sys._getframe(1)
            while frame and frame.f_globals.get('__name__', '').startswith(('importlib', '_frozen')):
                frame = frame.f_back
            attempts.append(None if frame is None else frame.f_globals.get('__name__'))
            raise ImportError('fixture blocks POSIX capability')
blocker = Blocker()
sys.meta_path.insert(0, blocker)
try:
    from common_lore_index.cli import main
    from common_lore_index.ops.config_migration import apply_cloe_config_migration
    before = list(attempts)
    result = apply_cloe_config_migration(sys.argv[1], confirmed=True)
finally:
    sys.meta_path.remove(blocker)
    if original_fcntl is not None:
        sys.modules['fcntl'] = original_fcntl
print(json.dumps({'before': before, 'after': attempts, 'result': result}))
"""
    home = tmp_path / "not-created"
    done = launch_observed_process(
        [sys.executable, "-c", program, str(home)], family="cli_module", pid_namespace_relation="shared",
        env=module_process_environment(), cwd=REPO_ROOT, timeout=30,
    )
    assert done.returncode == 0, done.stderr
    value = json.loads(done.stdout)
    owner = "common_lore_index.ops.config_migration_fs"
    assert owner not in value["before"] and owner in value["after"]
    assert value["result"]["refusals"] == [{"code": "config-migration-platform-unsupported", "file": ""}]
    assert value["result"]["writes_performed"] == 0 and not home.exists()


@pytest.mark.parametrize("backend", ["postgresql", "sqlite"])
def test_real_cli_distinct_replacement_refuses_all_recovery_commands(tmp_path: Path, backend: str) -> None:
    home = make_home(tmp_path, backend)
    assert cli(home, "apply", "--yes")["result"] == "applied"
    target, replacement = home / "00-project.cloe.toml", home / "replacement.tmp"
    original = target.stat()
    replacement.write_bytes(target.read_bytes())
    replacement.chmod(stat.S_IMODE(original.st_mode))
    assert replacement.stat().st_ino != original.st_ino
    replacement.replace(target)
    assert target.stat().st_ino != original.st_ino
    before = tree(home)
    for command, flags in (("rollback", ("--dry-run",)), ("rollback", ("--yes",)), ("finalize", ("--yes",))):
        result = cli(home, command, *flags, code=1)
        assert result["refusals"] == [{"code": "config-migration-drift", "file": "00-project.rp.toml"}]
        assert result["writes_performed"] == 0 and tree(home) == before


@pytest.mark.parametrize("backend", ["postgresql", "sqlite"])
@pytest.mark.parametrize("backup_change", ["missing", "corrupt"])
@pytest.mark.parametrize("confirmed", [False, True])
def test_cli_preserves_restore_scratch_when_required_backup_has_drifted(
    tmp_path: Path, backend: str, backup_change: str, confirmed: bool,
) -> None:
    home = make_home(tmp_path, backend)
    assert cli(home, "apply", "--yes")["result"] == "applied"
    operation = home / OPERATION
    backup = operation / "backup-0000.bin"
    (operation / "restore-0000.tmp").write_bytes(backup.read_bytes())
    (home / "00-project.cloe.toml").unlink()
    if backup_change == "missing":
        backup.unlink()
    else:
        backup.write_bytes(b"changed-backup")
    before = tree(home)
    flags = ("--yes",) if confirmed else ("--dry-run",)
    result = cli(home, "rollback", *flags, code=1)
    assert result["result"] == "refused"
    assert result["refusals"] == [{"code": "config-migration-drift", "file": "00-project.rp.toml"}]
    assert tree(home) == before
    assert result["writes_performed"] == 0


def make_home(tmp_path: Path, backend: str, source: Path | None = None) -> Path:
    home = tmp_path / "home with spaces"
    home.mkdir()
    base = OPS_CONFIG_TEMPLATE.replace('password_env = "REPOMAP_PG_PASSWORD"', f'password = "{SECRET}"')
    if backend == "sqlite":
        base = sqlite_home_toml(graph_toml("one", source or "/not-read/source"))
    (home / "00-project.rp.toml").write_text(base)
    (home / "90-machine.rpl.toml").write_text(OVERLAY)
    for path in home.iterdir():
        path.chmod(0o644)
    home.chmod(0o700)
    return home


def tree(root: Path) -> dict[str, tuple[int, bytes | None]]:
    found: dict[str, tuple[int, bytes | None]] = {".": (root.lstat().st_mode, None)}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        found[str(path.relative_to(root))] = (info.st_mode, path.read_bytes() if stat.S_ISREG(info.st_mode) else None)
    return found


def cli(home: Path, command: str, *flags: str, code: int = 0) -> dict[str, Any]:
    result = run_cli_module("ops", f"config-migration-{command}", "--repo-map-home", str(home), *flags, "--json")
    assert result.returncode == code, result.stderr
    assert SECRET not in result.stdout + result.stderr and str(home) not in result.stdout + result.stderr
    payload: dict[str, Any] = json.loads(result.stdout)
    assert payload["schema"] == "cloe-config-migration-result-v1" and payload["operation"] == command
    return payload


def graphs(home: Path) -> list[str]:
    checked = run_cli_module("ops", "config-check", "--repo-map-home", str(home), "--json")
    assert checked.returncode == 0, checked.stderr
    payload = json.loads(checked.stdout)
    assert payload["valid"] is True and SECRET not in checked.stdout
    return sorted(graph["id"] for graph in payload["graphs"])


@pytest.mark.parametrize("backend", ["postgresql", "sqlite"])
def test_real_cli_apply_load_rollback_reapply_finalize_lifecycle(tmp_path: Path, backend: str) -> None:
    home = make_home(tmp_path, backend)
    original = tree(home)
    configured = ["one"] if backend == "sqlite" else ["repo-map"]
    assert cli(home, "apply")["result"] == "would-apply" and tree(home) == original
    applied = cli(home, "apply", "--yes")
    assert applied["result"] == "applied" and applied["rollback_available"] and applied["finalize_pending"]
    assert sorted(p.name for p in home.iterdir()) == [OPERATION, "00-project.cloe.toml", "90-machine.local.cloe.toml"]
    assert graphs(home) == configured
    assert cli(home, "apply", "--yes")["result"] == "already-applied"
    assert cli(home, "rollback", "--yes")["result"] == "rolled-back" and tree(home) == original
    check = run_cli_module("ops", "config-check", "--repo-map-home", str(home), "--json")
    assert check.returncode == 1 and "config-migration-plan" in check.stderr
    assert cli(home, "apply", "--yes")["result"] == "applied"
    assert cli(home, "finalize", "--dry-run")["result"] == "would-finalize"
    finalized = cli(home, "finalize", "--yes")
    assert finalized["result"] == "finalized" and not finalized["rollback_available"]
    assert not (home / OPERATION).exists() and graphs(home) == configured
    expected_modes = dict.fromkeys(("00-project.cloe.toml", "90-machine.local.cloe.toml"), 0o644)
    assert {p.name: p.stat().st_mode & 0o777 for p in home.iterdir()} == expected_modes
    assert cli(home, "finalize", "--yes")["result"] == "nothing-to-finalize"
    assert cli(home, "rollback", "--yes")["result"] == "nothing-to-recover"
    assert cli(home, "apply", "--yes")["result"] == "already-converted"
    assert not (home / "state").exists()


def test_guarded_driver_free_cli_apply_and_rollback(tmp_path: Path) -> None:
    home = make_home(tmp_path, "postgresql")
    original = tree(home)
    harness = LocalHarness(tmp_path / "guarded", block_psycopg=True)
    for command in ("apply", "rollback"):
        result = harness.run(
            "ops", f"config-migration-{command}", "--repo-map-home", str(home), "--yes", "--json",
            extra_env={"CLOE_HOME": str(tmp_path / "wrong"), "REPOMAP_HOME": str(tmp_path / "wrong")},
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["refusals"] == []
    assert tree(home) == original and not (tmp_path / "wrong").exists()
    assert not harness.forbidden_events() and not harness.shim_invocations()
    assert "psycopg" not in harness.imported_modules()


def test_in_process_api_never_touches_source_roots_or_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "private.txt").write_text("source-read-tripwire")
    home = make_home(tmp_path, "sqlite", source)
    original = tree(home)

    def guarded(operation: Callable[..., Any]) -> Callable[..., Any]:
        def invoke(path: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(path, (str, bytes, os.PathLike)) and Path(os.fsdecode(path)).absolute().is_relative_to(source):
                raise AssertionError("source filesystem was accessed")
            return operation(path, *args, **kwargs)
        return invoke

    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.setitem(sys.modules, "sqlite3", None)
    for owner, name in (
        (Path, "open"), (Path, "iterdir"), (Path, "stat"), (Path, "lstat"), (builtins, "open"), (io, "open"),
        (os, "open"), (os, "scandir"), (os, "listdir"), (os, "stat"), (os, "lstat"), (os, "access"),
    ):
        monkeypatch.setattr(owner, name, guarded(getattr(owner, name)))
    refused = apply_cloe_config_migration(home)
    assert refused["result"] == "refused" and refused["refusals"][0]["code"] == "confirmation-required"
    assert apply_cloe_config_migration(home, confirmed=True)["result"] == "applied"
    assert rollback_cloe_config_migration(home, dry_run=True)["result"] == "would-roll-back"
    assert rollback_cloe_config_migration(home, confirmed=True)["result"] == "rolled-back"
    assert apply_cloe_config_migration(home, confirmed=True)["result"] == "applied"
    assert finalize_cloe_config_migration(home, confirmed=True)["result"] == "finalized"
    monkeypatch.undo()
    assert sorted(tree(home)) == [".", "00-project.cloe.toml", "90-machine.local.cloe.toml"]
    assert not (home / "state").exists() and original


def test_real_owned_dual_link_and_missing_target_recover_through_cli(tmp_path: Path) -> None:
    home = make_home(tmp_path, "postgresql")
    original = tree(home)
    cli(home, "apply", "--yes")
    os.link(home / "00-project.cloe.toml", home / "00-project.rp.toml")
    (home / "90-machine.local.cloe.toml").unlink()
    assert cli(home, "apply", "--yes", code=1)["refusals"][0]["code"] == "config-migration-recovery-required"
    preview = cli(home, "rollback")
    assert preview["result"] == "would-roll-back" and preview["dry_run"]
    assert cli(home, "rollback", "--yes")["result"] == "rolled-back"
    assert tree(home) == original


def test_edited_after_apply_refuses_with_json_exit_and_retained_bytes(tmp_path: Path) -> None:
    home = make_home(tmp_path, "postgresql")
    cli(home, "apply", "--yes")
    (home / "90-machine.local.cloe.toml").write_text(OVERLAY.replace("debug", "info"))
    edited = tree(home)
    for command in ("rollback", "finalize"):
        refused = cli(home, command, "--yes", code=1)
        assert refused["result"] == "refused" and refused["journal_state"] == "applied"
        assert refused["refusals"] == [{"code": "config-migration-drift", "file": "90-machine.rpl.toml"}]
    text = run_cli_module("ops", "config-migration-rollback", "--repo-map-home", str(home), "--yes")
    assert text.returncode == 1 and "configuration migration rollback refused: config-migration-drift" in text.stderr
    assert "'90-machine.rpl.toml'" in text.stdout and str(home) not in text.stdout + text.stderr
    assert tree(home) == edited


def test_cli_usage_and_unsafe_home_contracts(tmp_path: Path) -> None:
    home = make_home(tmp_path, "postgresql")
    assert run_cli_module("ops", "config-migration-apply", "--yes").returncode == 2
    assert run_cli_module("ops", "config-migration-apply", "--repo-map-home", str(home), "--yes", "--dry-run").returncode == 2
    home.chmod(0o755)
    before = tree(home)
    refused = cli(home, "apply", "--yes", code=1)
    assert refused["refusals"] == [{"code": "repo-map-home-unsafe", "file": ""}] and refused["writes_performed"] == 0
    assert tree(home) == before


@pytest.mark.parametrize(("case", "expected_file", "refusal_code"), [
    ("operation_alias", ".CLOE-CONFIG-MIGRATION", "config-migration-alias-collision"),
    ("mixed_families", "", "config-home-mixed-format"),
    ("external_hardlink", "50-external.rpl.toml", "config-entry-unsafe"),
    ("too_many_files", "", "config-migration-limit-exceeded"),
    ("large_file", "50-large.rpl.toml", "config-migration-limit-exceeded"),
    ("large_total", "57-budget.rpl.toml", "config-migration-limit-exceeded"),
])
def test_apply_refuses_invalid_layouts_without_writes(
    tmp_path: Path, case: str, expected_file: str, refusal_code: str,
) -> None:
    home = make_home(tmp_path, "sqlite")
    external = tmp_path / "owned external configuration"
    external.write_text(SECRET, encoding="utf-8")
    if case == "operation_alias":
        (home / expected_file).write_text(SECRET, encoding="utf-8")
    elif case == "mixed_families":
        (home / "50-new.cloe.toml").write_text(OVERLAY, encoding="utf-8")
    elif case == "external_hardlink":
        os.link(external, home / expected_file)
    elif case == "too_many_files":
        for index in range(MAX_CONFIG_FILES - 1):
            (home / f"50-extra-{index:02d}.rpl.toml").write_text(OVERLAY, encoding="utf-8")
    elif case == "large_file":
        with (home / expected_file).open("xb") as stream:
            stream.truncate(MAX_FILE_BYTES + 1)
    else:
        for index in range(MAX_TOTAL_BYTES // MAX_FILE_BYTES):
            with (home / f"{50 + index}-budget.rpl.toml").open("xb") as stream:
                stream.truncate(MAX_FILE_BYTES)

    before_home = tree(home)
    before_external = external.stat()
    for flags in (("--dry-run",), ("--yes",)):
        outcome = cli(home, "apply", *flags, code=1)
        assert outcome["result"] == "refused"
        assert outcome["refusals"] == [{"code": refusal_code, "file": expected_file}]
        assert outcome["writes_performed"] == 0
        assert tree(home) == before_home and not (home / OPERATION).exists()
        assert external.read_text(encoding="utf-8") == SECRET
        after_external = external.stat()
        assert (after_external.st_ino, after_external.st_nlink, after_external.st_mtime_ns) == (
            before_external.st_ino, before_external.st_nlink, before_external.st_mtime_ns,
        )


def test_migration_duplicate_journal_fields_refuse_before_recovery(tmp_path: Path) -> None:
    home = make_home(tmp_path, "postgresql")
    assert cli(home, "apply", "--yes")["result"] == "applied"
    journal = home / OPERATION / "journal.json"
    data = journal.read_bytes()
    assert data.startswith(b"{")
    journal.write_bytes(b'{"state":"preparing",' + data[1:])
    before = tree(home)
    for command in ("rollback", "finalize"):
        refusal = cli(home, command, "--yes", code=1)
        assert refusal["refusals"] == [{"code": "config-migration-journal-invalid", "file": ""}]
        assert refusal["writes_performed"] == 0
        assert tree(home) == before


@pytest.mark.parametrize("drift", ("inode", "change-time"))
def test_recovery_refuses_same_bytes_after_admitted_target_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    home = make_home(tmp_path, "postgresql")
    assert apply_cloe_config_migration(home, confirmed=True)["result"] == "applied"
    target = home / "00-project.cloe.toml"
    replacement = tmp_path / "replacement.toml"
    admitted = target.stat()
    identical = target.read_bytes()
    replacement.write_bytes(identical)
    replacement.chmod(stat.S_IMODE(admitted.st_mode))
    actual_read = Home.read
    journal = home / OPERATION / "journal.json"
    journal_before = journal.read_bytes()

    def swap(bound: Home, name: str, limit: int, code: str, *, in_op: bool = False) -> tuple[bytes, os.stat_result]:
        targeted = name == target.name and not in_op
        if targeted and drift == "inode" and replacement.exists():
            replacement.replace(target)
        data, info = actual_read(bound, name, limit, code, in_op=in_op)
        if targeted and drift == "change-time":
            info = os.stat_result(info, {
                "st_mtime_ns": info.st_mtime_ns, "st_ctime_ns": info.st_ctime_ns + 1,
            })
        return data, info

    monkeypatch.setattr(Home, "read", swap)
    outcome = finalize_cloe_config_migration(home, confirmed=True)
    assert outcome["refusals"] == [{"code": "config-migration-drift", "file": "00-project.rp.toml"}]
    assert outcome["writes_performed"] == 0
    assert (target.stat().st_ino != admitted.st_ino) is (drift == "inode")
    assert target.read_bytes() == identical
    assert journal.read_bytes() == journal_before


def test_recovery_streams_namespace_proofs_and_reports_first_foreign_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = make_home(tmp_path, "postgresql")
    assert apply_cloe_config_migration(home, confirmed=True)["result"] == "applied"
    for index in range(128):
        (home / f"unrelated-{index:03d}.txt").touch()

    def refuse_list(_bound: Home, *, in_op: bool = False) -> list[str]:
        raise AssertionError(f"recovery materialized names (operation directory={in_op})")

    monkeypatch.setattr(Home, "names", refuse_list)
    unsafe = home / OPERATION / "foreign.tmp"
    unsafe.touch()
    result = rollback_cloe_config_migration(home, confirmed=True)
    assert result["refusals"] == [{"code": "config-migration-residue-unsafe", "file": ""}]
    assert result["writes_performed"] == 0 and unsafe.exists()
    unsafe.unlink()
    for name in ("20-zeta.cloe.toml", "10-alpha.cloe.toml"):
        (home / name).write_text(OVERLAY)
    result = finalize_cloe_config_migration(home, confirmed=True)
    assert result["refusals"] == [{"code": "config-migration-drift", "file": "10-alpha.cloe.toml"}]
    assert result["writes_performed"] == 0
    for name in ("20-zeta.cloe.toml", "10-alpha.cloe.toml"):
        (home / name).unlink()
    done = rollback_cloe_config_migration(home, confirmed=True)
    assert done["result"] == "rolled-back" and not (home / OPERATION).exists()
    assert len(tuple(home.glob("unrelated-*.txt"))) == 128


def test_cli_apply_and_rollback_preserve_unrelated_home_entries(tmp_path: Path) -> None:
    home = make_home(tmp_path, "sqlite")
    (home / "readme.txt").write_text("fixture")
    before = tree(home)
    assert cli(home, "apply", "--yes")["result"] == "applied"
    assert cli(home, "rollback", "--yes")["result"] == "rolled-back"
    assert tree(home) == before
