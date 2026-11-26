"""W4 readback work ceilings and single-snapshot boundary tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import sqlite3

import pytest

from common_lore_index.retrieval.context_work import (
    MAX_LOGICAL_READ_UNITS,
    MAX_SCAN_UNITS,
    MAX_SEED_EXPANSIONS,
    MAX_SQL_STATEMENTS_POSTGRES,
    MAX_SQL_STATEMENTS_SQLITE,
    SQLITE_CONTEXT_PROGRESS_CALLBACK_LIMIT,
    SQLITE_PROGRESS_HANDLER_INTERVAL,
    WALL_CLOCK_DEADLINE_MS,
    ContextWorkExhaustedError,
    GuardedSQLiteConnection,
    WorkLedger,
)
import common_lore_index.server.context_read_sqlite as sqlite_context
from common_lore_index.server.context_read_sqlite import SqliteContextReadAdapter
from common_lore_index.storage.errors import StorageSchemaError
from common_lore_index.storage.navigation_search import NavigationSearchQuery
from common_lore_index.storage.node_locations import NodeLocationsQuery
from common_lore_index.storage.postgres_navigation_search import read_postgres_navigation_search_with_executor
from common_lore_index.storage.postgres_node_locations import read_postgres_node_locations_with_executor
from common_lore_index_test_support.context_fixture import build_sqlite_context_fixture


class _CountingConnection(sqlite3.Connection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.executed_sql: list[str] = []

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self.executed_sql.append(sql)
        return super().execute(sql, *args, **kwargs)


def test_zero_sqlite_progress_callbacks_are_truthful() -> None:
    ledger = WorkLedger(backend="sqlite")
    assert ledger.record_statement_start()
    report = ledger.to_dict()
    assert report["sqlite_progress_callbacks"] == 0
    assert report["sqlite_vm_steps_estimate"] == 0


def test_progress_handler_stops_at_the_250_callback_boundary() -> None:
    ledger = WorkLedger(backend="sqlite", sqlite_progress_callback_limit=1000)
    assert ledger.sqlite_progress_callback_limit == SQLITE_CONTEXT_PROGRESS_CALLBACK_LIMIT == 250
    assert ledger.record_statement_start()
    handler = ledger.make_sqlite_progress_handler()
    for _ in range(SQLITE_CONTEXT_PROGRESS_CALLBACK_LIMIT - 1):
        assert handler() == 0
    assert handler() == 1
    assert ledger.sqlite_progress_callbacks == SQLITE_CONTEXT_PROGRESS_CALLBACK_LIMIT
    assert ledger.exhausted_reason == "work_budget_exhausted"


def test_work_ledger_clamps_upward_overrides_but_preserves_lower_limits() -> None:
    sqlite_ledger = WorkLedger(
        backend="sqlite",
        max_sql_statements=100,
        max_logical_read_units=100,
        max_scan_units=100,
        max_seed_expansions=100,
        sqlite_progress_callback_limit=1000,
        sqlite_progress_interval_ops=10000,
        deadline_ms=100000,
    )
    assert sqlite_ledger.max_sql_statements == MAX_SQL_STATEMENTS_SQLITE
    assert sqlite_ledger.max_logical_read_units == MAX_LOGICAL_READ_UNITS
    assert sqlite_ledger.max_scan_units == MAX_SCAN_UNITS
    assert sqlite_ledger.max_seed_expansions == MAX_SEED_EXPANSIONS
    assert sqlite_ledger.sqlite_progress_interval_ops == SQLITE_PROGRESS_HANDLER_INTERVAL
    assert sqlite_ledger.deadline_ms == WALL_CLOCK_DEADLINE_MS

    postgres_ledger = WorkLedger(backend="postgresql", max_sql_statements=100)
    assert postgres_ledger.max_sql_statements == MAX_SQL_STATEMENTS_POSTGRES

    lower = WorkLedger(
        backend="sqlite",
        max_sql_statements=1,
        max_logical_read_units=2,
        max_scan_units=3,
        max_seed_expansions=4,
        sqlite_progress_callback_limit=5,
        sqlite_progress_interval_ops=6,
        deadline_ms=7,
    )
    assert lower.max_sql_statements == 1
    assert lower.max_logical_read_units == 2
    assert lower.max_scan_units == 3
    assert lower.max_seed_expansions == 4
    assert lower.sqlite_progress_callback_limit == 5
    assert lower.sqlite_progress_interval_ops == 6
    assert lower.deadline_ms == 7


def test_guard_refuses_statement_37_before_native_execute() -> None:
    connection = sqlite3.connect(":memory:", factory=_CountingConnection)
    ledger = WorkLedger(backend="sqlite")
    ledger.sql_statements_executed = MAX_SQL_STATEMENTS_SQLITE
    guarded = GuardedSQLiteConnection(connection, ledger)

    with pytest.raises(ContextWorkExhaustedError) as error:
        guarded.execute("SELECT 37")

    assert error.value.reason == "statement_limit"
    assert not any("SELECT 37" in sql for sql in connection.executed_sql)
    assert ledger.sql_statements_executed == MAX_SQL_STATEMENTS_SQLITE


def test_sqlite_adapter_stops_a_multi_statement_unit_before_statement_37(
    tmp_path: Path,
) -> None:
    db_path, _config, graph, _binding, _adapter = build_sqlite_context_fixture(tmp_path)
    connection = sqlite3.connect(db_path, factory=_CountingConnection)
    adapter = SqliteContextReadAdapter(
        graph_id=graph.id,
        connection=connection,
    )
    ledger = WorkLedger(backend="sqlite")
    ledger.sql_statements_executed = MAX_SQL_STATEMENTS_SQLITE - 1

    result = adapter.query_candidates_unit(
        NavigationSearchQuery(query="InvoiceService"),
        ledger=ledger,
        expected_identity={},
    )

    assert result == {"status": "exhausted", "reason": "statement_limit"}
    assert ledger.sql_statements_executed == MAX_SQL_STATEMENTS_SQLITE
    select_count = sum(sql.lstrip().upper().startswith("SELECT") for sql in connection.executed_sql)
    assert select_count == 1
    adapter.close()
    connection.close()


def test_owned_adapter_keeps_one_read_snapshot_after_writer_commit(tmp_path: Path) -> None:
    db_path, _config, graph, _binding, adapter = build_sqlite_context_fixture(tmp_path)
    writer = sqlite3.connect(db_path)
    try:
        first = adapter.read_initial_identity(WorkLedger(backend="sqlite"))
        assert first["status"] == "ok"
        writer.execute("UPDATE accepted_publication SET generation = 2 WHERE singleton = 1")
        writer.commit()
        second = adapter.read_initial_identity(WorkLedger(backend="sqlite"))
        assert second["status"] == "ok"
        assert second["accepted_publication"]["generation"] == first["accepted_publication"]["generation"]
    finally:
        adapter.close()
        writer.close()


@pytest.mark.parametrize("method", ["cursor", "executemany", "executescript"])
def test_guard_does_not_expose_unmetered_sql_methods(method: str) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        guarded = GuardedSQLiteConnection(connection, WorkLedger(backend="sqlite"))
        with pytest.raises(AttributeError):
            getattr(guarded, method)
        guarded.create_function("double_value", 1, lambda value: value * 2)
        assert guarded.execute("SELECT double_value(3)").fetchone() == (6,)
    finally:
        connection.close()


@pytest.mark.parametrize("requested", [0, -1, 1])
def test_reported_progress_interval_matches_the_installed_minimum(requested: int) -> None:
    ledger = WorkLedger(backend="sqlite", sqlite_progress_interval_ops=requested)
    assert ledger.sqlite_progress_interval_ops == 1
    assert ledger.record_statement_start()
    assert ledger.make_sqlite_progress_handler()() == 0
    report = ledger.to_dict()
    assert report["sqlite_progress_interval_ops"] == 1
    assert report["sqlite_progress_callbacks"] == report["sqlite_vm_steps_estimate"] == 1


@pytest.mark.parametrize("external_neighbor", [False, True])
def test_aggregate_relation_cap_reports_every_observed_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, external_neighbor: bool,
) -> None:
    _, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)
    seeds = tuple(f"seed:{index}" for index in range(5))

    def expand(_connection: Any, *, seed_canonical_key: str, **_kwargs: Any) -> dict[str, Any]:
        seed_index = seeds.index(seed_canonical_key)
        neighbor = f"neighbor:{seed_index}" if external_neighbor else seeds[(seed_index + 1) % len(seeds)]
        return {"truncated": False, "edges": [
            {"source_canonical_key": seed_canonical_key, "edge_kind": "calls",
             "target_canonical_key": neighbor, "edge_key": f"{seed_canonical_key}:{index}",
             "neighbor_canonical_key": neighbor,
             "neighbor_id": seed_index + 1, "neighbor_kind": "python.class",
             "neighbor_display_name": neighbor, "neighbor_first_seen_run_id": 1,
             "neighbor_last_seen_run_id": 1}
            for index in range(64)
        ]}

    monkeypatch.setattr(sqlite_context, "expand_seed_incident_edges", expand)
    monkeypatch.setattr(sqlite_context, "batch_neighbor_direct_locations", lambda *_a, **_k: {
        "locations_by_node_id": {}, "evidence_scanned": 0, "incomplete": False,
    })
    with adapter:
        result = adapter.expand_seeds_depth1(
            seeds, edge_kinds=("calls",), node_limit=64, edge_limit=64,
            ledger=WorkLedger(backend="sqlite"), expected_identity={"accepted_run_id": 1},
        )
    relations = result["seed_to_seed_edges"] + [
        edge for unit in result["neighbor_units"] for edge in unit["edges"]
    ]
    assert len(relations) == 256
    assert len({edge["edge_key"] for edge in relations}) == 256
    assert result["edge_limit_hit"] == 64
    assert result["seeds_expanded"] == 5

_VECTOR = [{"binding_id": "bind1:public", "revision": 7, "snapshot_id": "snap1:public"}]
_BINDING = "bind1:public"


def _node(node_id: int, key: str) -> dict[str, Any]:
    return {
        "id": node_id, "canonical_key": key, "kind": "python.module",
        "first_seen_run_id": 17, "last_seen_run_id": 17,
    }


def _evidence(node_id: int, path: str, *, run_id: int = 17) -> dict[str, Any]:
    return {
        "node_id": node_id, "run_id": run_id, "raw_observation_ordinal": node_id,
        "path": path, "link_kind": "observed", "snapshot_vector_json": _VECTOR,
        "metadata_json": {"binding_id": _BINDING, "snapshot_id": "snap1:public"},
    }


def _navigation_payload(nodes: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {"has_accepted": True, "accepted": _accepted_context(), "candidate_nodes": nodes, "direct_evidence": evidence}


def _location_payload(
    current_rows: list[dict[str, Any]], *, historical_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "has_accepted": True, "accepted": _accepted_context(), "has_node": True,
        "node": _node(91, "module:needle"), "selected_run_id": 17,
        "current_rows": current_rows, "historical_rows": historical_rows or [],
    }


def _accepted_context() -> dict[str, Any]:
    return {
        "generation": 4, "run_id": 17, "publication_bundle_id": "bundle:public",
        "snapshot_manifest_id": "manifest:public",
        "extraction_receipt_id": "receipt:public",
        "snapshot_vector_json": _VECTOR,
    }


def test_unscoped_context_navigation_validates_each_rows_persisted_vector() -> None:
    query = NavigationSearchQuery(query="needle", limit=5)
    nodes = [_node(91, "module:needle")]
    current = _evidence(91, "nested/current.py")
    current["snapshot_vector_json"] = json.dumps(_VECTOR)
    second = _evidence(91, "nested/other.py")
    second["snapshot_vector_json"] = [[_BINDING, 8, "snap1:public"]]
    payload = _navigation_payload(nodes, [current, second])
    result = read_postgres_navigation_search_with_executor(
        query, executor=lambda _sql: payload,
        context_identity=True,
    )
    locations = result["nodes"][0]["locations"]
    assert [location["source"]["binding_revision"] for location in locations] == [7, 8]
    assert [location["source_identity_status"] for location in locations] == ["verified_vector"] * 2

    second["snapshot_vector_json"] = [[_BINDING, 8, "snap1:public"], [_BINDING, 9, "snap1:public"]]
    with pytest.raises(StorageSchemaError, match="malformed context snapshot vector"):
        read_postgres_navigation_search_with_executor(
            query, executor=lambda _sql: payload,
            context_identity=True,
        )


def test_context_node_locations_validates_unreturned_sentinel_vector() -> None:
    query = NodeLocationsQuery(canonical_key="module:needle", evidence_role="direct", limit=1)
    first = _evidence(91, "nested/current.py")
    first["snapshot_vector_json"] = json.dumps(_VECTOR)
    sentinel = _evidence(92, "nested/other.py")
    payload = _location_payload([first, sentinel])
    result = read_postgres_node_locations_with_executor(
        query, executor=lambda _sql: payload, context_identity=True,
    )
    assert result["has_more"] is True
    assert [location["source"]["binding_revision"] for location in result["locations"]] == [7]

    sentinel["snapshot_vector_json"] = "{"
    with pytest.raises(StorageSchemaError, match="malformed context snapshot vector"):
        read_postgres_node_locations_with_executor(
            query, executor=lambda _sql: payload, context_identity=True,
        )


def test_scoped_context_navigation_keeps_normalized_utf8_admission_budget() -> None:
    query = NavigationSearchQuery(query="needle", source_binding_id=_BINDING, limit=5)
    row = _evidence(91, "nested/λ.py")
    row["metadata_json"]["source_relative_path"] = "nested/λ.py"
    row["snapshot_vector_json"] = json.dumps(_VECTOR, indent=4)
    payload = _navigation_payload([_node(91, "module:needle")], [row])
    result = read_postgres_navigation_search_with_executor(
        query, executor=lambda _sql: payload, admits_location=lambda _location: True,
        context_identity=True,
    )
    vector_bytes = len(b'[["bind1:public",7,"snap1:public"]]')
    metadata_bytes = len(json.dumps(
        row["metadata_json"], sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8"))
    admission = result["scan"]["admission"]
    assert admission["checks"] == admission["vector_decodes"] == 1
    assert admission["vector_bytes"] == vector_bytes
    assert admission["input_bytes"] == admission["decoded_bytes"] == metadata_bytes + vector_bytes


def test_context_node_locations_validates_historical_lookahead() -> None:
    query = NodeLocationsQuery(canonical_key="module:needle", evidence_role="direct", limit=1)
    current = _evidence(91, "nested/current.py")
    historical = _evidence(92, "nested/old.py", run_id=16)
    historical["snapshot_vector_json"] = [[_BINDING, 6, "snap1:public"]]
    sentinel = _evidence(93, "nested/older.py", run_id=15)
    payload = _location_payload([current], historical_rows=[historical, sentinel])
    result = read_postgres_node_locations_with_executor(
        query, executor=lambda _sql: payload, context_identity=True,
    )
    assert result["historical_has_more"] is True
    assert result["historical_locations"][0]["source"]["binding_revision"] == 6

    sentinel["snapshot_vector_json"] = "{"
    with pytest.raises(StorageSchemaError, match="malformed context snapshot vector"):
        read_postgres_node_locations_with_executor(
            query, executor=lambda _sql: payload, context_identity=True,
        )
