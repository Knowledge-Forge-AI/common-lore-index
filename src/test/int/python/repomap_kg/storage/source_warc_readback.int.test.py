import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common_lore_index_test_support.cli_in_process import run_repo_map_in_process
from common_lore_index_test_support.postgres_harness import (
    require_postgres_binaries,
    temporary_postgres,
)

from common_lore_index.ops.ingestion.source import import_warc_source
from common_lore_index.storage import (
    apply_migrations,
    default_rdbms_root,
    query_canonical_edge_explanation,
    query_canonical_edge_records,
    query_canonical_node_records,
    query_js_summary,
)
from common_lore_index_test_support.storage_integration import (
    copy_warc_fixture_root,
    fixed_source_clock,
)
from common_lore_index_test_support.source_ingestion_integration import (
    publish_acquisition_summary,
)


class StorageSourceWarcReadbackIntegrationTests(unittest.TestCase):
    def test_warc_payload_extraction_reads_the_admitted_snapshot(self) -> None:
        from common_lore_index.graph import _discovery_classify
        from common_lore_index.ops.ingestion import _source_warc_manifest as warc

        with tempfile.TemporaryDirectory() as directory:
            root = copy_warc_fixture_root(Path(directory)).resolve()
            config = warc.load_warc_source_config(root / "warc_sources" / "allowed-warc.toml")
            manifest = warc.build_warc_manifest(config, root_path=root, clock=fixed_source_clock)
            with patch.object(_discovery_classify, "content_hash", side_effect=AssertionError("payload rehash")):
                original = warc.warc_observations_from_manifest(config, manifest, root_path=root)
            records = {
                record.materialized_path: record
                for record in manifest.records
                if record.materialized_path is not None
            }
            extract = warc._observations_for_archive_file
            module_index = warc.PythonModuleIndex.from_python_paths((), repository_root=root)
            baseline = [
                observation
                for path in sorted(records)
                for observation in extract(
                    root, warc.classify_path(root, root / path), module_index=module_index,
                    repository_paths=frozenset(records), markdown_anchors={},
                )
            ]
            self.assertTrue({"html.document", "css.document"} <= {item.kind for item in baseline})
            baseline.extend(warc.extract_css_selector_match_observations(baseline))
            expected = warc._annotate_warc_observations(baseline, config, manifest, records)
            self.assertEqual(original[-len(expected):], expected)
            snapshots: list[Path] = []

            def revise_original(snapshot, file_info, **kwargs):
                self.assertNotEqual(snapshot, root)
                snapshots.append(snapshot)
                (root / file_info.path).write_bytes(b"fixture file edited after capture")
                return extract(snapshot, file_info, **kwargs)

            with patch.object(warc, "_observations_for_archive_file", side_effect=revise_original):
                repeated = warc.warc_observations_from_manifest(config, manifest, root_path=root)
            self.assertEqual(original, repeated)
            self.assertTrue(snapshots)
            self.assertTrue(all(not path.exists() for path in snapshots))
            with self.assertRaisesRegex(warc.SourcePolicyError, "payload differs from its manifest"):
                warc.warc_observations_from_manifest(config, manifest, root_path=root)

    def test_warc_import_refuses_archive_changed_during_read_without_metadata_output(self) -> None:
        from common_lore_index.ops.ingestion import _source_warc_extraction

        with tempfile.TemporaryDirectory() as tmpdir:
            root = copy_warc_fixture_root(Path(tmpdir))
            config = root / "warc_sources" / "allowed-warc.toml"
            artifact = root / "warc_artifacts" / "example.warc"
            record = (b"WARC/1.1\r\nWARC-Type: resource\r\nContent-Type: text/html\r\n"
                      b"Content-Length: 8\r\n\r\n<p>A</p>")
            artifact.write_bytes(record)
            before = artifact.stat()
            changed = record.replace(b"<p>A</p>", b"<p>B</p>")
            read = _source_warc_extraction._read_warc_record

            def mutate_after_read(*args):
                parsed = read(*args)
                artifact.write_bytes(changed)
                os.utime(artifact, ns=(before.st_atime_ns, before.st_mtime_ns))
                return parsed

            with patch.object(_source_warc_extraction, "_read_warc_record", side_effect=mutate_after_read):
                status, out, err = run_repo_map_in_process(
                    "sources", "import-warc", "--config", str(config), "--root-path", str(root), "--json",
                )
            self.assertEqual(status, 1)
            self.assertEqual(out, "")
            self.assertIn("artifact changed during WARC manifest scan", err)
            self.assertNotIn(str(root), err)
            self.assertEqual(artifact.read_bytes(), changed)
            self.assertFalse((root / ".repomap").exists())

    def test_warc_and_http_header_readback_preserve_start_lines_and_duplicate_order(self):
        from common_lore_index.ops.ingestion.source_warc import (
            _http_headers_and_body_offset,
            _parse_http_message_payload,
            _parse_warc_header,
        )

        header = "WARC/1.1\r\nContent-Length: 3\rX-Fixture: first\nX-Fixture: second\u2028\tcontinued".encode()
        self.assertEqual(_parse_warc_header(header, 0), (
            "WARC/1.1", {"content-length": "3", "x-fixture": "second continued"}, 3,
        ))
        payload = "HTTP/1.1 200 OK\rX-Fixture: first\nX-Fixture: second\u2028 continued\r\n\r\nABC".encode()
        self.assertEqual(
            _parse_http_message_payload(payload, response=True), ({"x-fixture": "second continued"}, b"ABC"),
        )
        headers, offset = _http_headers_and_body_offset(payload, response=True)
        self.assertEqual((headers, memoryview(payload)[offset:].tobytes()), ({"x-fixture": "second continued"}, b"ABC"))

    def test_sources_import_warc_loads_local_warc_fixture(self):
        require_postgres_binaries()

        with tempfile.TemporaryDirectory() as tmpdir:
            root_path = copy_warc_fixture_root(Path(tmpdir))
            config_path = root_path / "warc_sources" / "allowed-warc.toml"
            with temporary_postgres() as postgres:
                apply_migrations(
                    default_rdbms_root(),
                    postgres.psql_args,
                    psql_command=postgres.psql_command,
                )
                summary = import_warc_source(
                    config_path,
                    root_path=root_path,
                    clock=fixed_source_clock,
                )
                publication = publish_acquisition_summary(
                    postgres,
                    summary,
                    repository_name="fixture",
                    root_path=root_path,
                )
                warc_documents = query_canonical_node_records(
                    postgres.psql_args,
                    root_path=str(root_path),
                    kind="warc.document",
                    psql_command=postgres.psql_command,
                )
                warc_records = query_canonical_node_records(
                    postgres.psql_args,
                    root_path=str(root_path),
                    kind="warc.record",
                    psql_command=postgres.psql_command,
                )
                html_documents = query_canonical_node_records(
                    postgres.psql_args,
                    root_path=str(root_path),
                    kind="html.document",
                    psql_command=postgres.psql_command,
                )
                css_documents = query_canonical_node_records(
                    postgres.psql_args,
                    root_path=str(root_path),
                    kind="css.document",
                    psql_command=postgres.psql_command,
                )
                config_documents = query_canonical_node_records(
                    postgres.psql_args,
                    root_path=str(root_path),
                    kind="config.document",
                    psql_command=postgres.psql_command,
                )
                js_files = query_canonical_node_records(
                    postgres.psql_args,
                    root_path=str(root_path),
                    kind="js.file",
                    psql_command=postgres.psql_command,
                )
                js_summary = query_js_summary(
                    postgres.psql_args,
                    root_path=str(root_path),
                    psql_command=postgres.psql_command,
                )
                references = query_canonical_edge_records(
                    postgres.psql_args,
                    root_path=str(root_path),
                    kind="references",
                    psql_command=postgres.psql_command,
                )
                raw_count = postgres.psql_scalar("SELECT count(*) FROM raw_observations;")
                warc_reference = next(
                    edge
                    for edge in references
                    if edge.source_key.startswith("warc.record:")
                    and edge.target_key.startswith("external.url:")
                )
                explanation = query_canonical_edge_explanation(
                    postgres.psql_args,
                    root_path=str(root_path),
                    source_key=warc_reference.source_key,
                    kind=warc_reference.edge_kind,
                    target_key=warc_reference.target_key,
                    identity_metadata_hash=warc_reference.identity_metadata_hash,
                    psql_command=postgres.psql_command,
                )
                js_source_map_reference = next(
                    edge
                    for edge in references
                    if edge.target_key.endswith("/record-0005/payload.js.map")
                )
                js_source_map_explanation = query_canonical_edge_explanation(
                    postgres.psql_args,
                    root_path=str(root_path),
                    source_key=js_source_map_reference.source_key,
                    kind=js_source_map_reference.edge_kind,
                    target_key=js_source_map_reference.target_key,
                    identity_metadata_hash=js_source_map_reference.identity_metadata_hash,
                    psql_command=postgres.psql_command,
                )

        self.assertEqual(summary.source_id, "example-warc-archive")
        self.assertEqual(summary.record_count, 8)
        self.assertEqual(summary.routed_payloads, 4)
        self.assertEqual(publication.files, 4)
        self.assertEqual(raw_count, str(summary.observations))
        self.assertEqual(len(warc_documents), 1)
        self.assertEqual(len(warc_records), 8)
        self.assertTrue(html_documents)
        self.assertTrue(css_documents)
        self.assertTrue(config_documents)
        self.assertTrue(js_files)
        self.assertGreaterEqual(js_summary.saved_page_asset_files, 1)
        self.assertGreaterEqual(js_summary.source_map_references, 1)
        self.assertTrue(
            any(edge.source_key.startswith("warc.record:") for edge in references)
        )
        self.assertIsNotNone(explanation.edge)
        self.assertTrue(explanation.evidence)
        self.assertIsNotNone(js_source_map_explanation.edge)
        self.assertTrue(js_source_map_explanation.evidence)
        source_metadata = json.dumps(
            [observation.to_dict() for observation in summary.raw_observations],
            sort_keys=True,
        )
        explain_payload = json.dumps(
            {
                "warc": explanation.to_dict(),
                "source_map": js_source_map_explanation.to_dict(),
            },
            sort_keys=True,
        )
        self.assertIn('"warc_record_key"', source_metadata)
        self.assertIn('"warc_payload_path"', source_metadata)
        self.assertIn('"artifact_extractor_route": "javascript"', source_metadata)
        self.assertIn('"not_fetched": true', source_metadata)
        self.assertNotIn("fixture-secret", source_metadata)
        self.assertNotIn("fixture-secret", explain_payload)

    def test_sources_import_warc_cli_preserves_nonpublication_summary(self):
        require_postgres_binaries()

        with tempfile.TemporaryDirectory() as tmpdir:
            root_path = copy_warc_fixture_root(Path(tmpdir))
            config_path = root_path / "warc_sources" / "allowed-warc.toml"
            with temporary_postgres() as postgres:
                apply_migrations(
                    default_rdbms_root(),
                    postgres.psql_args,
                    psql_command=postgres.psql_command,
                )
                exit_code, stdout, stderr = run_repo_map_in_process(
                    "sources",
                    "import-warc",
                    "--config",
                    str(config_path),
                    "--root-path",
                    str(root_path),
                    "--json",
                )
                raw_count = postgres.psql_scalar("SELECT count(*) FROM raw_observations;")

        self.assertEqual(exit_code, 0, stderr)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["source_id"], "example-warc-archive")
        self.assertEqual(payload["source_type"], "saved_page.archive")
        self.assertEqual(payload["record_count"], 8)
        self.assertEqual(payload["routed_payloads"], 4)
        self.assertGreater(payload["observations"], 0)
        self.assertEqual(payload["publication"]["publication_state"], "not_published")
        self.assertEqual(raw_count, "0")
