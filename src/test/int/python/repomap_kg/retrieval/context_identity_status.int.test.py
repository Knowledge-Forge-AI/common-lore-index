"""Real SQLite/PostgreSQL source-identity status and publication-vector proofs."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Any

import psycopg
import pytest

from common_lore_index.retrieval.context_assembly import build_compact_context
from common_lore_index.retrieval.context_budget import serialize_compact_json
from common_lore_index.retrieval.context_contracts import ContextRequest
from common_lore_index.retrieval.context_work import WorkLedger
from common_lore_index.server.context_read_sqlite import SqliteContextReadAdapter
from common_lore_index.storage.errors import StorageSchemaError
from common_lore_index.storage.navigation_search import NavigationSearchQuery
from common_lore_index.storage.node_locations import NodeLocationsQuery
from common_lore_index.storage.sqlite_local import context_expansion_queries
from common_lore_index.storage.sqlite_local.context_expansion_queries import (
    batch_neighbor_direct_locations,
    read_sqlite_context_publication,
)
from common_lore_index.storage.sqlite_local.navigation_queries import navigation_candidates
from common_lore_index.storage.sqlite_local.node_location_queries import node_locations
from repomap_test_support.context_assertions import (
    assert_valid_context_packet,
    build_postgres_context_fixture,
)
from repomap_test_support.context_fixture import (
    BINDING_CORE,
    CTX_GRAPH_ID,
    SNAP_CORE,
    build_sqlite_context_fixture,
)
from repomap_test_support.postgres_harness import require_postgres_binaries, temporary_postgres


CANONICAL_KEY = "py:pkg.billing.InvoiceService"
EVIDENCE_KEY = "ev:1"


def _read_accepted(adapter: Any) -> Mapping[str, Any]:
    identity = adapter.read_initial_identity(WorkLedger(backend=adapter.backend))
    assert identity["status"] == "ok"
    accepted = identity.get("accepted_publication")
    assert isinstance(accepted, Mapping)
    return accepted


def _assert_status(
    adapter: Any,
    accepted: Mapping[str, Any],
    expected_status: str,
    *,
    expected_revision: int,
    expected_binding_id: str = BINDING_CORE,
    expected_snapshot_id: str = SNAP_CORE,
) -> None:
    query = NavigationSearchQuery(query="InvoiceService", limit=10)
    navigation = adapter.query_candidates_unit(
        query,
        ledger=WorkLedger(backend=adapter.backend),
        expected_identity=accepted,
    )
    assert navigation["status"] == "ok"
    nav_node = next(node for node in navigation["nodes"] if node["canonical_key"] == CANONICAL_KEY)
    assert len(nav_node["locations"]) == 1
    nav_location = nav_node["locations"][0]

    node_result = adapter.query_node_by_canonical_key(
        CANONICAL_KEY,
        ledger=WorkLedger(backend=adapter.backend),
        expected_identity=accepted,
    )
    assert node_result["status"] == "ok"
    node = node_result["node"]
    assert node is not None and len(node["locations"]) == 1
    node_location = node["locations"][0]

    for location in (nav_location, node_location):
        assert location["source_identity_status"] == expected_status
        assert location["source"]["binding_id"] == expected_binding_id
        assert location["source"]["binding_revision"] == expected_revision
        assert location["source"]["snapshot_id"] == expected_snapshot_id


def _reset_metadata(metadata: dict[str, Any]) -> None:
    metadata["binding_id"] = BINDING_CORE
    metadata["binding_revision"] = 1
    metadata["snapshot_id"] = SNAP_CORE


def _missing_revision(metadata: dict[str, Any]) -> None:
    _reset_metadata(metadata)
    metadata.pop("binding_revision")


def _wrong_revision(metadata: dict[str, Any]) -> None:
    _reset_metadata(metadata)
    metadata["binding_revision"] = 2


def _wrong_snapshot(metadata: dict[str, Any]) -> None:
    _reset_metadata(metadata)
    metadata["snapshot_id"] = "snap1:wrong"


def _foreign_binding(metadata: dict[str, Any]) -> None:
    _reset_metadata(metadata)
    metadata["binding_id"] = "bind1:foreign"


def _mutate_sqlite_metadata(
    db_path: Path, update: Callable[[dict[str, Any]], None],
) -> None:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT metadata_json FROM canonical_evidence WHERE evidence_key = ?",
            (EVIDENCE_KEY,),
        ).fetchone()
        assert row is not None
        metadata = json.loads(str(row[0]))
        assert isinstance(metadata, dict)
        update(metadata)
        connection.execute(
            "UPDATE canonical_evidence SET metadata_json = ? WHERE evidence_key = ?",
            (json.dumps(metadata, separators=(",", ":")), EVIDENCE_KEY),
        )


def _mutate_postgres_metadata(
    postgres: Any, update: Callable[[dict[str, Any]], None],
) -> None:
    with psycopg.connect(
        host=postgres.host,
        port=postgres.port,
        dbname=postgres.database,
        user=postgres.user,
        password=postgres.password,
    ) as connection:
        row = connection.execute(
            "SELECT metadata_json FROM canonical_evidence WHERE evidence_key = %s",
            (EVIDENCE_KEY,),
        ).fetchone()
        assert row is not None
        raw_metadata = row[0]
        metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else dict(raw_metadata)
        assert isinstance(metadata, dict)
        update(metadata)
        connection.execute(
            "UPDATE canonical_evidence SET metadata_json = %s::jsonb WHERE evidence_key = %s",
            (json.dumps(metadata, separators=(",", ":")), EVIDENCE_KEY),
        )


def test_sqlite_source_identity_status_tracks_vector_and_metadata_mutations(tmp_path: Path) -> None:
    db_path, _config, graph, binding, initial_adapter = build_sqlite_context_fixture(tmp_path)
    configured = initial_adapter.configured_bindings_by_id()

    def open_adapter() -> SqliteContextReadAdapter:
        return SqliteContextReadAdapter(
            graph_id=graph.id,
            db_path=db_path,
            binding=binding,
            configured_bindings=configured,
        )

    with initial_adapter:
        accepted = _read_accepted(initial_adapter)
        _assert_status(initial_adapter, accepted, "verified_vector", expected_revision=1)

    _mutate_sqlite_metadata(db_path, _missing_revision)
    with open_adapter() as adapter:
        accepted = _read_accepted(adapter)
        _assert_status(adapter, accepted, "verified_vector", expected_revision=1)

    _mutate_sqlite_metadata(db_path, _wrong_revision)
    with open_adapter() as adapter:
        accepted = _read_accepted(adapter)
        _assert_status(adapter, accepted, "unbound_or_conflicting_vector", expected_revision=2)

    _mutate_sqlite_metadata(db_path, _wrong_snapshot)
    with open_adapter() as adapter:
        accepted = _read_accepted(adapter)
        _assert_status(
            adapter,
            accepted,
            "unbound_or_conflicting_vector",
            expected_revision=1,
            expected_snapshot_id="snap1:wrong",
        )

    _mutate_sqlite_metadata(db_path, _foreign_binding)
    with open_adapter() as adapter:
        accepted = _read_accepted(adapter)
        _assert_status(
            adapter,
            accepted,
            "unbound_or_conflicting_vector",
            expected_revision=1,
            expected_binding_id="bind1:foreign",
        )


def test_postgres_source_identity_status_tracks_vector_and_metadata_mutations(tmp_path: Path) -> None:
    require_postgres_binaries()
    with temporary_postgres() as postgres:
        _home, adapter = build_postgres_context_fixture(tmp_path, postgres)
        with adapter:
            accepted = _read_accepted(adapter)
            _assert_status(adapter, accepted, "verified_vector", expected_revision=1)

        _mutate_postgres_metadata(postgres, _missing_revision)
        with adapter:
            accepted = _read_accepted(adapter)
            _assert_status(adapter, accepted, "verified_vector", expected_revision=1)

        _mutate_postgres_metadata(postgres, _wrong_revision)
        with adapter:
            accepted = _read_accepted(adapter)
            _assert_status(adapter, accepted, "unbound_or_conflicting_vector", expected_revision=2)

        _mutate_postgres_metadata(postgres, _wrong_snapshot)
        with adapter:
            accepted = _read_accepted(adapter)
            _assert_status(
                adapter,
                accepted,
                "unbound_or_conflicting_vector",
                expected_revision=1,
                expected_snapshot_id="snap1:wrong",
            )

        _mutate_postgres_metadata(postgres, _foreign_binding)
        with adapter:
            accepted = _read_accepted(adapter)
            _assert_status(
                adapter,
                accepted,
                "unbound_or_conflicting_vector",
                expected_revision=1,
                expected_binding_id="bind1:foreign",
            )


def test_sqlite_compact_context_tight_budget_preserves_utf8_bytes_and_rank_prefix(
    tmp_path: Path,
) -> None:
    """Verify real SQLite compact-context UTF-8 byte accounting and strict rank-prefix admission."""
    exact_key = "py:pkg.tariff.TariffLedger"
    tiny_tail_key = "py:pkg.tariff.TariffLedgerZTail"
    extras: list[dict[str, Any]] = [
        {
            "canonical_key": exact_key,
            "kind": "python.class",
            "display_name": "TariffLedger",
            "raw_kind": "python.class",
            "binding_id": BINDING_CORE,
            "snapshot_id": SNAP_CORE,
            "path": "src/pkg/tariff/ledger_α.py",
            "start_line": 1,
            "end_line": 12,
        },
        *(
            {
                "canonical_key": f"py:pkg.tariff.TariffLedgerCase{idx:02d}_" + ("Δ" * 220),
                "kind": "python.function",
                "display_name": f"TariffLedgerCase{idx:02d}_" + ("λ🚀" * 60),
                "raw_kind": "python.function",
                "binding_id": BINDING_CORE,
                "snapshot_id": SNAP_CORE,
                "path": f"src/pkg/tariff/case_{idx:02d}_β.py",
                "start_line": 1,
                "end_line": 4,
            }
            for idx in range(1, 6)
        ),
        {
            "canonical_key": tiny_tail_key,
            "kind": "python.function",
            "display_name": "TariffLedgerZTail",
            "raw_kind": "python.function",
            "binding_id": BINDING_CORE,
            "snapshot_id": SNAP_CORE,
            "path": "src/pkg/tariff/tiny.py",
            "start_line": 1,
            "end_line": 2,
        },
    ]
    _db_path, _config, _graph, _binding, adapter = build_sqlite_context_fixture(
        tmp_path / "compact_budget",
        extra_subjects=tuple(extras),
    )

    def make_request(budget_bytes: int) -> ContextRequest:
        return ContextRequest(
            graph_id=CTX_GRAPH_ID,
            mode="ranked_discovery",
            query="TariffLedger",
            kinds=("python.class", "python.function"),
            bindings=(BINDING_CORE,),
            path_prefixes={BINDING_CORE: ("src/pkg/tariff",)},
            max_items=12,
            budget_bytes=budget_bytes,
            freshness="none",
        )

    with adapter:
        wide_packet = build_compact_context(make_request(16384), adapter)
        tight_packet = build_compact_context(make_request(5120), adapter)

    assert_valid_context_packet(wide_packet)
    assert_valid_context_packet(tight_packet)

    wide_raw = serialize_compact_json(wide_packet)
    assert wide_raw.endswith(b"\n")
    assert wide_packet["status"] == "complete"
    assert wide_packet["work_ledger"]["actual_bytes"] == len(wide_raw) <= 16384
    assert len(wide_raw) > len(wide_raw.decode("utf-8"))
    wide_keys = [item["canonical_key"] for item in wide_packet["items"]]
    assert len(wide_keys) == 7
    assert wide_keys[0] == exact_key
    assert wide_keys[-1] == tiny_tail_key
    assert wide_packet["items"][0]["locations"][0]["source_relative_path"] == (
        "src/pkg/tariff/ledger_α.py"
    )

    tight_raw = serialize_compact_json(tight_packet)
    assert tight_raw.endswith(b"\n")
    assert tight_packet["status"] == "partial"
    assert tight_packet["work_ledger"]["actual_bytes"] == len(tight_raw) <= 5120
    assert len(tight_raw) > len(tight_raw.decode("utf-8"))
    tight_keys = [item["canonical_key"] for item in tight_packet["items"]]
    assert tight_keys == [exact_key]
    assert tiny_tail_key not in tight_keys
    assert tight_packet["scope"]["selection_fingerprint"] == wide_packet["scope"]["selection_fingerprint"]
    assert tight_packet["items"][0]["display_name"] == "TariffLedger"
    assert tight_packet["items"][0]["locations"][0]["source_relative_path"] == (
        "src/pkg/tariff/ledger_α.py"
    )
    assert [item["rank"] for item in tight_packet["items"]] == [1]
    assert tight_packet["omission_counts"]["output_budget"] == len(wide_keys) - len(tight_keys)

def test_neighbor_read_rechecks_persisted_vectors_with_a_valid_accepted_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)
    adapter.close()
    with closing(sqlite3.connect(db_path)) as connection:
        publication = read_sqlite_context_publication(connection)
        assert publication is not None
        ids = [int(row[0]) for row in connection.execute("SELECT id FROM canonical_nodes")]
        healthy = batch_neighbor_direct_locations(
            connection, neighbor_ids=ids, accepted_run_id=publication.run_id,
            snapshot_vector=publication.snapshot_vector,
        )
        assert healthy["evidence_scanned"] > 1
        assert not healthy["incomplete"]
        with monkeypatch.context() as small_window:
            small_window.setattr(context_expansion_queries, "EVIDENCE_SCAN_LIMIT", 1)
            prefix = batch_neighbor_direct_locations(
                connection, neighbor_ids=ids, accepted_run_id=publication.run_id,
                snapshot_vector=publication.snapshot_vector,
            )
        assert prefix["incomplete"] and prefix["evidence_scanned"] == 1
        assert sum(len(rows) for rows in prefix["locations_by_node_id"].values()) == 1
        for node_id, rows in prefix["locations_by_node_id"].items():
            assert rows == healthy["locations_by_node_id"][node_id][:len(rows)]
        _mutate_sqlite_metadata(db_path, _missing_revision)
        ambiguous = json.dumps([[BINDING_CORE, 1, SNAP_CORE], [BINDING_CORE, 2, SNAP_CORE]])
        with connection:
            connection.execute("UPDATE runs SET snapshot_vector_json = ? WHERE id = ?", (ambiguous, publication.run_id))
        result = node_locations(connection, NodeLocationsQuery(canonical_key=CANONICAL_KEY))
        (location,) = result["locations"]
        assert location["source"]["binding_id"] == BINDING_CORE
        assert "binding_revision" not in location["source"]
        query = NavigationSearchQuery(query="InvoiceService", source_binding_id=BINDING_CORE)

        def verified(item: Mapping[str, Any]) -> bool:
            return item["source_identity_status"] == "verified_vector"

        assert navigation_candidates(connection, query, admits_location=verified)["nodes"] == []
        with pytest.raises(StorageSchemaError, match="malformed context snapshot vector"):
            navigation_candidates(connection, query, admits_location=verified, context_identity=True)
        with connection:
            connection.execute("UPDATE runs SET snapshot_vector_json = '[]' WHERE id = ?", (publication.run_id,))
        with pytest.raises(StorageSchemaError, match="malformed context snapshot vector"):
            batch_neighbor_direct_locations(
                connection, neighbor_ids=ids, accepted_run_id=publication.run_id,
                snapshot_vector=publication.snapshot_vector,
            )
