"""Unit tests for W4 context_budget (Epic B §4.1-4.2)."""

from __future__ import annotations

from collections import UserDict
import copy
import json
from typing import Any

import pytest

from common_lore_index.retrieval.context_admission import OmissionCounter
from common_lore_index.retrieval.context_budget import (
    CANONICAL_KEY_CAP_BYTES, DISPLAY_NAME_CAP_BYTES, SOURCE_RELATIVE_PATH_CAP_BYTES,
    apply_skeleton_compaction, compute_actual_bytes_fixpoint,
    converge_actual_bytes_from_zero_length, finalize_packet_within_budget,
    maximally_compacted_skeleton_bytes, sanitize_stored_string,
    serialize_compact_json, validate_budget_bytes,
)
from common_lore_index.retrieval.context_budget_reservation import (
    empty_packet_size_with_reservation, packet_size_with_reservation,
)
from common_lore_index.retrieval.context_contracts import ContextRequest
from common_lore_index.retrieval.context_packet_builder import build_packet_skeleton
from common_lore_index.retrieval.context_work import WorkLedger
from repomap_test_support.context_assertions import (
    assert_valid_budget_error,
    assert_valid_context_packet,
)


def _sample_skeleton(budget_bytes: int = 16384, query: str = "InvoiceService") -> dict:
    req = ContextRequest(
        graph_id="repo",
        mode="ranked_discovery",
        query=query,
        intent="tests",
        budget_bytes=budget_bytes,
    )
    return build_packet_skeleton(
        request=req,
        status="complete",
        effective_mode="ranked_discovery",
        fallback=None,
        consistency_mode="single_read_transaction",
        backend="sqlite",
        repository_id="repo1:repo",
        selection_source="selection_file",
        selection_fingerprint="select_ctx1:" + "a" * 64,
        selected_binding_ids=("bind1:aaaa",),
        path_prefixes=({"binding_id": "bind1:aaaa", "prefix": "src/test/"},),
        scan_unit_count=1,
        unlocatable_patterns_excluded=(),
        scope_complete=True,
        publication={
            "generation": 1,
            "accepted_run_id": 1,
            "publication_bundle_id": "bundle1:" + "0123456789abcdef" * 4,
            "snapshot_manifest_id": "manifest1:" + "0123456789abcdef" * 4,
        },
        ambiguity_status="unambiguous",
        tied_top_count=0,
        expansion_state="completed",
        expansion_reason=None,
        seeds_expanded=0,
        omissions=OmissionCounter(),
        ledger=WorkLedger(backend="sqlite"),
    )


def test_validate_budget_bytes_type_and_range_envelopes() -> None:
    assert validate_budget_bytes(4096) is None
    assert validate_budget_bytes(16384) is None
    assert validate_budget_bytes(65536) is None

    for bad_type in (True, False, 4096.0, "4096", None, [4096]):
        err = validate_budget_bytes(bad_type)
        assert err is not None
        assert_valid_budget_error(err)
        assert err["reason"] == "invalid_budget_type"
        assert err["budget_bytes"] is None
        assert err["required_min_bytes"] is None

    for out_of_range in (0, -5, 2048, 4095, 65537, 100_000):
        err = validate_budget_bytes(out_of_range)
        assert err is not None
        assert_valid_budget_error(err)
        assert err["reason"] == "budget_out_of_range"
        assert err["budget_bytes"] == out_of_range

    huge = validate_budget_bytes(10**12)
    assert huge is not None
    assert_valid_budget_error(huge)
    assert huge["reason"] == "budget_out_of_range"
    assert huge["budget_bytes"] is None


def test_sanitize_stored_string_caps_and_surrogates() -> None:
    cases = (
        ("InvoiceService", DISPLAY_NAME_CAP_BYTES, ("InvoiceService", None)),
        ("a" * 513, DISPLAY_NAME_CAP_BYTES, (None, "oversize_field_withheld")),
        ("p" * 1025, SOURCE_RELATIVE_PATH_CAP_BYTES, (None, "oversize_field_withheld")),
        ("k" * 4097, CANONICAL_KEY_CAP_BYTES, (None, "oversize_field_withheld")),
        ("bad\ud800surrogate", DISPLAY_NAME_CAP_BYTES, (None, "unencodable_field_withheld")),
        ("🚀" * 128, DISPLAY_NAME_CAP_BYTES, ("🚀" * 128, None)),
        ("🚀" * 128 + "a", DISPLAY_NAME_CAP_BYTES, (None, "oversize_field_withheld")),
    )
    for raw_val, cap, expected in cases:
        assert sanitize_stored_string(raw_val, field_cap_bytes=cap) == expected


def test_skeleton_compaction_and_4096_byte_minimum_fit() -> None:
    long_q = "Δ" * 80  # 160 UTF-8 bytes (> 128)
    pkt = _sample_skeleton(budget_bytes=4096, query=long_q)
    many_bindings = [f"bind1:{i:060d}" for i in range(6)]
    pkt["scope"]["selected_binding_ids"] = many_bindings
    pkt["scope"]["path_prefixes"] = [
        {"binding_id": b, "prefix": "src/main/python/pkg/"} for b in many_bindings
    ]
    size = apply_skeleton_compaction(pkt, 4096)
    assert size <= 4096
    assert pkt["request"]["target_echo"] is None
    assert pkt["request"]["target_echo_withheld"] is True
    assert pkt["scope"]["selected_binding_ids"] is None
    assert pkt["scope"]["path_prefixes"] is None
    assert pkt["scope"]["scope_echo_complete"] is False
    assert_valid_context_packet(pkt)


def test_actual_bytes_fixpoint_across_10000_digit_boundary_and_envelope_overflow() -> None:
    sizes: list[int] = []
    for additional in (0, 32):
        pkt = _sample_skeleton(budget_bytes=16384, query="InvoiceService")
        pkt["omissions"] = [{"reason": "output_budget", "count": 1, "detail": ""}]
        base_raw, base_len = compute_actual_bytes_fixpoint(pkt)
        assert base_len == len(base_raw)
        pkt["omissions"][0]["detail"] = "x" * (9990 - base_len + additional)
        final_pkt = finalize_packet_within_budget(pkt, 16384)
        raw = serialize_compact_json(final_pkt)
        assert len(raw) == final_pkt["work_ledger"]["actual_bytes"] <= 16384
        sizes.append(len(raw))
    assert sizes[0] < 10000 <= sizes[1]

    # If required skeleton itself exceeds a tiny synthetic budget, return required_envelope_does_not_fit
    pkt2 = _sample_skeleton(budget_bytes=4096, query="InvoiceService")
    min_b = maximally_compacted_skeleton_bytes(pkt2)
    err = finalize_packet_within_budget(pkt2, min_b - 1)
    assert_valid_budget_error(err)
    assert err["reason"] == "required_envelope_does_not_fit"
    assert err["required_min_bytes"] == min_b


def test_finalize_rejects_unreserved_optional_growth_and_preserves_records() -> None:
    from common_lore_index.retrieval.context_admission import (
        admit_items_and_relations,
        format_context_item,
        format_context_relation,
    )

    pkt = _sample_skeleton(budget_bytes=4096, query="InvoiceService")
    om = OmissionCounter()
    seed = format_context_item(
        item_id="seed:1", item_role="seed", canonical_key="py:pkg.Seed",
        kind="python.class", display_name="Seed", identity_status="available",
        raw_locations=(), markers=(), locations_per_item=2, reserve_freshness=False,
        omissions=om, rank=1, match_class="exact_canonical_key",
        rank_tiers={"exactness_tier": 0, "affinity_tier": 0, "match_class_tier": 0, "evidence_tier": 1, "publication_tier": 1},
        rank_reasons=("exact_canonical_key",),
    )
    seed["omitted_location_count"] = 2
    nbr = format_context_item(
        item_id="neighbor:1", item_role="neighbor", canonical_key="py:pkg.Nbr",
        kind="python.function", display_name="Nbr", identity_status="available",
        raw_locations=(), markers=(), locations_per_item=2, reserve_freshness=False,
        omissions=om, match_surface="graph_topology",
    )
    rel = format_context_relation({
        "source_canonical_key": "py:pkg.Seed", "edge_kind": "calls",
        "target_canonical_key": "py:pkg.Nbr", "edge_key": "sha256:" + "1" * 64,
        "confidence": "high", "conflict": False, "first_seen_run_id": 1, "last_seen_run_id": 1,
    })
    pkt.update(items=[seed], neighbors=[nbr], relations=[rel], absence_basis=None)
    pkt["expansion"].update(neighbors_admitted=1, relations_admitted=1)
    pkt["omission_counts"]["location_limit"] = 2
    compute_actual_bytes_fixpoint(pkt)
    before = copy.deepcopy(pkt)
    # A caller bypassing reservation cannot make finalization drop inspected records.
    _, empty_len = compute_actual_bytes_fixpoint(_sample_skeleton(budget_bytes=4096, query="InvoiceService"))
    with pytest.raises(AssertionError):
        finalize_packet_within_budget(pkt, empty_len + 10, top_tied_keys=frozenset({"py:pkg.Seed"}))
    for key in ("items", "neighbors", "relations", "omission_counts", "status", "absence_basis"):
        assert pkt[key] == before[key]

    # Also verify failed 1-location retry in admit_items_and_relations rolls back both counts and records
    pkt3 = _sample_skeleton(budget_bytes=4096, query="InvoiceService")
    om3 = OmissionCounter()
    seed_2locs = dict(seed)
    seed_2locs["locations"] = [
        {"source_binding_id": "b", "binding_revision": 1, "snapshot_id": "s", "source_relative_path": "a.py",
         "run_id": 1, "publication_relation": "accepted_run", "path_namespace": "source_relative",
         "graph_path": "a.py", "start_line": 1, "end_line": 2, "evidence_link_kind": "observed",
         "evidence_raw_kind": "python.class", "evidence_confidence": "high", "freshness": None},
        {"source_binding_id": "b", "binding_revision": 1, "snapshot_id": "s", "source_relative_path": "b.py",
         "run_id": 1, "publication_relation": "accepted_run", "path_namespace": "source_relative",
         "graph_path": "b.py", "start_line": 1, "end_line": 2, "evidence_link_kind": "observed",
         "evidence_raw_kind": "python.class", "evidence_confidence": "high", "freshness": None},
    ]
    seed_2locs["omitted_location_count"] = 1
    admit_items_and_relations(
        pkt3, candidate_seeds=[seed_2locs], total_post_offset_seeds=1, top_tied_keys=frozenset(),
        neighbor_units=(), extra_relations=(), omissions=om3, budget_bytes=empty_len,
    )
    assert om3.counts["location_limit"] == 0
    assert all(r["reason"] != "location_limit" for r in om3.records)


class _NoDeepCopyDict(dict[str, Any]):
    def __deepcopy__(self, memo: object) -> Any:
        raise AssertionError("opaque payload must not be deep-copied during reservation")


def _plain_clone(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _plain_clone(val) for k, val in v.items()}
    if isinstance(v, tuple):
        return tuple(_plain_clone(x) for x in v)
    return [_plain_clone(x) for x in v] if isinstance(v, list) else v


def _incumbent_fixpoint(packet: dict[str, Any]) -> tuple[bytes, int]:
    ledger = packet["work_ledger"]
    ledger["actual_bytes"] = 0
    raw = serialize_compact_json(packet)
    length = len(raw)
    for _ in range(3):
        ledger["actual_bytes"] = length
        raw_next = serialize_compact_json(packet)
        if len(raw_next) == length:
            return raw_next, length
        raw, length = raw_next, len(raw_next)
    ledger["actual_bytes"] = length
    raw = serialize_compact_json(packet)
    return raw, len(raw)


_REF_OMISSION_LIMITS = {
    "output_budget": 16_384, "item_limit": 16_000, "location_limit": 90_000,
    "ambiguous_alternative_omitted_by_budget": 16_000, "expansion_node_limit": 320,
    "expansion_edge_limit": 5, "expansion_backend_unsupported": 1,
    "candidate_scan_incomplete": 9, "unlocatable_kind_pattern_excluded": 32,
    "oversize_field_withheld": 106_129, "unencodable_field_withheld": 106_129,
}
_REF_WORK_MAXIMA = {
    "config_revalidations_executed": 1, "logical_read_units": 16, "sql_statements_executed": 36,
    "scan_units_executed": 8, "seed_expansions_executed": 5, "candidate_rows_scanned": 16_000,
    "evidence_rows_scanned": 90_000, "sqlite_vm_steps_estimate": 9_000_000,
    "elapsed_ms": 10_001, "sqlite_progress_callbacks": 9_000,
}


def _incumbent_reserve_size(packet: dict[str, Any], *, empty: bool = False) -> int:
    if empty:
        empty_pkt = copy.deepcopy(_plain_clone(packet))
        empty_pkt["items"], empty_pkt["neighbors"], empty_pkt["relations"], empty_pkt["omissions"] = [], [], [], []
        return _incumbent_reserve_size(empty_pkt)
    res = copy.deepcopy(_plain_clone(packet))
    if res.get("status") in {"complete", "partial"}:
        res["status"] = "complete"
    if isinstance(res.get("omission_counts"), dict):
        for k, v in tuple(res["omission_counts"].items()):
            res["omission_counts"][k] = max(_REF_OMISSION_LIMITS.get(k, 0), int(v or 0))
    if isinstance(res.get("omissions"), list):
        recs = [
            {**dict(r), "count": max(_REF_OMISSION_LIMITS.get(str(r.get("reason", "")), 0), int(r.get("count") or 0))}
            for r in res["omissions"]
        ]
        if not any(r.get("reason") == "output_budget" for r in recs):
            recs.append({"reason": "output_budget", "count": 16_384})
        res["omissions"] = recs
    if isinstance(res.get("work_ledger"), dict):
        for k, mx in _REF_WORK_MAXIMA.items():
            if type(res["work_ledger"].get(k)) is int:
                res["work_ledger"][k] = max(res["work_ledger"][k], mx)
    locs = [
        loc for gk in ("items", "neighbors") for item in (res.get(gk, ()) or ())
        for loc in item.get("locations", ()) if isinstance(loc, dict)
    ]
    fresh = [loc for loc in locs if loc.get("freshness") is not None]
    if res.get("absence_basis") is None and not res.get("items"):
        sc = res.get("scope") or {}
        scoped = int(sc.get("selected_binding_count") or 0) > 0 or int(sc.get("path_prefix_count") or 0) > 0
        res["absence_basis"] = (
            "stored_direct_locatable_metadata_fields_in_selected_scope"
            if scoped else "stored_metadata_fields_in_selected_scope"
        )
    if fresh:
        for loc in fresh:
            if isinstance(loc["freshness"], dict):
                loc["freshness"].update(
                    state="no_change_observed_with_limits", reason="structural_span_only_no_lexical_anchor",
                    limits=["structural_span_only", "no_lexical_anchor", "no_body_text"],
                )
        if isinstance(res.get("freshness"), dict):
            n = len(fresh)
            res["freshness"].update(
                state="checked", files_checked=16, bytes_read=262_144,
                counts={"changed_observed": n, "no_change_observed_with_limits": n, "not_checked": n, "unavailable": n},
            )
    return _incumbent_fixpoint(res)[1]


def test_arithmetic_fixpoint_and_single_encode_reservation_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    for zero_len in (*range(1, 20), *range(96, 105), *range(995, 1005), *range(9994, 10006), *range(99993, 100007)):
        base, cur = zero_len - 1, zero_len
        for _ in range(3):
            cur = base + len(str(cur))
        assert converge_actual_bytes_from_zero_length(zero_len) == cur
    real_dumps, calls = json.dumps, 0

    def counting_dumps(*args: Any, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(json, "dumps", counting_dumps)
    tricky = "\x00\x08\t\n\r\x1f\"\\/Δλ漢🚀e\u0301\"actual_bytes\":0"
    probe = _sample_skeleton(budget_bytes=16384, query="InvoiceService")
    probe["omissions"] = [{"reason": "output_budget", "count": 1, "detail": tricky}]
    _, base_size = _incumbent_fixpoint(copy.deepcopy(probe))
    for delta in range(-6, 8):
        pkt = _sample_skeleton(budget_bytes=16384, query="InvoiceService")
        pkt["omissions"] = [{"reason": "output_budget", "count": 1, "detail": tricky + ("x" * max(0, 10000 - base_size + delta))}]
        ref_pkt = copy.deepcopy(pkt)
        ref_raw, ref_len = _incumbent_fixpoint(ref_pkt)
        calls = 0
        got_raw, got_len = compute_actual_bytes_fixpoint(pkt)
        assert calls == 2 and (got_raw, got_len, pkt) == (ref_raw, ref_len, ref_pkt)
        calls = 0
        got_sz = packet_size_with_reservation(pkt)
        assert calls == 1 and got_sz == _incumbent_reserve_size(pkt)

    calls = 0
    assert maximally_compacted_skeleton_bytes(probe) > 0 and calls == 1
    calls = 0
    assert finalize_packet_within_budget(copy.deepcopy(probe), 16384)["work_ledger"]["actual_bytes"] == base_size and calls == 1
    shared_w: dict[str, Any] = {"actual_bytes": 0}
    aliased, aliased_ref = {"work_ledger": shared_w, "mirror": shared_w}, {"work_ledger": {"actual_bytes": 0}}
    aliased_ref["mirror"] = aliased_ref["work_ledger"]
    assert compute_actual_bytes_fixpoint(aliased) == _incumbent_fixpoint(aliased_ref)

    rich = _sample_skeleton(budget_bytes=16384, query="Δ🚀\t\"q\"")
    rich.update(status="partial")
    rich["freshness"]["state"] = "checked"
    rich["work_ledger"].update(sqlite_vm_steps_estimate=None, elapsed_ms=25_000)
    rich["omission_counts"]["custom_key"] = 7
    rich["omissions"] = [{"reason": "item_limit", "count": 2, "detail": tricky}]
    rich["items"] = [
        {
            "canonical_key": "py:pkg.A", "rank_tiers": _NoDeepCopyDict(exactness_tier=0), "opaque": _NoDeepCopyDict(nested="keep"),
            "locations": [
                {"path": "a.py", "opaque": _NoDeepCopyDict(tag=1), "freshness": {
                    "state": "not_checked", "reason": "r", "limits": [], "inspected_span": _NoDeepCopyDict(start_line=1, end_line=3),
                }},
                _NoDeepCopyDict(path="untracked.py", freshness=None),
                None,
            ],
        },
        _NoDeepCopyDict(canonical_key="py:pkg.Plain", locations=[_NoDeepCopyDict(path="b.py", freshness=None)]),
    ]
    rich["neighbors"] = [{"canonical_key": "py:pkg.N", "locations": [{"path": "n.py", "freshness": "raw_marker"}]}]
    rich["relations"] = [{"source_key": "py:pkg.A", "target_key": "py:pkg.N", "support": _NoDeepCopyDict(basis="stored_canonical_edge")}]
    snap = _plain_clone(rich)
    calls = 0
    got_res = packet_size_with_reservation(rich)
    assert calls == 1
    calls = 0
    got_empty = empty_packet_size_with_reservation(rich)
    assert calls == 1 and _plain_clone(rich) == snap
    assert got_res == _incumbent_reserve_size(snap) and got_empty == _incumbent_reserve_size(snap, empty=True)

    for defensive in (
        {"status": "unavailable", "work_ledger": {"actual_bytes": 0, "elapsed_ms": False}},
        {"work_ledger": {"actual_bytes": 0}, "scope": {"path_prefix_count": 1}, "omissions": [{"reason": "output_budget", "count": 999999}], "items": ({"locations": ()},)},
        {"work_ledger": {"actual_bytes": 0}, "freshness": None, "items": [{"locations": [{"freshness": {"state": "not_checked"}}]}]},
    ):
        before_def = _plain_clone(defensive)
        assert packet_size_with_reservation(defensive) == _incumbent_reserve_size(before_def)
        assert empty_packet_size_with_reservation(defensive) == _incumbent_reserve_size(before_def, empty=True)
        assert _plain_clone(defensive) == before_def

    non_dict_ledger: UserDict[str, int] = UserDict({"actual_bytes": 77})
    with pytest.raises(TypeError):
        packet_size_with_reservation({"work_ledger": non_dict_ledger})
    assert dict(non_dict_ledger) == {"actual_bytes": 77}
