"""Selected SQLite Local source reads hydrate only their own evidence links."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from common_lore_index.storage.errors import StorageSchemaError
from common_lore_index.storage.sqlite_local import source_feed_queries, source_queries
from common_lore_index.storage.sqlite_local.connection import initialize_graph_database, read_transaction
from common_lore_index.storage.sqlite_local.publisher import publish_generation
from common_lore_index_test_support.sqlite_local_feed_corpus import (
    ATOM, RSS, acquire_feed_runs, feed_bundle, generation_observations,
)
from common_lore_index_test_support.sqlite_local_fixtures import local_binding, publication_for


@pytest.fixture
def published_feeds(tmp_path: Path) -> Path:
    name = "source-selection"
    path = tmp_path / "feeds.sqlite3"
    initialize_graph_database(path, local_binding(name), applied_at="2026-09-29T00:00:00Z")
    runs = acquire_feed_runs(tmp_path / "feeds")
    for generation in (1, 2):
        bundle = feed_bundle(name, generation, generation_observations(runs, generation))
        publish_generation(path, publication_for(bundle, name), bundle.families,
                           expected_generation=generation - 1)
    return path


@pytest.mark.parametrize("operation,source_id", [
    ("inventory", ATOM), ("summary", RSS), ("items", RSS), ("references", RSS),
    ("items", "no-such-source"), ("references", "no-such-source"),
])
def test_selected_read_keeps_link_hydration_within_source(
    published_feeds: Path, operation: str, source_id: str,
) -> None:
    with read_transaction(published_feeds, local_binding("source-selection")) as connection:
        selected_keys = {
            row.key for row in source_queries.source_observations(connection) if row.source_id == source_id
        }
        selected_ids = {
            evidence_id for run_id, ordinal, evidence_id in connection.execute(
                "SELECT run_id, raw_observation_ordinal, id FROM canonical_evidence"
            )
            if (run_id, ordinal) in selected_keys
        }
        fetched: set[int] = set()
        transferred: set[tuple[int, int]] = set()

        def observe(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> tuple[Any, ...]:
            if cursor.description and cursor.description[0][0] == "canonical_evidence_id":
                fetched.add(int(row[0]))
            if cursor.description and tuple(column[0] for column in cursor.description) == (
                "run_id", "raw_observation_ordinal", "id",
            ):
                transferred.add((row[0], row[1]))
            return row

        connection.row_factory = observe
        if operation == "inventory":
            page = source_queries.ingested_sources(connection, source_type=None, policy_status=None, limit=1)
            assert [row.source_id for row in page] == [ATOM]
        elif operation == "summary":
            assert source_queries.source_summary(connection, source_id=source_id).feed_items > 0
        elif operation == "items":
            items = source_feed_queries.source_feed_items(
                connection, source_id=source_id, source_run_id=None, limit=1
            )
            assert bool(items) == bool(selected_keys)
        else:
            references = source_feed_queries.source_references(
                connection, source_id=source_id, source_run_id=None, target_kind=None, limit=1
            )
            assert bool(references) == bool(selected_keys)
        assert transferred <= selected_keys
        assert bool(transferred) == bool(selected_ids)
        assert fetched <= selected_ids
        assert bool(fetched) == bool(selected_ids)


def test_source_and_run_filters_precede_metadata_projection(published_feeds: Path, monkeypatch) -> None:
    with read_transaction(published_feeds, local_binding("source-selection")) as connection:
        all_rows = source_queries.source_observations(connection)
        run_id = next(row.source_run_id for row in all_rows if row.source_id == RSS and row.source_run_id)
        selected = [row for row in all_rows if row.source_id == RSS and row.source_run_id == run_id]
        assert 0 < len(selected) < len(all_rows)
        text_at = source_queries.raw_payload.text_at
        projected = []

        def observe(metadata, *fields):
            if fields == ("source_display_name",):
                projected.append((text_at(metadata, "source_id_configured"), text_at(metadata, "source_run_id")))
            return text_at(metadata, *fields)

        monkeypatch.setattr(source_queries.raw_payload, "text_at", observe)
        assert source_queries.source_observations(connection, source_id=RSS, source_run_id=run_id) == selected
        assert projected == [(RSS, run_id)] * len(selected)


def test_inventory_releases_evicted_groups_and_retains_selected_rows(published_feeds, monkeypatch):
    from weakref import ref

    with read_transaction(published_feeds, local_binding("source-selection")) as connection:
        sample = source_queries.source_observations(connection, source_id=RSS)[0]

        def observations(*_):
            outside = replace(sample, source_id="zzz")
            reference = ref(outside)
            yield outside
            del outside
            yield replace(sample, source_id="a", artifact_id="early", acquired_at="2026-02-01")
            assert reference() is None
            yield replace(sample, source_id="zzz")
            yield replace(sample, source_id="a", artifact_id="latest", acquired_at="2026-02-02")

        monkeypatch.setattr(source_queries, "_iter_source_observations", observations)
        monkeypatch.setattr(source_queries, "evidence_ids", lambda *_: {})
        monkeypatch.setattr(source_queries, "node_links", lambda *_: {})
        (chosen,) = source_queries.ingested_sources(connection, source_type=None, policy_status=None, limit=1)
        assert chosen.source_id == "a"
        assert chosen.latest_artifact_id == "latest"
        assert chosen.feed_observation_count == 2

        rows = [replace(sample, source_id=source_id) for source_id in ("c", "a", "d", "b", "c", "a")]
        monkeypatch.setattr(source_queries, "_iter_source_observations", lambda *_: iter(rows))
        page = source_queries.ingested_sources(connection, source_type=None, policy_status=None, limit=2)
        assert [(row.source_id, row.feed_observation_count) for row in page] == [("a", 2), ("b", 1)]


def test_run_page_preserves_ties_and_validates_unreturned_runs(published_feeds: Path, monkeypatch) -> None:
    with read_transaction(published_feeds, local_binding("source-selection")) as connection:
        sample = source_queries.source_observations(connection, source_id=RSS)[0]
        dated = replace(sample, source_run_id="b", acquired_at="2026-02-01T00:00:00Z")
        undated = replace(sample, source_run_id="z", acquired_at=None)
        rows = [replace(dated, source_run_id="a"), undated, replace(dated, artifact_bytes="2", http_status="201"),
                replace(dated, kind="feed.parse_error", artifact_bytes="10", http_status="200")]
        monkeypatch.setattr(source_queries, "_iter_source_observations", lambda *_: iter(rows))

        page = source_queries.source_runs(connection, source_id=RSS, limit=2)
        assert [row.source_run_id for row in page] == ["b", "a"]
        assert (page[0].artifact_byte_length, page[0].http_status, page[0].observation_count) == (10, 201, 2)
        assert page[0].status_summary == "parse_errors"
        assert source_queries.source_runs(connection, source_id=RSS, limit=1) == page[:1]
        rows[1] = replace(undated, artifact_bytes="12x")
        with pytest.raises(StorageSchemaError, match="not a valid integer"):
            source_queries.source_runs(connection, source_id=RSS, limit=1)


def test_reference_projection_only_reads_metadata_for_the_page(
    published_feeds: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with read_transaction(published_feeds, local_binding("source-selection")) as connection:
        full = source_feed_queries.source_references(
            connection, source_id=RSS, source_run_id=None, target_kind=None, limit=100,
        )
        assert len(full) > 1
        original = source_feed_queries.raw_payload.text_at
        projected_targets: list[Any] = []

        def observe(payload: Any, *keys: str) -> str | None:
            if keys == ("raw_target_summary",):
                projected_targets.append(payload)
            return original(payload, *keys)

        edge_ids: set[int] = set()

        def measure(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> tuple[Any, ...]:
            if tuple(column[0] for column in cursor.description) == ("id", "metadata_json"):
                edge_ids.add(row[0])
            return row

        assert connection.in_transaction
        identity_queries: list[str] = []

        def trace(sql: str) -> None:
            if "link.canonical_evidence_id, edge.id" in sql:
                identity_queries.append(sql)

        connection.set_trace_callback(trace)
        connection.row_factory = measure
        monkeypatch.setattr(source_feed_queries.raw_payload, "text_at", observe)
        assert source_feed_queries.source_references(
            connection, source_id=RSS, source_run_id=None, target_kind=None, limit=1,
        ) == full[:1]
        assert len(projected_targets) == len(edge_ids) == 1
        assert identity_queries and all("link.link_kind, NULL" in sql for sql in identity_queries)


def test_latest_source_aggregates_all_rows_of_the_tied_winner(published_feeds: Path) -> None:
    with read_transaction(published_feeds, local_binding("source-selection")) as connection:
        sample = source_queries.source_observations(connection)[0]
    recent = "2026-02-01T00:00:00Z"
    rows = [
        replace(sample, source_id="z", acquired_at=recent, artifact_path="z", source_run_id="r9"),
        replace(sample, source_id="a", acquired_at=recent, artifact_path="b", source_type=None, source_run_id="r3"),
        replace(sample, source_id="a", acquired_at=None, artifact_path="y", source_type="feed.rss", source_run_id="r2"),
        replace(sample, source_id="0", acquired_at=None, artifact_path="z"),
    ]
    selected, keys = source_feed_queries._explanation_source_scope(iter(rows))
    assert selected is not None and keys == {sample.key}
    assert (selected["source_id"], selected["acquired_at"], selected["artifact_path"]) == ("a", recent, "y")
    assert (selected["source_type"], selected["source_run_id"]) == ("feed.rss", "r3")
    assert source_feed_queries._explanation_source_scope(reversed(rows)) == (selected, keys)


def test_evidence_batches_keep_exact_keys_and_per_key_link_order() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(
            "CREATE TABLE canonical_evidence(id INTEGER PRIMARY KEY, run_id, raw_observation_ordinal);"
            "CREATE INDEX evidence_run ON canonical_evidence(run_id, raw_observation_ordinal);"
            "CREATE TABLE canonical_nodes(id INTEGER PRIMARY KEY, canonical_key, kind);"
            "CREATE TABLE canonical_node_evidence(canonical_evidence_id, canonical_node_id, link_kind);"
            "INSERT INTO canonical_nodes VALUES (1, 'item', 'feed.item');"
        )
        connection.executemany(
            "INSERT INTO canonical_evidence VALUES (?, ?, ?)",
            [*((i, 1, i) for i in range(1, 502)), (600, 1, 1), (601, 1, 800), (602, 2, 1)],
        )
        connection.executemany("INSERT INTO canonical_node_evidence VALUES (?, 1, 'observed')",
                               ((i,) for i in (*range(1, 502), 600, 601, 602)))
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 500)
        statements: list[str] = []
        connection.set_trace_callback(statements.append)
        assert source_queries.evidence_ids(connection, ()) == {}
        assert source_queries.node_links(connection, ()) == {}
        assert source_queries.edge_links(connection, ()) == {}
        assert not statements
        keys = [(1, i) for i in range(1, 502)]
        selected = source_queries.evidence_ids(connection, [*reversed(keys), keys[0]])
        assert set(selected) == set(keys) and selected[(1, 1)] == [1, 600]
        assert len(statements) == 1  # the large selection scans its run once
        links = source_queries.node_links(connection, (i for evidence in selected.values() for i in evidence))
        assert set(links) == {*range(1, 502), 600}
        assert all(len(nodes) == 1 and nodes[0].kind == "feed.item" for nodes in links.values())
        assert source_queries._node_counts(links, links, frozenset({"feed.item"})) == {"feed.item": 1}
        assert source_queries._node_counts(links, links, frozenset({"feed.author"})) == {}


def test_sparse_evidence_uses_bounded_exact_pairs_across_runs() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("CREATE TABLE canonical_evidence(id INTEGER PRIMARY KEY, run_id, raw_observation_ordinal)")
        connection.execute("CREATE INDEX evidence_run ON canonical_evidence(run_id)")
        connection.executemany(
            "INSERT INTO canonical_evidence VALUES (?, ?, ?)",
            ((run_id * 1000 + ordinal, run_id, ordinal) for run_id in range(1, 4) for ordinal in range(300)),
        )
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 500)
        keys = [(run_id, ordinal) for run_id in range(1, 4) for ordinal in range(200)]
        fetched = []

        def observe(_cursor, row):
            fetched.append(row[:2])
            return row

        connection.row_factory = observe
        evidence = source_queries.evidence_ids(connection, [*keys, keys[0], (9, 9)])
        assert set(evidence) == set(keys)
        assert sorted(fetched) == sorted(keys)



def test_feed_node_batches_stream_in_global_tie_order() -> None:
    keys = [(f"item-{(601 - index) // 2:03}", index) for index in range(1, 602)]
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute(
            "CREATE TABLE canonical_nodes(id INTEGER PRIMARY KEY, canonical_key, kind, metadata_json)"
        )
        connection.executemany("INSERT INTO canonical_nodes VALUES (?, ?, 'feed.item', ?)", (
            (index, key, json.dumps({"title": str(index)})) for key, index in keys
        ))
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 500)
        fetched: list[int] = []

        def observe(_cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> tuple[Any, ...]:
            fetched.append(int(row[0]))
            return row

        connection.row_factory = observe
        linked = {key: source_feed_queries._ItemProvenance() for key in keys}
        items = source_feed_queries._feed_item_nodes(connection, linked)
        first = next(items)
        assert len(fetched) == 1
        assert [(key, int(metadata["title"])) for key, metadata, _ in (first, *items)] == sorted(keys)


def test_feed_explanation_decodes_evidence_before_the_remaining_rows_are_read(
    published_feeds: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[int] = []
    decoded_after: list[int] = []
    decode = source_feed_queries._json

    def observe(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> tuple[Any, ...]:
        if cursor.description and "evidence_key" in {column[0] for column in cursor.description}:
            fetched.append(len(fetched) + 1)
        return row

    def track_decode(raw: str) -> Any:
        if fetched:
            decoded_after.append(len(fetched))
        return decode(raw)

    with read_transaction(published_feeds, local_binding("source-selection")) as connection:
        items = source_feed_queries.source_feed_items(connection, source_id=RSS, source_run_id=None, limit=1)
        item_key = items[0].item_key
        expected = source_feed_queries.source_feed_item_explanation(connection, item_key=item_key, source_id=RSS)
        assert len(expected["evidence"]) > 1
        connection.row_factory = observe
        monkeypatch.setattr(source_feed_queries, "_json", track_decode)
        observed = source_feed_queries.source_feed_item_explanation(connection, item_key=item_key, source_id=RSS)
    assert observed == expected
    assert decoded_after[0] == 1 < len(fetched)


def test_feed_explanation_seeks_the_item_in_each_retained_graph_version(
    published_feeds: Path,
) -> None:
    binding = local_binding("source-selection")
    with read_transaction(published_feeds, binding) as connection:
        items = source_feed_queries.source_feed_items(connection, source_id=RSS, source_run_id=None, limit=1)
        key = items[0].item_key
        baseline = source_feed_queries.source_feed_item_explanation(connection, item_key=key, source_id=RSS)
    with closing(sqlite3.connect(published_feeds)) as writer:
        base = writer.execute(
            "SELECT id FROM canonical_nodes WHERE graph_key_version = 1 AND canonical_key = ?", (key,),
        ).fetchone()[0]
        later = writer.execute(
            "INSERT INTO canonical_nodes(graph_key_version, canonical_key, kind, display_name, "
            "metadata_json, confidence, conflict, first_seen_run_id, last_seen_run_id) "
            "SELECT 7, canonical_key, kind, 'Later graph version', metadata_json, confidence, "
            "conflict, first_seen_run_id, last_seen_run_id FROM canonical_nodes WHERE id = ?", (base,),
        ).lastrowid
        assert later is not None
        writer.execute(
            "INSERT INTO canonical_node_evidence "
            "SELECT ?, canonical_evidence_id, link_kind "
            "FROM canonical_node_evidence WHERE canonical_node_id = ?", (later, base),
        )
        writer.executemany(
            "INSERT INTO canonical_nodes(graph_key_version, canonical_key, kind, display_name, "
            "metadata_json, confidence, conflict, first_seen_run_id, last_seen_run_id) "
            "VALUES (?, ?, 'file', 'unrelated', '{}', 'observed', 0, 1, 1)",
            ((version, f"unrelated:{index}") for version in (1, 3, 7) for index in range(8000)),
        )
        writer.execute(
            "INSERT INTO canonical_node_evidence "
            "SELECT id, (SELECT min(id) FROM canonical_evidence), 'observed' "
            "FROM canonical_nodes WHERE canonical_key LIKE 'unrelated:%'",
        )
        writer.commit()

    callbacks = 0

    def observe_work() -> int:
        nonlocal callbacks
        callbacks += 1
        return 0

    with read_transaction(published_feeds, binding) as connection:
        connection.set_progress_handler(observe_work, 100)
        try:
            result = source_feed_queries.source_feed_item_explanation(connection, item_key=key, source_id=RSS)
        finally:
            connection.set_progress_handler(None, 0)
    assert result["item"]["graph_key_version"] == 1
    assert len(baseline["evidence"]) > 1
    assert result["evidence"] == [row for earlier in baseline["evidence"] for row in (earlier, earlier)]
    assert callbacks < 150


def test_feed_explanation_reports_invalid_item_metadata_first(published_feeds: Path) -> None:
    binding = local_binding("source-selection")
    with read_transaction(published_feeds, binding) as connection:
        page = source_feed_queries.source_feed_items(connection, source_id=RSS, source_run_id=None, limit=1)
    key = page[0].item_key
    with closing(sqlite3.connect(published_feeds)) as writer:
        writer.execute("UPDATE canonical_nodes SET metadata_json = '!item' WHERE canonical_key = ?", (key,))
        writer.execute("UPDATE canonical_evidence SET metadata_json = '!evidence'")
        writer.commit()
    with read_transaction(published_feeds, binding) as connection:
        with pytest.raises(json.JSONDecodeError) as caught:
            source_feed_queries.source_feed_item_explanation(connection, item_key=key, source_id=RSS)
    assert caught.value.doc == "!item"


def test_feed_item_provenance_preserves_independent_nullable_maxima(published_feeds: Path) -> None:
    with closing(sqlite3.connect(published_feeds)) as connection:
        sample = source_queries.source_observations(connection, source_id=RSS)[0]
    completed = source_feed_queries._ItemProvenance()
    completed.include(replace(sample, source_run_id=None, artifact_id=None, artifact_path="z/path"))
    completed.include(replace(sample, source_run_id="r3", artifact_id="a1", artifact_path=None))
    completed.include(replace(sample, source_run_id="r1", artifact_id="a2", artifact_path="a/path"))
    assert (completed.source_run_id, completed.artifact_id, completed.artifact_path) == ("r3", "a2", "z/path")
