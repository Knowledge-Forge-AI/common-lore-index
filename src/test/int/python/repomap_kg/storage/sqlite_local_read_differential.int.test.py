"""Crafted-corpus differential: SQLite Local reads vs PostgreSQL query owners (LOCAL4).

One public-safe raw-observation corpus (``read_corpus_bundle``) is published by
the SQLite Local publisher and by the incumbent portable PostgreSQL publisher
into a disposable C-collation database under the same ``graph:`` root. The
five summary operations, the legacy storage summary, the configured
neighborhood and observation-search pages are then compared with the
maintained PostgreSQL owners: the ``query_*_summary`` functions,
``query_canonical_storage_summary``, ``query_canonical_neighborhood`` and the
MCP observation SQL (``build_mcp_search_sql``) through the JSON readback owner.

The corpus reaches predicate branches a tiny real tree does not: boolean
spellings, ``?`` on objects and arrays, ``LIKE`` wildcards, NULL operands under
``<>``/``NOT LIKE``, method case, tfvars paths, Nix sections/shapes/patterns,
and the ``jsonb::text`` spellings observation search matches. Only run ids
are normalized (PostgreSQL's single run maps to SQLite run 1); observation
rows are compared as exact JSON text, so key order and number spelling count.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from common_lore_index.server._ops_search import build_mcp_search_sql
from common_lore_index.storage import (
    apply_migrations,
    default_rdbms_root,
    query_canonical_neighborhood,
    query_canonical_storage_summary,
    query_js_framework_summary,
    query_nix_summary,
    query_openapi_summary,
    query_python_summary,
    query_terraform_summary,
)
from common_lore_index.storage.canonical import (
    query_canonical_edge_explanation,
    query_canonical_edge_records,
    query_canonical_node_records,
)
from common_lore_index.storage.readback_driver import READBACK_DRIVER_ENV, execute_json_readback
from common_lore_index.storage.sqlite_local import (
    api_summary_queries,
    investigation_queries,
    nix_summary_queries,
    queries,
    summary_queries,
)
from common_lore_index.storage.sqlite_local.connection import initialize_graph_database, read_transaction
from common_lore_index.storage.sqlite_local.publisher import publish_generation
from common_lore_index_test_support.portable_publication_fixtures import publish_portable_bundle
from common_lore_index_test_support.postgres_harness import require_postgres_binaries, temporary_postgres
from common_lore_index_test_support.sqlite_local_fixtures import local_binding, publication_for
from common_lore_index_test_support.sqlite_local_read_corpus import read_corpus_bundle

GRAPH = "read-corpus"
ROOT = f"graph:{GRAPH}"
C_COLLATIONS = frozenset({"C", "POSIX", "C.UTF-8", "C.utf8"})
HEADLINES = {"python": "python_observations", "terraform": "terraform_observations",
             "openapi": "openapi_observations", "js_framework": "framework_observations",
             "nix": "nix_observations"}
SUMMARIES: dict[str, tuple[Callable[..., Any], Callable[..., Any]]] = {
    "python": (query_python_summary, summary_queries.python_summary),
    "terraform": (query_terraform_summary, summary_queries.terraform_summary),
    "openapi": (query_openapi_summary, api_summary_queries.openapi_summary),
    "js_framework": (query_js_framework_summary, api_summary_queries.js_framework_summary),
    "nix": (query_nix_summary, nix_summary_queries.nix_summary),
}
# (query, kind, path, limit, offset, include_raw)
SEARCHES: tuple[tuple[str, str | None, str | None, int, int, bool], ...] = (
    ("#corpus:", None, None, 100, 0, False),
    ("#corpus:", None, None, 7, 7, True),
    ("API.YAML", "openapi.operation", "api.yaml", 1, 1, True),
    ("CORPUS", None, None, 3, 68, False),
    ("tfvars", "terraform.file", None, 10, 0, True),
    ("corpus", "openapi.operation", "api.yaml", 2, 1, False),
    ('"b": 150, "c": "', None, None, 5, 0, True),
    ('"b":150', None, None, 5, 0, False),
    ('"e": 100000000000000000000', None, None, 5, 0, False),
    ("100% done_\\x", None, None, 5, 0, False),
    ("100%x", None, None, 5, 0, False),
    ("doneXx", None, None, 5, 0, False),
    ('\\"quoted\\"\\ttab', None, None, 5, 0, False),
    ('"quoted"', None, None, 5, 0, False),
    ("ÄRGER", None, None, 5, 0, False),
    ("ÄRGER", "custom.note", None, 1, 0, True),
    ("CUSTOM.NOTE", "custom.note", None, 1, 0, False),
    ("ärger", None, None, 5, 0, False),
    ("Ünïcode", None, None, 5, 0, True),
    ('"program": null', None, None, 5, 0, False),
    ("common_lore_index", None, None, 5, 0, False),
    ("repomapXkg", None, None, 5, 0, False),
)


def _runs(value: Any, pg_run: int) -> Any:
    if isinstance(value, dict):
        return {key: (1 if key.endswith("run_id") and item == pg_run else _runs(item, pg_run))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_runs(item, pg_run) for item in value]
    return value


def test_cloe_and_legacy_dogfood_markers_match_in_both_stores(tmp_path: Path, monkeypatch):
    from common_lore_index_test_support import sqlite_local_read_corpus as corpus
    require_postgres_binaries()
    monkeypatch.setenv(READBACK_DRIVER_ENV, "psycopg")
    cases: tuple[tuple[str, dict[str, str], bool], ...] = (
        ("synthetic/repomap_kg/requirements.txt", {}, True),
        ("synthetic/common_lore_index/requirements.txt", {}, True),
        ("pkg/requirements.txt", {"project_name": "common-lore-index-sample"}, True),
        ("pkg/requirements.txt", {}, False),
    )
    with temporary_postgres() as postgres:
        for index, (source_path, metadata, expected) in enumerate(cases):
            graph = f"dogfood-{index}"
            root = "graph:" + graph
            database = postgres.create_database(f"cloe_dogfood_{index}")
            apply_migrations(default_rdbms_root(), database.psql_args, psql_command=database.psql_command)
            monkeypatch.setattr(corpus, "CORPUS_ROWS", (("python.package_file", source_path, metadata, {}),))
            bundle = corpus.read_corpus_bundle(graph)
            publish_portable_bundle(database.psql_args, bundle, repository_name=graph, root_path=root)
            path = tmp_path / f"{graph}.sqlite3"
            binding = local_binding(graph)
            initialize_graph_database(path, binding, applied_at="2026-10-30T00:00:00Z")
            publish_generation(path, publication_for(bundle, graph), bundle.families, expected_generation=0)
            pg = query_python_summary(database.psql_args, root_path=root, psql_command=database.psql_command)
            with read_transaction(path, binding) as connection:
                local = summary_queries.python_summary(connection, root_path=root)
            assert local.python_observations == pg.python_observations == 1
            assert local.dogfooding == pg.dogfooding
            assert local.dogfooding["repo_map_profile_observed"] is expected


def test_read_corpus_matches_postgresql_query_owners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    require_postgres_binaries()
    for name in ("PGPASSWORD", "REPOMAP_PG_PASSWORD", "CLOE_STORAGE_PG_CONNECTOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(READBACK_DRIVER_ENV, "psycopg")
    with temporary_postgres() as postgres:
        name = f"sqlite_differential_{uuid.uuid4().hex[:10]}"
        postgres.psql_scalar(
            f'CREATE DATABASE "{name}" WITH TEMPLATE template0 ENCODING \'UTF8\' '
            "LC_COLLATE 'C' LC_CTYPE 'C';"
        )
        try:
            database = replace(postgres, database=name)
            if getattr(database, "password", None) is not None:
                monkeypatch.setenv("PGPASSWORD", database.password)
            _prove(tmp_path, database)
        finally:
            postgres.psql_scalar(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE);')


def _compare_edge_pages(connection: sqlite3.Connection, database: Any, run_id: int) -> None:
    pg: dict[str, Any] = {"root_path": ROOT, "psql_command": database.psql_command}
    expected = query_canonical_edge_records(database.psql_args, limit=2, offset=0, **pg)
    actual = queries.canonical_edges(
        connection, kind=None, source_key=None, target_key=None, graph_key_version=1, limit=2, offset=0,
    )
    assert [asdict(edge) for edge in actual] == [_runs(asdict(edge), run_id) for edge in expected]
    assert actual
    first = actual[0]
    window: dict[str, Any] = {
        "source_key": first.source_key, "kind": first.edge_kind, "target_key": first.target_key,
        "identity_metadata_hash": first.identity_metadata_hash, "graph_key_version": 1,
        "evidence_limit": 1, "evidence_offset": 0,
    }
    actual_explanation = queries.canonical_edge_explanation(connection, **window).to_dict()
    pg_explanation = query_canonical_edge_explanation(database.psql_args, **pg, **window).to_dict()
    assert actual_explanation == _runs(pg_explanation, run_id) and actual_explanation["evidence"]


def _prove(tmp_path: Path, database: Any) -> None:
    collation = database.psql_scalar("SELECT datcollate || '|' || datctype FROM pg_database "
                                     "WHERE datname = current_database();")
    assert set(collation.split("|")) <= C_COLLATIONS, f"comparator is not byte-ordered C: {collation}"
    apply_migrations(default_rdbms_root(), database.psql_args, psql_command=database.psql_command)
    bundle = read_corpus_bundle(GRAPH, extra_rows=(
        ("python.indexed_probe", "indexed_probe.txt", {}, {}),
        ("Python.indexed_probe", "upper_case_probe.txt", {}, {}),
        ("pythone.indexed_probe", "outside_prefix_probe.txt", {}, {}),
    ))
    python_count = sum(str(row["kind"]).startswith("python.") for row in bundle.families["raw_observations"])
    loaded = publish_portable_bundle(database.psql_args, bundle, repository_name=GRAPH, root_path=ROOT)
    pg_run = int(loaded.run_id)
    path = tmp_path / f"{GRAPH}.sqlite3"
    binding = local_binding(GRAPH)
    initialize_graph_database(path, binding, applied_at="2026-09-29T00:00:00Z")
    publish_generation(path, publication_for(bundle, GRAPH), bundle.families, expected_generation=0)
    pg = {"psql_command": database.psql_command}

    with read_transaction(path, binding) as connection:
        for family, (pg_owner, sqlite_owner) in SUMMARIES.items():
            expected = asdict(pg_owner(database.psql_args, root_path=ROOT, **pg))
            actual = asdict(sqlite_owner(connection, root_path=ROOT))
            assert actual[HEADLINES[family]] > 0, family  # never a zero-vs-zero comparison
            assert actual == expected, (family, actual, expected)
            if family == "python":
                assert actual["python_observations"] == python_count

        expected_summary = _runs(asdict(query_canonical_storage_summary(
            database.psql_args, root_path=ROOT, **pg)), pg_run)
        actual_summary = asdict(investigation_queries.storage_summary(connection, root_path=ROOT))
        assert actual_summary == expected_summary and actual_summary["raw_observations"] > 0
        _compare_edge_pages(connection, database, pg_run)

        centers = [node.canonical_key for node in queries.canonical_nodes(
            connection, kind=None, canonical_key=None, path_prefix=None, graph_key_version=1,
            limit=None, offset=0)]
        for prefix, limit, offset in ((".", 3, 1), ("pkg/", 10, 0), ("PKG", 10, 0)):
            expected_nodes = query_canonical_node_records(
                database.psql_args, root_path=ROOT, path_prefix=prefix, limit=limit, offset=offset, **pg,
            )
            actual_nodes = queries.canonical_nodes(
                connection, kind=None, canonical_key=None, path_prefix=prefix,
                graph_key_version=1, limit=limit, offset=offset,
            )
            assert [asdict(node) for node in actual_nodes] == [_runs(asdict(node), pg_run) for node in expected_nodes]
        compared_edges = 0
        for center in (*centers, "file:absent.nix"):
            for direction in ("both", "in", "out"):
                expected_around = _runs(asdict(query_canonical_neighborhood(
                    database.psql_args, root_path=ROOT, node=center, direction=direction, depth=1,
                    graph_key_version=1, **pg)), pg_run)
                actual_around = asdict(queries.canonical_neighborhood(
                    connection, node=center, direction=direction, depth=1, graph_key_version=1,
                    node_limit=None, node_offset=0, edge_limit=None, edge_offset=0))
                assert actual_around == expected_around, (center, direction)
                compared_edges += len(actual_around["edges"])
        assert compared_edges > 0

        for node_query in ("file", "docs", "%"):
            payload = investigation_queries.search(
                connection, target="nodes", query=node_query, kind=None, path=None, limit=1, offset=0,
            )
            pg_page = execute_json_readback(
                build_mcp_search_sql(root_path=ROOT, target="nodes", query=node_query, kind=None,
                                     path=None, limit=1, offset=0, include_raw=False),
                psql_args=database.psql_args, label="node search differential", expected_shape="array", **pg,
            )
            assert isinstance(pg_page, list)
            assert payload["has_more"] == (len(pg_page) > 1)
            assert [node["canonical_key"] for node in payload["results"]] == [node["canonical_key"] for node in pg_page[:1]]

        nonempty = 0
        for query, kind, file_path, limit, offset, include_raw in SEARCHES:
            rows = execute_json_readback(
                build_mcp_search_sql(root_path=ROOT, target="observations", query=query, kind=kind,
                                     path=file_path, limit=limit, offset=offset, include_raw=include_raw),
                psql_args=database.psql_args, label="observation search differential",
                expected_shape="array", **pg,
            )
            assert isinstance(rows, list)
            expected_page = {"results": rows[:limit], "total": offset + len(rows[:limit]),
                             "has_more": len(rows) > limit}
            actual_page = investigation_queries.observation_search(
                connection, query=query, kind=kind, path=file_path, limit=limit, offset=offset,
                include_raw=include_raw)
            assert json.dumps(actual_page, ensure_ascii=False) == json.dumps(
                expected_page, ensure_ascii=False), (query, actual_page, expected_page)
            nonempty += bool(actual_page["results"])
        assert nonempty >= 12, nonempty
