"""Read accepted SQLite Local source records using the Server projection contract.

SQLite decodes stored JSON without a JSON SQL extension, counts LEFT JOIN
fan-out, validates PostgreSQL integer casts, and keeps NULLs last in descending
order. Raw and link keys break otherwise unspecified ties.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import closing
from heapq import heappush, heapreplace, nlargest
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
from common_lore_index.storage.sqlite_local.source_run_summary import (
    SourceFeedProvenance,
    SourceObservation,
    SourceProfile,
    SourceRunSummary,
)

Ordered = TypeVar("Ordered", str, int)
FeedObservation = TypeVar("FeedObservation", SourceObservation, SourceFeedProvenance)
RawKey = tuple[int, int]

_MARKER = {"marker": '"source_id_configured"'}
_SOURCE_RAW = "instr(raw.payload_json, :marker) > 0"
_RAW_JOIN = (
    "JOIN raw_observations raw ON raw.run_id = evidence.run_id "
    "AND raw.ordinal = evidence.raw_observation_ordinal"
)
_ID_BATCH = 500
_FEED_ITEM_KINDS = frozenset({"feed.item"})
_SOURCE_SUMMARY_KINDS = _FEED_ITEM_KINDS | {"feed.document", "feed.channel", "feed.author", "feed.category"}


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
            metadata = payload.get("metadata") if isinstance(payload, Mapping) else None
            configured_id = raw_payload.text_at(metadata, "source_id_configured")
            if configured_id is None or (source_id is not None and configured_id != source_id):
                continue
            observed_run_id = raw_payload.text_at(metadata, "source_run_id")
            if source_run_id is not None and observed_run_id != source_run_id:
                continue
            yield SourceObservation(
                (int(run_id), int(ordinal)), kind, configured_id,
                raw_payload.text_at(metadata, "source_type"), raw_payload.text_at(metadata, "source_display_name"),
                raw_payload.text_at(metadata, "source_policy_status"), observed_run_id,
                *(raw_payload.text_at(metadata, name) for name in (
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


def edge_links(connection: sqlite3.Connection, ids: Iterable[int] | None = None, *,
               include_metadata: bool = True) -> dict[int, list[_Edge]]:
    projection = "edge.metadata_json" if include_metadata else "NULL"
    sql = (
        "SELECT link.canonical_evidence_id, edge.id, edge.source_canonical_key, edge.edge_kind, "
        f"edge.target_canonical_key, edge.identity_metadata_hash, link.link_kind, {projection} "
        "FROM canonical_edge_evidence link JOIN canonical_edges edge ON edge.id = link.canonical_edge_id"
    )
    by_evidence: dict[int, list[_Edge]] = {}
    metadata_by_id: dict[int, Any] = {}
    for evidence_id, edge_id, source, kind, target, identity, link_kind, text in _link_rows(connection, sql, "edge", ids):
        if include_metadata and edge_id not in metadata_by_id:
            metadata_by_id[edge_id] = raw_payload.load(text)
        edge = _Edge(edge_id, source, kind, target, identity, link_kind, metadata_by_id.get(edge_id))
        by_evidence.setdefault(evidence_id, []).append(edge)
    return by_evidence


def fanout(
    observations: Iterable[FeedObservation],
    evidence: dict[RawKey, list[int]],
    nodes: dict[int, list[_Node]],
    edges: dict[int, list[_Edge]] | None = None,
) -> Iterator[tuple[FeedObservation, _Node | None, _Edge | None]]:
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


def by_source(observations: Iterable[SourceObservation]) -> dict[str, list[SourceObservation]]:
    return group_by((observation.source_id, observation) for observation in observations)


def _observation_fanout_count(
    key: RawKey,
    evidence: dict[RawKey, list[int]],
    nodes: dict[int, list[_Node]],
    edges: dict[int, list[_Edge]] | None = None,
) -> int:
    matched = evidence.get(key)
    if not matched:
        return 1
    if edges is None:
        return sum(max(1, len(nodes.get(evidence_id, ()))) for evidence_id in matched)
    return sum(
        max(1, len(nodes.get(evidence_id, ()))) * max(1, len(edges.get(evidence_id, ())))
        for evidence_id in matched
    )


def _matched_evidence(keys: Iterable[RawKey], evidence: dict[RawKey, list[int]]) -> set[int]:
    return {evidence_id for key in keys for evidence_id in evidence.get(key, ())}


def _node_counts(
    evidence_set: Iterable[int], nodes: dict[int, list[_Node]], kinds: frozenset[str],
) -> dict[str, int]:
    by_kind: dict[str, set[str]] = {}
    for evidence_id in evidence_set:
        for node in nodes.get(evidence_id, ()):
            if node.kind in kinds:
                by_kind.setdefault(node.kind, set()).add(node.key)
    return {kind: len(keys) for kind, keys in by_kind.items()}


def _reference_counts(evidence_set: Iterable[int], edges: dict[int, list[_Edge]]) -> tuple[int, int]:
    unique = {
        edge.id: edge for evidence_id in evidence_set
        for edge in edges.get(evidence_id, ()) if edge.kind == "references"
    }
    links = enclosures = 0
    for edge in unique.values():
        scope = raw_payload.text_at(edge.metadata, "scope")
        if scope == "link":
            links += 1
        elif scope == "enclosure":
            enclosures += 1
    return links, enclosures


class _DescendingSourceId(str):
    """Reverse the lexical order for the retained inventory max heap."""

    def __lt__(self, other: str) -> bool:
        return str.__gt__(self, other)


def ingested_sources(
    connection: sqlite3.Connection, *, source_type: str | None, policy_status: str | None, limit: int
) -> tuple[IngestedSourceRecord, ...]:
    safe_limit = positive_limit(limit)
    require_accepted_publication(connection)
    profiles: dict[str, SourceProfile] = {}
    selected: list[_DescendingSourceId] = []
    for observation in _iter_source_observations(connection):
        if ((source_type is not None and observation.source_type != source_type)
                or (policy_status is not None and observation.policy_status != policy_status)):
            continue
        source_id = observation.source_id
        profile = profiles.get(source_id)
        if profile is None:
            if len(selected) == safe_limit:
                if source_id >= selected[0]:
                    continue
                del profiles[heapreplace(selected, _DescendingSourceId(source_id))]
            else:
                heappush(selected, _DescendingSourceId(source_id))
            profile = SourceProfile()
            profiles[source_id] = profile
        profile.add(observation)
    selected_ids = sorted(profiles)
    if not selected_ids:
        return ()
    evidence = evidence_ids(
        connection, (key for source_id in selected_ids for key in profiles[source_id].keys)
    )
    nodes = node_links(connection, (eid for ids in evidence.values() for eid in ids))
    records = []
    for source_id in selected_ids:
        profile = profiles[source_id]
        latest = profile.latest
        assert latest is not None
        node_counts = _node_counts(_matched_evidence(profile.keys, evidence), nodes, _FEED_ITEM_KINDS)
        records.append(ingested_source_record_from_storage_payload({
            "source_id": source_id, "source_type": profile.source_type,
            "display_name": profile.display_name, "policy_status": profile.policy_status,
            "latest_source_run_id": latest.source_run_id, "latest_artifact_id": latest.artifact_id,
            "latest_artifact_path": latest.artifact_path,
            "latest_acquired_at": latest.acquired_at,
            "feed_observation_count": sum(_observation_fanout_count(key, evidence, nodes) for key in profile.keys),
            "canonical_feed_item_count": node_counts.get("feed.item", 0),
        }))
    return tuple(records)


def source_summary(connection: sqlite3.Connection, *, source_id: str) -> SourceSummaryRecord:
    require_accepted_publication(connection)
    profile = SourceProfile()
    for item in _iter_source_observations(connection, source_id):
        profile.add(item)
    latest = profile.latest
    if latest is None:
        return source_summary_from_storage_payload({
            "source_id": source_id, "source_type": None, "display_name": None,
            "policy_status": "unknown", "configured_url_summary": None,
            "latest_source_run_id": None, "latest_artifact_id": None, "latest_artifact_path": None,
            "latest_acquired_at": None, "feed_documents": 0, "feed_channels": 0, "feed_items": 0,
            "feed_authors": 0, "feed_categories": 0, "link_references": 0,
            "enclosure_references": 0, "parse_errors": 0,
            "known_limitations": ["source metadata unavailable"],
        })
    evidence = evidence_ids(connection, profile.keys)
    matched = {evidence_id for ids in evidence.values() for evidence_id in ids}
    nodes, edges = node_links(connection, matched), edge_links(connection, matched)
    node_counts = _node_counts(matched, nodes, _SOURCE_SUMMARY_KINDS)
    references = _reference_counts(matched, edges)
    return source_summary_from_storage_payload({
        "source_id": source_id, "source_type": profile.source_type,
        "display_name": profile.display_name, "policy_status": profile.policy_status,
        "configured_url_summary": profile.url_summary,
        "latest_source_run_id": latest.source_run_id, "latest_artifact_id": latest.artifact_id,
        "latest_artifact_path": latest.artifact_path,
        "latest_acquired_at": latest.acquired_at,
        "feed_documents": node_counts.get("feed.document", 0),
        "feed_channels": node_counts.get("feed.channel", 0), "feed_items": node_counts.get("feed.item", 0),
        "feed_authors": node_counts.get("feed.author", 0),
        "feed_categories": node_counts.get("feed.category", 0),
        "link_references": references[0], "enclosure_references": references[1],
        "parse_errors": sum(
            _observation_fanout_count(key, evidence, nodes, edges) for key in profile.parse_error_keys
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
