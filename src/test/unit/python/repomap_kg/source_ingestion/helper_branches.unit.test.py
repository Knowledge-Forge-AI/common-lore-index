import hashlib
import unittest
from dataclasses import replace
from io import BufferedReader, BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping, cast
from unittest.mock import patch


from repomap_test_support.source_ingestion import SourceIngestionUnitTestCase

from common_lore_index.ops.ingestion import _source_warc_manifest as warc
from common_lore_index.ops.ingestion import source
from common_lore_index.ops.ingestion import source_common
from common_lore_index.ops.ingestion.source_warc import _parse_header_lines, _read_warc_record


class SourceIngestionHelperBranchUnitTests(unittest.TestCase):
    def test_streamed_source_manifest_identity_equals_the_compact_json_digest(self):
        payload = {"ζ": {"nested": [True, None, -320, 0.25]}, "a": "輸入\0\n"}
        serialized = source.json_dumps_stable(payload).encode("utf-8")
        self.assertEqual(source_common._source_manifest_id(payload), hashlib.sha256(serialized).hexdigest()[:16])

    def test_streamed_manifest_record_arrays_preserve_canonical_order_and_escaping(self) -> None:
        payload = {"status": "allowed", "route": None, "info": {"n": 3}}
        items: list[dict[str, Any]] = [{"path": 'é/"\n', "sizes": [0, 1]}, {"path": "輸入", "sizes": []}]
        computed = source_common._source_manifest_id(
            payload, records={"ordered": iter(items), "omitted": iter(())},
        )
        serialized = source.json_dumps_stable({**payload, "ordered": items, "omitted": []}).encode()
        self.assertEqual(computed, hashlib.sha256(serialized).hexdigest()[:16])
        with self.assertRaisesRegex(ValueError, "duplicate source manifest field"):
            source_common._source_manifest_id(payload, records={"status": iter(items)})

    def test_warc_record_reader_skips_oversized_payload_io(self):
        class CountedFile(BytesIO):
            bytes_read = 0

            def readinto(self, buffer: Any) -> int:
                count = super().readinto(buffer)
                self.bytes_read += count
                return count

        body = b"x" * (1024 * 1024)
        header = f"WARC/1.0\r\nContent-Length: {len(body)}".encode()
        first = header + b"\r\n\r\n" + body
        second = b"WARC/1.1\nContent-Length: 2\n\n{}"
        raw = CountedFile(first + second)
        with BufferedReader(raw) as reader:
            skipped = _read_warc_record(reader, len(first + second), 512)
            self.assertEqual(skipped, ("WARC/1.0", {"content-length": str(len(body))}, len(header) + len(body), None))
            self.assertEqual(reader.tell(), len(first))
            accepted = _read_warc_record(reader, len(first + second), 512)
            self.assertIsInstance(accepted, tuple)
            assert isinstance(accepted, tuple)
            self.assertEqual(accepted[-1], b"{}")
            self.assertLess(raw.bytes_read, 32768)

    def test_warc_header_continuations_preserve_duplicate_and_whitespace_policy(self) -> None:
        headers = _parse_header_lines(iter((
            "ETag: first", "  fragment", "discard this", "  next", "",
            "Other: another", "etag: replacement", "    ", "  final", "discard this", "\tlast ",
        )))
        self.assertEqual(list(headers.items()), [
            ("etag", "replacement  final last"),
            ("other", "another"),
        ])

    def test_source_ingestion_validation_helpers_cover_safe_error_branches(self):
        self.assertEqual(source._mapping({"source": {"id": "x"}}, "source"), {"id": "x"})
        with self.assertRaises(source.SourcePolicyError):
            source._mapping({"source": []}, "source")

        self.assertEqual(source._required_text({"id": " source "}, "id", "id"), "source")
        payload: Mapping[str, object]
        for payload in ({}, {"id": ""}, {"id": 7}):
            with self.subTest(payload=payload):
                with self.assertRaises(source.SourcePolicyError):
                    source._required_text(payload, "id", "id")
        self.assertIsNone(source._optional_text({}, "id"))
        self.assertEqual(source._optional_text({"id": " value "}, "id"), "value")
        self.assertIsNone(source._optional_text({"id": "   "}, "id"))
        with self.assertRaises(source.SourcePolicyError):
            source._optional_text({"id": 7}, "id")

        self.assertEqual(source._required_positive_int({"limit": 1}, "limit", "limit"), 1)
        for payload in ({}, {"limit": True}, {"limit": 0}):
            with self.subTest(payload=payload):
                with self.assertRaises(source.SourcePolicyError):
                    source._required_positive_int(payload, "limit", "limit")
        self.assertEqual(source._positive_int_or_default(None, "limit", default=3), 3)
        with self.assertRaises(source.SourcePolicyError):
            source._positive_int_or_default(None, "limit", default=None)
        for value in (False, -1):
            with self.subTest(value=value):
                with self.assertRaises(source.SourcePolicyError):
                    source._positive_int_or_default(value, "limit", default=3)

        self.assertTrue(source._required_bool({"flag": True}, "flag", "flag"))
        with self.assertRaises(source.SourcePolicyError):
            source._required_bool({}, "flag", "flag")
        self.assertFalse(source._optional_bool(None, "flag", default=False))
        with self.assertRaises(source.SourcePolicyError):
            source._optional_bool("true", "flag", default=False)

        source._validate_source_id("safe-id")
        for source_id in ("https://example.invalid/feed", "bad id"):
            with self.subTest(source_id=source_id):
                with self.assertRaises(source.SourcePolicyError):
                    source._validate_source_id(source_id)
        source._validate_source_type("feed.rss")
        with self.assertRaises(source.SourcePolicyError):
            source._validate_source_type("unknown")
        source._validate_archive_source_type("local.file")
        with self.assertRaises(source.SourcePolicyError):
            source._validate_archive_source_type("feed.rss")
        source._validate_warc_source_type("saved_page.archive")
        with self.assertRaises(source.SourcePolicyError):
            source._validate_warc_source_type("feed.rss")
        source._validate_policy_status("allowed")
        with self.assertRaises(source.SourcePolicyError):
            source._validate_policy_status("blocked_terms_risk")
        with self.assertRaises(source.SourcePolicyError):
            source._validate_policy_status("unknown")

        source._validate_url("https://example.invalid/feed")
        for url in ("ftp://example.invalid/feed", "https:///feed", "https://u:p@example.invalid/feed"):
            with self.subTest(url=url):
                with self.assertRaises(source.SourcePolicyError):
                    source._validate_url(url)
        source._validate_method("get")
        with self.assertRaises(source.SourcePolicyError):
            source._validate_method("POST")
        source._validate_local_artifact_path("archive.zip")
        for path in ("https://example.invalid/archive.zip", "//server/archive.zip"):
            with self.subTest(path=path):
                with self.assertRaises(source.SourcePolicyError):
                    source._validate_local_artifact_path(path)

        source._reject_archive_network_fields({"artifact": {"path": "archive.zip"}})
        for payload in (
            {"acquisition": {"url": "https://example.invalid/archive.zip"}},
            {"artifact": {"url": "archive.zip"}},
            {"artifact": {"path": "https://example.invalid/archive.zip"}},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(source.SourcePolicyError):
                    source._reject_archive_network_fields(payload)

        source._validate_disallowed_flags({"policy": {"requires_login": False}})
        with self.assertRaises(source.SourcePolicyError):
            source._validate_disallowed_flags({"policy": {"requires_login": True}})
        self.assertEqual(
            source._secret_key_paths(
                {"credentials": {"api_token": "x"}, "safe": {"name": "repo"}},
            ),
            ["credentials", "credentials.api_token"],
        )
        self.assertEqual(
            source._flatten_mapping({"a": {"b": 1}, "c": 2}),
            [("a", {"b": 1}), ("a.b", 1), ("c", 2)],
        )
        self.assertEqual(source._artifact_filename("feed.json"), "feed.json")
        self.assertEqual(source._artifact_filename("feed.atom"), "atom.xml")
        self.assertEqual(source._artifact_filename("feed.rss"), "rss.xml")
        self.assertEqual(
            source._safe_url_summary("https://example.invalid/feed?token=secret#frag"),
            "https://example.invalid/feed",
        )
        self.assertEqual(
            source._content_type({"Content-Type": "application/json"}),
            "application/json",
        )
        self.assertIsNone(source._content_type({}))
        self.assertEqual(source._header_mapping({"X-Test": "1"}), {"x-test": "1"})
        self.assertEqual(source._header_mapping([]), {})

    def test_source_archive_route_and_media_type_helpers_cover_suffix_matrix(self):
        route_cases = {
            "index.html": "html",
            "style.css": "css",
            "data.json": "config-or-feed",
            "data.jsonc": "config-or-feed",
            "data.jsonl": "config-or-feed",
            "config.toml": "config-or-feed",
            "policy.plist": "config-or-feed",
            "feed.xml": "config-or-feed",
            "README.md": "markdown",
            "script.sh": "shell",
            "module.py": "python",
            "flake.nix": "nix",
            "app.tsx": "javascript",
            "image.png": "file",
        }
        for path, route in route_cases.items():
            with self.subTest(path=path):
                self.assertEqual(source._archive_extractor_route(Path(path)), route)

        media_cases = {
            "index.htm": "text/html",
            "style.css": "text/css",
            "data.json": "application/json",
            "data.jsonc": "application/jsonc",
            "events.jsonl": "application/jsonl",
            "config.toml": "application/toml",
            "policy.plist": "application/xml",
            "feed.xml": "application/xml",
            "README.markdown": "text/markdown",
            "app.mjs": "text/javascript",
            "app.cts": "text/typescript",
            "image.png": "application/octet-stream",
        }
        for path, media_type in media_cases.items():
            with self.subTest(path=path):
                self.assertEqual(source._archive_media_type(Path(path)), media_type)

    def test_source_warc_helpers_cover_target_header_and_payload_branches(self):
        target_cases = {
            None: (None, None, False),
            "": (None, None, False),
            "https://example.invalid/page?token=secret#frag": (
                "https://example.invalid/page?token=<redacted>#frag",
                True,
            ),
            "http://user:pass@example.invalid:8080/page": (
                "http://example.invalid:8080/page",
                True,
            ),
            "mailto:ops@example.invalid": ("mailto:ops@example.invalid", False),
            "javascript:alert(1)": ("javascript:alert(1)", False),
            "${dynamic}": ("${dynamic}", False),
            "file:///tmp/report.html": ("file:///tmp/report.html", False),
            "/absolute/path.html": ("/absolute/path.html", False),
            "relative/page.html": ("relative/page.html", False),
            "../parent/page.html": ("../parent/page.html", False),
            "ftp://example.invalid/file": ("ftp://example.invalid/file", False),
        }
        for uri, expected in target_cases.items():
            with self.subTest(uri=uri):
                summary, target_key, redacted = source._warc_target(uri)
                if uri in (None, ""):
                    self.assertIsNone(summary)
                    self.assertIsNone(target_key)
                    self.assertFalse(redacted)
                    continue
                expected_summary, expected_redacted = expected
                self.assertEqual(summary, expected_summary)
                self.assertEqual(redacted, expected_redacted)
                self.assertIsInstance(target_key, str)

        self.assertEqual(
            source._parse_header_lines(
                [
                    "Content-Type: text/html",
                    "\tcharset=utf-8",
                    "Malformed",
                    "",
                    "WARC-Type: response",
                ]
            ),
            {
                "content-type": "text/html charset=utf-8",
                "warc-type": "response",
            },
        )
        self.assertEqual(
            source._safe_warc_headers(
                {
                    "WARC-Type": "response",
                    "WARC-Target-URI": "https://example.invalid/?token=secret",
                    "Authorization": "Bearer secret",
                    "X-Ignored": "ignored",
                }
            ),
            {
                "authorization": "<redacted>",
                "warc-target-uri": "https://example.invalid/?token=<redacted>",
                "warc-type": "response",
            },
        )

        response_headers, response_body = source._parse_http_message_payload(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<html/>",
            response=True,
        )
        self.assertEqual(response_headers["content-type"], "text/html")
        self.assertEqual(response_body, b"<html/>")
        request_headers, request_body = source._parse_http_message_payload(
            b"GET / HTTP/1.1\nHost: example.invalid\n\nbody",
            response=False,
        )
        self.assertEqual(request_headers["host"], "example.invalid")
        self.assertEqual(request_body, b"body")
        self.assertEqual(
            source._parse_http_message_payload(b"no headers", response=True),
            ({}, b""),
        )
        self.assertEqual(
            source._parse_http_message_payload(
                b"GET / HTTP/1.1\r\nHost: example.invalid\r\n\r\nbody",
                response=True,
            ),
            ({}, b""),
        )


def test_warc_http_body_offset_preserves_raw_bytes_and_legacy_empty_result():
    from common_lore_index.ops.ingestion.source_warc import _http_headers_and_body_offset

    body = b"\x00\xff\r\n"
    block = b"HTTP/1.0 200 OK\r\nX-Test: synthetic\r\n\r\n" + body
    headers, offset = _http_headers_and_body_offset(block, response=True)
    view = memoryview(block)[offset:]
    assert view.obj is block and view.tobytes() == body
    assert headers == {"x-test": "synthetic"}
    malformed = b"not-http\n\n" + body
    assert _http_headers_and_body_offset(malformed, response=True) == ({}, len(malformed))


class RetainedWarcPayloadUnitTests(SourceIngestionUnitTestCase):
    def test_retained_warc_payload_limits_apply_before_any_body_is_opened(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.write_warc_fixture(root)
            config = warc.load_warc_source_config(self.write_warc_config(root / "source.toml"))
            manifest = warc.build_warc_manifest(config, root_path=root, clock=self.fixed_clock)
            self.assertGreater(manifest.routed_payload_count, 1)
            with patch.object(warc, "open_source_body", side_effect=AssertionError("body opened")):
                for limited in (
                    replace(config, max_file_count=1),
                    replace(config, max_record_bytes=1),
                    replace(config, max_total_payload_bytes=1),
                ):
                    with self.subTest(limited=limited):
                        with self.assertRaisesRegex(warc.SourcePolicyError, "payloads exceed source policy"):
                            warc.warc_observations_from_manifest(limited, manifest, root_path=root)
                last = next(record for record in reversed(manifest.records) if record.materialized_path)
                for length in (-1, True, "1"):
                    with self.subTest(length=length):
                        changed = tuple(
                            replace(record, payload_byte_length=cast(int, length)) if record is last else record
                            for record in manifest.records
                        )
                        with self.assertRaisesRegex(warc.SourcePolicyError, "invalid retained WARC payload length"):
                            warc.warc_observations_from_manifest(config, replace(manifest, records=changed), root_path=root)

    def test_retained_warc_payload_refuses_executable_and_linked_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.write_warc_fixture(root)
            config = warc.load_warc_source_config(self.write_warc_config(root / "source.toml"))
            manifest = warc.build_warc_manifest(config, root_path=root, clock=self.fixed_clock)
            payload = root / next(record.materialized_path for record in manifest.records if record.materialized_path)
            payload.chmod(0o744)
            with self.assertRaisesRegex(warc.SourcePolicyError, "payload differs from its manifest"):
                warc.warc_observations_from_manifest(config, manifest, root_path=root)
            payload.chmod(0o644)
            alias = root / "payload-alias"
            alias.hardlink_to(payload)
            with self.assertRaisesRegex(warc.SourcePolicyError, "payload differs from its manifest"):
                warc.warc_observations_from_manifest(config, manifest, root_path=root)
            alias.unlink()
            payload.rename(alias)
            payload.symlink_to(alias)
            with self.assertRaisesRegex(warc.SourcePolicyError, "payload differs from its manifest"):
                warc.warc_observations_from_manifest(config, manifest, root_path=root)

    def test_retained_warc_payloads_refuse_noncanonical_and_duplicate_references(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.write_warc_fixture(root)
            config = warc.load_warc_source_config(self.write_warc_config(root / "source.toml"))
            manifest = warc.build_warc_manifest(config, root_path=root, clock=self.fixed_clock)
            record = next(item for item in manifest.records if item.materialized_path is not None)
            payload = Path(str(record.materialized_path))
            for path in (f"/{payload}", f"../{payload}", f"bad//{payload.name}", "payload.py"):
                with self.subTest(path=path):
                    changed = replace(record, materialized_path=path)
                    with self.assertRaisesRegex(warc.SourcePolicyError, "invalid retained WARC payload reference"):
                        warc.warc_observations_from_manifest(config, replace(manifest, records=(changed,)), root_path=root)
            for changed in (
                replace(record, extractor_route="python"),
                replace(record, content_type="text/x-python"),
            ):
                with self.subTest(route=changed.extractor_route, content_type=changed.content_type):
                    with self.assertRaisesRegex(warc.SourcePolicyError, "invalid retained WARC payload reference"):
                        warc.warc_observations_from_manifest(config, replace(manifest, records=(changed,)), root_path=root)
            with self.assertRaisesRegex(warc.SourcePolicyError, "duplicate retained WARC payload reference"):
                warc.warc_observations_from_manifest(config, replace(manifest, records=(record, record)), root_path=root)
