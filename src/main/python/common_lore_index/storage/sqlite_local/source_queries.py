"""Read accepted SQLite Local source records using the Server projection contract.

SQLite decodes stored JSON without a JSON SQL extension, counts LEFT JOIN
fan-out, validates PostgreSQL integer casts, and keeps NULLs last in descending
order. Raw and link keys break otherwise unspecified ties.
"""

from __future__ import annotations

import sqlite3
from bisect import insort
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass
from heapq import nlargest
from itertools import batched
from typing import Any, NamedTuple, TypeVar

from common_lore_index.storage.source_rows import (
    IngestedSourceRecord,
    SourceRunRecord,
    SourceSummaryRecord,
    ingested_source_record_from_storage_payload,
    source_summary_from_storage_payload,
)
from common_lore_index.storage.sql_core import positive_limit
from common_lore_index.storage.sqlite_local import raw_payload
from common_lore_index.storage.sqlite_local.queries import require_accepted_publication
from common_lore_index.storage.sqlite_local.source_run_summary import SourceRunSummary

Ordered = TypeVar("Ordered", str, int)
RawKey = tuple[int, int]

_MARKER = {"marker": '"source_id_configured"'}
_SOURCE_RAW = "instr(raw.payload_json, :marker) > 0"
_RAW_JOIN = (
    "JOIN raw_observations raw ON raw.run_id = evidence.run_id "
    "AND raw.ordinal = evidence.raw_observation_ordinal"
)
_ID_BATCH = 500


@dataclass(frozen=True)
class SourceObservation:
    """One raw row with ``metadata.source_id_configured``: the ``source_observations`` CTE."""

    key: RawKey
    kind: str
    source_id: str
    source_type: str | None
    display_name: str | None
    policy_status: str | None
    source_run_id: str | None
    artifact_id: str | None
    artifact_path: str | None
    artifact_sha256: str | None
    artifact_bytes: str | None
    acquired_at: str | None
    http_status: str | None
    content_type: str | None
    url_summary: str | None


class _Node(NamedTuple):
    id: int
    key: str
    kind: str


class _Edge(NamedTuple):
    id: int
    source: str
    kind: str
    target: str
    identity_hash: str
    link_kind: str
    metadata: Any


def source_observations(connection: sqlite3.Connection, *, source_id: str | None = None,
                        source_run_id: str | None = None) -> list[SourceObservation]:
    return list(_iter_source_observations(connection, source_id, source_run_id))


def _iter_source_observations(connection: sqlite3.Connection, source_id: str | None = None,
                              source_run_id: str | None = None) -> Iterator[SourceObservation]:
    with closing(connection.execute(
        "SELECT run_id, ordinal, kind, payload_json FROM raw_observations raw "
        f"WHERE {_SOURCE_RAW} ORDER BY run_id, ordinal", _MARKER,
    )) as rows:
        for run_id, ordinal, kind, text in rows:
            payload = raw_payload.load(text)
            configured_id = raw_payload.meta(payload, "source_id_configured")
            if configured_id is None or (source_id is not None and configured_id != source_id):
                continue
            observed_run_id = raw_payload.meta(payload, "source_run_id")
            if source_run_id is not None and observed_run_id != source_run_id:
                continue
            yield SourceObservation(
                (int(run_id), int(ordinal)), kind, configured_id,
                raw_payload.meta(payload, "source_type"), raw_payload.meta(payload, "source_display_name"),
                raw_payload.meta(payload, "source_policy_status"), observed_run_id,
                *(raw_payload.meta(payload, name) for name in (
                    "source_artifact_id", "source_artifact_path", "source_artifact_sha256",
                    "source_artifact_bytes", "source_acquired_at", "acquisition_http_status",
                    "acquisition_content_type", "acquisition_url_summary",
                )),
            )


def group_by(rows: Iterable[tuple[Any, ...]]) -> dict[Any, list[Any]]:
    grouped: dict[Any, list[Any]] = {}
    for key, *value in rows:
        grouped.setdefault(key, []).append(value[0] if len(value) == 1 else tuple(value))
    return grouped


def _batched_in(
    connection: sqlite3.Connection, sql: str, values: Iterable[int], order: str
) -> Iterator[tuple[Any, ...]]:
    for page in batched(sorted(set(values)), _ID_BATCH):
        yield from connection.execute(f"{sql} ({', '.join('?' for _ in page)}) {order}", page)


def _link_rows(
    connection: sqlite3.Connection, select: str, target: str, ids: Iterable[int] | None
) -> Iterable[tuple[Any, ...]]:
    order = f"ORDER BY link.canonical_evidence_id, {target}.id, link.link_kind"
    if ids is not None:
        return _batched_in(connection, f"{select} WHERE link.canonical_evidence_id IN", ids, order)
    return connection.execute(
        f"{select} JOIN canonical_evidence evidence ON evidence.id = link.canonical_evidence_id "
        f"{_RAW_JOIN} WHERE {_SOURCE_RAW} {order}", _MARKER,
    )


def evidence_ids(
    connection: sqlite3.Connection, keys: Iterable[RawKey] | None = None
) -> dict[RawKey, list[int]]:
    """Evidence ids per source raw row, optionally restricted to exact ``keys``."""
    if keys is None:
        sql = (
            "SELECT evidence.run_id, evidence.raw_observation_ordinal, evidence.id "
            f"FROM canonical_evidence evidence {_RAW_JOIN} WHERE {_SOURCE_RAW} ORDER BY evidence.id"
        )
        return group_by(((r[0], r[1]), r[2]) for r in connection.execute(sql, _MARKER))
    selected = {(int(run), int(ordinal)) for run, ordinal in keys}
    pair_limit = _ID_BATCH // 2
    # A run with many selected keys would otherwise be scanned for every batch.
    counts = Counter(run for run, _ in selected)
    wide_runs = {run for run, count in counts.items() if count > pair_limit}
    query = "SELECT run_id, raw_observation_ordinal, id FROM canonical_evidence WHERE run_id IN"
    matches = group_by(
        ((run, ordinal), evidence_id)
        for run, ordinal, evidence_id in _batched_in(connection, query, wide_runs, "")
        if (run, ordinal) in selected
    )
    for ids in matches.values():
        ids.sort()
    sparse = sorted(key for key in selected if key[0] not in wide_runs)
    for page in batched(sparse, pair_limit):
        values = ", ".join("(?, ?)" for _ in page)
        # The run predicate uses the existing run-led index. The tuple predicate admits exact observations.
        sql = f"""WITH selected(run_id, ordinal) AS (VALUES {values})
            SELECT run_id, raw_observation_ordinal, id FROM canonical_evidence
            WHERE run_id IN (SELECT run_id FROM selected)
              AND (run_id, raw_observation_ordinal) IN (SELECT run_id, ordinal FROM selected)
            ORDER BY id"""
        with closing(connection.execute(sql, tuple(value for key in page for value in key))) as rows:
            for run_id, ordinal, evidence_id in rows:
                matches.setdefault((run_id, ordinal), []).append(evidence_id)
    return matches


def node_links(connection: sqlite3.Connection, ids: Iterable[int] | None = None) -> dict[int, list[_Node]]:
    sql = (
        "SELECT link.canonical_evidence_id, node.id, node.canonical_key, node.kind "
        "FROM canonical_node_evidence link JOIN canonical_nodes node ON node.id = link.canonical_node_id"
    )
    return group_by((row[0], _Node(*row[1:])) for row in _link_rows(connection, sql, "node", ids))


def edge_links(connection: sqlite3.Connection, ids: Iterable[int] | None = None) -> dict[int, list[_Edge]]:
    sql = (
        "SELECT link.canonical_evidence_id, edge.id, edge.source_canonical_key, edge.edge_kind, "
        "edge.target_canonical_key, edge.identity_metadata_hash, link.link_kind, edge.metadata_json "
        "FROM canonical_edge_evidence link JOIN canonical_edges edge ON edge.id = link.canonical_edge_id"
    )
    return group_by(
        (row[0], _Edge(row[1], row[2], row[3], row[4], row[5], row[6], raw_payload.load(row[7])))
        for row in _link_rows(connection, sql, "edge", ids)
    )


def fanout(
    observations: Iterable[SourceObservation],
    evidence: dict[RawKey, list[int]],
    nodes: dict[int, list[_Node]],
    edges: dict[int, list[_Edge]] | None = None,
) -> Iterator[tuple[SourceObservation, _Node | None, _Edge | None]]:
    """The LEFT JOIN rows: an unmatched observation or evidence row still counts once."""
    for observation in observations:
        evidence_rows: Sequence[int | None] = evidence.get(observation.key) or (None,)
        for evidence_id in evidence_rows:
            node_rows: Sequence[_Node | None] = (nodes.get(evidence_id) if evidence_id is not None else None) or (None,)
            edge_rows: Sequence[_Edge | None] = (
                edges.get(evidence_id) if edges is not None and evidence_id is not None else None
            ) or (None,)
            for node in node_rows:
                for edge in edge_rows:
                    yield observation, node, edge


def max_of(values: Iterable[Ordered | None]) -> Ordered | None:
    """``MAX``: NULLs ignored; NULL when nothing remains."""
    return max((value for value in values if value is not None), default=None)


def min_of(values: Iterable[Ordered | None]) -> Ordered | None:
    """``MIN``: NULLs ignored; NULL when nothing remains."""
    return min((value for value in values if value is not None), default=None)


def not_fetched(value: str | None) -> bool:
    """``COALESCE(value::boolean, true)``."""
    return True if value is None else raw_payload.flag(value)


def _latest(group: list[SourceObservation]) -> SourceObservation:
    """First element of ``ARRAY_AGG(... ORDER BY source_acquired_at DESC NULLS LAST)``."""
    return max(group, key=lambda item: (item.acquired_at is not None, item.acquired_at or ""))


def by_source(observations: Iterable[SourceObservation]) -> dict[str, list[SourceObservation]]:
    return group_by((observation.source_id, observation) for observation in observations)


def _observation_fanout_count(
    observation: SourceObservation,
    evidence: dict[RawKey, list[int]],
    nodes: dict[int, list[_Node]],
    edges: dict[int, list[_Edge]] | None = None,
) -> int:
    matched = evidence.get(observation.key)
    if not matched:
        return 1
    if edges is None:
        return sum(max(1, len(nodes.get(evidence_id, ()))) for evidence_id in matched)
    return sum(
        max(1, len(nodes.get(evidence_id, ()))) * max(1, len(edges.get(evidence_id, ())))
        for evidence_id in matched
    )


def _matched_evidence(group: Iterable[SourceObservation], evidence: dict[RawKey, list[int]]) -> set[int]:
    return {evidence_id for item in group for evidence_id in evidence.get(item.key, ())}


def _node_counts(evidence_set: Iterable[int], nodes: dict[int, list[_Node]]) -> dict[str, int]:
    by_kind: dict[str, set[str]] = {}
    for evidence_id in evidence_set:
        for node in nodes.get(evidence_id, ()):
            by_kind.setdefault(node.kind, set()).add(node.key)
    return {kind: len(keys) for kind, keys in by_kind.items()}


def _reference_counts(evidence_set: Iterable[int], edges: dict[int, list[_Edge]]) -> tuple[int, int]:
    unique = {
        edge.id: edge for evidence_id in evidence_set
        for edge in edges.get(evidence_id, ()) if edge.kind == "references"
    }
    scopes = [raw_payload.text_at(edge.metadata, "scope") for edge in unique.values()]
    return scopes.count("link"), scopes.count("enclosure")


def ingested_sources(
    connection: sqlite3.Connection, *, source_type: str | None, policy_status: str | None, limit: int
) -> tuple[IngestedSourceRecord, ...]:
    safe_limit = positive_limit(limit)
    require_accepted_publication(connection)
    groups: dict[str, list[SourceObservation]] = {}
    selected_ids: list[str] = []
    for observation in _iter_source_observations(connection):
        if ((source_type is not None and observation.source_type != source_type)
                or (policy_status is not None and observation.policy_status != policy_status)):
            continue
        source_id = observation.source_id
        if source_id in groups:
            groups[source_id].append(observation)
            continue
        if len(selected_ids) == safe_limit:
            if source_id >= selected_ids[-1]:
                continue
            del groups[selected_ids.pop()]
        insort(selected_ids, source_id)
        groups[source_id] = [observation]
    if not selected_ids:
        return ()
    evidence = evidence_ids(
        connection, (item.key for source_id in selected_ids for item in groups[source_id])
    )
    nodes = node_links(connection, (eid for ids in evidence.values() for eid in ids))
    records = []
    for source_id in selected_ids:
        group = groups[source_id]
        latest = _latest(group)
        node_counts = _node_counts(_matched_evidence(group, evidence), nodes)
        records.append(ingested_source_record_from_storage_payload({
            "source_id": source_id, "source_type": min_of(item.source_type for item in group),
            "display_name": min_of(item.display_name for item in group),
            "policy_status": min_of(item.policy_status for item in group),
            "latest_source_run_id": latest.source_run_id, "latest_artifact_id": latest.artifact_id,
            "latest_artifact_path": latest.artifact_path,
            "latest_acquired_at": max_of(item.acquired_at for item in group),
            "feed_observation_count": sum(_observation_fanout_count(item, evidence, nodes) for item in group),
            "canonical_feed_item_count": node_counts.get("feed.item", 0),
        }))
    return tuple(records)


def source_summary(connection: sqlite3.Connection, *, source_id: str) -> SourceSummaryRecord:
    require_accepted_publication(connection)
    group = source_observations(connection, source_id=source_id)
    if not group:
        return source_summary_from_storage_payload({
            "source_id": source_id, "source_type": None, "display_name": None,
            "policy_status": "unknown", "configured_url_summary": None,
            "latest_source_run_id": None, "latest_artifact_id": None, "latest_artifact_path": None,
            "latest_acquired_at": None, "feed_documents": 0, "feed_channels": 0, "feed_items": 0,
            "feed_authors": 0, "feed_categories": 0, "link_references": 0,
            "enclosure_references": 0, "parse_errors": 0,
            "known_limitations": ["source metadata unavailable"],
        })
    evidence = evidence_ids(connection, (item.key for item in group))
    matched = _matched_evidence(group, evidence)
    nodes, edges = node_links(connection, matched), edge_links(connection, matched)
    node_counts = _node_counts(matched, nodes)
    references = _reference_counts(matched, edges)
    latest = _latest(group)
    return source_summary_from_storage_payload({
        "source_id": source_id, "source_type": min_of(item.source_type for item in group),
        "display_name": min_of(item.display_name for item in group),
        "policy_status": min_of(item.policy_status for item in group),
        "configured_url_summary": min_of(item.url_summary for item in group),
        "latest_source_run_id": latest.source_run_id, "latest_artifact_id": latest.artifact_id,
        "latest_artifact_path": latest.artifact_path,
        "latest_acquired_at": max_of(item.acquired_at for item in group),
        "feed_documents": node_counts.get("feed.document", 0),
        "feed_channels": node_counts.get("feed.channel", 0), "feed_items": node_counts.get("feed.item", 0),
        "feed_authors": node_counts.get("feed.author", 0),
        "feed_categories": node_counts.get("feed.category", 0),
        "link_references": references[0], "enclosure_references": references[1],
        "parse_errors": sum(
            _observation_fanout_count(item, evidence, nodes, edges)
            for item in group if item.kind == "feed.parse_error"
        ),
        "known_limitations": ["source metadata is inferred from RSS2 evidence"],
    })


def source_runs(
    connection: sqlite3.Connection, *, source_id: str, limit: int
) -> tuple[SourceRunRecord, ...]:
    safe_limit = positive_limit(limit)
    require_accepted_publication(connection)
    runs: dict[str, SourceRunSummary] = {}
    for item in _iter_source_observations(connection, source_id):
        if item.source_run_id is None:
            continue
        summary = runs.get(item.source_run_id)
        if summary is None:
            summary = SourceRunSummary()
            runs[item.source_run_id] = summary
        summary.add(item)
    page = nlargest(
        safe_limit, runs,
        key=lambda run_id: (runs[run_id].acquired_at is not None, runs[run_id].acquired_at or "", run_id),
    )
    return tuple(runs[run_id].record(run_id) for run_id in page)


__all__ = (
    "RawKey", "SourceObservation", "by_source", "edge_links",
    "evidence_ids", "fanout", "group_by", "ingested_sources", "max_of", "min_of",
    "node_links", "not_fetched", "source_observations", "source_runs", "source_summary",
)
