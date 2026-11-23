from __future__ import annotations

import hashlib
import json
import os
import threading

from common_lore_index.ops import source_generation
from common_lore_index.graph.discovery import discover_observations
from common_lore_index.ops.generations import source_generation as observation_generation
from common_lore_index.ops.generations import source_generation_records


def test_scan_source_generation_is_content_aware_and_path_independent(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        (root / "src").mkdir(parents=True)
        (root / "README.md").write_text("# Repo\n", encoding="utf-8")
        (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

    first_result = source_generation.scan_source_generation(first)
    second_result = source_generation.scan_source_generation(second)

    assert first_result.category == "ready"
    assert first_result.generation == second_result.generation
    assert first_result.file_count == 2
    assert first_result.total_bytes == len("# Repo\n") + len("print('ok')\n")
    assert first_result.to_public() == {
        "category": "ready",
        "file_count": 2,
        "total_bytes": first_result.total_bytes,
    }


def test_scan_source_generation_matches_full_discovery_generation(tmp_path):
    root = tmp_path / "repository"
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("# Repo\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

    polled = source_generation.scan_source_generation(root)
    refreshed = observation_generation(discover_observations(root))

    assert polled.generation == refreshed


def test_source_generation_records_sort_paths_and_entry_types():
    first = source_generation_records(
        (
            ("b.txt", "file", "digest-b"),
            ("a.txt", "file", "digest-a"),
        )
    )
    second = source_generation_records(
        (
            ("a.txt", "file", "digest-a"),
            ("b.txt", "file", "digest-b"),
        )
    )

    assert first == second


def test_scan_source_generation_is_order_independent_and_exclusion_aware(tmp_path):
    root = tmp_path / "repository"
    (root / "src").mkdir(parents=True)
    (root / "ignored").mkdir()
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "README.md").write_text("# Repo\n", encoding="utf-8")
    (root / "ignored" / "secret.txt").write_text("secret\n", encoding="utf-8")

    before = source_generation.scan_source_generation(root, exclude_paths=("ignored",))
    (root / "ignored" / "secret.txt").write_text("changed\n", encoding="utf-8")
    after = source_generation.scan_source_generation(root, exclude_paths=("ignored",))

    assert before.generation == after.generation
    assert before.file_count == 2

    (root / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8")
    changed = source_generation.scan_source_generation(root, exclude_paths=("ignored",))
    assert changed.generation != before.generation


def test_scan_source_generation_applies_glob_excludes_to_directory_contents(tmp_path):
    root = tmp_path / "repository"
    (root / "result-build" / "nested").mkdir(parents=True)
    (root / "result-build" / "nested" / "generated.txt").write_text(
        "generated\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# Repo\n", encoding="utf-8")

    result = source_generation.scan_source_generation(root, exclude_paths=("result-*",))

    assert result.category == "ready"
    assert result.file_count == 1


def test_scan_source_generation_preserves_unavailable_source_as_condition(tmp_path):
    result = source_generation.scan_source_generation(tmp_path / "missing")

    assert result.generation is None
    assert result.category == "source_unavailable"
    assert result.to_public() == {
        "category": "source_unavailable",
        "file_count": 0,
        "total_bytes": 0,
    }


def test_scan_source_generation_rejects_symlinked_entries(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "README.md").write_text("# Repo\n", encoding="utf-8")
    try:
        (root / "linked.md").symlink_to(root / "README.md")
    except OSError as error:
        import pytest

        pytest.skip(f"symlink creation unavailable: {error}")

    result = source_generation.scan_source_generation(root)

    assert result.generation is None
    assert result.category == "source_invalid"


def test_scan_source_generation_honors_bounds_and_cancellation(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "large.txt").write_text("0123456789", encoding="utf-8")

    limited = source_generation.scan_source_generation(
        root,
        limits=source_generation.SourceGenerationLimits(max_file_bytes=4),
    )
    assert limited.category == "source_limit_exceeded"
    assert limited.generation is None

    cancelled = threading.Event()
    cancelled.set()
    result = source_generation.scan_source_generation(root, cancel_event=cancelled)
    assert result.category == "cancelled"
    assert result.generation is None

    (root / "second.txt").write_text("abcdefghij", encoding="utf-8")
    total_limited = source_generation.scan_source_generation(
        root,
        limits=source_generation.SourceGenerationLimits(
            max_file_bytes=20, max_total_bytes=15
        ),
    )
    assert total_limited.category == "source_limit_exceeded"


def test_scan_source_generation_reports_persistent_file_instability(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    root.mkdir()
    source = root / "README.md"
    source.write_text("# Repo\n", encoding="utf-8")
    monkeypatch.setattr(
        source_generation,
        "_read_stable_file",
        lambda *args, **kwargs: None,
    )

    result = source_generation.scan_source_generation(
        root,
        limits=source_generation.SourceGenerationLimits(max_file_retries=1),
    )

    assert result.category == "source_unstable"
    assert result.generation is None


def test_nested_source_budget_refuses_before_opening_the_next_subtree_file(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    allowed = root / "a" / "first.txt"
    denied = root / "b" / "second.txt"
    allowed.write_bytes(b"12345678")
    denied.write_bytes(b"12345678")
    real_open = source_generation.os.open
    opened = []

    def record_open(path, *args, **kwargs):
        opened.append(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(source_generation.os, "open", record_open)
    result = source_generation.scan_source_generation(
        root, limits=source_generation.SourceGenerationLimits(max_file_bytes=16, max_total_bytes=15),
    )
    assert result.category == "source_limit_exceeded"
    assert allowed in opened and denied not in opened


def test_source_generation_refuses_descriptor_replacement_before_reading(tmp_path, monkeypatch):
    path = tmp_path / "source.txt"
    other = tmp_path / "replacement.txt"
    path.write_bytes(b"before")
    other.write_bytes(b"after!")
    real_open, real_fdopen = source_generation.os.open, source_generation.os.fdopen

    def replace_at_open(requested, *args, **kwargs):
        if requested == path:
            other.replace(path)
        return real_open(requested, *args, **kwargs)

    class NoRead:
        def __init__(self, stream):
            self.stream = stream

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            raise AssertionError("read from an unexpected inode")

        def __enter__(self):
            return self

        def __exit__(self, *_exception):
            self.stream.close()

    monkeypatch.setattr(source_generation.os, "open", replace_at_open)
    monkeypatch.setattr(source_generation.os, "fdopen", lambda *a, **kw: NoRead(real_fdopen(*a, **kw)))
    result = source_generation._read_stable_file(
        path, source_generation.SourceGenerationLimits(max_file_retries=0),
        None, lambda: 0.0, 1.0, max_bytes=100,
    )
    assert result is None


def test_source_scan_checks_cancellation_during_directory_enumeration(tmp_path, monkeypatch):
    source = tmp_path / "fixture.txt"
    source.write_text("fixture")
    cancelled = threading.Event()
    real_scandir = os.scandir

    class CancellingDirectory:
        def __init__(self, repository):
            self.iterator = real_scandir(repository)

        def __enter__(self):
            return self

        def __exit__(self, *_exception):
            self.iterator.close()

        def __iter__(self):
            cancelled.set()
            yield next(self.iterator)
            raise AssertionError("enumeration continued after cancellation")

    monkeypatch.setattr(source_generation.os, "scandir", CancellingDirectory)
    result = source_generation.scan_source_generation(tmp_path, cancel_event=cancelled)
    assert result.category == "cancelled"


def test_source_growth_is_bounded_by_one_lookahead_byte(tmp_path, monkeypatch):
    source = tmp_path / "fixture.txt"
    source.write_bytes(b"1234")
    with source.open("ab", buffering=0) as writer:
        real_fdopen = source_generation.os.fdopen
        requests = []

        class GrowingStream:
            def __init__(self, stream):
                self.stream = stream

            def fileno(self):
                return self.stream.fileno()

            def read(self, size):
                requests.append(size)
                if len(requests) == 1:
                    writer.write(b"567890123456")
                return self.stream.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *_exception):
                self.stream.close()

        monkeypatch.setattr(source_generation.os, "fdopen", lambda *a, **kw: GrowingStream(real_fdopen(*a, **kw)))
        result = source_generation.scan_source_generation(
            tmp_path, limits=source_generation.SourceGenerationLimits(max_file_bytes=8),
        )
    assert result.category == "source_limit_exceeded"
    assert requests == [5]


def test_source_inventory_digest_preserves_exact_framing_for_order_and_escapes():
    records = [
        ('pkg/quote".py', "file", "a"),
        ("pkg/é.py", "file", "b"),
        ("pkg/é.py", "file", "a"),
        ("pkg/é.py", "test", "b"),
    ]
    for entries in (records, []):
        expected_bytes = json.dumps({
            "algorithm": "source-inventory-v1",
            "records": [
                {"path": path, "entry_type": entry_type, "content_digest": content}
                for path, entry_type, content in sorted(entries)
            ],
        }, sort_keys=True, separators=(",", ":")).encode()
        assert source_generation_records(iter(entries)) == "sg1:" + hashlib.sha256(expected_bytes).hexdigest()
