"""Pre-freshness packet reservation helpers for W4 admission."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from common_lore_index.retrieval.context_budget import (
    _withhold_scope_echo,
    _withhold_target_echo,
    apply_skeleton_compaction,
    compact_json_byte_length,
    converge_actual_bytes_from_zero_length,
)

_FRESHNESS_REASON = "structural_span_only_no_lexical_anchor"
_FRESHNESS_LIMITS = ["structural_span_only", "no_lexical_anchor", "no_body_text"]

# These are the closed W4 ceilings, expressed as packet-field maxima rather
# than a generic digit cushion.  They bound the largest diagnostics that can
# be produced before the freshness pass runs.
_MAX_SCAN_UNITS = 8
_MAX_SCAN_NODES = 2_000
_MAX_NEIGHBORS = 128
_MAX_RELATIONS = 256
_MAX_CANDIDATE_ROWS = _MAX_SCAN_UNITS * _MAX_SCAN_NODES
_MAX_LOCATIONS = (_MAX_SCAN_UNITS + 1) * 10_000
_MAX_OUTPUT_OMISSIONS = _MAX_CANDIDATE_ROWS + _MAX_NEIGHBORS + _MAX_RELATIONS
_MAX_FIELD_OMISSIONS = _MAX_LOCATIONS + _MAX_CANDIDATE_ROWS + _MAX_NEIGHBORS + 1
_MAX_EXPANSION_EDGES = 5 * 64
_MAX_SQL_STATEMENTS = 36
_MAX_PROGRESS_CALLBACKS = _MAX_SQL_STATEMENTS * 250
_MAX_VM_STEPS = _MAX_PROGRESS_CALLBACKS * 1_000
_MAX_EVIDENCE_ROWS = (_MAX_SCAN_UNITS + 1) * 10_000

_OMISSION_COUNT_LIMITS = {
    "output_budget": _MAX_OUTPUT_OMISSIONS,
    "item_limit": _MAX_CANDIDATE_ROWS,
    "location_limit": _MAX_LOCATIONS,
    "ambiguous_alternative_omitted_by_budget": _MAX_CANDIDATE_ROWS,
    "expansion_node_limit": _MAX_EXPANSION_EDGES,
    "expansion_edge_limit": 5,
    "expansion_backend_unsupported": 1,
    "candidate_scan_incomplete": _MAX_SCAN_UNITS + 1,
    "unlocatable_kind_pattern_excluded": 32,
    "oversize_field_withheld": _MAX_FIELD_OMISSIONS,
    "unencodable_field_withheld": _MAX_FIELD_OMISSIONS,
}

_WORK_LEDGER_MAXIMA = {
    "config_revalidations_executed": 1,
    "logical_read_units": 16,
    "sql_statements_executed": _MAX_SQL_STATEMENTS,
    "scan_units_executed": _MAX_SCAN_UNITS,
    "seed_expansions_executed": 5,
    "candidate_rows_scanned": _MAX_CANDIDATE_ROWS,
    "evidence_rows_scanned": _MAX_EVIDENCE_ROWS,
    "sqlite_vm_steps_estimate": _MAX_VM_STEPS,
    "elapsed_ms": 10_001,
    "sqlite_progress_callbacks": _MAX_PROGRESS_CALLBACKS,
}


def _project_location_for_reservation(loc: Any) -> tuple[Any, int]:
    if not isinstance(loc, dict) or loc.get("freshness") is None:
        return loc, 0
    obs = loc["freshness"]
    if not isinstance(obs, dict):
        return loc, 1
    return (
        {
            **loc,
            "freshness": {
                **obs,
                "state": "no_change_observed_with_limits",
                "reason": _FRESHNESS_REASON,
                "limits": list(_FRESHNESS_LIMITS),
            },
        },
        1,
    )


def _project_item_for_reservation(item: Any) -> tuple[Any, int]:
    locs = item.get("locations") if isinstance(item, Mapping) else None
    if not locs:
        return item, 0
    projected_locs = []
    fresh_count = 0
    for location in locs:
        projected, count = _project_location_for_reservation(location)
        projected_locs.append(projected)
        fresh_count += count
    if fresh_count == 0:
        return item, 0
    return {**item, "locations": projected_locs}, fresh_count


def _project_group_for_reservation(group: Any) -> tuple[Any, int]:
    projected_items = []
    fresh_count = 0
    for item in group:
        projected, count = _project_item_for_reservation(item)
        projected_items.append(projected)
        fresh_count += count
    if fresh_count == 0:
        return group, 0
    return projected_items, fresh_count


def _project_reserved_packet(
    packet: Mapping[str, Any], *, empty_collections: bool = False
) -> dict[str, Any]:
    projected = dict(packet)
    if empty_collections:
        projected["items"] = []
        projected["neighbors"] = []
        projected["relations"] = []
        projected["omissions"] = []

    if projected.get("status") in {"complete", "partial"}:
        projected["status"] = "complete"

    counts = projected.get("omission_counts")
    if isinstance(counts, dict):
        projected["omission_counts"] = {
            key: max(_OMISSION_COUNT_LIMITS.get(key, 0), int(value or 0))
            for key, value in counts.items()
        }

    records = projected.get("omissions")
    if isinstance(records, list):
        reserved: list[dict[str, Any]] = []
        for record in records:
            copy_record = dict(record)
            reason = str(copy_record.get("reason", ""))
            copy_record["count"] = max(
                _OMISSION_COUNT_LIMITS.get(reason, 0),
                int(copy_record.get("count") or 0),
            )
            reserved.append(copy_record)
        if not any(record.get("reason") == "output_budget" for record in reserved):
            reserved.append({"reason": "output_budget", "count": _MAX_OUTPUT_OMISSIONS})
        projected["omissions"] = reserved

    work = projected.get("work_ledger")
    if isinstance(work, dict):
        reserved_work = dict(work)
        for key, maximum in _WORK_LEDGER_MAXIMA.items():
            if type(reserved_work.get(key)) is int:
                reserved_work[key] = max(reserved_work[key], maximum)
        reserved_work["actual_bytes"] = 0
        projected["work_ledger"] = reserved_work

    fresh_count = 0
    if not empty_collections:
        for group_key in ("items", "neighbors"):
            group = projected.get(group_key)
            if group:
                projected[group_key], group_fresh = _project_group_for_reservation(group)
                fresh_count += group_fresh

    if projected.get("absence_basis") is None and not projected.get("items"):
        scope = projected.get("scope") or {}
        scoped = (
            int(scope.get("selected_binding_count") or 0) > 0
            or int(scope.get("path_prefix_count") or 0) > 0
        )
        projected["absence_basis"] = (
            "stored_direct_locatable_metadata_fields_in_selected_scope"
            if scoped else "stored_metadata_fields_in_selected_scope"
        )

    if fresh_count > 0:
        freshness = projected.get("freshness")
        if isinstance(freshness, dict):
            projected["freshness"] = {
                **freshness,
                "state": "checked",
                "files_checked": 16,
                "bytes_read": 262_144,
                "counts": {
                    "changed_observed": fresh_count,
                    "no_change_observed_with_limits": fresh_count,
                    "not_checked": fresh_count,
                    "unavailable": fresh_count,
                },
            }
    return projected


def _measure_projected_packet_bytes(projected: dict[str, Any]) -> int:
    work = projected["work_ledger"]
    if not isinstance(work, dict):
        raise TypeError("packet work_ledger must be a dict")
    work["actual_bytes"] = 0
    return converge_actual_bytes_from_zero_length(compact_json_byte_length(projected))


def packet_size_with_reservation(packet: dict[str, Any]) -> int:
    """Measure a packet after reserving all bounded post-admission fields."""
    return _measure_projected_packet_bytes(_project_reserved_packet(packet))


def fits_packet_with_reservation(packet: dict[str, Any], budget_bytes: int) -> bool:
    """Keep compaction only for trials that fit the reserved budget."""
    request, scope, work = packet["request"], packet["scope"], packet["work_ledger"]
    saved_request, saved_scope, saved_work = dict(request), dict(scope), dict(work)
    accepted = False
    try:
        apply_skeleton_compaction(packet, budget_bytes)
        size = packet_size_with_reservation(packet)
        if size > budget_bytes and request.get("target_echo") is not None:
            _withhold_target_echo(packet)
            size = packet_size_with_reservation(packet)
        if size > budget_bytes and scope.get("scope_echo_complete") is not False:
            _withhold_scope_echo(packet)
            size = packet_size_with_reservation(packet)
        accepted = size <= budget_bytes
        return accepted
    finally:
        if not accepted:
            for fields, saved in ((request, saved_request), (scope, saved_scope), (work, saved_work)):
                fields.clear()
                fields.update(saved)


def empty_packet_size_with_reservation(packet: dict[str, Any]) -> int:
    """Measure the final empty envelope with current immutable diagnostics."""
    return _measure_projected_packet_bytes(
        _project_reserved_packet(packet, empty_collections=True)
    )
