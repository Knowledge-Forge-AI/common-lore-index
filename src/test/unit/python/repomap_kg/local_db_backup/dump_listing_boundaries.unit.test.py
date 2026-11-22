import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from repomap_test_support.local_db_backup import LocalDbBackupUnitTestCase

from common_lore_index.runtime import _backup_manifest_records as records
from common_lore_index.runtime.backup import (
    LocalDbBackupError,
    dump_database,
    format_backup_info_table,
    format_backup_listing_table,
    format_backup_result_table,
    list_backups,
    read_backup_info,
)
from common_lore_index.runtime.backup_manifests import summarize_pg_restore_toc
from common_lore_index.runtime.backup_records import MAX_TOC_TABLE_NAMES
from common_lore_index.runtime.local import setup_local_runtime


class LocalDbBackupDumpListingBoundariesUnitTests(LocalDbBackupUnitTestCase):
    def test_inspect_parse_error_and_unavailable_container_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "repo-map-home"
            setup_local_runtime(home)

            def unavailable_runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")

            with self.assertRaises(LocalDbBackupError) as unavailable:
                dump_database(
                    home,
                    database="repomap",
                    timestamp="20260702T010203Z",
                    command_runner=unavailable_runner,
                )
            self.assertEqual(
                unavailable.exception.diagnostics[0].code,
                "runtime-container-unavailable",
            )

            def malformed_runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, stdout="{not-json", stderr="")

            with self.assertRaises(LocalDbBackupError) as malformed:
                dump_database(
                    home,
                    database="repomap",
                    timestamp="20260702T010204Z",
                    command_runner=malformed_runner,
                )
            self.assertEqual(
                malformed.exception.diagnostics[0].code,
                "runtime-container-inspect-error",
            )

    def test_listing_reports_malformed_manifests_and_tables_stay_bounded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "repo-map-home"
            setup_local_runtime(home)
            bad_dir = home / "backups" / "runtime123" / "repomap" / "bad"
            bad_dir.mkdir(parents=True)
            (bad_dir / "manifest.json").write_text("{not-json", encoding="utf-8")

            listing = list_backups(home)

            payload = listing.to_jsonable()
            self.assertEqual(payload["backup_count"], 0)
            self.assertEqual(payload["diagnostics"][0]["code"], "backup-manifest-unreadable")
            listing_table = format_backup_listing_table(listing)
            self.assertIn("backup-manifest-unreadable", listing_table)

            dry_run = dump_database(
                home,
                database="repomap",
                dry_run=True,
                timestamp="20260702T010203Z",
            )
            self.assertIn("direct_db_required=false", format_backup_result_table(dry_run))

    def test_backup_info_by_manifest_path_and_table_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "repo-map-home"
            backup_dir = home / "backups" / "runtime123" / "repomap" / "20260702T010203Z"
            backup_dir.mkdir(parents=True)
            manifest = {
                "backup_format_version": 1,
                "backup_id": "runtime123--repomap--20260702T010203Z",
                "backup_kind": "manual-dump",
                "runtime_id": "runtime123",
                "database": "repomap",
                "timestamp": "20260702T010203Z",
                "dump_files": [],
                "restore_supported": False,
            }
            (backup_dir / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            info = read_backup_info(home, backup_dir / "manifest.json")
            table = format_backup_info_table(info)

            self.assertIn("Common Lore Index local DB backup info", table)
            self.assertIn("restore_supported=false", table)


def test_manifest_walk_preserves_literal_lookup_directory_order_and_symlink_boundary(tmp_path, monkeypatch):
    for directory, filename in (("a/inner", "Manifest.json"), ("a/x", "manifest.json"), ("a-b", "manifest.json")):
        branch = tmp_path / directory
        branch.mkdir(parents=True)
        (branch / filename).write_text("{}")
    (tmp_path / "alias").symlink_to(tmp_path / "a", target_is_directory=True)

    expected = sorted(tmp_path.rglob("manifest.json"))
    iterdir = Path.iterdir

    def deny_listing(path):
        if path == tmp_path / "a-b":
            raise PermissionError("directory may only be searched")
        return iterdir(path)

    monkeypatch.setattr(Path, "iterdir", deny_listing)
    actual = list(records.iter_backup_manifest_paths(tmp_path))
    assert actual == expected
    assert all("alias" not in path.parts for path in actual)


def test_manifest_lookup_stops_before_later_branches(tmp_path, monkeypatch):
    first = tmp_path / "a" / "manifest.json"
    later = tmp_path / "z" / "nested" / "manifest.json"
    for path in (later, first):
        path.parent.mkdir(parents=True)
        path.write_text('{"backup_id":"selected"}', encoding="utf-8")

    original = Path.iterdir
    visited = []

    def observe(path):
        visited.append(path)
        return original(path)

    monkeypatch.setattr(Path, "iterdir", observe)
    assert records.resolve_backup_manifest_path(tmp_path, "selected") == first
    assert visited == [tmp_path, first.parent]
    assert list(records.iter_backup_manifest_paths(tmp_path)) == [first, later]


def test_inspection_rejects_wrong_length_without_hashing(tmp_path, monkeypatch):
    path = tmp_path / "backup.pgcustom"
    path.write_bytes(b"good")
    original = records.describe_dump_file
    hashed = []

    def observe(path):
        hashed.append(path)
        return original(path)

    monkeypatch.setattr(records, "describe_dump_file", observe)
    manifest = {"size_bytes": 5, "sha256": hashlib.sha256(b"good").hexdigest()}
    ok, diagnostics = records.verify_dump_file_checksum_for_inspection(path, manifest, path.name)
    assert not ok and diagnostics[0].code == "backup-checksum-mismatch"
    assert not hashed
    ok, diagnostics = records.verify_dump_file_checksum_for_inspection(
        path, {"size_bytes": 4, "sha256": "0" * 64}, path.name,
    )
    assert not ok and diagnostics[0].code == "backup-checksum-mismatch"
    assert hashed == [path]


def test_large_toc_keeps_sorted_unique_prefix_and_exact_entry_count():
    size = MAX_TOC_TABLE_NAMES + 9
    lines = [
        f"{n + 1}; 1259 16390 TABLE public item_{n:03} owner"
        for n in reversed(range(size))
    ]
    summary = summarize_pg_restore_toc(
        "\r\n".join(["; comment", "", *lines, lines[-1], "invalid; 0 0 TABLE public ignored"]),
        dump_file="test.pgcustom", dump_format="pgcustom",
    )
    assert summary.toc_entry_count == size + 1
    assert summary.tables == tuple(f"item_{n:03}" for n in range(MAX_TOC_TABLE_NAMES))
    assert summary.table_count == MAX_TOC_TABLE_NAMES
