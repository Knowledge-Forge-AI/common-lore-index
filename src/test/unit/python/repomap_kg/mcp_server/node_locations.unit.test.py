"""Product callable plus real SQLite adapter tests for stored-source navigation."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from common_lore_index.runtime.database_role_contract import read_role_secrets
from common_lore_index.runtime.local import setup_local_runtime
from common_lore_index.server import mcp
from common_lore_index.server.mcp_core import RepoMapMcpError
from common_lore_index.storage.sqlite_local.connection import read_transaction
from common_lore_index.storage.sqlite_local.publisher import publish_generation
from common_lore_index_test_support.host_read_store_config import (
    NO_FALLBACK_GUARDS, PG_BINDING_TRIPWIRES, patched_guards, setup_owned_config,
)
from common_lore_index_test_support.node_location_fixtures import (
    BINDING, DOC, GRAPH, SYMBOL, locator_home, publish_locator_generation,
)
from common_lore_index_test_support.sqlite_local_fixtures import generation_bundle, publication_for


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home, database = locator_home(tmp_path)
    monkeypatch.setenv("CLOE_HOME", str(home))
    monkeypatch.delenv("CLOE_OPS_CONFIG", raising=False)
    return database


def call(key: str = SYMBOL, *, graph: str = GRAPH, limit: object = 10) -> dict[str, Any]:
    return mcp.handle_tool_call("cloe_node_locations", {
        "graph_id": graph, "canonical_key": key, "limit": limit,
    })["structuredContent"]


def edit(database: Path, sql: str, params: tuple = ()) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(sql, params)


def test_symbol_evidence_bounds_and_legacy_search_are_source_blind(store: Path, tmp_path: Path) -> None:
    stored_digest = hashlib.sha256(store.read_bytes()).hexdigest()
    arguments: dict[str, Any] = {"graph_id": GRAPH, "query": "sample.service", "limit": 1}
    with patched_guards(PG_BINDING_TRIPWIRES):
        before = mcp.cloe_search_nodes(**arguments)
        first, every = call(limit=1), call()
        after = mcp.cloe_search_nodes(**arguments)
        second = mcp.cloe_search_nodes(**arguments, offset=1)
    assert before == after
    assert hashlib.sha256(store.read_bytes()).hexdigest() == stored_digest
    assert set(before) == {"server", "version", "read_only", "graph", "target", "query", "kind",
                           "path", "limit", "offset", "result_count", "total", "has_more", "results", "safety"}
    assert before["results"][0]["canonical_key"] == SYMBOL
    assert before["results"][0]["metadata"] == {}
    assert (before["total"], before["has_more"], second["total"], second["has_more"]) == (1, False, 1, False)
    assert first["has_more"] is True and every["has_more"] is False
    assert first["locations"] == every["locations"][:1]
    assert first["ambiguity"] == "truncated" and every["ambiguity"] == "multiple_stored_locations"
    assert every["status"] == "located" and every["freshness"] == "unverified"
    assert every["repository_identity"] == "repo1:locator"
    assert [row["path"] for row in every["locations"]] == ["root/src/service.py", "root/alternate/service.py"]
    row = first["locations"][0]
    assert (row["start_line"], row["end_line"], row["link_kind"]) == (5, 15, "observed")
    assert row["raw_observation"]["kind"] == "python.function"
    assert row["raw_observation"]["payload_hash"] and row["evidence_key"]
    assert row["publication_relation"] == "accepted_run" and row["publication"]["publication_bundle_id"]
    assert row["source"]["binding_id"] == BINDING and row["source"]["snapshot_id"].startswith("snap1:")
    assert row["source"]["binding_revision"] == 1 and row["source"]["binding_alias"] == "root"
    assert [item["source"]["source_relative_path"] for item in every["locations"]] == [
        "src/service.py", "alternate/service.py",
    ]
    assert str(tmp_path) not in json.dumps(every)
    assert not (tmp_path / "never-created-source").exists()


def test_document_stored_path_and_metadata_only_fallback(store: Path) -> None:
    result = call(DOC)
    assert result["status"] == "located"
    assert result["locations"][0]["path"] == "root/docs/guide.md"
    assert result["locations"][0]["range_status"] == "missing_or_invalid"
    assert result["ambiguity"] == "single_stored_location"
    assert result["locations"][0]["path_namespace"] == "graph_source_relative"
    assert result["metadata_location"]["path_namespace"] == "source_relative"
    assert result["metadata_location"]["binding_status"] == "unavailable"
    edit(store, "DELETE FROM canonical_node_evidence WHERE canonical_node_id = "
         "(SELECT id FROM canonical_nodes WHERE canonical_key = ?)", (DOC,))
    result = call(DOC)
    assert result["status"] == "metadata_only" and result["locations"] == []
    assert result["metadata_location"]["basis"] == "node_metadata.doc_path"
    assert result["metadata_location"]["path"] == "docs/guide.md"
    assert result["metadata_location"]["start_line"] is None
    assert "run_id" not in result["metadata_location"]
    assert "publication_relation" not in result["metadata_location"]


@pytest.mark.parametrize("path", ["", "/Users/example/private.py", "../outside.py", "https://example.test/a",
                                    "C:\\private.py", "root/" + "x" * 501])
def test_missing_or_unsafe_paths_are_ordinary_unavailable_results(store: Path, path: str) -> None:
    edit(store, "UPDATE canonical_evidence SET path = ?", (path,))
    result = call()
    assert result["status"] == "unavailable" and result["fallback"] == "native_search"
    assert all(row["path"] is None for row in result["locations"])
    if path:
        assert path not in json.dumps(result)


def test_missing_range_evidence_and_node_are_ordinary_results(store: Path) -> None:
    edit(store, "UPDATE canonical_evidence SET start_line = NULL, end_line = NULL")
    result = call()
    assert result["status"] == "located"
    assert all(row["start_line"] is None and row["end_line"] is None for row in result["locations"])
    edit(store, "DELETE FROM canonical_node_evidence")
    assert call()["reason"] == "no_usable_location"
    assert call("python.function:absent:serve")["reason"] == "node_not_found"
    assert call(graph="unpublished")["reason"] == "publication_absent"


def test_historical_nodes_keep_evidence_and_never_claim_current_membership(store: Path) -> None:
    publish_locator_generation(store, 2, start_line=40, alternatives=False)
    bundle = generation_bundle(2)
    publish_generation(store, publication_for(bundle, GRAPH), bundle.families, expected_generation=2)
    result = call()
    assert result["status"] == "located" and result["accepted_publication"]["run_id"] == 3
    assert result["node_last_seen_run_id"] == result["location_run_id"] == 2
    assert result["location_scope"] == "historical_run" and result["ambiguity"] == "single_stored_location"
    assert result["locations"][0]["start_line"] == 40
    assert all(row["publication_relation"] == "historical_run" for row in result["locations"])
    assert [row["run_id"] for row in result["historical_locations"]] == [1, 1]


@pytest.mark.parametrize("graph, message", [("hidden", "not MCP-visible"), ("disabled", "not enabled"),
                                           ("unknown", "unknown graph_id")])
def test_graph_selection_refuses_before_database_open(store: Path, graph: str, message: str) -> None:
    with patch("common_lore_index.server.sqlite_read_binding.read_transaction", side_effect=AssertionError("open")):
        with pytest.raises(RepoMapMcpError, match=message):
            call(graph=graph)


def test_graph_and_repository_identity_isolation(store: Path) -> None:
    assert call(graph="sibling")["reason"] == "node_not_found"
    edit(store, "UPDATE graph_binding SET repository_identity = 'repo1:foreign'")
    with pytest.raises(RepoMapMcpError, match="graph-database-graph-mismatch"):
        call()


@pytest.mark.parametrize("limit", [0, 101, True, "2"])
def test_limit_validation_precedes_io(store: Path, limit: object) -> None:
    with patch("common_lore_index.server.sqlite_read_binding.read_transaction", side_effect=AssertionError("open")):
        with pytest.raises(RepoMapMcpError, match="between 1 and 100"):
            call(limit=limit)


def test_search_literal_order_and_pages_are_unchanged(store: Path) -> None:
    args: dict[str, Any] = {"graph_id": GRAPH, "query": "root"}
    before = mcp.cloe_search_nodes(**args, limit=100)
    call()
    assert mcp.cloe_search_nodes(**args, limit=100) == before
    keys = [row["canonical_key"] for row in before["results"]]
    assert len(keys) >= 3 and keys == sorted(keys)
    first = mcp.cloe_search_nodes(**args, limit=2)
    second = mcp.cloe_search_nodes(**args, limit=2, offset=2)
    assert first["results"] + second["results"] == before["results"][:4]
    assert first["total"] == 2 and first["has_more"] is True
    assert second["total"] == min(4, len(keys))
    assert mcp.cloe_search_nodes(graph_id=GRAPH, query="sample%service")["results"] == []


def test_node_search_lookahead_does_not_transfer_metadata(store: Path) -> None:
    from common_lore_index.storage.sqlite_local.investigation_queries import search

    transferred: list[str | None] = []
    with closing(sqlite3.connect(store)) as connection:
        def observe(cursor, row):
            if len(cursor.description) == 9 and cursor.description[6][0] == "metadata_json":
                transferred.append(row[6])
            return row

        connection.row_factory = observe
        page = search(connection, target="nodes", query="root", kind=None, path=None, limit=1, offset=0)
    assert page["has_more"] and len(page["results"]) == 1
    assert len(transferred) == 2 and transferred[0] is not None and transferred[1] is None


def test_contradictory_ranges_remain_distinct_without_reranking(store: Path) -> None:
    edit(store, "UPDATE canonical_evidence SET path = 'root/src/service.py'")
    result = call()
    assert result["ambiguity"] == "multiple_stored_locations"
    assert [(row["start_line"], row["end_line"]) for row in result["locations"]] == [(5, 15), (20, 23)]


def test_unbound_metadata_path_is_not_compared_across_namespaces(store: Path) -> None:
    edit(store, "UPDATE canonical_nodes SET metadata_json = ? WHERE canonical_key = ?",
         (json.dumps({"doc_path": "docs/other.md"}), DOC))
    result = call(DOC)
    assert result["ambiguity"] == "single_stored_location"
    assert result["locations"][0]["path"] == "root/docs/guide.md"
    assert result["metadata_location"]["path"] == "docs/other.md"


def test_repeated_refreshes_do_not_fill_the_accepted_window(store: Path) -> None:
    for generation in range(2, 13):
        publish_locator_generation(store, generation, alternatives=False)
    result = call(limit=2)
    assert result["location_scope"] == "accepted_run"
    assert result["location_run_id"] == 12
    assert result["ambiguity"] == "single_stored_location" and result["has_more"] is False
    assert len(result["locations"]) == 1 and result["locations"][0]["run_id"] == 12
    assert result["historical_has_more"] is True and len(result["historical_locations"]) == 2
    assert [row["run_id"] for row in result["historical_locations"]] == [11, 10]
    assert call(limit=2) == result


def test_line_shift_is_history_not_current_ambiguity(store: Path) -> None:
    publish_locator_generation(store, 2, start_line=40, alternatives=False)
    result = call()
    assert result["ambiguity"] == "single_stored_location" and result["has_more"] is False
    assert [(row["start_line"], row["end_line"]) for row in result["locations"]] == [(40, 50)]
    assert [(row["start_line"], row["end_line"]) for row in result["historical_locations"]] == [(5, 15), (20, 23)]
    assert all(row["publication_relation"] == "historical_run" for row in result["historical_locations"])


def test_no_usable_path_in_truncated_window_is_explicitly_incomplete(store: Path) -> None:
    edit(store, "UPDATE canonical_evidence SET path = '' WHERE start_line = 5")
    result = call(limit=1)
    assert result["status"] == "incomplete" and result["reason"] == "no_usable_location_in_window"
    assert result["has_more"] is True and result["fallback"] == "native_search"
    assert call()["status"] == "located"


def test_canonical_evidence_metadata_is_authoritative_for_source_binding(store: Path) -> None:
    snapshot = call()["locations"][0]["source"]["snapshot_id"]
    edit(store, "UPDATE canonical_evidence SET metadata_json = ? WHERE start_line = 5",
         (json.dumps({"binding_id": BINDING, "binding_alias": "canonical", "binding_revision": 7,
                      "source_relative_path": "override/service.py"}),))
    source = call()["locations"][0]["source"]
    assert source == {"binding_id": BINDING, "binding_alias": "canonical", "binding_revision": 7,
                      "source_relative_path": "override/service.py"}
    edit(store, "UPDATE canonical_evidence SET metadata_json = ? WHERE start_line = 5",
         (json.dumps({"binding_id": BINDING, "snapshot_id": snapshot}),))
    assert call()["locations"][0]["source"]["binding_revision"] == 1


def test_missing_raw_observation_retains_evidence_with_explicit_status(store: Path) -> None:
    edit(store, "DELETE FROM raw_observations WHERE kind = 'python.function'")
    result = call()
    assert result["status"] == "located" and len(result["locations"]) == 2
    assert all(row["raw_observation"]["status"] == "unavailable" for row in result["locations"])
    assert all(row["raw_observation"]["payload_hash"] is None for row in result["locations"])
    assert all(row["raw_observation"]["kind"] == "python.function" for row in result["locations"])


def test_historical_paths_use_the_same_navigation_sanitization(store: Path) -> None:
    edit(store, "UPDATE canonical_evidence SET path = '/Users/example/private.py'")
    publish_locator_generation(store, 2, alternatives=False)
    result = call()
    assert result["status"] == "located" and result["locations"][0]["path"] == "root/src/service.py"
    assert all(row["path"] is None for row in result["historical_locations"])
    assert "/Users/example/private.py" not in json.dumps(result)


def test_redacted_paths_and_missing_ranges_do_not_become_navigation_targets(store: Path, tmp_path: Path) -> None:
    edit(store, "UPDATE canonical_evidence SET path = ?, start_line = 9, end_line = 2",
         ("root/" + str(tmp_path / "never-created-source") + "/hidden.py",))
    result = call()
    assert result["status"] == "unavailable" and str(tmp_path) not in json.dumps(result)
    assert all(row["path"] is None and row["start_line"] is None for row in result["locations"])


def test_postgres_helper_routes_via_read_status_and_refuses_missing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    unauth = tmp_path / "unauth-home"
    unauth.mkdir()
    (unauth / "repomap.local.cloe.toml").write_text(setup_owned_config(), encoding="utf-8")
    monkeypatch.setenv("CLOE_HOME", str(unauth))
    monkeypatch.delenv("CLOE_OPS_CONFIG", raising=False)
    with (
        patched_guards(NO_FALLBACK_GUARDS),
        patch("common_lore_index.storage.postgres_node_locations.execute_json_readback", side_effect=AssertionError("io")),
    ):
        with pytest.raises(RepoMapMcpError, match="read/status credential unavailable or unsafe") as exc_info:
            call(graph="host-one")
        assert str(tmp_path) not in str(exc_info.value) and "REPOMAP_READ_STATUS_PASSWORD" not in str(exc_info.value)

    home = tmp_path / "postgres-home"
    setup_local_runtime(home)
    (home / "repomap.local.cloe.toml").write_text(setup_owned_config(), encoding="utf-8")
    secret = read_role_secrets(home / "runtime" / ".env").read_status
    monkeypatch.setenv("CLOE_HOME", str(home))
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_exec(sql: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((sql, kwargs))
        row = {
            "evidence_key": "ev1:11", "link_kind": "observed", "run_id": 7, "raw_observation_ordinal": 1,
            "payload_hash": "b" * 64, "raw_kind": "python.function", "raw_source_id": "src1:1",
            "path": "root/src/service.py", "start_line": 5, "end_line": 15, "extractor": "python-ast",
            "extractor_version": "1", "confidence": "extracted",
            "metadata_json": {"binding_id": BINDING, "binding_alias": "root", "binding_revision": 1,
                              "snapshot_id": "snap1:host", "source_relative_path": "src/service.py"},
            "publication_bundle_id": "bundle1:" + "a" * 64, "snapshot_manifest_id": "snapmanifest1:" + "c" * 64,
            "extraction_receipt_id": "receipt1:" + "d" * 64, "graph_candidate_id": "cand1:" + "e" * 64,
            "snapshot_vector_json": [{"binding_id": BINDING, "revision": 1, "snapshot_id": "snap1:host"}],
            "evidence_role": "direct",
        }
        return {
            "has_accepted": True,
            "accepted": {"generation": 2, "run_id": 7, "publication_bundle_id": "bundle1:" + "a" * 64},
            "has_node": True,
            "node": {"id": 11, "metadata_json": {}, "first_seen_run_id": 6, "last_seen_run_id": 7},
            "selected_run_id": 7,
            "current_rows": [row],
            "historical_rows": [{**row, "run_id": 6, "path": "/Users/example/private.py"}],
        }

    with (
        patched_guards(NO_FALLBACK_GUARDS),
        patch("common_lore_index.storage.postgres_node_locations.execute_json_readback", side_effect=fake_exec),
    ):
        result = call(graph="host-one")
    assert len(calls) == 1
    sql, kwargs = calls[0]
    assert kwargs["psql_args"] == ["-h", "127.0.0.1", "-p", "55439", "-U", "repomap_read_status", "-d", "repomap_host_one"]
    assert kwargs["password"] == secret and kwargs["label"] == "node locations" and kwargs["expected_shape"] == "object"
    assert sql.startswith("WITH matched_repo AS MATERIALIZED") and "cloe_lexical" not in sql
    assert result["status"] == "located" and result["fallback"] == "native_search"
    assert result["locations"][0]["path"] == "root/src/service.py"
    assert result["historical_locations"][0]["path"] is None
    serialized = json.dumps(result)
    assert secret not in serialized and "/Users/example/private.py" not in serialized and str(tmp_path) not in serialized
    assert "chunk_text" not in serialized and "normalized_text" not in serialized


def test_node_location_pages_decode_only_selected_and_historical_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common_lore_index.storage.node_locations import NodeLocationsQuery
    from common_lore_index.storage.sqlite_local import node_location_queries
    from common_lore_index_test_support.sqlite_local_fixtures import local_binding

    _, database = locator_home(tmp_path)
    for generation in (2, 3):
        publish_locator_generation(database, generation, start_line=40, alternatives=True)
    render = node_location_queries._location
    decoded_runs: list[int] = []

    def observe(row: Any, accepted_run_id: int, **kwargs: Any) -> dict[str, Any]:
        decoded_runs.append(row[2])
        return render(row, accepted_run_id, **kwargs)

    monkeypatch.setattr(node_location_queries, "_location", observe)
    with read_transaction(database, local_binding(GRAPH)) as connection:
        page = node_location_queries.node_locations(
            connection, NodeLocationsQuery(canonical_key=SYMBOL, limit=1),
        )
    assert decoded_runs == [3, 2]
    assert page["location_scope"] == "accepted_run" and page["location_run_id"] == 3
    assert page["has_more"] and page["historical_has_more"]


def test_node_location_reuses_vector_across_adjacent_rows(tmp_path, monkeypatch) -> None:
    from itertools import groupby

    from common_lore_index.storage.node_locations import NodeLocationsQuery
    from common_lore_index.storage.sqlite_local import node_location_queries
    from common_lore_index_test_support.sqlite_local_fixtures import local_binding

    _, database = locator_home(tmp_path)
    publish_locator_generation(database, 2, start_line=40, alternatives=True)
    original = json.loads
    queried_vectors = []

    def observe(raw):
        value = original(raw)
        if isinstance(value, list) and value and isinstance(value[0], list):
            queried_vectors.append(raw)
        return value

    monkeypatch.setattr(node_location_queries.json, "loads", observe)
    with read_transaction(database, local_binding(GRAPH)) as connection:
        vectors = dict(connection.execute("SELECT id, snapshot_vector_json FROM runs"))
        page = node_location_queries.node_locations(
            connection, NodeLocationsQuery(canonical_key=SYMBOL, limit=5),
        )
    assert len(page["locations"]) > 1 and page["historical_locations"]
    raw = [vectors[loc["run_id"]] for loc in page["locations"] + page["historical_locations"]]
    assert queried_vectors == [vector for vector, _ in groupby(raw)]
