"""Pure release recipe leaf and public API equivalence owners."""

from __future__ import annotations

from pathlib import Path
import hashlib
import subprocess
import sys

from common_lore_index.runtime import commands, release, release_manifest
from common_lore_index.runtime.release_dockerfile import ARCHIVE_APT_PATH, archive_apt_sources, render_server_dockerfile

ROOT = Path(__file__).resolve().parents[6]


def test_commands_reexports_pure_leaf_and_preserves_recipe_bytes() -> None:
    assert commands.render_server_dockerfile is render_server_dockerfile
    # Pin the unresolved recipe and the frozen recipe with its archive sources.
    cases = (
        (release.DYNAMIC_PACKAGE_MANIFEST, release.PSYCOPG_LIBPQ_VERSION,
         "20db9ee3957945882d3a2f1fa83626a6cd37235ed0fdd4665e6d83c99ca37cf2"),
        (release_manifest.D12_CANDIDATE_PACKAGE_MANIFEST, release_manifest.D12_CANDIDATE_LIBPQ_VERSION,
         "e00d77a0f544b69b4d9ae2437e50f510fa41dec0bc6bba76a2f0b3dfda2472b9"),
    )
    for manifest, libpq_version, expected in cases:
        assert hashlib.sha256(render_server_dockerfile(manifest, libpq_version).encode()).hexdigest() == expected


def test_older_builder_header_selection_has_a_distinct_recipe_identity() -> None:
    manifest = dict(release_manifest.D12_CANDIDATE_PACKAGE_MANIFEST)
    platform = manifest["amd64"]
    previous = release.DebPackage(
        "linux-libc-dev", "6.12.116-1", "all",
        "c43f5c6dc104f2a2ce985d6d68d3575a7be6fe904313efa7b10edfa0170f9dce",
    )
    assert sum(row.name == previous.name for row in platform.builder) == 1
    manifest["amd64"] = platform._replace(builder=tuple(
        previous if row.name == previous.name else row for row in platform.builder
    ))
    rendered = render_server_dockerfile(manifest, release_manifest.D12_CANDIDATE_LIBPQ_VERSION)
    assert hashlib.sha256(rendered.encode()).hexdigest() == (
        "d5dcfb910dda3160af68c7a428d825c6852ccff994815ac8faa40b9ef896c49e"
    )


def test_frozen_builder_archives_use_base_keyrings_before_package_resolution() -> None:
    manifest = release_manifest.D12_CANDIDATE_PACKAGE_MANIFEST
    keyring = manifest["amd64"].pgdg_keyring[0]
    pgdg, debian, security = archive_apt_sources(keyring).strip().split("\n\n")
    assert f"Signed-By: {keyring}" in pgdg and "Check-Valid-Until" not in pgdg
    for source in (debian, security):
        assert "Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp" in source
        assert "Check-Valid-Until: no" in source
    assert all("Trusted:" not in source for source in (pgdg, debian, security))
    recipe = render_server_dockerfile(manifest, release_manifest.D12_CANDIDATE_LIBPQ_VERSION)
    builder, = (line for line in recipe.splitlines() if "apt-archive.postgresql.org" in line)
    assert builder.index("sha256sum --check") < builder.index(f"> {ARCHIVE_APT_PATH}")
    assert builder.index(f"> {ARCHIVE_APT_PATH}") < builder.index("apt-get update")
    assert builder.index("sha256sum --check --strict --quiet") < builder.index("--no-download")
    assert builder.index("--no-download") < builder.index(f"rm {ARCHIVE_APT_PATH}")


def test_pure_leaf_imports_with_database_modules_blocked() -> None:
    canary = r"""
import importlib.abc
import sys

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        forbidden = (
            "psycopg", "psycopg2", "sqlalchemy",
            "sqlite3", "tree_sitter", "tree_sitter_rust",
            "common_lore_index.storage",
            "common_lore_index.ops.rust_pilot_publication",
        )
        if any(name == item or name.startswith(item + ".") for item in forbidden):
            raise ImportError("database/publication import blocked")
        return None

sys.path.insert(0, sys.argv[1])
sys.meta_path.insert(0, Blocker())
from common_lore_index.runtime.release_dockerfile import render_server_dockerfile
assert callable(render_server_dockerfile)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", canary, str(ROOT / "src/main/python")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
