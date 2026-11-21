"""Read semantics of the SQLite Local canonical, search, summary and status operations.

Expected values are derived from the same public-safe bundle families the
publisher consumed, using the PostgreSQL owners' documented order and paging
rules; observation search is checked against the ``jsonb::text`` rendering
PostgreSQL matches. Parity against a real PostgreSQL publication is an
integration claim.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from common_lore_index.storage.errors import StorageSchemaError
from common_lore_index.storage.sql_core import canonical_file_path_prefix
from common_lore_index.storage.sqlite_local import investigation_queries, queries, raw_payload, source_feed_queries
from common_lore_index.storage.sqlite_local.connection import initialize_graph_database, read_transaction
from common_lore_index.storage.sqlite_local.publisher import publish_generation
from common_lore_index.storage.sqlite_local.schema import LocalStoreError
from repomap_test_support.sqlite_local_fixtures import generation_bundle, local_binding, publication_for
from repomap_test_support.sqlite_local_read_corpus import CORPUS_ROWS, read_corpus_bundle

BINDING = local_binding()
BUNDLE = generation_bundle(1)
NODE_KEYS = sorted({str(row["canonical_key"]) for row in BUNDLE.families["canonical_nodes"]})
EDGES = sorted(
    {
        (
            str(row["source_canonical_key"]), str(row["edge_kind"]),
            str(row["target_canonical_key"]), str(row["identity_metadata_hash"]),
        )
        for row in BUNDLE.families["canonical_edges"]
    }
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "g.sqlite3"
    initialize_graph_database(path, BINDING, applied_at="2026-09-29T00:00:00Z")
    return path


@pytest.fixture
def published(db: Path) -> Path:
    publish_generation(db, publication_for(BUNDLE), BUNDLE.families, expected_generation=0)
    return db


def test_absent_publication_refuses_reads_but_summaries_report_empty(db: Path) -> None:
    with read_transaction(db, BINDING) as connection:
        with pytest.raises(LocalStoreError) as caught:
            queries.canonical_nodes(
                connection, kind=None, canonical_key=None, path_prefix=None,
                graph_key_version=1, limit=5, offset=0,
            )
        assert caught.value.code == "graph-publication-absent"
        with pytest.raises(LocalStoreError):
            investigation_queries.search(connection, target="files", query="py", kind=None, path=None, limit=5, offset=0)
        with pytest.raises(LocalStoreError):
            investigation_queries.observation_search(
                connection, query="py", kind=None, path=None, limit=5, offset=0, include_raw=False)
        summary = investigation_queries.storage_summary(connection, root_path="graph:portable-fixture")
        assert (summary.repository_name, summary.latest_run_id, summary.runs, summary.files) == (None, None, 0, 0)
        assert investigation_queries.status_fields(connection)["repository_exists"] is False


def test_nodes_order_page_and_filter_like_postgresql(published: Path) -> None:
    with read_transaction(published, BINDING) as connection:
        def keys(**kwargs: object) -> list[str]:
            arguments: dict[str, Any] = {
                "kind": None, "canonical_key": None, "path_prefix": None,
                "graph_key_version": 1, "limit": 50, "offset": 0,
            }
            arguments.update(kwargs)
            return [record.canonical_key for record in queries.canonical_nodes(connection, **arguments)]

        assert keys() == NODE_KEYS
        assert keys(limit=2) + keys(limit=50, offset=2) == NODE_KEYS
        assert keys(canonical_key=NODE_KEYS[0]) == NODE_KEYS[:1]
        module = [key for key in NODE_KEYS if key.startswith("python.module:")]
        assert keys(kind="python.module") == module
        prefix = canonical_file_path_prefix("pkg")
        assert keys(path_prefix="pkg") == [key for key in NODE_KEYS if key.startswith(prefix)]
        assert keys(path_prefix="PKG") == []  # LIKE-prefix in PostgreSQL is case-sensitive
        with pytest.raises(StorageSchemaError):
            keys(graph_key_version=2)
        with pytest.raises(StorageSchemaError):
            keys(limit=None, offset=1)
        with pytest.raises(StorageSchemaError):
            keys(offset=-1)


def test_edges_order_and_filters(published: Path) -> None:
    with read_transaction(published, BINDING) as connection:
        def edges(**kwargs: object) -> list[tuple[str, str, str, str]]:
            arguments: dict[str, Any] = {
                "kind": None, "source_key": None, "target_key": None,
                "graph_key_version": 1, "limit": 50, "offset": 0,
            }
            arguments.update(kwargs)
            return [
                (r.source_key, r.edge_kind, r.target_key, r.identity_metadata_hash)
                for r in queries.canonical_edges(connection, **arguments)
            ]

        assert edges() == EDGES
        assert edges(limit=1, offset=1) == EDGES[1:2]
        assert edges(kind=EDGES[0][1]) == [edge for edge in EDGES if edge[1] == EDGES[0][1]]
        assert edges(source_key=EDGES[0][0]) == [edge for edge in EDGES if edge[0] == EDGES[0][0]]
        assert edges(target_key=EDGES[-1][2]) == [edge for edge in EDGES if edge[2] == EDGES[-1][2]]


def test_canonical_file_windows_skip_unrelated_node_keys(published: Path) -> None:
    prefix = canonical_file_path_prefix("pkg %雪")
    with closing(sqlite3.connect(published)) as writer:
        writer.executemany(
            "INSERT INTO canonical_nodes(graph_key_version, canonical_key, kind, display_name, "
            "metadata_json, confidence, conflict, first_seen_run_id, last_seen_run_id) "
            "VALUES (1, ?, 'file', 'fixture', '{}', 'observed', 0, 1, 1)",
            [(f"file:noise/{index:05}",) for index in range(15000)]
            + [(prefix + f"file{index}.py",) for index in range(3)]
            + [(prefix[:-1] + "0/other.py",)],
        )
        writer.commit()
    with read_transaction(published, BINDING) as connection:
        callbacks = 0

        def tick() -> int:
            nonlocal callbacks
            callbacks += 1
            return 0

        connection.set_progress_handler(tick, 50)
        try:
            rows = queries.canonical_nodes(
                connection, kind=None, canonical_key=None, path_prefix="pkg %雪",
                graph_key_version=1, limit=1, offset=1,
            )
        finally:
            connection.set_progress_handler(None, 0)
    assert [row.canonical_key for row in rows] == [prefix + "file1.py"]
    assert callbacks < 100


def test_edge_explanation_windows_evidence_and_misses_cleanly(published: Path) -> None:
    source, kind, target, digest = EDGES[0]
    expected = sorted(
        (str(row["evidence_key"]), str(row["link_kind"]))
        for row in BUNDLE.families["canonical_edge_evidence"]
        if (row["source_canonical_key"], row["edge_kind"], row["target_canonical_key"]) == (source, kind, target)
    )
    with read_transaction(published, BINDING) as connection:
        found = queries.canonical_edge_explanation(
            connection, source_key=source, kind=kind, target_key=target,
            identity_metadata_hash=digest, graph_key_version=1, evidence_limit=50, evidence_offset=0,
        )
        assert found.edge is not None and found.edge.first_seen_run_id == 1
        assert sorted((item.evidence_key, item.link_kind) for item in found.evidence) == expected
        assert all(item.raw_observation["run_id"] == 1 and item.raw_observation["payload_hash"]
                   for item in found.evidence)
        paged = queries.canonical_edge_explanation(
            connection, source_key=source, kind=kind, target_key=target,
            identity_metadata_hash=digest, graph_key_version=1, evidence_limit=1, evidence_offset=0,
        )
        assert len(paged.evidence) == 1 and paged.evidence[0] == found.evidence[0]
        missing = queries.canonical_edge_explanation(
            connection, source_key=source, kind=kind, target_key="python.module:absent",
            identity_metadata_hash=digest, graph_key_version=1, evidence_limit=5, evidence_offset=0,
        )
        assert missing.edge is None and missing.evidence == ()


def test_neighborhood_directions_depth_and_missing_center(published: Path) -> None:
    center = EDGES[0][0]
    with read_transaction(published, BINDING) as connection:
        def around(direction: str, node: str = center) -> tuple[list[str], list[str]]:
            record = queries.canonical_neighborhood(
                connection, node=node, direction=direction, depth=1, graph_key_version=1,
                node_limit=50, node_offset=0, edge_limit=50, edge_offset=0,
            )
            return [n.canonical_key for n in record.nodes], [e.target_key for e in record.edges]

        out_nodes, out_edges = around("out")
        assert out_edges == [edge[2] for edge in EDGES if edge[0] == center]
        assert center not in out_nodes and out_nodes == sorted(set(out_edges) - {center})
        both_nodes, _ = around("both")
        assert set(out_nodes) <= set(both_nodes)
        assert around("both", "python.module:absent") == ([], [])
        with pytest.raises(StorageSchemaError):
            queries.canonical_neighborhood(
                connection, node=center, direction="both", depth=2, graph_key_version=1,
                node_limit=5, node_offset=0, edge_limit=5, edge_offset=0,
            )


def test_search_is_ascii_case_insensitive_escaped_and_probes_one_extra_row(published: Path) -> None:
    with read_transaction(published, BINDING) as connection:
        def search(target: str, query: str, **kwargs: object) -> dict[str, Any]:
            arguments: dict[str, Any] = {"kind": None, "path": None, "limit": 20, "offset": 0}
            arguments.update(kwargs)
            return investigation_queries.search(connection, target=target, query=query, **arguments)

        lower = search("nodes", "init")
        assert search("nodes", "INIT")["results"] == lower["results"] and lower["results"]
        assert search("nodes", "init_fn")["results"] and not search("nodes", "initXfn")["results"]
        assert search("nodes", "%")["results"] == []
        page = search("nodes", "pkg", limit=1)
        assert page["has_more"] is True and page["total"] == 1
        files = search("files", "PY")
        assert files["results"] == [
            {"path": "pkg/init.py", "language": "python", "role": "source",
             "executable": False, "generated": False}
        ]
        assert search("files", "py", path="pkg/other.py")["results"] == []
        with pytest.raises(StorageSchemaError):
            search("observations", "py")


def test_summary_and_status_report_the_accepted_generation(published: Path) -> None:
    with read_transaction(published, BINDING) as connection:
        summary = investigation_queries.storage_summary(connection, root_path="graph:portable-fixture")
        counts = BUNDLE.family_counts
        assert (summary.repository_name, summary.latest_run_id, summary.runs) == ("portable-fixture", 1, 1)
        assert summary.raw_observations == counts["raw_observations"] == summary.latest_run_raw_observations
        assert summary.canonical_evidence == counts["canonical_evidence"]
        status = investigation_queries.status_fields(connection)
        publication = status["publication"]
        assert status["latest_run_status"] == "complete"
        assert publication["publication_bundle_id"] == BUNDLE.bundle_id
        assert publication["source_binding_count"] == 1
        assert publication["family_counts"] == dict(counts)
        assert str(status["latest_run_started_at"]).endswith("Z")


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    path = tmp_path / "corpus.sqlite3"
    initialize_graph_database(path, BINDING, applied_at="2026-09-29T00:00:00Z")
    bundle = read_corpus_bundle()
    publish_generation(path, publication_for(bundle), bundle.families, expected_generation=0)
    return path


def test_observation_search_orders_pages_and_filters_like_postgresql(corpus: Path) -> None:
    with read_transaction(corpus, BINDING) as connection:
        def search(query: str, **kwargs: object) -> dict[str, Any]:
            arguments: dict[str, Any] = {"kind": None, "path": None, "limit": 100, "offset": 0,
                                         "include_raw": False}
            arguments.update(kwargs)
            return investigation_queries.observation_search(connection, query=query, **arguments)

        every = search("#corpus:")["results"]
        assert [row["ordinal"] for row in every] == list(range(len(CORPUS_ROWS)))
        assert list(every[0]) == ["ordinal", "kind", "source_id", "path", "metadata"]
        first, second = search("#corpus:", limit=3), search("#corpus:", limit=3, offset=3)
        assert (first["has_more"], first["total"], second["total"]) == (True, 3, 6)
        assert first["results"] + second["results"] == every[:6]
        last = search("#corpus:", limit=3, offset=len(CORPUS_ROWS) - 2)
        assert (len(last["results"]), last["has_more"]) == (2, False)
        tfvars = search("tfvars", kind="terraform.file")["results"]
        assert {row["path"] for row in tfvars} == {
            "terraform.tfvars", "env/terraform.tfvars", "prod.auto.tfvars", "custom.tfvars"}
        assert [row["kind"] for row in search("corpus", path="api.yaml", kind="openapi.operation")[
            "results"]] == ["openapi.operation"] * 3
        raw = search("custom.note", include_raw=True)["results"]
        assert raw[0]["payload"]["metadata"] == raw[0]["metadata"] and "payload" not in every[-1]


def test_observation_search_matches_the_jsonb_text_literally(corpus: Path) -> None:
    with read_transaction(corpus, BINDING) as connection:
        def kinds(query: str) -> list[str]:
            return [row["kind"] for row in investigation_queries.observation_search(
                connection, query=query, kind=None, path=None, limit=100, offset=0,
                include_raw=False)["results"]]

        note = ["custom.note"]
        assert kinds('"b": 150, "c": "') == note  # PostgreSQL separators and key-length order
        assert kinds('"b":150') == []  # the stored compact spelling is not what PostgreSQL matches
        assert kinds('"e": 100000000000000000000') == note
        assert kinds('"d": -7, "e"') == note
        assert kinds("100% done_\\x") == note and kinds("100%x") == [] and kinds("doneXx") == []
        assert kinds('\\"quoted\\"\\ttab') == note and kinds('"quoted"') == []
        assert kinds("CUSTOM.NOTE") == note and kinds("ÄRGER") == note and kinds("ärger") == []
        assert kinds('"name": "Ünïcode Name"') == note
        metadata = investigation_queries.observation_search(
            connection, query="custom.note", kind=None, path=None, limit=1, offset=0,
            include_raw=False)["results"][0]["metadata"]
        assert list(metadata) == ["b", "c", "d", "e", "aa"] and metadata["e"] == 10**20


def test_local_feed_reference_lookup_copies_only_page_keys_from_sqlite() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        assert source_feed_queries._item_references(connection, set()) == {}
        connection.execute("CREATE TABLE canonical_nodes(canonical_key, kind, display_name)")
        connection.execute("CREATE TABLE canonical_edges(graph_key_version, source_canonical_key, target_canonical_key, edge_kind, metadata_json)")
        connection.executemany("INSERT INTO canonical_nodes VALUES (?, ?, ?)",
                               [("person", "feed.author", "Ada"), ("person", "feed.category", "News")])
        connection.executemany("INSERT INTO canonical_edges VALUES (?, ?, ?, ?, ?)", (
            (1, "item-000", "person", "references", "{}"),
            (2, "item-520", "asset", "references", '{"scope":"enclosure"}'),
            (2, "item-520", "ignored", "other-kind", '{"scope":"link"}'),
            *((1, f"other-{i}", "unselected", "references", '{"scope":"link"}') for i in range(700)),
        ))
        fetched = []

        def record(_cursor: sqlite3.Cursor, row: tuple[object, ...]) -> tuple[object, ...]:
            if len(row) == 5:
                fetched.append(row)
            return row

        connection.row_factory = record
        result = source_feed_queries._item_references(
            connection, {f"item-{i:03d}" for i in range(521)}
        )
        assert result == {
            "item-000": (set(), {"Ada"}, {"News"}),
            "item-520": ({"asset"}, set(), set()),
        }
        assert len(fetched) == 3

def test_observation_search_renders_payload_only_when_needed(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = raw_payload.jsonb_text
    payload_calls = []

    def count(value: object) -> str:
        if isinstance(value, dict) and "metadata" in value:
            payload_calls.append(value)
        return original(value)

    monkeypatch.setattr(raw_payload, "jsonb_text", count)
    with read_transaction(corpus, BINDING) as connection:
        def search(query: str, *, include_raw: bool, path: str | None = None) -> dict[str, Any]:
            return investigation_queries.observation_search(
                connection, query=query, kind="custom.note", path=path, limit=2, offset=0, include_raw=include_raw,
            )

        header = search("custom.note", include_raw=False)
        assert len(header["results"]) == 1 and not payload_calls

        result = search('"b": 150, "c": "', include_raw=True, path=header["results"][0]["path"])
        row, = result["results"]
        assert row["payload"]["metadata"] == row["metadata"]
        assert row["metadata"]["c"] == 'Ärger "quoted"\ttab'
        assert payload_calls.count(row["payload"]) == 1


def test_jsonb_string_rendering_preserves_unicode_and_escape_spelling() -> None:
    text = '雪"\\\b\f\n\r\t\x01\x1f\x7f'
    quoted = '"雪\\"\\\\\\b\\f\\n\\r\\t\\u0001\\u001f\x7f"'
    assert raw_payload.jsonb_text({"a": text}) == '{"a": ' + quoted + '}'


def test_observation_header_page_decodes_only_returned_rows(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    original = raw_payload.load

    def record(text: str) -> Any:
        calls.append(text)
        return original(text)

    monkeypatch.setattr(raw_payload, "load", record)
    with read_transaction(corpus, BINDING) as connection:
        result = investigation_queries.observation_search(
            connection, query="#corpus:", kind=None, path=None, limit=2, offset=8, include_raw=True,
        )
    assert result["has_more"] is True and result["total"] == 10
    assert [row["ordinal"] for row in result["results"]] == [8, 9]
    assert len(calls) == 2


def test_observation_filter_proving_membership_pushes_offset_into_sqlite(corpus: Path) -> None:
    fetched: list[int] = []

    def observe(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> tuple[Any, ...]:
        if cursor.description and cursor.description[0][0] == "ordinal":
            fetched.append(int(row[0]))
        return row

    with read_transaction(corpus, BINDING) as connection:
        connection.row_factory = observe
        page = investigation_queries.observation_search(
            connection, query="API.YAML", kind="openapi.operation", path="api.yaml",
            limit=1, offset=1, include_raw=True,
        )
    assert page["total"] == 2 and page["has_more"] is True
    assert [row["path"] for row in page["results"]] == ["api.yaml"]
    assert len(fetched) == 2 and fetched[0] == page["results"][0]["ordinal"]
