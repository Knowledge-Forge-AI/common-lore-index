"""Source-derived inventory, input census and wheel inventory witnesses over temporary trees."""

from __future__ import annotations

import base64
import hashlib
import tarfile
import zipfile
from pathlib import Path

import pytest

from repomap_test_support.installed_candidate import (
    DIST_INFO, BuiltWheel, expected_entry_points, expected_inventory, expected_requires, input_digest,
    input_names, installed_graph_parser_digest, inventory_sha256, license_bytes_match, license_members,
    rows_sha256, wheel_inventory,
)

PYPROJECT = """[project]
name = "common-lore-index"
version = "0.0.4"
dependencies = ["typing-extensions==4.16.0"]

[project.scripts]
cloe = "common_lore_index.entrypoints:cloe_main"
common-lore-index = "common_lore_index.entrypoints:long_main"

[tool.setuptools.data-files]
"share/common-lore-index/rdbms" = ["src/main/resources/rdbms/changelog.yaml"]
"share/common-lore-index/rdbms/2026/06" = ["src/main/resources/rdbms/2026/06/*.sql"]
"""
LISTED = [
    "LICENSE", "NOTICE", "README.md", "pyproject.toml", "tools/run_tests.py", "src/test/x.py",
    "src/main/python/common_lore_index/__init__.py", "src/main/python/common_lore_index/cli/main.py",
    "src/main/python/common_lore_index/third_party_licenses/a.json",
    "src/main/python/common_lore_index/__pycache__/x.cpython-313.pyc",
    "src/main/python/repomap_kg/__init__.py",
    "src/main/resources/rdbms/changelog.yaml", "src/main/resources/rdbms/2026/06/01.sql",
    "src/main/resources/rdbms/2026/06/nested/02.sql",
]


def _tree(root: Path) -> Path:
    for name in LISTED:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PYPROJECT if name == "pyproject.toml" else name + "\n", encoding="utf-8")
    return root


def _record_row(name: str, body: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(body)}"


def _wheel(path: Path, members: dict[str, bytes], *, break_hash: bool = False, extra_row: str = "") -> Path:
    rows = [_record_row(name, body) for name, body in members.items()]
    if break_hash:
        rows[0] = rows[0].replace("sha256=", "sha256=A")
    rows.append(f"{DIST_INFO}/RECORD,,")
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
        archive.writestr(f"{DIST_INFO}/RECORD", "\n".join(rows) + "\n" + extra_row)
    return path


def _members(root: Path) -> dict[str, bytes]:
    return {
        "common_lore_index/__init__.py": b"x", f"{DIST_INFO}/METADATA":
            b"Metadata-Version: 2.4\nName: common-lore-index\nRequires-Dist: typing-extensions==4.16.0\n"
            b"Requires-Dist: psycopg[c]==3.3.6; extra == \"postgres\"\n",
        f"{DIST_INFO}/entry_points.txt": b"[console_scripts]\ncloe = common_lore_index.entrypoints:cloe_main\n",
        f"{DIST_INFO}/licenses/LICENSE": (root / "LICENSE").read_bytes(),
        f"{DIST_INFO}/licenses/NOTICE": (root / "NOTICE").read_bytes(),
    }


def test_input_census_includes_notice_and_excludes_tests_and_tools(tmp_path: Path) -> None:
    names = input_names(LISTED)
    assert "NOTICE" in names and "LICENSE" in names
    assert not any(name.startswith(("tools/", "src/test/")) for name in names)
    root = _tree(tmp_path)
    digest, count = input_digest(root, names)
    assert count == len(names) and len(digest) == 64
    (root / "NOTICE").write_text("changed\n", encoding="utf-8")
    assert input_digest(root, names)[0] != digest


def test_expected_inventory_is_derived_from_owners_alone(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    expected = expected_inventory(root, LISTED)
    assert "common_lore_index/cli/main.py" in expected
    assert "common_lore_index/third_party_licenses/a.json" in expected
    assert not any("__pycache__" in name for name in expected)
    assert not any(name.startswith("repomap_kg/") for name in expected)
    data = "common_lore_index-0.0.4.data/data/share/common-lore-index/rdbms"
    assert f"{data}/changelog.yaml" in expected and f"{data}/2026/06/01.sql" in expected
    assert not any(name.endswith("02.sql") for name in expected)
    assert {f"{DIST_INFO}/licenses/LICENSE", f"{DIST_INFO}/licenses/NOTICE", f"{DIST_INFO}/RECORD"} <= expected
    assert license_members(["LICENSE", "NOTICE", "COPYING.txt", "docs/LICENSE", "README.md"]) == [
        "COPYING.txt", "LICENSE", "NOTICE"]


def test_source_entry_points_and_requirement(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    assert expected_entry_points(root) == ["cloe=common_lore_index.entrypoints:cloe_main",
                                           "common-lore-index=common_lore_index.entrypoints:long_main"]
    assert expected_requires(root) == ["typing-extensions==4.16.0"]
    assert rows_sha256(["a"]) != rows_sha256(["b"])


def test_wheel_inventory_reads_only_wheel_bytes(tmp_path: Path) -> None:
    root = _tree(tmp_path / "root")
    inventory = wheel_inventory(_wheel(tmp_path / "w.whl", _members(root)))
    assert inventory.groups == ("console_scripts",)
    assert inventory.entry_points == ("cloe=common_lore_index.entrypoints:cloe_main",)
    assert inventory.requires == ("typing-extensions==4.16.0",)
    assert inventory.record_valid and inventory.pth_members == 0 and inventory.forbidden_members == 0
    assert inventory.licenses == ("LICENSE", "NOTICE") and license_bytes_match(inventory, root)
    (root / "NOTICE").write_text("drifted\n", encoding="utf-8")
    assert not license_bytes_match(inventory, root)


def test_record_hash_drift_and_forbidden_members_are_visible(tmp_path: Path) -> None:
    root = _tree(tmp_path / "root")
    broken = wheel_inventory(_wheel(tmp_path / "broken.whl", _members(root), break_hash=True))
    assert not broken.record_valid
    members = dict(_members(root), **{"leak.pth": b"import x", "tools/run_tests.py": b"x"})
    leaky = wheel_inventory(_wheel(tmp_path / "leaky.whl", members))
    assert leaky.pth_members == 1 and leaky.forbidden_members == 1


def test_record_refuses_duplicate_member_rows(tmp_path: Path) -> None:
    root = _tree(tmp_path / "root")
    members = _members(root)
    name = "common_lore_index/__init__.py"
    duplicate = _record_row(name, members[name]) + "\n"
    assert not wheel_inventory(_wheel(tmp_path / "duplicate.whl", members, extra_row=duplicate)).record_valid


def test_inventory_digest_ignores_only_optional_top_level() -> None:
    names = {"a.py", f"{DIST_INFO}/RECORD"}
    assert inventory_sha256(names) == inventory_sha256(names | {f"{DIST_INFO}/top_level.txt"})
    assert inventory_sha256(names) != inventory_sha256(names | {"b.py"})


def test_material_refuses_an_unreviewed_message_body(tmp_path: Path) -> None:
    import shutil

    from repomap_test_support.installed_experience_fixture import FIXTURE_ROOT, write_tiny_material

    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT, fixture)
    message = fixture / "messages" / "ledger.eml"
    message.write_bytes(message.read_bytes() + b"Changed body.\n")
    target = tmp_path / "owned material"
    with pytest.raises(AssertionError, match="installed-message-fixture-unreviewed"):
        write_tiny_material(target, fixture)
    assert not target.exists()


def test_candidate_pins_follow_current_release_and_keep_incumbent_frozen() -> None:
    import tomllib

    from installed_experience_pins import (
        CANDIDATE_DISTRIBUTION, CANDIDATE_VERSION, CANDIDATE_WHEEL_PREFIX,
        INCUMBENT_COMMIT, INCUMBENT_DISTRIBUTION, INCUMBENT_WHEEL_PREFIX,
    )
    from repomap_test_support.installed_candidate import ROOT

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert CANDIDATE_DISTRIBUTION == project["name"] == "common-lore-index"
    assert CANDIDATE_VERSION == project["version"] == "0.0.4"
    assert CANDIDATE_WHEEL_PREFIX == f"common_lore_index-{project['version']}-"
    assert INCUMBENT_COMMIT == "6d818ad6d43d38d47c64b8c474c2b64b853f631a"
    assert INCUMBENT_DISTRIBUTION == "repomap-kg"
    assert INCUMBENT_WHEEL_PREFIX == "repomap_kg-0.0.3-"


def test_released_wheel_uses_exact_source_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import repomap_test_support.installed_candidate as installed

    factory = object()
    recorded = object()

    def archive(
        request_factory: object, root: Path, commit: str, prefix: str, paths: tuple[str, ...],
    ) -> object:
        assert request_factory is factory and root == tmp_path
        assert commit == "39cb2b3452bfd32b05d38f842c0f891614cbf58d"
        assert prefix == "common_lore_index-0.0.4-"
        assert paths == ("README.md", "LICENSE", "NOTICE", "pyproject.toml",
                         "src/main/python", "src/main/resources")
        return recorded

    monkeypatch.setattr(installed, "_archived_wheel", archive)
    assert installed.released_cloe_wheel(factory, tmp_path) is recorded


def test_archive_memo_distinguishes_wheel_prefix_and_input_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory, tmp_path: Path,
) -> None:
    import repomap_test_support.installed_candidate as installed

    root = tmp_path / "repository"
    root.mkdir()
    for name in ("NOTICE", "LICENSE"):
        (root / name).write_text(name, encoding="utf-8")

    def archive(args: tuple[str, ...], cwd: Path, family: str) -> str:
        assert cwd == root and family == "arch7f_probe"
        with tarfile.open(args[3].removeprefix("--output="), "w") as members:
            for name in args[5:]:
                members.add(root / name, arcname=name)
        return ""

    build_sources: list[Path] = []

    def build(source: Path, output: Path, prefix: str) -> Path:
        build_sources.append(source)
        output.mkdir()
        wheel = output / f"{prefix}py3-none-any.whl"
        wheel.write_text(sorted(path.name for path in source.iterdir())[0], encoding="utf-8")
        return wheel

    monkeypatch.setattr(installed, "_require", archive)
    monkeypatch.setattr(installed, "_build", build)
    first = installed._archived_wheel(tmp_path_factory, root, "commit", "first-", ("NOTICE",))
    renamed = installed._archived_wheel(tmp_path_factory, root, "commit", "second-", ("NOTICE",))
    different = installed._archived_wheel(tmp_path_factory, root, "commit", "first-", ("LICENSE",))

    assert installed._archived_wheel(tmp_path_factory, root, "commit", "first-", ("NOTICE",)) is first
    assert renamed.wheel.name == "second-py3-none-any.whl"
    assert renamed.input_sha256 == first.input_sha256
    assert different.input_sha256 != first.input_sha256
    assert different.wheel.read_text(encoding="utf-8") == "LICENSE"

    for mismatch in (
        (different.input_sha256, first.input_count),
        (first.input_sha256, first.input_count + 1),
    ):
        with pytest.raises(AssertionError, match="archive differs from pinned package inputs"):
            installed._archived_wheel(
                tmp_path_factory, root, "commit", "first-", ("NOTICE",),
                expected_inputs=mismatch,
            )
    assert len(build_sources) == 3

    def checked_archive() -> BuiltWheel:
        return installed._archived_wheel(
            tmp_path_factory, root, "commit", "first-", ("NOTICE",),
            expected_inputs=(first.input_sha256, first.input_count),
        )

    checked = checked_archive()
    assert checked.wheel != first.wheel
    assert checked.input_sha256 == first.input_sha256
    assert checked_archive() is checked
    assert len(build_sources) == 4


@pytest.mark.parametrize("drift", ("none", "source", "installation", "version", "metadata"))
def test_installed_graph_parser_digest_checks_both_witnesses(tmp_path: Path, drift: str) -> None:
    member = "common_lore_index/cli/_parser_ops_graph.py"
    body = b"public wheel fixture"
    source = tmp_path / "prepared"
    installed = tmp_path / "venv"
    for name, path in (("source", source / "src/main/python" / member), ("installation", installed / member)):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"changed" if name == drift else body)
    archive = tmp_path / "common_lore_index-0.0.4-py3-none-any.whl"
    metadata_path = "common_lore_index-0.0.4.dist-info/METADATA"
    metadata = b"Version: 0.0.3\n" if drift == "version" else b"Version: 0.0.4\n"
    installed_metadata = installed / metadata_path
    installed_metadata.parent.mkdir()
    installed_metadata.write_bytes(b"Version: 0.0.2\n" if drift == "metadata" else metadata)
    with zipfile.ZipFile(archive, "w") as wheel:
        wheel.writestr(member, body)
        wheel.writestr(metadata_path, metadata)
    built = BuiltWheel(archive, source, "unused", 1)
    if drift == "none":
        assert installed_graph_parser_digest(built, installed) == hashlib.sha256(body).hexdigest()
    else:
        with pytest.raises(AssertionError, match="differs from"):
            installed_graph_parser_digest(built, installed)

@pytest.mark.parametrize("fault", (
    "none", "omitted", "drift", "unexpected", "duplicate", "traversal", "link", "metadata",
))
def test_source_archive_rejects_changed_or_unowned_members(tmp_path: Path, fault: str) -> None:
    import io
    import tarfile

    from installed_experience_pins import CANDIDATE_VERSION
    from repomap_test_support.installed_sdist import SDIST_FILENAME, verify_sdist

    archive = tmp_path / SDIST_FILENAME
    stem = SDIST_FILENAME.removesuffix(".tar.gz")
    bodies = {"pyproject.toml": b"[project]\n", "LICENSE": b"license", "NOTICE": b"notice"}
    expected = {name: hashlib.sha256(data).hexdigest() for name, data in bodies.items()}
    version = "0.0.0" if fault == "metadata" else CANDIDATE_VERSION
    metadata = (f"Name: common-lore-index\nVersion: {version}\n"
                "License-File: LICENSE\nLicense-File: NOTICE\n").encode()
    with tarfile.open(archive, "w:gz") as stream:
        def add(name: str, data: bytes) -> None:
            item = tarfile.TarInfo(f"{stem}/{name}")
            item.size = len(data)
            stream.addfile(item, io.BytesIO(data))

        root = tarfile.TarInfo(stem)
        root.type = tarfile.DIRTYPE
        stream.addfile(root)
        for name, data in bodies.items():
            if fault == "omitted" and name == "NOTICE":
                continue
            add(name, b"changed" if fault == "drift" and name == "LICENSE" else data)
        add("PKG-INFO", metadata)
        if fault == "unexpected":
            add("private.py", b"x")
        if fault == "duplicate":
            add("LICENSE", bodies["LICENSE"])
        if fault == "traversal":
            add("../pyproject.toml", b"x")
        if fault == "link":
            item = tarfile.TarInfo(f"{stem}/link")
            item.type, item.linkname = tarfile.SYMTYPE, "LICENSE"
            stream.addfile(item)
    if fault == "none":
        verify_sdist(archive, expected)
    else:
        with pytest.raises(AssertionError, match="source archive"):
            verify_sdist(archive, expected)
