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
