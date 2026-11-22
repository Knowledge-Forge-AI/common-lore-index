"""Feed item, reference and explanation reads over one accepted SQLite Local graph.

The item-level half of the SQLite source/feed owners; the source-level half,
the shared source-observation scan and the LEFT JOIN fan-out live in
``storage.sqlite_local.source_queries``, whose module docstring states the
PostgreSQL semantics both halves keep.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from heapq import nlargest, nsmallest
from itertools import batched
from typing import Any, Literal

from common_lore_index.storage.errors import StorageSchemaError
from common_lore_index.storage.source_rows import (
    SourceFeedItemRecord,
    SourceReferenceRecord,
    source_feed_item_record_from_storage_payload,
    source_reference_record_from_storage_payload,
)
from common_lore_index.storage.sql_core import positive_limit
from common_lore_index.storage.sqlite_local import raw_payload
from common_lore_index.storage.sqlite_local.queries import require_accepted_publication
from common_lore_index.storage.sqlite_local.source_queries import (
    RawKey,
    _Edge,
    SourceObservation,
    edge_links,
    evidence_ids,
    fanout,
    group_by,
    max_of,
    min_of,
    node_links,
    not_fetched,
    source_observations,
)

CONTENT_POLICY = "full feed bodies are not exposed"
_REFERENCE_KEY_BATCH = 500


def _kind_prefix(key: str) -> str:
    """``split_part(key, ':', 1)``."""
    return key.split(":", 1)[0]


def _graph_version_seeks(table: Literal["canonical_nodes", "canonical_edges"]) -> str:
    # Each MIN starts after the previous version in the existing identity index.
    return (
        "WITH RECURSIVE graph_versions(version) AS ("
        f"SELECT min(graph_key_version) FROM {table} UNION ALL "
        f"SELECT (SELECT min(next_version.graph_key_version) FROM {table} next_version "
        "WHERE next_version.graph_key_version > graph_versions.version) "
        "FROM graph_versions WHERE version IS NOT NULL) "
    )


def _item_references(
    connection: sqlite3.Connection, keys: set[str]
) -> dict[str, tuple[set[str], set[str], set[str]]]:
    """The three feed-item lateral lookups for the selected keys.

    Link targets are ``references`` edges scoped ``link``/``enclosure``;
    authors and categories are target display names joined by key alone.
    """
    found: dict[str, tuple[set[str], set[str], set[str]]] = {}
    if not keys:
        return found
    ordered_keys = sorted(keys)
    # The existing edge identity index leads with graph version. Keep every
    # stored version, and keep the target join by key alone for readback parity.
    versions = connection.execute(
        _graph_version_seeks("canonical_edges") + "SELECT version FROM graph_versions WHERE version IS NOT NULL"
    )
    for (version,) in versions:
        for page in batched(ordered_keys, _REFERENCE_KEY_BATCH):
            placeholders = ", ".join("?" for _ in page)
            for source, target, metadata, target_kind, name in connection.execute(
                "SELECT edge.source_canonical_key, edge.target_canonical_key, edge.metadata_json, "
                "target.kind, target.display_name FROM canonical_edges edge "
                "LEFT JOIN canonical_nodes target ON target.canonical_key = edge.target_canonical_key "
                "WHERE edge.graph_key_version = ? AND edge.edge_kind = 'references' "
                f"AND edge.source_canonical_key IN ({placeholders})",
                (version, *page),
            ):
                links, authors, categories = found.setdefault(source, (set(), set(), set()))
                if raw_payload.text_at(raw_payload.load(metadata), "scope") in ("link", "enclosure"):
                    links.add(target)
                if target_kind == "feed.author":
                    authors.add(name)
                elif target_kind == "feed.category":
                    categories.add(name)
    return found


def _feed_item_nodes(
    connection: sqlite3.Connection, linked: dict[tuple[str, int], list[SourceObservation]]
) -> Iterator[tuple[str, Any, list[SourceObservation]]]:
    # Sort lightweight identity keys first. Each SQL batch can then emit in
    # the same global tie order without retaining the other batches' bodies.
    for page in batched(sorted(linked), _REFERENCE_KEY_BATCH):
        placeholders = ", ".join("?" for _ in page)
        for node_id, key, metadata in connection.execute(
            "SELECT id, canonical_key, metadata_json FROM canonical_nodes "
            f"WHERE kind = 'feed.item' AND id IN ({placeholders}) ORDER BY canonical_key, id",
            tuple(node_id for _, node_id in page),
        ):
            yield key, raw_payload.load(metadata), linked[(key, node_id)]


def source_feed_items(
    connection: sqlite3.Connection, *, source_id: str, source_run_id: str | None, limit: int
) -> tuple[SourceFeedItemRecord, ...]:
    safe_limit = positive_limit(limit)
    require_accepted_publication(connection)
    group = [
        item for item in source_observations(connection)
        if item.source_id == source_id and (source_run_id is None or item.source_run_id == source_run_id)
    ]
    if not group:
        return ()
    evidence = evidence_ids(connection, (item.key for item in group))
    nodes = node_links(connection, (eid for ids in evidence.values() for eid in ids))
    linked = group_by(
        ((node.key, node.id), observation) for observation, node, _ in fanout(group, evidence, nodes)
        if node is not None and node.kind == "feed.item"
    )
    if not linked:
        return ()
    items = _feed_item_nodes(connection, linked)

    def published_order(item: tuple[str, Any, list[SourceObservation]]) -> tuple[bool, str]:
        published = raw_payload.text_at(item[1], "published_at")
        return published is not None, published or ""

    # The bounded heap keeps the SQL order among ties, including undated items.
    page = nlargest(safe_limit, items, key=published_order)
    lookups = _item_references(connection, {key for key, _, _ in page})
    records = []
    for key, metadata, observations in page:
        links, authors, categories = lookups.get(key, (set(), set(), set()))
        records.append(source_feed_item_record_from_storage_payload({
            "item_key": key,
            "title": raw_payload.text_at(metadata, "title"),
            "published_at": raw_payload.text_at(metadata, "published_at"),
            "updated_at": raw_payload.text_at(metadata, "updated_at"),
            "identity_source": raw_payload.text_at(metadata, "identity_source"),
            "identity_strength": raw_payload.text_at(metadata, "identity_strength"),
            "duplicate_identity": raw_payload.flag(raw_payload.text_at(metadata, "duplicate_identity")),
            "link_targets": sorted(links),
            "authors": sorted(authors),
            "categories": sorted(categories),
            "source_run_id": max_of(item.source_run_id for item in observations),
            "artifact_id": max_of(item.artifact_id for item in observations),
            "artifact_path": max_of(item.artifact_path for item in observations),
        }))
    return tuple(records)


def source_references(
    connection: sqlite3.Connection,
    *,
    source_id: str,
    source_run_id: str | None,
    target_kind: str | None,
    limit: int,
) -> tuple[SourceReferenceRecord, ...]:
    safe_limit = positive_limit(limit)
    require_accepted_publication(connection)
    group = [
        item for item in source_observations(connection)
        if item.source_id == source_id and (source_run_id is None or item.source_run_id == source_run_id)
    ]
    if not group:
        return ()
    evidence = evidence_ids(connection, (item.key for item in group))
    edges = edge_links(connection, (eid for ids in evidence.values() for eid in ids))

    def candidates() -> Iterator[tuple[tuple[str, str, str, RawKey, str], SourceObservation, _Edge]]:
        for observation in group:
            for evidence_id in evidence.get(observation.key, []):
                for edge in edges.get(evidence_id, []):
                    if edge.kind != "references" or _kind_prefix(edge.source) != "feed.item":
                        continue
                    if target_kind is not None and _kind_prefix(edge.target) != target_kind:
                        continue
                    order = (edge.source, edge.target, edge.identity_hash, observation.key, edge.link_kind)
                    yield order, observation, edge

    return tuple(
        source_reference_record_from_storage_payload({
            "source_item_key": edge.source,
            "relation": edge.kind,
            "target_key": edge.target,
            "target_display": raw_payload.text_at(edge.metadata, "raw_target_summary"),
            "not_fetched": not_fetched(raw_payload.text_at(edge.metadata, "not_fetched")),
            "media_type": raw_payload.text_at(edge.metadata, "mime_type"),
            "source_run_id": observation.source_run_id,
            "artifact_id": observation.artifact_id,
            "artifact_path": observation.artifact_path,
        })
        for _, observation, edge in nsmallest(safe_limit, candidates(), key=lambda row: row[0])
    )


def _json(text: str) -> Any:
    return raw_payload.jsonb_value(raw_payload.load(text))


def _is_preferred_source(
    candidate_id: str,
    acquired_at: Any,
    best_id: str,
    best_acquired_at: Any,
) -> bool:
    if acquired_at is None:
        return best_acquired_at is None and candidate_id < best_id
    if best_acquired_at is None:
        return True
    if acquired_at != best_acquired_at:
        return bool(acquired_at > best_acquired_at)
    return candidate_id < best_id


def _latest_source(observations: list[SourceObservation]) -> dict[str, Any] | None:
    """Select the latest source summary without sorting all source groups.

    Source selection summarizes the scoped ``source_observations`` rows (all
    sources when ``source_id`` is ``None``, or that exact source) independently
    of whether ``item_key`` resolves to any canonical node or evidence row.
    """
    best: SourceObservation | None = None
    for observation in observations:
        if best is None or _is_preferred_source(
            observation.source_id, observation.acquired_at, best.source_id, best.acquired_at
        ):
            best = observation
    if best is None:
        return None
    best_id = best.source_id
    group = [entry for entry in observations if entry.source_id == best_id]
    return {
        "source_id": best_id,
        "source_type": min_of(entry.source_type for entry in group),
        "policy_status": min_of(entry.policy_status for entry in group),
        "source_run_id": max_of(entry.source_run_id for entry in group),
        "artifact_id": max_of(entry.artifact_id for entry in group),
        "artifact_path": max_of(entry.artifact_path for entry in group),
        "acquired_at": best.acquired_at,
    }


def _selected_item_and_evidence(
    connection: sqlite3.Connection, item_key: str, selected: set[RawKey]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not selected:
        return None, []
    earliest_node: tuple[Any, Any] | None = None
    item_inputs: tuple[Any, Any, Any, Any, Any, Any, Any] | None = None
    evidence: list[dict[str, Any]] = []
    evidence_error: Exception | None = None
    for row in connection.execute(
        _graph_version_seeks("canonical_nodes") +
        "SELECT evidence.run_id, evidence.raw_observation_ordinal, node.graph_key_version, "
        "node.id, node.canonical_key, node.kind, node.display_name, node.confidence, "
        "node.conflict, node.metadata_json, evidence.evidence_key, evidence.raw_kind, "
        "evidence.raw_source_id, evidence.path, evidence.start_line, evidence.end_line, "
        "evidence.extractor, evidence.extractor_version, evidence.confidence, "
        "evidence.metadata_json FROM graph_versions CROSS JOIN canonical_nodes node "
        "JOIN canonical_node_evidence link ON link.canonical_node_id = node.id "
        "JOIN canonical_evidence evidence ON evidence.id = link.canonical_evidence_id "
        "WHERE node.graph_key_version = graph_versions.version AND node.canonical_key = ? "
        "ORDER BY evidence.raw_observation_ordinal, "
        "evidence.run_id, evidence.evidence_key, link.link_kind, node.graph_key_version, node.id",
        (item_key,),
    ):
        if (row[0], row[1]) not in selected:
            continue
        node_order = (row[2], row[3])
        if earliest_node is None or node_order < earliest_node:
            earliest_node = node_order
            item_inputs = (row[4], row[2], row[5], row[6], row[7], row[8], row[9])
        if evidence_error is not None:
            continue
        try:
            metadata = _json(row[19])
        except (ArithmeticError, StorageSchemaError, TypeError, ValueError) as error:
            # Find the winning item even when evidence is corrupt. The
            # explanation's item metadata has precedence over evidence errors.
            evidence_error = error
            continue
        evidence.append({
            "evidence_key": row[10],
            "raw_kind": row[11],
            "raw_source_id": row[12],
            "path": row[13],
            "start_line": row[14],
            "end_line": row[15],
            "extractor": row[16],
            "extractor_version": row[17],
            "confidence": row[18],
            "metadata": metadata,
        })
    if item_inputs is None:
        return None, evidence
    item = {
        "canonical_key": item_inputs[0],
        "graph_key_version": item_inputs[1],
        "kind": item_inputs[2],
        "display_name": item_inputs[3],
        "confidence": item_inputs[4],
        "conflict": bool(item_inputs[5]),
        "metadata": _json(item_inputs[6]),
    }
    if evidence_error is not None:
        raise evidence_error
    return item, evidence


def source_feed_item_explanation(
    connection: sqlite3.Connection, *, item_key: str, source_id: str | None
) -> dict[str, Any]:
    require_accepted_publication(connection)
    observations = [
        item for item in source_observations(connection)
        if source_id is None or item.source_id == source_id
    ]
    selected = {item.key for item in observations}
    source = _latest_source(observations)
    item, evidence = _selected_item_and_evidence(connection, item_key, selected)
    return {
        "item": item,
        "source": source,
        "evidence": evidence,
        "references": [
            {"target_key": target_key, "metadata": _json(metadata_json)}
            for target_key, metadata_json in connection.execute(
                "SELECT target_canonical_key, metadata_json FROM canonical_edges "
                "WHERE source_canonical_key = ? AND edge_kind = 'references' "
                "ORDER BY target_canonical_key, identity_metadata_hash",
                (item_key,),
            )
        ],
        "content_policy": CONTENT_POLICY,
    }


__all__ = (
    "CONTENT_POLICY",
    "source_feed_item_explanation",
    "source_feed_items",
    "source_references",
)
