"""Selected SQLite Local source reads hydrate only their own evidence links."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from common_lore_index.storage.sqlite_local import source_feed_queries, source_queries
from common_lore_index.storage.sqlite_local.connection import initialize_graph_database, read_transaction
from common_lore_index.storage.sqlite_local.publisher import publish_generation
from repomap_test_support.sqlite_local_feed_corpus import (
    ATOM, RSS, acquire_feed_runs, feed_bundle, generation_observations,
)
from repomap_test_support.sqlite_local_fixtures import local_binding, publication_for


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

        def observe(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> tuple[Any, ...]:
            if cursor.description and cursor.description[0][0] == "canonical_evidence_id":
                fetched.add(int(row[0]))
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
        assert fetched <= selected_ids
        assert bool(fetched) == bool(selected_ids)


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
        selected = source_queries.evidence_ids(connection, keys)
        assert set(selected) == set(keys) and selected[(1, 1)] == [1, 600]
        assert all("ORDER BY" not in sql for sql in statements if "FROM canonical_evidence WHERE" in sql)
        links = source_queries.node_links(connection, (i for evidence in selected.values() for i in evidence))
        assert set(links) == {*range(1, 502), 600}
        assert all(len(nodes) == 1 and nodes[0].kind == "feed.item" for nodes in links.values())


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
        linked: dict[tuple[str, int], list[source_queries.SourceObservation]] = {key: [] for key in keys}
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
