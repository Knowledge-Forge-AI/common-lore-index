from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common_lore_index._utf8 import byte_length
from common_lore_index.storage.row_spool import RowSpool, RowSpoolWriter


class RowSpoolUnitTests(unittest.TestCase):
    def test_replays_rows_and_cleans_up_private_file(self):
        rows = (
            {"stage_id": "stage-1", "family_ordinal": 0, "value": 1},
            {"stage_id": "stage-1", "family_ordinal": 1, "value": True},
        )

        with RowSpool.from_rows(rows) as spool:
            path = spool.path
            self.assertEqual(len(spool), len(rows))
            self.assertEqual(tuple(spool), rows)
            self.assertEqual(tuple(spool), rows)
            self.assertEqual(spool.byte_count, path.stat().st_size)
            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode),
                stat.S_IRUSR | stat.S_IWUSR,
            )

        self.assertFalse(path.exists())

    def test_wide_unicode_row_preserves_encoded_byte_count(self):
        row = {"value": "é楚😀" * 30000}
        encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode() + b"\n"
        with RowSpool.from_rows((row,)) as spool:
            self.assertEqual(spool.path.read_bytes(), encoded)
            self.assertEqual(tuple(spool), (row,))
        observed: list[int] = []
        with RowSpoolWriter(artifact_observer=observed.append) as writer:
            writer.write_row(row)
            with writer.finish() as spool:
                self.assertEqual(spool.path.read_bytes(), encoded)
                self.assertEqual(spool.byte_count, len(encoded))
                self.assertEqual(tuple(spool), (row,))
        self.assertEqual(observed, [len(encoded)])

    def test_chunked_utf8_preserves_replacement_and_strict_error_offsets(self):
        text = "é" * 16383 + "\ud800\ud801" + "😀" * 9000
        self.assertEqual(byte_length(text, errors="replace"), len(text.encode("utf-8", "replace")))
        self.assertEqual(byte_length(text, errors="surrogatepass"), len(text.encode("utf-8", "surrogatepass")))
        with self.assertRaises(UnicodeEncodeError) as expected:
            text.encode("utf-8")
        with self.assertRaises(UnicodeEncodeError) as actual:
            byte_length(text)
        self.assertEqual(actual.exception.args, expected.exception.args)

    def test_strict_utf8_boundary_errors_match_the_full_encoder(self):
        with patch("common_lore_index._utf8._CHUNK_CHARACTERS", 4):
            for value in (
                "aaa\ud800b", "aaa\ud800\udfff", "aaaa\ud800b",
                "aa\ud800\udfffb", "aa\ud800a\udfff", "楚😀a\ud800\udfff",
            ):
                with self.subTest(value=value):
                    with self.assertRaises(UnicodeEncodeError) as expected:
                        value.encode("utf-8")
                    with self.assertRaises(UnicodeEncodeError) as actual:
                        byte_length(value)
                    self.assertIs(actual.exception.object, value)
                    self.assertEqual(actual.exception.args, expected.exception.args)

    def test_closed_spool_rejects_replay(self):
        spool = RowSpool.from_rows(({"value": "test"},))
        spool.close()

        with self.assertRaisesRegex(RuntimeError, "spool is closed"):
            tuple(spool)

    def test_rejects_non_mapping_rows_without_leaking_file(self):
        with self.assertRaisesRegex(ValueError, "staged row is invalid"):
            RowSpool.from_rows(({"value": "ok"}, "invalid"))  # type: ignore[arg-type]

    def test_artifact_growth_is_observed_before_spool_bytes_are_written(self):
        samples = []
        spool_path = None

        with tempfile.TemporaryDirectory() as directory:
            original_mkstemp = tempfile.mkstemp

            def private_mkstemp(*, prefix, suffix):
                nonlocal spool_path
                descriptor, raw_path = original_mkstemp(
                    prefix=prefix,
                    suffix=suffix,
                    dir=directory,
                )
                spool_path = Path(raw_path)
                return descriptor, raw_path

            def reject_growth(projected_bytes):
                samples.append(projected_bytes)
                assert spool_path is not None
                self.assertEqual(spool_path.stat().st_size, 0)
                raise MemoryError("artifact ceiling")

            with patch(
                "common_lore_index.storage.row_spool.tempfile.mkstemp",
                private_mkstemp,
            ):
                with self.assertRaisesRegex(MemoryError, "artifact ceiling"):
                    RowSpool.from_rows(
                        ({"value": "private"},),
                        artifact_observer=reject_growth,
                    )

            self.assertTrue(samples)
            assert spool_path is not None
            self.assertFalse(spool_path.exists())
