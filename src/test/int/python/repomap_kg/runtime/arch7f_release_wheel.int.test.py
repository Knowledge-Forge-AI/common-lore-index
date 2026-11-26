import importlib.util
import json
import os
import shutil
import sys
import tomllib
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from common_lore_index.coordinator._control_schema import discover_control_migrations
from common_lore_index.storage import discover_migrations


REPO_ROOT = Path(__file__).resolve().parents[6]


def _verify_wheel_contents(
    wheel: Path,
    expected_version: str,
    tmp_path: Path,
    cap: Any,
    family_suffix: str,
) -> None:
    from runner_coverage_execution import prepare_child_coverage_environment
    from runner_coverage_observer import launch_observed_process

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = archive.read(f"common_lore_index-{expected_version}.dist-info/METADATA").decode("utf-8")
    header = metadata.split("\n\n", 1)[0].splitlines()

    requirements = [line for line in header if line.startswith("Requires-Dist: ")]
    unconditional = [line for line in requirements if "; extra ==" not in line]

    assert unconditional == ["Requires-Dist: typing-extensions==4.16.0"]
    assert [line for line in requirements if "psycopg" in line] == [
        'Requires-Dist: psycopg[c]==3.3.6; extra == "postgres"'
    ]
    assert "Provides-Extra: postgres" in header

    graph_resources = tuple(
        path.relative_to(REPO_ROOT / "src/main/resources").as_posix()
        for path in sorted((REPO_ROOT / "src/main/resources/rdbms").rglob("*"))
        if path.is_file()
    )
    control_resources = tuple(
        path.relative_to(REPO_ROOT / "src/main/resources").as_posix()
        for path in sorted(
            (REPO_ROOT / "src/main/resources/coordinator-rdbms").rglob("*")
        )
        if path.is_file()
    )
    installed_data = {
        name.split(".data/data/share/common-lore-index/", 1)[1]
        for name in names
        if ".data/data/share/common-lore-index/" in name
    }

    assert installed_data == {*graph_resources, *control_resources}
    october = {path for path in control_resources if path.startswith("coordinator-rdbms/2026/10/")}
    assert october, "October coordinator migration catalog must be nonempty"
    assert october <= installed_data

    install_root = tmp_path / f"installed_{family_suffix}"
    pip_install_env = prepare_child_coverage_environment(
        os.environ, family="arch7f_pip", capability=cap
    )
    pip_install = launch_observed_process(
        (
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(install_root),
            str(wheel),
        ),
        family="arch7f_pip",
        env=pip_install_env,
        capability=cap,
    )
    assert pip_install.returncode == 0, pip_install.stderr[-2_000:]
    probe_env = prepare_child_coverage_environment(
        os.environ,
        family="arch7f_probe",
        extra_env={"PYTHONPATH": str(install_root)},
        capability=cap,
    )
    probe = launch_observed_process(
        (
            sys.executable,
            "-c",
            "import json,sys,sysconfig; "
            "from pathlib import Path; "
            "data_root=Path(sys.argv[1]); "
            "original_get_path=sysconfig.get_path; "
            "sysconfig.get_path=lambda name,*args,**kwargs: "
            "str(data_root) if name == 'data' else "
            "original_get_path(name,*args,**kwargs); "
            "from common_lore_index.storage import discover_migrations; "
            "from common_lore_index.coordinator._control_schema import "
            "discover_control_migrations; "
            "encode=lambda items: [(item.relative_path,item.changeset_id,"
            "item.checksum) for item in items]; "
            "print(json.dumps({'graph':encode(discover_migrations()),"
            "'control':encode(discover_control_migrations())},sort_keys=True))",
            str(install_root),
        ),
        family="arch7f_probe",
        env=probe_env,
        capability=cap,
    )
    assert probe.returncode == 0, probe.stderr[-2_000:]
    installed = json.loads(probe.stdout)
    source_graph = [
        [item.relative_path, item.changeset_id, item.checksum]
        for item in discover_migrations(REPO_ROOT / "src/main/resources/rdbms")
    ]
    source_control = [
        [item.relative_path, item.changeset_id, item.checksum]
        for item in discover_control_migrations(
            REPO_ROOT / "src/main/resources/coordinator-rdbms"
        )
    ]

    assert installed == {"control": source_control, "graph": source_graph}


def test_wheel_contains_complete_graph_and_control_migration_catalogs(
    tmp_path: Path,
) -> None:
    if importlib.util.find_spec("setuptools") is None:
        pytest.skip("setuptools build backend is unavailable")
    from runner_coverage_bootstrap import resolve_bootstrap_capability
    from runner_coverage_execution import prepare_child_coverage_environment
    from runner_coverage_observer import launch_observed_process

    cap = resolve_bootstrap_capability(env=os.environ)
    wheel_directory = tmp_path / "wheel_private"
    wheel_directory.mkdir()
    pip_env = prepare_child_coverage_environment(os.environ, family="arch7f_pip", capability=cap)
    wheel_build = launch_observed_process(
        (
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_directory),
            str(REPO_ROOT),
        ),
        family="arch7f_pip",
        env=pip_env,
        capability=cap,
    )
    assert wheel_build.returncode == 0, wheel_build.stderr[-2_000:]
    wheel = next(wheel_directory.glob("common_lore_index-*.whl"))
    version = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["version"]
    _verify_wheel_contents(wheel, version, tmp_path, cap, "private")


def test_projected_public_wheel_contains_complete_graph_and_control_migration_catalogs(
    tmp_path: Path,
) -> None:
    if importlib.util.find_spec("setuptools") is None:
        pytest.skip("setuptools build backend is unavailable")
    from runner_coverage_bootstrap import resolve_bootstrap_capability
    from runner_coverage_execution import prepare_child_coverage_environment
    from runner_coverage_observer import launch_observed_process

    cap = resolve_bootstrap_capability(env=os.environ)
    current_version = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["version"]
    if current_version == "0.0.4":
        test_wheel_contains_complete_graph_and_control_migration_catalogs(tmp_path)
        return

    projected_src = tmp_path / "projected_source"
    projected_src.mkdir()
    raw_pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.1.0"' in raw_pyproject
    (projected_src / "pyproject.toml").write_text(
        raw_pyproject.replace('version = "0.1.0"', 'version = "0.0.4"', 1),
        encoding="utf-8",
    )
    shutil.copyfile(REPO_ROOT / "README.md", projected_src / "README.md")
    shutil.copytree(REPO_ROOT / "src", projected_src / "src")

    wheel_directory = tmp_path / "wheel_public"
    wheel_directory.mkdir()
    pip_env = prepare_child_coverage_environment(os.environ, family="arch7f_pip", capability=cap)
    wheel_build = launch_observed_process(
        (
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_directory),
            str(projected_src),
        ),
        family="arch7f_pip",
        env=pip_env,
        capability=cap,
    )
    assert wheel_build.returncode == 0, wheel_build.stderr[-2_000:]
    wheel = next(wheel_directory.glob("common_lore_index-0.0.4-*.whl"))
    _verify_wheel_contents(wheel, "0.0.4", tmp_path, cap, "public")

def test_source_distribution_rebuilds_an_equivalent_installed_wheel(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path,
) -> None:
    from hashlib import sha256

    from installed_experience_pins import CANDIDATE_VERSION
    from common_lore_index_test_support.installed_candidate import (
        DIST_INFO, candidate_sdist_wheel, candidate_wheel, expected_inventory, wheel_inventory,
    )
    from common_lore_index_test_support.installed_environment import run_child
    from common_lore_index_test_support.installed_experience_driver import pure_environment
    from common_lore_index_test_support.installed_origin import closure, observe_origin

    direct = candidate_wheel(tmp_path_factory)
    archive, rebuilt = candidate_sdist_wheel(direct, tmp_path / "source archive proof")
    expected = expected_inventory()
    record = f"{DIST_INFO}/RECORD"

    def members(wheel: Path) -> dict[str, str]:
        facts = wheel_inventory(wheel)
        assert facts.record_valid and not (facts.pth_members or facts.forbidden_members)
        assert expected <= facts.names <= expected | {f"{DIST_INFO}/top_level.txt"}
        with zipfile.ZipFile(wheel) as reader:
            names = reader.namelist()
            assert len(names) == len(facts.names)
            return {name: sha256(reader.read(name)).hexdigest() for name in names if name != record}

    rebuilt_members = members(rebuilt)
    assert rebuilt_members == members(direct.wheel)
    env = pure_environment(tmp_path / "rebuilt Local environment", replace(direct, wheel=rebuilt))
    normal = observe_origin(env, REPO_ROOT, isolated=False)
    isolated = observe_origin(env, REPO_ROOT, isolated=True)
    assert closure("pure-local", normal, isolated)
    cli = run_child(env, (str(env.wrapper("cloe")), "--version"))
    assert cli.exit == 0 and cli.stdout == f"cloe {CANDIDATE_VERSION}\n"
    print(json.dumps({"event": "cloe-source-distribution-roundtrip", "input_count": direct.input_count,
                      "input_sha256": direct.input_sha256, "wheel_members": len(rebuilt_members),
                      "sdist_sha256": sha256(archive.read_bytes()).hexdigest(),
                      "direct_wheel_sha256": direct.sha256,
                      "rebuilt_wheel_sha256": sha256(rebuilt.read_bytes()).hexdigest(),
                      "wheel_payload_equivalent": True, "fresh_local_install": True}, sort_keys=True))
