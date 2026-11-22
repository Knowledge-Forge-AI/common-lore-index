"""Unit tests for W4 SQLite context_expansion_queries and SqliteContextReadAdapter."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from common_lore_index.retrieval.context_work import WorkLedger
from common_lore_index.server.context_read_sqlite import SqliteContextReadAdapter
from common_lore_index.storage.sqlite_local import context_expansion_queries, navigation_queries, node_location_queries
from common_lore_index.storage.sqlite_local.context_expansion_queries import (
    batch_neighbor_direct_locations,
    expand_seed_incident_edges,
    read_sqlite_context_publication,
)
from common_lore_index.storage.sqlite_local.navigation_queries import evidence_sql, navigation_candidates
from common_lore_index.storage.navigation_search import NavigationSearchQuery
from repomap_test_support.context_fixture import build_sqlite_context_fixture


def test_read_sqlite_context_publication_full_identity(tmp_path: Path) -> None:
    db_path, _, graph, _, _ = build_sqlite_context_fixture(tmp_path)
    with sqlite3.connect(db_path) as conn:
        pub = read_sqlite_context_publication(conn)
        assert pub is not None
        assert pub.graph_id == graph.id
        assert pub.repository_id == f"repo1:{graph.id}"
        assert pub.generation == 1
        assert pub.run_id == 1
        assert pub.publication_job_id == "job-ctx-1"
        assert pub.publication_attempt == 1
        assert pub.snapshot_manifest_id.startswith("snapmanifest1:")
        assert pub.extraction_receipt_id.startswith("receipt1:")
        assert len(pub.snapshot_vector) == 2


def test_expand_seed_incident_edges_and_batch_neighbor_locations(tmp_path: Path) -> None:
    db_path, _, _, _, _ = build_sqlite_context_fixture(tmp_path)
    with sqlite3.connect(db_path) as conn:
        res = expand_seed_incident_edges(
            conn,
            seed_canonical_key="py:pkg.billing.InvoiceService",
            edge_kinds=("contains", "defines", "references", "links_to"),
            per_seed_limit=10,
        )
        assert not res["truncated"]
        assert len(res["edges"]) >= 3
        nbr_ids: list[int] = []
        for e in res["edges"]:
            assert len(e["edge_key"]) == 64
            assert all(char in "0123456789abcdef" for char in e["edge_key"])
            nbr_ids.append(int(e["neighbor_id"]))

        # Truncation when per_seed_limit=1
        res_lim = expand_seed_incident_edges(
            conn,
            seed_canonical_key="py:pkg.billing.InvoiceService",
            edge_kinds=("contains", "defines", "references", "links_to"),
            per_seed_limit=1,
        )
        assert res_lim["truncated"]
        assert len(res_lim["edges"]) == 1
        kinds = ("contains", "defines", "links_to", "references")
        lexical = expand_seed_incident_edges(conn, seed_canonical_key="py:pkg.billing.InvoiceService", edge_kinds=kinds, per_seed_limit=1)
        reverse = expand_seed_incident_edges(conn, seed_canonical_key="py:pkg.billing.InvoiceService", edge_kinds=tuple(reversed(kinds)), per_seed_limit=1)
        assert reverse == lexical
        assert lexical["edges"][0]["edge_kind"] == "contains"

        # Single batched SQL query for neighbor direct locations
        nbr_map = batch_neighbor_direct_locations(
            conn, neighbor_ids=nbr_ids, accepted_run_id=1
        )
        assert not nbr_map["incomplete"]
        for nid in nbr_ids:
            assert len(nbr_map["locations_by_node_id"][nid]) >= 1


def test_incident_expansion_only_decodes_edges_in_the_returned_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = "py:pkg.billing.InvoiceService"
    db_path, _, _, _, _ = build_sqlite_context_fixture(
        tmp_path, extra_edges=[("contains", seed, seed)],
    )
    render = context_expansion_queries.incident_edge_record
    rendered_directions: list[str] = []

    def observe(value: dict[str, Any], *, edge_kind_priority: int) -> dict[str, Any]:
        rendered_directions.append(value["direction"])
        return render(value, edge_kind_priority=edge_kind_priority)

    monkeypatch.setattr(context_expansion_queries, "incident_edge_record", observe)
    with closing(sqlite3.connect(db_path)) as connection:
        page = expand_seed_incident_edges(
            connection, seed_canonical_key=seed, edge_kinds=("contains", "contains"), per_seed_limit=1,
        )
    assert page["truncated"] and page["scanned"] == 1
    assert page["edges"][0]["direction"] == "outgoing"
    assert rendered_directions == ["outgoing"]


def test_sqlite_adapter_progress_handler_interrupt_discards_partial_statement(
    tmp_path: Path,
) -> None:
    _, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)
    ledger = WorkLedger(
        backend="sqlite",
        sqlite_progress_callback_limit=0,
        sqlite_progress_interval_ops=1,
    )
    res = adapter.expand_seeds_depth1(
        ("py:pkg.billing.InvoiceService",),
        edge_kinds=("contains", "defines", "references"),
        node_limit=8,
        edge_limit=16,
        ledger=ledger,
        expected_identity={"accepted_run_id": 1},
    )
    assert res["status"] == "partial"
    assert res["reason"] == "work_budget_exhausted"
    assert res["neighbor_units"] == []

    # Missing accepted_run_id in expected_identity refuses to fabricate run_id=1
    res_unver = adapter.expand_seeds_depth1(
        ("py:pkg.billing.InvoiceService",),
        edge_kinds=("contains", "defines", "references"),
        node_limit=8,
        edge_limit=16,
        ledger=WorkLedger(backend="sqlite"),
        expected_identity={},
    )
    assert res_unver["status"] == "partial"
    assert res_unver["reason"] == "accepted_publication_unverified"
    assert res_unver["neighbor_units"] == []


def test_owned_adapter_requires_binding_before_opening_store(tmp_path: Path) -> None:
    db_path, _, graph, _, adapter = build_sqlite_context_fixture(tmp_path)
    adapter.close()
    missing_binding = SqliteContextReadAdapter(graph_id=graph.id, db_path=db_path)
    try:
        result = missing_binding.read_initial_identity(WorkLedger(backend="sqlite"))
    finally:
        missing_binding.close()
    assert result == {"status": "unavailable", "reason": "database_unavailable"}


def test_ordinary_sqlite_errors_are_unavailable_not_exhausted() -> None:
    connection = sqlite3.connect(":memory:")
    adapter = SqliteContextReadAdapter(graph_id="graph-test", connection=connection)
    try:
        result = adapter.query_candidates_unit(
            NavigationSearchQuery(query="InvoiceService"),
            ledger=WorkLedger(backend="sqlite"),
            expected_identity={},
        )
    finally:
        adapter.close()
        connection.close()
    assert result == {"status": "unavailable", "reason": "database_unavailable"}

@pytest.mark.parametrize("accepted_override", (False, True))
def test_neighbor_context_validates_the_shared_persisted_snapshot_vector_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, accepted_override: bool,
) -> None:
    db_path, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)
    adapter.close()
    parse = node_location_queries._parse_context_snapshot_vector
    inputs: list[object] = []

    def counting_parse(raw: object) -> list[list[object]]:
        inputs.append(raw)
        return parse(raw)

    monkeypatch.setattr(node_location_queries, "_parse_context_snapshot_vector", counting_parse)
    with closing(sqlite3.connect(db_path)) as connection:
        publication = read_sqlite_context_publication(connection)
        assert publication is not None
        ids = [int(row[0]) for row in connection.execute("SELECT id FROM canonical_nodes")]
        result = batch_neighbor_direct_locations(
            connection, neighbor_ids=ids, accepted_run_id=publication.run_id,
            snapshot_vector=publication.snapshot_vector if accepted_override else None,
        )
    assert result["evidence_scanned"] > 1
    assert len(inputs) == 1 and isinstance(inputs[0], str)


def test_neighbor_location_cursor_keeps_only_the_result_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)
    adapter.close()
    fetched_keys: list[str] = []
    decoded_after: list[int] = []
    render = context_expansion_queries._direct_location

    def observe(cursor: sqlite3.Cursor, row: tuple[object, ...]) -> tuple[object, ...]:
        if cursor.description and cursor.description[0][0] == "evidence_key":
            fetched_keys.append(str(row[0]))
        return row

    def decode(row: tuple[object, ...], accepted_run_id: int, **kwargs: Any) -> dict[str, Any]:
        decoded_after.append(len(fetched_keys))
        return render(row, accepted_run_id, **kwargs)

    monkeypatch.setattr(context_expansion_queries, "EVIDENCE_SCAN_LIMIT", 2)
    monkeypatch.setattr(context_expansion_queries, "_direct_location", decode)
    with closing(sqlite3.connect(db_path)) as connection:
        ids = [int(row[0]) for row in connection.execute("SELECT id FROM canonical_nodes")]
        connection.row_factory = observe
        prefix = batch_neighbor_direct_locations(connection, neighbor_ids=ids, accepted_run_id=1)

    decoded_keys = [
        location["evidence_key"]
        for locations in prefix["locations_by_node_id"].values()
        for location in locations
    ]
    assert sorted(decoded_keys) == sorted(fetched_keys[:2])
    assert prefix["incomplete"] and prefix["evidence_scanned"] == 2
    assert len(fetched_keys) == 3 and decoded_after == [1, 2]


def _clone_run(conn: sqlite3.Connection, new_run_id: int, vector_json: str) -> None:
    conn.execute(
        "INSERT INTO runs SELECT ?, 1, status, started_at, finished_at, publisher, execution_route, "
        "portable_protocol_version, snapshot_manifest_id, ?, extraction_receipt_id, publication_bundle_id, "
        "graph_candidate_id, resolver_identity, portable_canonicalizer_identity, semantic_contract_identity, "
        "quality_rule_identity, worker_capability_identity, portable_stage_id, portable_execution_mode, "
        "source_generation, config_generation, extractor_generation, canonicalizer_generation, "
        "'job-ctx-' || ?, ?, family_receipts_json, privacy FROM runs WHERE id = 1",
        (new_run_id, vector_json, new_run_id, new_run_id),
    )


def test_evidence_sql_plan_bounds_window_before_wide_joins(tmp_path: Path) -> None:
    db_path, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)
    adapter.close()
    with closing(sqlite3.connect(db_path)) as conn:
        ids = [int(r[0]) for r in conn.execute("SELECT id FROM canonical_nodes ORDER BY id")]
        pred = "subject.id IN (" + ",".join(str(nid) for nid in ids) + ")"
        sql, params = evidence_sql(pred), {"accepted": 1, "evidence_fetch": 100}
        for analyzed in (False, True):
            if analyzed:
                conn.execute("ANALYZE")
            plan = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
            top_ops = [d.split()[:2] for _, parent, _, d in plan if parent == 0]
            inner_ops = [d.split()[:2] for _, parent, _, d in plan if parent != 0]
            assert any(d.split()[:2] == ["MATERIALIZE", "bounded"] for _, _, _, d in plan)
            assert ["SCAN", "bounded"] in top_ops and ["SEARCH", "e"] in top_ops and ["SEARCH", "raw"] in top_ops
            assert any(op in (["SCAN", "r"], ["SEARCH", "r"]) for op in top_ops)
            assert any("TEMP B-TREE FOR ORDER BY" in d for _, parent, _, d in plan if parent != 0)
            assert all(op[1] not in {"r", "raw"} for op in inner_ops if len(op) > 1)
            assert all(d != "SCAN e" for _, _, _, d in plan)
        roots = dict(conn.execute("SELECT name, rootpage FROM sqlite_master WHERE type = 'table'"))
        opcodes = conn.execute("EXPLAIN " + sql, params).fetchall()
        cur_tbl = {int(r[2]): k for r in opcodes if r[1] == "OpenRead" for k, v in roots.items() if int(r[3]) == int(v)}
        e_cols: dict[int, set[int]] = {}
        for r in opcodes:
            if r[1] == "Column" and cur_tbl.get(int(r[2])) == "canonical_evidence":
                e_cols.setdefault(int(r[2]), set()).add(int(r[3]))
        assert len([c for c, cols in e_cols.items() if any(col >= 7 for col in cols)]) == 1
        assert sum(t == "runs" for t in cur_tbl.values()) == 1 and sum(t == "raw_observations" for t in cur_tbl.values()) == 1
        role = node_location_queries._EVIDENCE_ROLE_SQL
        ref_sql = (
            f"WITH selected AS (SELECT subject.id AS node_id, coalesce(max(CASE WHEN e.run_id = :accepted "
            f"THEN e.run_id END), max(e.run_id)) AS run_id FROM canonical_nodes subject "
            f"JOIN canonical_node_evidence link ON link.canonical_node_id = subject.id "
            f"JOIN canonical_evidence e ON e.id = link.canonical_evidence_id AND e.graph_key_version = 1 "
            f"WHERE {pred} GROUP BY subject.id) SELECT e.evidence_key, link.link_kind, e.run_id, "
            f"e.raw_observation_ordinal, raw.payload_hash, e.raw_kind, e.raw_source_id, e.path, e.start_line, "
            f"e.end_line, e.extractor, e.extractor_version, e.confidence, e.metadata_json, "
            f"r.publication_bundle_id, r.snapshot_manifest_id, r.extraction_receipt_id, r.graph_candidate_id, "
            f"r.snapshot_vector_json, {role}, subject.id FROM selected "
            f"JOIN canonical_nodes subject ON subject.id = selected.node_id "
            f"JOIN canonical_node_evidence link ON link.canonical_node_id = subject.id "
            f"JOIN canonical_evidence e ON e.id = link.canonical_evidence_id AND e.run_id = selected.run_id "
            f"AND e.graph_key_version = 1 JOIN runs r ON r.id = e.run_id "
            f"LEFT JOIN raw_observations raw ON raw.run_id = e.run_id AND raw.ordinal = e.raw_observation_ordinal "
            f"WHERE ({role}) = 'direct' ORDER BY subject.canonical_key, e.raw_observation_ordinal, "
            f"e.evidence_key, link.link_kind LIMIT :evidence_fetch"
        )
        cur, ref = conn.execute(sql, params), conn.execute(ref_sql, params)
        assert [c[0] for c in cur.description[:19]] == [c[0] for c in ref.description[:19]]
        assert [cur.description[19][0], cur.description[20][0]] == ["evidence_role", "id"]
        assert cur.fetchall() == ref.fetchall()


def test_evidence_window_deterministic_byte_ordering_alternatives_and_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)
    adapter.close()
    role_sql = (
        "CASE WHEN link.link_kind IN ('observed', 'observed_alt') AND subject.kind = 'python.function' "
        "AND e.raw_kind = 'python.function' THEN 'direct' ELSE 'unknown' END"
    )
    with closing(sqlite3.connect(db_path)) as conn:
        meta = conn.execute("SELECT metadata_json FROM canonical_evidence LIMIT 1").fetchone()[0]
        b_node = int(conn.execute("SELECT max(id) FROM canonical_nodes").fetchone()[0]) + 10
        b_ev = int(conn.execute("SELECT max(id) FROM canonical_evidence").fetchone()[0]) + 10
        b_ord = int(conn.execute("SELECT max(ordinal) FROM raw_observations WHERE run_id = 1").fetchone()[0]) + 10
        ord_scale = 10 ** len(str(b_ord))
        ord_lo, ord_hi = 9 * ord_scale, 10 * ord_scale
        keys = ("py:test_é_last", "py:test_a_third", "py:test__second", "py:test_Z_first")
        nids = {key: b_node + i for i, key in enumerate(keys)}
        conn.executemany("INSERT INTO canonical_nodes VALUES (?, 1, ?, 'python.function', ?, '{}', 'high', 0, 1, 1)",
                         [(nid, key, key) for key, nid in nids.items()])
        conn.execute("INSERT INTO raw_observations VALUES (1, ?, 1, 'python.function', 's', 'a.py', '{}', 'h2')", (ord_lo,))
        z_id = nids["py:test_Z_first"]
        specs = [(b_ev + 1, z_id, "ek-ord-hi", ord_hi, ("observed",)), (b_ev + 2, z_id, "ek-b", ord_lo, ("observed",)),
                 (b_ev + 3, z_id, "ek-a", ord_lo, ("observed_alt", "observed")),
                 (b_ev + 4, nids["py:test__second"], "ek-u", ord_lo, ("observed",)),
                 (b_ev + 5, nids["py:test_a_third"], "ek-l", ord_lo, ("observed",)),
                 (b_ev + 6, nids["py:test_é_last"], "ek-n", ord_lo, ("observed",))]
        conn.executemany("INSERT INTO canonical_evidence VALUES (?, 1, 1, ?, ?, 1, 'python.function', 's', 'a.py', 1, 5, 'ex', '1', 'high', ?)",
                         [(eid, ek, o, meta) for eid, _, ek, o, _ in specs])
        conn.executemany("INSERT INTO canonical_node_evidence VALUES (?, ?, ?)",
                         [(nid, eid, lk) for eid, nid, _, _, lks in specs for lk in lks])
        pred = "subject.id IN (" + ",".join(str(nid) for nid in nids.values()) + ")"
        rows = conn.execute(evidence_sql(pred, role_sql=role_sql), {"accepted": 1, "evidence_fetch": 20}).fetchall()
        assert [(r[20], r[3], r[0], r[1], r[4]) for r in rows] == [
            (z_id, ord_lo, "ek-a", "observed", "h2"), (z_id, ord_lo, "ek-a", "observed_alt", "h2"),
            (z_id, ord_lo, "ek-b", "observed", "h2"), (z_id, ord_hi, "ek-ord-hi", "observed", None),
            (nids["py:test__second"], ord_lo, "ek-u", "observed", "h2"),
            (nids["py:test_a_third"], ord_lo, "ek-l", "observed", "h2"),
            (nids["py:test_é_last"], ord_lo, "ek-n", "observed", "h2"),
        ]
        monkeypatch.setattr(context_expansion_queries, "EVIDENCE_SCAN_LIMIT", 7)
        exact = batch_neighbor_direct_locations(conn, neighbor_ids=list(nids.values()), accepted_run_id=1, role_sql=role_sql)
        assert not exact["incomplete"] and exact["evidence_scanned"] == 7
        assert [loc["raw_observation"]["status"] for loc in exact["locations_by_node_id"][z_id]] == ["available", "available", "available", "unavailable"]
        monkeypatch.setattr(context_expansion_queries, "EVIDENCE_SCAN_LIMIT", 6)
        over = batch_neighbor_direct_locations(conn, neighbor_ids=list(nids.values()), accepted_run_id=1, role_sql=role_sql)
        assert over["incomplete"] and over["evidence_scanned"] == 6 and over["locations_by_node_id"][nids["py:test_é_last"]] == []


def _seed_multi_run_and_wide_tail(conn: sqlite3.Connection) -> tuple[tuple[int, int, int, int], int, int]:
    vec1 = json.loads(conn.execute("SELECT snapshot_vector_json FROM runs WHERE id = 1").fetchone()[0])
    vec2 = [[b, int(rev) + 1, s] for b, rev, s in vec1]
    _clone_run(conn, 2, json.dumps(vec2))
    _clone_run(conn, 3, "not-json-wide-vector-" + ("v" * 65536))
    conn.execute("UPDATE accepted_publication SET generation = 2, run_id = 2 WHERE singleton = 1")
    meta = json.dumps({"binding_id": vec1[0][0], "snapshot_id": vec1[0][2], "source_relative_path": "pkg/mod.py"})
    b_node = int(conn.execute("SELECT max(id) FROM canonical_nodes").fetchone()[0]) + 10
    b_ev = int(conn.execute("SELECT max(id) FROM canonical_evidence").fetchone()[0]) + 10
    ord1 = int(conn.execute("SELECT max(ordinal) FROM raw_observations WHERE run_id = 1").fetchone()[0]) + 10
    ids = (b_node + 1, b_node + 2, b_node + 3, b_node + 4)
    keys = ("py:w7_1_accepted", "py:w7_2_historical", "py:w7_3_superseded", "py:zz_wide_tail")
    conn.executemany("INSERT INTO canonical_nodes VALUES (?, 1, ?, 'python.function', ?, '{}', 'high', 0, 1, 2)",
                     [(nid, k, k) for nid, k in zip(ids, keys, strict=True)])
    conn.executemany("INSERT INTO raw_observations VALUES (?, ?, 1, 'python.function', 's', 'pkg/mod.py', '{}', 'h')", [(1, ord1), (2, 1)])
    n_cur, n_hist, n_sup, n_tail = ids
    entries = [(b_ev + 1, 1, n_cur, "ek-cur-r1", ord1, "pkg/old.py", "observed"),
               (b_ev + 2, 2, n_cur, "ek-cur-r2a", 1, "pkg/new_a.py", "observed"),
               (b_ev + 3, 2, n_cur, "ek-cur-r2b", 1, "pkg/new_b.py", "observed"),
               (b_ev + 4, 1, n_hist, "ek-hist-r1", ord1, "pkg/hist.py", "observed"),
               (b_ev + 5, 1, n_sup, "ek-sup-r1", ord1, "pkg/stale.py", "observed"),
               (b_ev + 6, 2, n_sup, "ek-sup-r2-ref", 1, "pkg/ref.py", "inferred_from_edge")]
    conn.executemany("INSERT INTO canonical_evidence VALUES (?, ?, 1, ?, ?, 1, 'python.function', 's', ?, 1, 2, 'ex', '1', 'high', ?)",
                     [(eid, rid, ek, o, p, meta) for eid, rid, _, ek, o, p, _ in entries])
    conn.executemany("INSERT INTO canonical_node_evidence VALUES (?, ?, ?)", [(nid, eid, lk) for eid, _, nid, _, _, _, lk in entries])
    w_meta, w_pay = "not-json-meta-" + ("m" * 16384), "p" * 32768
    conn.executemany("INSERT INTO raw_observations VALUES (3, ?, 1, 'python.function', 's', 'pkg/tail.py', ?, 'ht')",
                     [(i + 1, w_pay) for i in range(32)])
    conn.executemany("INSERT INTO canonical_evidence VALUES (?, 3, 1, ?, ?, 1, 'python.function', 's', 'pkg/tail.py', 1, 2, 'ex', '1', 'high', ?)",
                     [(b_ev + 100 + i, f"ek-tail-{i:03d}", i + 1, w_meta) for i in range(32)])
    conn.executemany("INSERT INTO canonical_node_evidence VALUES (?, ?, 'observed')", [(n_tail, b_ev + 100 + i) for i in range(32)])
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    return ids, int(vec1[0][1]), int(vec2[0][1])


def test_evidence_window_selected_run_precedence_and_wide_tail_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)
    adapter.close()
    with closing(sqlite3.connect(db_path)) as conn:
        (n_cur, n_hist, n_sup, n_tail), rev1, rev2 = _seed_multi_run_and_wide_tail(conn)
        monkeypatch.setattr(context_expansion_queries, "EVIDENCE_SCAN_LIMIT", 2)
        monkeypatch.setattr(navigation_queries, "EVIDENCE_SCAN_LIMIT", 2)
        bounded = batch_neighbor_direct_locations(conn, neighbor_ids=[n_tail, n_sup, n_hist, n_cur], accepted_run_id=2)
        assert bounded["incomplete"] and bounded["evidence_scanned"] == 2 and bounded["locations_by_node_id"][n_tail] == []
        assert [loc["path"] for loc in bounded["locations_by_node_id"][n_cur]] == ["pkg/new_a.py", "pkg/new_b.py"]
        nav_over = navigation_candidates(conn, NavigationSearchQuery(query="py:", scope="code"))
        assert nav_over["status"] == "incomplete" and nav_over["reason"] == "evidence_scan_limit"
        monkeypatch.setattr(context_expansion_queries, "EVIDENCE_SCAN_LIMIT", 10)
        monkeypatch.setattr(navigation_queries, "EVIDENCE_SCAN_LIMIT", 10)
        full = batch_neighbor_direct_locations(conn, neighbor_ids=[n_sup, n_hist, n_cur], accepted_run_id=2)
        assert not full["incomplete"] and full["evidence_scanned"] == 3 and full["locations_by_node_id"][n_sup] == []
        assert [loc["publication_relation"] for loc in full["locations_by_node_id"][n_cur]] == ["accepted_run", "accepted_run"]
        assert full["locations_by_node_id"][n_cur][0]["source"]["binding_revision"] == rev2
        assert full["locations_by_node_id"][n_hist][0]["publication_relation"] == "historical_run"
        assert full["locations_by_node_id"][n_hist][0]["source"]["binding_revision"] == rev1
        nav_ok = navigation_candidates(conn, NavigationSearchQuery(query="py:w7_", scope="code"))
        assert nav_ok["status"] == "ok" and [len(n["locations"]) for n in nav_ok["nodes"]] == [2, 1, 0]
