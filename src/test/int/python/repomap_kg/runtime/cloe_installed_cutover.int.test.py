"""Offline incumbent/candidate ownership proof in disposable interpreter prefixes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv
import zipfile

import pytest

from runner_coverage_bootstrap import resolve_bootstrap_capability
from runner_coverage_execution import prepare_child_coverage_environment
from runner_coverage_observer import launch_observed_process

ROOT = Path(__file__).resolve().parents[6]
INCUMBENT = "6d818ad6d43d38d47c64b8c474c2b64b853f631a"
CATALOGS = ("rdbms", "coordinator-rdbms")
W3_APPEND_ONLY = {"rdbms/2026/10/31-001-cloe-lexical-create-derived-schema.sql"}


def run(args: tuple[str, ...], cwd: Path, *, pip: bool = False) -> subprocess.CompletedProcess[str]:
    family = "arch7f_pip" if pip else "arch7f_probe"
    cap = resolve_bootstrap_capability(env=os.environ)
    env = prepare_child_coverage_environment(os.environ, family=family, capability=cap,
        extra_env={"PYTHONPATH": "", "PYTHONNOUSERSITE": "1", "PIP_NO_INDEX": "1",
                   "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    return launch_observed_process(args, family=family, capability=cap, env=env, cwd=cwd)


def require(args: tuple[str, ...], cwd: Path, *, pip: bool = False) -> str:
    result = run(args, cwd, pip=pip)
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


def catalog_bytes(wheel: Path, prefix: str) -> dict[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        marker = ".data/data/share/" + prefix + "/"
        return {name.split(marker, 1)[1]: hashlib.sha256(archive.read(name)).hexdigest()
                for name in archive.namelist() if marker in name}


@pytest.fixture(scope="module")
def wheels(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    # Immutable predecessor objects and the exact candidate, shared per pytest process.
    from repomap_test_support.installed_candidate import candidate_wheel, incumbent_wheel

    old_build, new_build = incumbent_wheel(tmp_path_factory, ROOT), candidate_wheel(tmp_path_factory, ROOT)
    incumbent = old_build.source
    built = [old_build.wheel, new_build.wheel]
    old, new = built
    assert old.name.startswith("repomap_kg-0.0.3-")
    assert new.name.startswith("common_lore_index-0.0.4-")
    old_catalog = catalog_bytes(old, "repomap-kg")
    new_catalog = catalog_bytes(new, "common-lore-index")
    old_expected = {path.relative_to(incumbent / "src/main/resources").as_posix():
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    for catalog in CATALOGS for path in (incumbent / "src/main/resources" / catalog).rglob("*")
                    if path.is_file()}
    new_expected = {path.relative_to(ROOT / "src/main/resources").as_posix():
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    for catalog in CATALOGS for path in (ROOT / "src/main/resources" / catalog).rglob("*")
                    if path.is_file()}
    incumbent_tree_files = {
        line.removeprefix("src/main/resources/")
        for line in require(("git", "ls-tree", "-r", "--name-only", INCUMBENT, "--",
                             "src/main/resources/rdbms", "src/main/resources/coordinator-rdbms"), ROOT).splitlines()
        if line
    }
    assert set(old_expected) == incumbent_tree_files
    assert old_catalog == old_expected and old_expected
    assert new_catalog == new_expected and new_expected
    assert old_expected.items() <= new_expected.items()
    assert set(new_expected) - set(old_expected) == W3_APPEND_ONLY
    dated_rdbms = [k for k in sorted(new_expected) if k.startswith("rdbms/202")]
    assert dated_rdbms and dated_rdbms[-1] == "rdbms/2026/10/31-001-cloe-lexical-create-derived-schema.sql"
    with zipfile.ZipFile(new) as wheel:
        names = wheel.namelist()
        assert not any(".data/data/share/repomap-kg/" in name for name in names)
        assert not any(name.startswith("repomap_kg/") for name in names)
    source_files = require(("git", "ls-files", "--cached", "--others", "--exclude-standard"), ROOT).splitlines()
    inputs = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(set(source_files))
              if (ROOT / name).is_file() and (name in {"README.md", "LICENSE", "pyproject.toml"}
                  or name.startswith(("src/main/python/", "src/main/resources/")))}
    old_cat_sha = hashlib.sha256(json.dumps(old_expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    new_cat_sha = hashlib.sha256(json.dumps(new_expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    print(json.dumps({"schema": "cloe-offline-wheel-proof-v1", "incumbent_commit": INCUMBENT,
        "old_wheel_sha256": hashlib.sha256(old.read_bytes()).hexdigest(),
        "new_wheel_sha256": hashlib.sha256(new.read_bytes()).hexdigest(),
        "candidate_input_sha256": hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "candidate_input_count": len(inputs), "old_catalog_files": len(old_expected),
        "old_catalog_sha256": old_cat_sha, "new_catalog_files": len(new_expected),
        "new_catalog_sha256": new_cat_sha, "w3_additions": sorted(W3_APPEND_ONLY),
        "catalog_files": len(new_expected), "catalog_sha256": new_cat_sha}, sort_keys=True))
    return old, new


def interpreter(tmp_path: Path) -> Path:
    # Reuse the pinned SDK dependencies; do not seed or download another installer.
    environment = tmp_path / "environment"
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(environment)
    return environment / "bin/python"


def install(python: Path, wheel: Path, cwd: Path) -> None:
    require((sys.executable, "-m", "pip", "--python", str(python), "install", "--no-index",
             "--no-deps", str(wheel)), cwd, pip=True)


def probe(python: Path, code: str, cwd: Path) -> dict:
    return json.loads(require((str(python), "-I", "-c", code), cwd))


OWNERSHIP = """import json
from common_lore_index.runtime.installation_ownership import inspect_installation_ownership
print(json.dumps(inspect_installation_ownership().as_dict()))
"""
CATALOG_PROBE = """import json
from common_lore_index.storage import discover_migrations
from common_lore_index.coordinator._control_schema import discover_control_migrations
encode=lambda items:[(i.relative_path,i.changeset_id,i.checksum) for i in items]
print(json.dumps({'graph':encode(discover_migrations()),'control':encode(discover_control_migrations())}))
"""


def test_fresh_candidate_commands_resources_and_legacy_absence(wheels, tmp_path: Path) -> None:
    _old, new = wheels
    python = interpreter(tmp_path)
    install(python, new, tmp_path)
    assert probe(python, OWNERSHIP, tmp_path)["state"] == "single-owner"
    from smoke.host_mcp_native_launch import console_entrypoint
    console = console_entrypoint(python.parent / "cloe")
    assert console.form == "absolute-shebang" and console.targets_cli_main
    obsolete = python.parent / "repomap-kg"
    assert not obsolete.exists() and not obsolete.is_symlink()
    for name in ("cloe", "common-lore-index"):
        wrapper = python.parent / name
        result = run((str(wrapper), "--version"), tmp_path)
        assert result.returncode == 0 and result.stdout == name + " 0.0.4\n"
        assert result.stderr == ""
        identity = run((str(wrapper), "identity", "--json"), tmp_path)
        payload = json.loads(identity.stdout)
        assert identity.returncode == 0 and len(payload) == 6
        assert payload["distribution"] == "common-lore-index" and payload["package"] == "common_lore_index"
        assert payload["cli"] == "cloe"
    package_absent = probe(python, """import importlib.util,json
print(json.dumps({'absent':importlib.util.find_spec('repomap_kg') is None}))
""", tmp_path)
    assert package_absent == {"absent": True}
    installed = probe(python, CATALOG_PROBE, tmp_path)
    from common_lore_index.storage import discover_migrations
    from common_lore_index.coordinator._control_schema import discover_control_migrations
    encode = lambda items: [[i.relative_path, i.changeset_id, i.checksum] for i in items]
    assert installed == {"graph": encode(discover_migrations(ROOT / "src/main/resources/rdbms")),
                         "control": encode(discover_control_migrations(ROOT / "src/main/resources/coordinator-rdbms"))}


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
@pytest.mark.parametrize("remove", [0, 1])
def test_installer_ownership_remains_separate_in_both_orders(wheels, tmp_path: Path, order, remove):
    python = interpreter(tmp_path)
    for index in order:
        install(python, wheels[index], tmp_path)
    assert probe(python, OWNERSHIP, tmp_path)["state"] == "conflicting"
    canonical = python.parent / "cloe"
    legacy = python.parent / "repomap-kg"
    assert canonical.is_file() and legacy.is_file()
    wrappers = (legacy, canonical)
    survivor = wrappers[1 - remove]
    surviving_bytes = survivor.read_bytes()
    owner = ("repomap-kg", "common-lore-index")[remove]
    require((sys.executable, "-m", "pip", "--python", str(python), "uninstall", "-y", owner), tmp_path, pip=True)
    retired = wrappers[remove]
    assert not retired.exists() and not retired.is_symlink()
    assert survivor.read_bytes() == surviving_bytes
    identity = probe(python, """from importlib.machinery import PathFinder
import json, sysconfig
owned = [sysconfig.get_path('purelib')]
def has_package(name):
    spec = PathFinder.find_spec(name, owned)
    return spec is not None and spec.origin is not None
print(json.dumps({name: has_package(name) for name in ('common_lore_index', 'repomap_kg')}))
""", tmp_path)
    assert identity == {"common_lore_index": remove == 0, "repomap_kg": remove == 1}, identity
    if remove == 0:
        version = run((str(canonical), "--version"), tmp_path)
        assert version.returncode == 0 and version.stdout.startswith("cloe ")
        assert probe(python, OWNERSHIP, tmp_path)["state"] == "single-owner"
    print(json.dumps({"schema": "cloe-installer-separate-ownership-proof-v1", "order": order,
                      "removed": owner, "surviving_wrapper_preserved": True,
                      "canonical_wrapper_present": canonical.exists()}, sort_keys=True))
