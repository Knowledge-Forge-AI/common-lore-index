"""Unit tests for W4 context_assembly and context_admission (Epics A-C)."""

from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3

import pytest

from common_lore_index.retrieval.context_admission import OmissionCounter, admit_items_and_relations
from common_lore_index.retrieval.context_assembly import build_compact_context
from common_lore_index.retrieval.context_budget import (
    _withhold_scope_echo, _withhold_target_echo, compute_actual_bytes_fixpoint,
)
from common_lore_index.retrieval.context_budget_reservation import packet_size_with_reservation
from common_lore_index.retrieval.context_contracts import ContextRequest
from common_lore_index.retrieval.context_work import WorkLedger
from repomap_test_support.context_assertions import (
    assert_valid_budget_error,
    assert_valid_context_packet,
)
from repomap_test_support.context_fixture import (
    BINDING_CORE,
    BINDING_DOCS,
    build_sqlite_context_fixture,
)
from common_lore_index.server.context_read_postgres import PostgresContextReadAdapter


def test_build_compact_context_modes_and_source_deletion_invariance(tmp_path: Path) -> None:
    _, _, _, _, adapter = build_sqlite_context_fixture(
        tmp_path, include_private_binding=True
    )
    req = ContextRequest(
        graph_id=adapter.graph_id,
        mode="ranked_discovery",
        query="InvoiceService",
        intent="definition",
        expand_depth=1,
        budget_bytes=16384,
    )
    pkt1 = build_compact_context(req, adapter)
    assert_valid_context_packet(pkt1, forbidden_substrings=adapter.private_markers)
    assert pkt1["status"] == "complete"
    assert pkt1["items"][0]["canonical_key"] == "py:pkg.billing.InvoiceService"
    assert len(pkt1["neighbors"]) >= 1
    assert len(pkt1["relations"]) >= 1
    assert pkt1["freshness"]["files_checked"] == 0

    # Delete source trees on disk: default freshness=none packet must be identical
    shutil.rmtree(tmp_path / "src_core")
    shutil.rmtree(tmp_path / "src_docs")
    pkt2 = build_compact_context(req, adapter)
    assert_valid_context_packet(pkt2, forbidden_substrings=adapter.private_markers)
    assert {k: v for k, v in pkt1.items() if k != "work_ledger"} == {
        k: v for k, v in pkt2.items() if k != "work_ledger"
    }
    # Metadata and deterministic work are invariant; measured VM work is per invocation.
    for packet in (pkt1, pkt2):
        work = packet["work_ledger"]
        assert 0 <= work["sqlite_progress_callbacks"] <= 250 * work["sql_statements_executed"]
        assert work["sqlite_vm_steps_estimate"] == work["sqlite_progress_callbacks"] * work["sqlite_progress_interval_ops"]
    before = {k: v for k, v in pkt1["work_ledger"].items() if k not in {"elapsed_ms", "actual_bytes", "sqlite_progress_callbacks", "sqlite_vm_steps_estimate"}}
    after = {k: v for k, v in pkt2["work_ledger"].items() if k not in {"elapsed_ms", "actual_bytes", "sqlite_progress_callbacks", "sqlite_vm_steps_estimate"}}
    assert before == after, {key: (value, after.get(key)) for key, value in before.items() if value != after.get(key)}


def test_unsupported_modes_and_postgresql_refusal_need_no_storage(tmp_path: Path) -> None:
    _, _, _, _, sq_adapter = build_sqlite_context_fixture(tmp_path)
    def unexpected_sql(_sql: str):
        raise AssertionError("unsupported mode must not read storage")

    pg_adapter = PostgresContextReadAdapter(graph_id=sq_adapter.graph_id, executor=unexpected_sql)
    pg_adapter.supports_depth1_expansion = False

    for unadmitted in ("literal_captured_text", "semantic_discovery"):
        pkt = build_compact_context(
            ContextRequest(graph_id=sq_adapter.graph_id, mode=unadmitted, query="x"),
            sq_adapter,
        )
        assert_valid_context_packet(pkt)
        assert pkt["status"] == "unavailable"
        assert pkt["reason"] == "capability_not_admitted"

    # An explicitly unsupported PostgreSQL port retains the refusal without storage.
    pg_no_fb = build_compact_context(
        ContextRequest(
            graph_id=pg_adapter.graph_id,
            mode="graph_investigation",
            canonical_key="py:pkg.billing.InvoiceService",
        ),
        pg_adapter,
    )
    assert_valid_context_packet(pg_no_fb)
    assert pg_no_fb["status"] == "unavailable"
    assert pg_no_fb["reason"] == "backend_unsupported"

    # Actual fallback and expansion behavior is proved by the real PostgreSQL
    # integration owner; this unit case makes no emulated PostgreSQL claim.


def test_scoped_unlocatable_patterns_and_explicit_unlocatable_kind(tmp_path: Path) -> None:
    _, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)

    # Scoped + explicit unlocatable --kind tool -> unavailable
    unloc = build_compact_context(
        ContextRequest(
            graph_id=adapter.graph_id,
            mode="ranked_discovery",
            query="pytest",
            kinds=("tool",),
            bindings=(BINDING_CORE,),
        ),
        adapter,
    )
    assert_valid_context_packet(unloc)
    assert unloc["status"] == "unavailable"
    assert unloc["reason"] == "unlocatable_kind_in_scoped_selection"

    # Scoped + definition without --kind -> partial because open prefixes (go.* etc.) are excluded
    scoped_def = build_compact_context(
        ContextRequest(
            graph_id=adapter.graph_id,
            mode="ranked_discovery",
            query="InvoiceService",
            intent="definition",
            bindings=(BINDING_CORE,),
        ),
        adapter,
    )
    assert_valid_context_packet(scoped_def)
    assert scoped_def["status"] == "partial"
    assert scoped_def["scope"]["scope_complete"] is False
    assert "go.*" in scoped_def["scope"]["unlocatable_kind_patterns_excluded"]
    assert scoped_def["omission_counts"]["unlocatable_kind_pattern_excluded"] == len(
        scoped_def["scope"]["unlocatable_kind_patterns_excluded"]
    )

    # Scoped + docs without --kind -> complete because all 4 doc affinity kinds are directly locatable
    scoped_docs = build_compact_context(
        ContextRequest(
            graph_id=adapter.graph_id,
            mode="ranked_discovery",
            query="ADR 0010",
            intent="docs",
            bindings=(BINDING_DOCS,),
        ),
        adapter,
    )
    assert_valid_context_packet(scoped_docs)
    assert scoped_docs["status"] == "complete"
    assert scoped_docs["scope"]["unlocatable_kind_patterns_excluded"] == []
    assert scoped_docs["omission_counts"]["unlocatable_kind_pattern_excluded"] == 0


def test_duplicate_path_identity_and_withheld_privacy_isolation(tmp_path: Path) -> None:
    db_path, _, _, _, adapter = build_sqlite_context_fixture(
        tmp_path, include_private_binding=True
    )
    # Add a private subject in BINDING_PRIV with same query token "SharedReadme"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO canonical_nodes VALUES (99, 1, 'py:priv.SharedReadme', 'python.class', "
            "'SharedReadme Secret', '{}', 'high', 0, 1, 1)"
        )
        conn.execute(
            "INSERT INTO raw_observations VALUES (1, 99, 1, 'python.class', 'src-99', "
            "'secret.py', '{}', 'hash-0099')"
        )
        conn.execute(
            "INSERT INTO canonical_evidence VALUES (99, 1, 1, 'ev:99', 99, 1, 'python.class', "
            "'src-99', ?, 1, 2, 'polyglot', '1.0', 'high', '{}')",
            (f"{adapter.private_markers[0]}/secret.py",),
        )
        conn.execute("INSERT INTO canonical_node_evidence VALUES (99, 99, 'observed')")

    pkt = build_compact_context(
        ContextRequest(
            graph_id=adapter.graph_id,
            mode="ranked_discovery",
            query="SharedReadme",
            intent="docs",
            locations_per_item=5,
        ),
        adapter,
    )
    assert_valid_context_packet(pkt, forbidden_substrings=adapter.private_markers)
    locs = [loc for it in pkt["items"] for loc in it["locations"] if loc["source_relative_path"] == "shared/README.md"]
    assert {loc["source_binding_id"] for loc in locs} == {BINDING_CORE, BINDING_DOCS}
    priv_items = [it for it in pkt["items"] if it["canonical_key"] == "py:priv.SharedReadme"]
    assert len(priv_items) == 1
    assert priv_items[0]["identity_state"] == "withheld"


def test_budget_validation_before_io_and_sqlite_statement_ceiling(tmp_path: Path) -> None:
    _, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)
    err = build_compact_context(
        ContextRequest(
            graph_id=adapter.graph_id,
            mode="ranked_discovery",
            query="InvoiceService",
            budget_bytes=2048,
        ),
        adapter,
    )
    assert_valid_budget_error(err)
    assert err["reason"] == "budget_out_of_range"
    assert err["required_min_bytes"] is None

    # Test statement ceiling interception (e.g. max_sql_statements=5 with 2 scan units needing 1 + 3 + 3 = 7)
    pkt = build_compact_context(
        ContextRequest(
            graph_id=adapter.graph_id,
            mode="ranked_discovery",
            query="InvoiceService",
            bindings=(BINDING_CORE, BINDING_DOCS),
        ),
        adapter,
        work_ledger=WorkLedger(backend="sqlite", max_sql_statements=5),
    )
    assert_valid_context_packet(pkt)
    assert pkt["status"] == "partial"
    assert pkt["work_ledger"]["sql_statements_executed"] <= 5


@pytest.mark.parametrize("with_seed", [False, True])
def test_context_admits_ranked_records_before_the_omission_detail_prefix(
    tmp_path: Path, with_seed: bool,
) -> None:
    _, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)
    budget = 4096
    packet = build_compact_context(
        ContextRequest(graph_id=adapter.graph_id, mode="ranked_discovery",
                       query="InvoiceService" if with_seed else "absent fixture", budget_bytes=budget),
        adapter,
    )
    seeds = packet["items"][:]
    assert len(seeds) == int(with_seed)
    assert packet["neighbors"] == [] and packet["relations"] == []
    _withhold_target_echo(packet)
    _withhold_scope_echo(packet)
    omissions = OmissionCounter()
    for index in range(32):
        omissions.increment("candidate_scan_incomplete", detail=f"fixture-detail-{index:02d}")
    packet["omission_counts"] = omissions.to_counts_dict()

    sizes = [
        packet_size_with_reservation({**packet, "omissions": omissions.records[:count]})
        for count in range(len(omissions.records) + 1)
    ]
    prefix_length = max(count for count, size in enumerate(sizes) if size <= budget)
    assert 0 < prefix_length < len(omissions.records)
    packet["items"] = []
    admit_items_and_relations(
        packet, candidate_seeds=seeds, total_post_offset_seeds=len(seeds), top_tied_keys=frozenset(),
        neighbor_units=(), extra_relations=(), omissions=omissions, budget_bytes=budget,
    )
    assert packet["items"] == seeds
    assert packet["omissions"] == omissions.records[:prefix_length]
    assert packet["omission_counts"]["candidate_scan_incomplete"] == 32
    raw, size = compute_actual_bytes_fixpoint(packet)
    assert size == len(raw) <= budget


def test_location_cap_retains_late_status_without_materializing_extra_freshness(monkeypatch) -> None:
    from common_lore_index.retrieval import context_admission

    calls = []

    def reserve(location):
        calls.append(location["graph_path"])
        return None

    monkeypatch.setattr(context_admission, "reserve_freshness_observation", reserve)
    monkeypatch.setattr(context_admission, "_public_navigation_location", lambda row, _markers: row)
    base = {
        "path": "docs/a.md", "path_status": "available", "run_id": 9,
        "publication_relation": "accepted_run", "source_identity_status": "verified_vector",
        "source": {"binding_id": "binding:public", "binding_revision": 1,
                   "snapshot_id": "snap1:public", "source_relative_path": "docs/a.md"},
    }
    later = {**base, "path": "docs/b.md"}
    missing = {**base, "path": None, "path_status": "missing_or_withheld"}
    kept, omitted, verified, withheld = context_admission.format_and_deduplicate_locations(
        [base, base, later, missing], markers=(), locations_per_item=1,
        reserve_freshness=True, omissions=context_admission.OmissionCounter(),
    )
    assert len(kept) == 1 and omitted == 2
    assert verified and withheld
    assert calls == ["docs/a.md"]


def test_admission_commits_only_successful_seed_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from common_lore_index.retrieval import context_admission

    budget = 4096
    _, _, _, _, adapter = build_sqlite_context_fixture(tmp_path)
    initial = build_compact_context(
        ContextRequest(graph_id=adapter.graph_id, mode="ranked_discovery",
                       query="InvoiceService", budget_bytes=budget),
        adapter,
    )
    seed, = initial["items"]
    source = seed["locations"][0]
    seed = {**seed, "locations": [source, {**source, "graph_path": "other.py"}],
            "omitted_location_count": 0}

    def measure(packet):
        items = packet["items"]
        if not items:
            return budget - 1
        return budget - 1 if len(items[0]["locations"]) == 1 and allow_retry else budget + 1

    monkeypatch.setattr(context_admission, "packet_size_with_reservation", measure)
    for allow_retry in (True, False):
        packet = {**initial, "items": [], "request": dict(initial["request"]),
                  "scope": dict(initial["scope"])}
        omissions = OmissionCounter()
        admit_items_and_relations(
            packet, candidate_seeds=[seed], total_post_offset_seeds=1,
            top_tied_keys=frozenset({seed["canonical_key"]}), neighbor_units=(),
            extra_relations=(), omissions=omissions, budget_bytes=budget,
        )
        assert omissions.counts["location_limit"] == int(allow_retry)
        assert omissions.counts["output_budget"] == int(not allow_retry)
        assert omissions.counts["ambiguous_alternative_omitted_by_budget"] == int(not allow_retry)
        if allow_retry:
            assert packet["items"][0]["locations"] == [source]
        else:
            assert packet["items"] == []
    assert len(seed["locations"]) == 2
