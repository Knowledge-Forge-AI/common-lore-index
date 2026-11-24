"""SQLite Local source/feed reads over real offline feed publications (PRODUCT3-SQLITE-LOCAL5).

Four real offline acquisitions (``acquire_feed_runs``) are published through
the production canonicalizer and the SQLite Local publisher as two
generations. Expected values come from the acquisition summaries and the
checked-in fixture bodies, not from the reader: source identity and policy,
the latest-run transition, run artifacts and statuses, item order with
undated items last, author/category/link references, filters, limits,
absent and non-matching cases, and the explanation shape. The comparison-only
crafted corpus covers NULL and default paths real feeds never reach.
Same-input equality with the PostgreSQL owners is the integration claim.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from common_lore_index.observations.raw import RawObservation
from common_lore_index.ops.ingestion.source import FeedIngestionSummary
from common_lore_index.storage.errors import StorageSchemaError
from common_lore_index.storage.sqlite_local import source_feed_queries, source_queries
from common_lore_index.storage.sqlite_local.connection import initialize_graph_database, read_transaction
from common_lore_index.storage.sqlite_local.publisher import publish_generation
from common_lore_index.storage.sqlite_local.schema import LocalStoreError
from repomap_test_support.sqlite_local_feed_corpus import (
    ATOM,
    RSS,
    SECRET,
    SECRET_MARKERS,
    acquire_feed_runs,
    crafted_feed_observations,
    feed_bundle,
    generation_observations,
)
from repomap_test_support.sqlite_local_fixtures import local_binding, publication_for

GRAPH = "feed-unit"
BINDING = local_binding(GRAPH)
ABSENT_ITEM = "feed.item:feed.channel%3Aabsent:guid%3Anone"


def _database(tmp_path: Path, name: str = GRAPH) -> Path:
    path = tmp_path / f"{name}.sqlite3"
    initialize_graph_database(path, local_binding(name), applied_at="2026-09-29T00:00:00Z")
    return path


def _publish(path: Path, generation: int, observations: tuple[RawObservation, ...], name: str = GRAPH) -> None:
    bundle = feed_bundle(name, generation, observations)
    publish_generation(path, publication_for(bundle, name), bundle.families, expected_generation=generation - 1)


@pytest.fixture
def feeds(tmp_path: Path) -> tuple[Path, dict[str, FeedIngestionSummary]]:
    runs = acquire_feed_runs(tmp_path / "feed-root")
    path = _database(tmp_path)
    _publish(path, 1, generation_observations(runs, 1))
    return path, runs


def _read(path: Path, name: str = GRAPH) -> Any:
    return read_transaction(path, local_binding(name))


def _everything(connection: Any, sources: tuple[str, ...]) -> str:
    """Every operation's output as one JSON text, for the privacy scan."""
    out: list[Any] = [asdict(row) for row in source_queries.ingested_sources(
        connection, source_type=None, policy_status=None, limit=50)]
    for source_id in sources:
        out.append(asdict(source_queries.source_summary(connection, source_id=source_id)))
        out += [asdict(row) for row in source_queries.source_runs(connection, source_id=source_id, limit=25)]
        items = source_feed_queries.source_feed_items(connection, source_id=source_id, source_run_id=None, limit=50)
        out += [asdict(row) for row in items]
        out += [asdict(row) for row in source_feed_queries.source_references(
            connection, source_id=source_id, source_run_id=None, target_kind=None, limit=100)]
        out += [source_feed_queries.source_feed_item_explanation(connection, item_key=item.item_key, source_id=None)
                for item in items]
    return json.dumps(out)


def test_inventory_summary_and_runs_follow_the_acquisitions(
    feeds: tuple[Path, dict[str, FeedIngestionSummary]],
) -> None:
    path, runs = feeds
    rss1 = runs["rss1"]
    with _read(path) as connection:
        inventory = source_queries.ingested_sources(connection, source_type=None, policy_status=None, limit=50)
        assert [row.source_id for row in inventory] == [ATOM, RSS, SECRET]  # source-id order
        by_id = {row.source_id: row for row in inventory}
        assert (by_id[RSS].source_type, by_id[RSS].display_name, by_id[RSS].policy_status) == (
            "feed.rss", "Example RSS Feed", "allowed_with_limits")
        assert (by_id[ATOM].source_type, by_id[ATOM].policy_status) == ("feed.atom", "allowed")
        assert by_id[SECRET].source_type == "feed.json"
        assert (by_id[RSS].latest_source_run_id, by_id[RSS].latest_artifact_path) == (
            rss1.source_run_id, rss1.artifact_path)
        assert by_id[RSS].latest_acquired_at == "2026-06-30T12:00:00Z"
        assert [by_id[key].canonical_feed_item_count for key in (ATOM, RSS, SECRET)] == [2, 5, 1]
        # COUNT(*) over the evidence x node-link fan-out, never below the raw rows.
        assert by_id[RSS].feed_observation_count >= rss1.observations
        filtered = source_queries.ingested_sources(connection, source_type="feed.atom", policy_status=None, limit=50)
        assert [row.source_id for row in filtered] == [ATOM]
        allowed = source_queries.ingested_sources(connection, source_type=None, policy_status="allowed", limit=50)
        assert [row.source_id for row in allowed] == [ATOM]
        assert [row.source_id for row in source_queries.ingested_sources(
            connection, source_type=None, policy_status=None, limit=1)] == [ATOM]
        assert source_queries.ingested_sources(connection, source_type="nope", policy_status=None, limit=5) == ()

        summary = source_queries.source_summary(connection, source_id=RSS)
        assert summary.configured_url_summary == "https://example.invalid/rss.xml"
        assert (summary.feed_documents, summary.feed_channels, summary.feed_items) == (1, 1, 5)
        assert (summary.feed_authors, summary.feed_categories, summary.parse_errors) == (1, 1, 0)
        # The extractor writes item/channel scopes; the maintained contract counts link/enclosure.
        assert (summary.link_references, summary.enclosure_references) == (0, 0)
        assert summary.known_limitations == ("source metadata is inferred from RSS2 evidence",)

        (run,) = source_queries.source_runs(connection, source_id=RSS, limit=25)
        assert (run.source_run_id, run.artifact_path, run.artifact_sha256) == (
            rss1.source_run_id, rss1.artifact_path, rss1.artifact_sha256)
        assert (run.artifact_id, run.artifact_byte_length) == (rss1.artifact_sha256[:16], rss1.artifact_bytes)
        assert (run.http_status, run.content_type) == (200, "application/rss+xml")
        assert (run.observation_count, run.status_summary) == (rss1.observations, "ok")


def test_feed_items_references_and_explanations(feeds: tuple[Path, dict[str, FeedIngestionSummary]]) -> None:
    path, runs = feeds
    with _read(path) as connection:
        items = source_feed_queries.source_feed_items(connection, source_id=RSS, source_run_id=None, limit=50)
        assert [item.title for item in items] == [
            "RSS Weak Fallback", "RSS Link Fallback", "RSS GUID Item", "RSS Duplicate A", "RSS Duplicate B"]
        assert [item.published_at is None for item in items] == [False, False, False, True, True]
        guid = items[2]
        assert (guid.authors, guid.categories, guid.identity_source) == (("RSS Writer",), ("Release Notes",), "guid")
        assert [item.duplicate_identity for item in items] == [False, False, False, True, True]
        assert items[0].identity_strength == "weak" and all(not item.link_targets for item in items)
        assert {(item.source_run_id, item.artifact_path) for item in items} == {
            (runs["rss1"].source_run_id, runs["rss1"].artifact_path)}
        assert source_feed_queries.source_feed_items(
            connection, source_id=RSS, source_run_id=None, limit=2) == items[:2]

        references = source_feed_queries.source_references(
            connection, source_id=RSS, source_run_id=None, target_kind=None, limit=50)
        assert [(row.source_item_key, row.target_key) for row in references] == sorted(
            (row.source_item_key, row.target_key) for row in references)
        assert {row.target_key.split(":", 1)[0] for row in references} == {
            "external.url", "feed.author", "feed.category", "file"}
        enclosure = next(row for row in references if row.target_key.endswith("/media/rss-audio.mp3"))
        assert (enclosure.media_type, enclosure.not_fetched, enclosure.relation) == ("audio/mpeg", True, "references")
        assert enclosure.target_display == "media/rss-audio.mp3"
        authors = source_feed_queries.source_references(
            connection, source_id=RSS, source_run_id=None, target_kind="feed.author", limit=50)
        assert [row.source_item_key for row in authors] == [guid.item_key]
        assert source_feed_queries.source_references(
            connection, source_id=RSS, source_run_id="no-such-run", target_kind=None, limit=50) == ()

        explained = source_feed_queries.source_feed_item_explanation(connection, item_key=guid.item_key, source_id=None)
        assert list(explained) == ["item", "source", "evidence", "references", "content_policy"]
        assert explained["content_policy"] == "full feed bodies are not exposed"
        assert explained["item"]["canonical_key"] == guid.item_key and explained["item"]["conflict"] is False
        assert explained["item"]["metadata"]["title"] == "RSS GUID Item"
        assert explained["source"]["source_id"] == RSS  # the newest source overall, not tied to the item
        assert explained["evidence"] and all(row["raw_kind"] for row in explained["evidence"])
        assert [row["target_key"] for row in explained["references"]] == sorted(
            row.target_key for row in references if row.source_item_key == guid.item_key)
        other = source_feed_queries.source_feed_item_explanation(connection, item_key=guid.item_key, source_id=ATOM)
        # A non-matching source: no item or evidence, but references are not source-filtered.
        assert (other["item"], other["evidence"], other["source"]["source_id"]) == (None, [], ATOM)
        assert other["references"] == explained["references"]
        absent = source_feed_queries.source_feed_item_explanation(connection, item_key=ABSENT_ITEM, source_id=None)
        assert (absent["item"], absent["evidence"], absent["references"]) == (None, [], [])
        assert absent["source"]["source_id"] == RSS

        missing = source_queries.source_summary(connection, source_id="no-such-source")
        assert (missing.policy_status, missing.source_type, missing.feed_items) == ("unknown", "unknown", 0)
        assert missing.known_limitations == ("source metadata unavailable",)
        assert source_queries.source_runs(connection, source_id="no-such-source", limit=5) == ()
        assert source_feed_queries.source_feed_items(
            connection, source_id="no-such-source", source_run_id=None, limit=5) == ()
        text = _everything(connection, (ATOM, RSS, SECRET))
    assert not any(marker in text for marker in SECRET_MARKERS)
    assert "/" + "Users" not in text and str(path.parent) not in text  # artifact-relative paths only


def test_second_generation_moves_the_latest_run(feeds: tuple[Path, dict[str, FeedIngestionSummary]]) -> None:
    path, runs = feeds
    with _read(path) as connection:
        before = {row.source_id: row for row in source_queries.ingested_sources(
            connection, source_type=None, policy_status=None, limit=50)}
    _publish(path, 2, generation_observations(runs, 2))
    rss1, rss2 = runs["rss1"], runs["rss2"]
    with _read(path) as connection:
        after = {row.source_id: row for row in source_queries.ingested_sources(
            connection, source_type=None, policy_status=None, limit=50)}
        assert (after[RSS].latest_source_run_id, after[RSS].latest_acquired_at) == (
            rss2.source_run_id, "2026-07-01T12:00:00Z")
        assert after[ATOM].latest_source_run_id == before[ATOM].latest_source_run_id
        # Both publications keep their raw rows, so the fan-out doubles; distinct items do not.
        assert after[ATOM].feed_observation_count == 2 * before[ATOM].feed_observation_count
        assert after[RSS].canonical_feed_item_count == before[RSS].canonical_feed_item_count == 5
        runs_after = source_queries.source_runs(connection, source_id=RSS, limit=25)
        assert [(run.source_run_id, run.status_summary) for run in runs_after] == [
            (rss2.source_run_id, "parse_errors"), (rss1.source_run_id, "ok")]
        assert [run.observation_count for run in runs_after] == [rss2.observations, 2 * rss1.observations]
        assert source_queries.source_runs(connection, source_id=RSS, limit=1) == runs_after[:1]
        summary = source_queries.source_summary(connection, source_id=RSS)
        assert (summary.parse_errors, summary.feed_items, summary.latest_source_run_id) == (
            1, 5, rss2.source_run_id)
        assert source_feed_queries.source_feed_items(
            connection, source_id=RSS, source_run_id=rss2.source_run_id, limit=50) == ()
        assert len(source_feed_queries.source_feed_items(
            connection, source_id=RSS, source_run_id=rss1.source_run_id, limit=50)) == 5
        references = source_feed_queries.source_references(
            connection, source_id=RSS, source_run_id=None, target_kind=None, limit=100)
        assert len(references) == 10 and references[0] == references[1]  # one row per publication's evidence


def test_crafted_corpus_reaches_null_and_default_paths(tmp_path: Path) -> None:
    path = _database(tmp_path, "crafted")
    _publish(path, 1, crafted_feed_observations(), name="crafted")
    with _read(path, "crafted") as connection:
        alpha = source_queries.source_summary(connection, source_id="crafted-alpha")
        assert (alpha.link_references, alpha.enclosure_references) == (3, 1)
        assert alpha.latest_source_run_id is None and alpha.latest_acquired_at == "2026-01-03T00:00:00Z"
        beta = source_queries.source_summary(connection, source_id="crafted-beta")
        assert beta.latest_source_run_id is None and beta.latest_acquired_at is None
        assert alpha.source_type == "feed.atom"  # MIN over the group
        beta = source_queries.source_summary(connection, source_id="crafted-beta")
        assert (beta.latest_acquired_at, beta.display_name) == (None, None)
        runs = source_queries.source_runs(connection, source_id="crafted-alpha", limit=25)
        assert [(run.source_run_id, run.artifact_byte_length, run.http_status) for run in runs] == [
            ("r1", 1000, 201), ("r2", None, None)]  # numeric MAX; NULL acquisition last
        assert source_queries.source_runs(connection, source_id="crafted-beta", limit=25) == ()
        allowed = source_queries.ingested_sources(connection, source_type=None, policy_status="allowed", limit=50)
        everything = source_queries.ingested_sources(connection, source_type=None, policy_status=None, limit=50)
        assert [row.feed_observation_count < full.feed_observation_count
                for row, full in zip(allowed, everything, strict=True)] == [True, True]  # filter before GROUP BY
        references = source_feed_queries.source_references(
            connection, source_id="crafted-alpha", source_run_id=None, target_kind=None, limit=50)
        assert {row.not_fetched for row in references} == {True, False}
        items = source_feed_queries.source_feed_items(
            connection, source_id="crafted-alpha", source_run_id=None, limit=50)
        assert {item.duplicate_identity for item in items} == {True, False}
        assert items[2].link_targets == tuple(sorted(items[2].link_targets)) and len(items[2].link_targets) == 2
        explained = source_feed_queries.source_feed_item_explanation(
            connection, item_key=ABSENT_ITEM, source_id=None)
        assert explained["source"]["source_id"] == "crafted-alpha"  # NULL acquisitions rank last
        beta_only = source_feed_queries.source_feed_item_explanation(
            connection, item_key=ABSENT_ITEM, source_id="crafted-beta")
        assert beta_only["source"]["acquired_at"] is None


def test_run_casts_refuse_what_postgresql_rejects(tmp_path: Path) -> None:
    observations = list(crafted_feed_observations())
    first = observations[0]
    observations[0] = replace(first, metadata={**first.metadata, "source_run_id": "r1",
                                               "source_artifact_bytes": "12x"})
    path = _database(tmp_path, "casts")
    _publish(path, 1, tuple(observations), name="casts")
    with _read(path, "casts") as connection:
        assert source_queries.ingested_sources(connection, source_type=None, policy_status=None, limit=5)
        with pytest.raises(StorageSchemaError, match="not a valid integer"):
            source_queries.source_runs(connection, source_id="crafted-alpha", limit=5)


def test_unpublished_graph_refuses_every_operation(tmp_path: Path) -> None:
    path = _database(tmp_path)
    calls = (
        lambda c: source_queries.ingested_sources(c, source_type=None, policy_status=None, limit=5),
        lambda c: source_queries.source_summary(c, source_id=RSS),
        lambda c: source_queries.source_runs(c, source_id=RSS, limit=5),
        lambda c: source_feed_queries.source_feed_items(c, source_id=RSS, source_run_id=None, limit=5),
        lambda c: source_feed_queries.source_references(
            c, source_id=RSS, source_run_id=None, target_kind=None, limit=5),
        lambda c: source_feed_queries.source_feed_item_explanation(c, item_key=ABSENT_ITEM, source_id=None),
    )
    with _read(path) as connection:
        for call in calls:
            with pytest.raises(LocalStoreError) as caught:
                call(connection)
            assert caught.value.code == "graph-publication-absent"
        with pytest.raises(StorageSchemaError, match="limit must be a positive integer"):
            source_queries.ingested_sources(connection, source_type=None, policy_status=None, limit=0)


def test_aggregate_and_default_helpers() -> None:
    assert [source_queries.not_fetched(value) for value in (None, "true", "off", "Y", "0")] == [
        True, True, False, True, False]
    with pytest.raises(StorageSchemaError):
        source_queries.not_fetched("maybe")
    assert source_queries.max_of([None, "b", "a"]) == "b" and source_queries.min_of([None, None]) is None


def _seed_fanout_topologies(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys = ON")
        run1, run2 = [row[0] for row in db.execute("SELECT id FROM runs ORDER BY id").fetchall()[:2]]
        ord0 = int(db.execute("SELECT COALESCE(MAX(ordinal), -1) + 1 FROM raw_observations").fetchone()[0])
        nid0 = int(db.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM canonical_nodes").fetchone()[0])
        eid0 = int(db.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM canonical_edges").fetchone()[0])
        ev0 = int(db.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM canonical_evidence").fetchone()[0])
        meta_r1 = {"source_id_configured": "w7-main", "source_type": "feed.rss", "source_display_name": "W7 Feed",
                   "source_policy_status": "allowed", "source_run_id": "w7-r1", "source_artifact_id": "a1",
                   "source_artifact_path": "f/r1.xml", "source_acquired_at": "2026-08-01T00:00:00Z",
                   "acquisition_url_summary": "https://w7.invalid/rss"}
        meta_r2 = {**meta_r1, "source_run_id": "w7-r2", "source_artifact_id": "a2",
                   "source_artifact_path": "f/r2.xml", "source_acquired_at": "2026-08-02T00:00:00Z"}
        null_meta = {"source_id_configured": "w7-nulls", "source_type": "feed.atom",
                     "source_policy_status": "allowed_with_limits"}
        db.executemany(
            "INSERT INTO raw_observations (run_id, ordinal, schema_version, kind, source_id, path, payload_json, "
            "payload_hash) VALUES (?, ?, 1, ?, 'src', 'f.xml', ?, 'h')",
            [(r, ord0 + i, k, json.dumps({"metadata": m})) for i, (r, k, m) in enumerate((
                (run1, "feed.rss", meta_r1), (run2, "feed.rss", meta_r2), (run1, "feed.rss", meta_r1),
                (run2, "feed.parse_error", meta_r2), (run1, "feed.parse_error", meta_r1),
                (run1, "feed.parse_error", null_meta), (run2, "feed.atom", null_meta)))])
        db.executemany(
            "INSERT INTO canonical_nodes (id, graph_key_version, canonical_key, kind, display_name, metadata_json, "
            "confidence, conflict, first_seen_run_id, last_seen_run_id) VALUES (?, ?, ?, ?, ?, '{}', 'high', 0, ?, ?)",
            [(nid0 + off, ver, key, kind, key, run1, run2) for off, ver, key, kind in (
                (0, 1, "feed.document:w7:1", "feed.document"), (1, 1, "feed.channel:w7:1", "feed.channel"),
                (2, 1, "feed.item:w7:1", "feed.item"), (3, 2, "feed.item:w7:1", "feed.item"),
                (4, 1, "feed.item:w7:2", "feed.item"), (5, 1, "feed.author:w7:1", "feed.author"),
                (6, 1, "feed.category:w7:1", "feed.category"), (7, 1, "feed.category:w7:2", "feed.category"),
                (8, 1, "external.url:w7:1", "external.url"))])
        db.executemany(
            "INSERT INTO canonical_edges (id, graph_key_version, source_canonical_key, edge_kind, "
            "target_canonical_key, identity_metadata_json, identity_metadata_hash, metadata_json, confidence, "
            "conflict, first_seen_run_id, last_seen_run_id) "
            "VALUES (?, 1, 'feed.item:w7:1', ?, 'external.url:w7:1', '{}', ?, ?, 'high', 0, ?, ?)",
            [(eid0 + off, kind, f"w7-e-{off}", meta_json, run1, run2) for off, kind, meta_json in (
                (0, "references", '{"scope":"link"}'), (1, "references", '{"scope":"link"}'),
                (2, "references", '{"scope":"enclosure"}'), (3, "references", '{"scope":"item"}'),
                (4, "contains", '{"scope":"link"}'))])
        db.executemany(
            "INSERT INTO canonical_evidence (id, run_id, graph_key_version, evidence_key, raw_observation_ordinal, "
            "raw_schema_version, raw_kind, raw_source_id, path, extractor, extractor_version, confidence, "
            "metadata_json) VALUES (?, ?, 1, ?, ?, 1, 'rss', 'src', 'f.xml', 'ex', '1', 'high', '{}')",
            [(ev0 + off, r, f"w7-ev-{off}", ord0 + ro) for off, r, ro in ((0, run1, 0), (1, run1, 0), (2, run2, 1),
             (3, run2, 3), (4, run2, 3), (5, run2, 3), (6, run2, 3), (7, run2, 6))])
        db.executemany(
            "INSERT INTO canonical_node_evidence (canonical_node_id, canonical_evidence_id, link_kind) VALUES (?,?,?)",
            [(nid0 + n, ev0 + e, k) for n, e, k in ((0, 0, "p"), (1, 0, "p"), (2, 0, "p"), (2, 0, "dup"), (3, 1, "p"),
             (5, 1, "p"), (8, 1, "p"), (2, 2, "p"), (4, 2, "p"), (6, 2, "p"), (7, 3, "p"), (7, 3, "dup"), (4, 3, "p"),
             (0, 4, "p"), (5, 4, "p"))])
        db.executemany(
            "INSERT INTO canonical_edge_evidence (canonical_edge_id, canonical_evidence_id, link_kind) VALUES (?,?,?)",
            [(eid0 + d, ev0 + e, k) for d, e, k in ((0, 0, "p"), (0, 0, "dup"), (2, 0, "p"), (0, 2, "p"), (1, 2, "p"),
             (3, 2, "p"), (2, 3, "p"), (2, 3, "dup"), (1, 3, "p"), (4, 3, "p"), (4, 3, "dup"), (0, 5, "p"),
             (2, 5, "p"), (4, 5, "p"))])


def test_fanout_counts_distinct_keys_multiple_evidence_and_unmatched_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _database(tmp_path, "fanout-parity")
    _publish(path, 1, crafted_feed_observations(), name="fanout-parity")
    _publish(path, 2, crafted_feed_observations(), name="fanout-parity")
    _seed_fanout_topologies(path)
    kinds = ("feed.document", "feed.channel", "feed.item", "feed.author", "feed.category")
    with _read(path, "fanout-parity") as connection:
        observations, ev = source_queries.source_observations(connection), source_queries.evidence_ids(connection)
        nl, el = source_queries.node_links(connection), source_queries.edge_links(connection)
        for source_id in ("crafted-alpha", "crafted-beta", "w7-main", "w7-nulls"):
            grp = [item for item in observations if item.source_id == source_id]
            ref_rows = list(source_queries.fanout(grp, ev, nl, el))
            inv_rows = list(source_queries.fanout(grp, ev, nl))
            with monkeypatch.context() as patch:
                patch.setattr(source_queries, "fanout", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()))
                summary = source_queries.source_summary(connection, source_id=source_id)
                by_id = {r.source_id: r for r in source_queries.ingested_sources(
                    connection, source_type=None, policy_status=None, limit=50)}
            assert (summary.feed_documents, summary.feed_channels, summary.feed_items,
                    summary.feed_authors, summary.feed_categories) == tuple(
                len({n.key for _, n, _ in ref_rows if n is not None and n.kind == kind}) for kind in kinds)
            assert (summary.link_references, summary.enclosure_references) == tuple(
                len({e.id for _, _, e in ref_rows if e is not None and e.kind == "references"
                     and source_queries.raw_payload.text_at(e.metadata, "scope") == scope})
                for scope in ("link", "enclosure"))
            assert summary.parse_errors == sum(1 for obs, _, _ in ref_rows if obs.kind == "feed.parse_error")
            assert (by_id[source_id].feed_observation_count, by_id[source_id].canonical_feed_item_count) == (
                len(inv_rows), summary.feed_items)
        main = source_queries.source_summary(connection, source_id="w7-main")
        assert (main.feed_documents, main.feed_channels, main.feed_items, main.feed_authors, main.feed_categories) == (
            1, 1, 2, 1, 2)
        assert (main.link_references, main.enclosure_references, main.parse_errors) == (2, 1, 22)
        assert (main.latest_source_run_id, main.latest_acquired_at) == ("w7-r2", "2026-08-02T00:00:00Z")
        nulls = source_queries.source_summary(connection, source_id="w7-nulls")
        assert (nulls.parse_errors, nulls.feed_items, nulls.link_references, nulls.display_name,
                nulls.configured_url_summary, nulls.latest_source_run_id, nulls.latest_acquired_at) == (
            1, 0, 0, None, None, None, None)
        rss_only = source_queries.ingested_sources(connection, source_type="feed.rss", policy_status="allowed", limit=5)
        assert [row.source_id for row in rss_only if row.source_id.startswith("w7-")] == ["w7-main"]
        assert next(row for row in rss_only if row.source_id == "w7-main").feed_observation_count == 19
