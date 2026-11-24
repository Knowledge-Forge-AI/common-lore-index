"""Read persisted node-evidence links inside the caller's bound read transaction."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from functools import lru_cache
from itertools import islice
from pathlib import Path
from typing import Any

from common_lore_index.retrieval.context_identity import (
    ContextIdentityError,
    parse_snapshot_vector,
)
from common_lore_index.storage.errors import StorageSchemaError
from common_lore_index.storage.node_locations import NodeLocationsQuery, unavailable
from common_lore_index.storage.sqlite_local.schema import (
    LocalGraphBinding,
    SqliteReadConnection,
    accepted_identity,
)
from common_lore_index.storage.sqlite_local.connection import read_transaction


# Definition producers put observed on the subject, inferred_from_edge on
# the containing file. Other observed producers remain unknown (ADR 0079).
_EVIDENCE_ROLE_SQL = """CASE
    WHEN link.link_kind = 'inferred_from_edge' THEN 'reference'
    WHEN link.link_kind = 'observed' AND (
        (e.raw_kind IN ('python.module', 'python.class', 'python.function', 'python.method')
         AND subject.kind = e.raw_kind)
        OR (e.raw_kind = 'markdown.document' AND subject.kind = 'doc.page')
        OR (e.raw_kind = 'markdown.heading' AND subject.kind = 'doc.section')
        OR (e.raw_kind = 'markdown.adr_metadata' AND subject.kind = 'doc.adr')
        OR (e.raw_kind = 'markdown.skill_metadata' AND subject.kind = 'doc.skill')
    ) THEN 'direct'
    ELSE 'unknown' END"""


def read_node_locations(path: Path, binding: LocalGraphBinding, query: NodeLocationsQuery) -> dict[str, Any]:
    query.validate()
    with read_transaction(path, binding) as connection:
        return node_locations(connection, query)


def node_locations(
    connection: SqliteReadConnection,
    query: NodeLocationsQuery,
    *,
    role_sql: str = _EVIDENCE_ROLE_SQL,
    include_node_summary: bool = False,
    context_identity: bool = False,
) -> dict[str, Any]:
    query.validate()
    accepted = accepted_identity(connection)
    if accepted is None:
        return unavailable("publication_absent")
    node_sql = (
        "SELECT id, metadata_json, first_seen_run_id, last_seen_run_id, kind, display_name "
        "FROM canonical_nodes WHERE graph_key_version = 1 AND canonical_key = ?"
        if include_node_summary
        else "SELECT id, metadata_json, first_seen_run_id, last_seen_run_id "
        "FROM canonical_nodes WHERE graph_key_version = 1 AND canonical_key = ?"
    )
    with closing(connection.execute(node_sql, (query.canonical_key,))) as nodes:
        node = nodes.fetchone()
    if node is None:
        return unavailable("node_not_found")
    with closing(connection.execute(
        "SELECT COALESCE(MAX(CASE WHEN e.run_id = ? THEN e.run_id END), MAX(e.run_id)) "
        "FROM canonical_node_evidence link "
        "JOIN canonical_evidence e ON e.id = link.canonical_evidence_id "
        "WHERE link.canonical_node_id = ? AND e.graph_key_version = 1",
        (accepted.run_id, node[0]),
    )) as runs:
        selected = runs.fetchone()
    location_run_id = selected[0] if selected else None
    read_context_vector = (_context_vector_reader() if context_identity
                           else _cached_vector_reader(_parse_advisory_snapshot_vector))
    locations, has_more = _evidence_window(
        connection, node[0], location_run_id, query, historical=False, role_sql=role_sql,
        accepted_run_id=accepted.run_id, read_context_vector=read_context_vector,
    )
    history, historical_has_more = _evidence_window(
        connection, node[0], location_run_id, query, historical=True, role_sql=role_sql,
        accepted_run_id=accepted.run_id, read_context_vector=read_context_vector,
    )
    result: dict[str, Any] = {
        "status": "ok", "reason": None,
        "locations": locations, "historical_locations": history,
        "historical_has_more": historical_has_more,
        "location_run_id": location_run_id,
        "location_scope": _relation(location_run_id, accepted.run_id) if location_run_id is not None else "unavailable",
        "metadata_location": _metadata_location(node), "has_more": has_more,
        "node_first_seen_run_id": node[2], "node_last_seen_run_id": node[3],
        "accepted_publication": {"generation": accepted.generation, "run_id": accepted.run_id,
                                 "publication_bundle_id": accepted.publication_bundle_id},
    }
    if include_node_summary:
        result["node_summary"] = {
            "id": int(node[0]), "canonical_key": query.canonical_key,
            "kind": str(node[4]), "display_name": str(node[5]),
        }
    return result


def _evidence_window(
    connection: SqliteReadConnection,
    node_id: int,
    run_id: int | None,
    query: NodeLocationsQuery,
    *,
    historical: bool,
    role_sql: str,
    accepted_run_id: int,
    read_context_vector: ContextVectorReader | None,
) -> tuple[list[dict[str, Any]], bool]:
    if run_id is None:
        return [], False
    relation = "<>" if historical else "="
    filtered = query.evidence_role != "all"
    role_column = ", bounded.evidence_role" if filtered else ""
    projected_role = f", {role_sql} AS evidence_role" if filtered else ""
    role_predicate = f"AND ({role_sql}) = ? " if filtered else ""
    parameters = (node_id, run_id, query.evidence_role, query.limit + 1) if filtered else (
        node_id, run_id, query.limit + 1,
    )
    with closing(connection.execute(
        f"""WITH bounded AS MATERIALIZED (
            SELECT e.id AS evidence_id, e.run_id, e.raw_observation_ordinal,
                   e.evidence_key, link.link_kind{projected_role}
            FROM canonical_node_evidence link
            JOIN canonical_nodes subject ON subject.id = link.canonical_node_id
            JOIN canonical_evidence e ON e.id = link.canonical_evidence_id
            JOIN runs r ON r.id = e.run_id
            WHERE link.canonical_node_id = ? AND e.graph_key_version = 1 AND e.run_id {relation} ?
            {role_predicate}
            ORDER BY e.run_id DESC, e.raw_observation_ordinal, e.evidence_key, link.link_kind
            LIMIT ?
        )
        SELECT bounded.evidence_key, bounded.link_kind, bounded.run_id, bounded.raw_observation_ordinal,
               raw.payload_hash, e.raw_kind, e.raw_source_id, e.path, e.start_line, e.end_line,
               e.extractor, e.extractor_version, e.confidence, e.metadata_json,
               r.publication_bundle_id, r.snapshot_manifest_id, r.extraction_receipt_id,
               r.graph_candidate_id, r.snapshot_vector_json{role_column}
        FROM bounded
        CROSS JOIN canonical_evidence e ON e.id = bounded.evidence_id
        CROSS JOIN runs r ON r.id = bounded.run_id
        LEFT JOIN raw_observations raw
          ON raw.run_id = bounded.run_id AND raw.ordinal = bounded.raw_observation_ordinal
        ORDER BY bounded.run_id DESC, bounded.raw_observation_ordinal, bounded.evidence_key, bounded.link_kind""",
        parameters,
    )) as rows:
        page = [
            _location(row, accepted_run_id, read_context_vector=read_context_vector)
            for row in islice(rows, query.limit)
        ]
        return page, rows.fetchone() is not None


def _metadata_location(node: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any] | None:
    metadata = json.loads(node[1])
    doc_path = metadata.get("doc_path") if isinstance(metadata, dict) else None
    if doc_path is None:
        return None
    return {
        "basis": "node_metadata.doc_path", "path": doc_path,
        "path_namespace": "source_relative", "binding_status": "unavailable",
        "start_line": None, "end_line": None, "node_last_seen_run_id": node[3],
    }


def _relation(run_id: int, accepted_run_id: int) -> str:
    return "accepted_run" if run_id == accepted_run_id else "historical_run"


def _parse_context_snapshot_vector(raw_vector: Any) -> list[list[Any]]:
    """Validate a persisted evidence vector before context normalization."""
    if isinstance(raw_vector, str):
        try:
            raw_vector = json.loads(raw_vector)
        except (TypeError, ValueError, RecursionError) as exc:
            raise StorageSchemaError("malformed context snapshot vector") from exc
    try:
        parsed = parse_snapshot_vector(raw_vector)
    except (ContextIdentityError, TypeError, ValueError) as exc:
        raise StorageSchemaError("malformed context snapshot vector") from exc
    return [list(item) for item in parsed]


def _parse_advisory_snapshot_vector(raw_vector: str) -> list[list[Any]]:
    """Read a legacy vector without inferring identity from invalid persisted fields."""
    try:
        parsed = parse_snapshot_vector(json.loads(raw_vector))
    except ContextIdentityError:
        return []
    return [list(item) for item in parsed]


ContextVectorReader = Callable[[Any], list[list[Any]]]


def _cached_vector_reader(decode: ContextVectorReader) -> ContextVectorReader:
    """Retain only the most recent persisted vector within the current read."""
    read_cached = lru_cache(maxsize=1)(decode)

    def read(raw: Any) -> list[list[Any]]:
        return read_cached(raw) if isinstance(raw, str) else decode(raw)

    return read


def _context_vector_reader() -> ContextVectorReader:
    return _cached_vector_reader(_parse_context_snapshot_vector)


def _location(row: sqlite3.Row | tuple[Any, ...], accepted_run_id: int, *,
              read_context_vector: ContextVectorReader | None) -> dict[str, Any]:
    vector = (_parse_advisory_snapshot_vector(row[18])
              if read_context_vector is None else read_context_vector(row[18]))
    return _location_from_vector(row, accepted_run_id, vector)


def _location_from_vector(
    row: sqlite3.Row | tuple[Any, ...], accepted_run_id: int, vector: list[Any],
    *, include_vector_status: bool = False,
) -> dict[str, Any]:
    metadata = json.loads(row[13])
    source = {key: metadata[key] for key in (
        "binding_id", "binding_alias", "binding_revision", "snapshot_id", "source_relative_path"
    ) if isinstance(metadata, dict) and type(metadata.get(key)) in {str, int}}
    # Readers normalize and check binding uniqueness before sharing a vector.
    binding_id, snapshot_id = source.get("binding_id"), source.get("snapshot_id")
    revision = next((rev for bid, rev, sid in vector if bid == binding_id and sid == snapshot_id), None)
    if revision is not None:
        source.setdefault("binding_revision", revision)
    result = {
        "basis": "canonical_node_evidence", "evidence_key": row[0], "link_kind": row[1],
        "run_id": row[2], "publication_relation": _relation(row[2], accepted_run_id),
        "raw_observation": {"status": "available" if row[4] is not None else "unavailable",
                            "run_id": row[2], "ordinal": row[3], "payload_hash": row[4],
                            "kind": row[5], "source_id": row[6]},
        "path": row[7], "start_line": row[8], "end_line": row[9],
        "path_namespace": "graph_source_relative",
        "extractor": row[10], "extractor_version": row[11], "confidence": row[12],
        "source": source,
        "publication": {"publication_bundle_id": row[14], "snapshot_manifest_id": row[15],
                        "extraction_receipt_id": row[16], "graph_candidate_id": row[17]},
    }
    if len(row) > 19:
        result["evidence_role"] = row[19]
    if include_vector_status:
        result["source_identity_status"] = (
            "verified_vector" if revision is not None and source["binding_revision"] == revision
            else "unbound_or_conflicting_vector"
        )
    return result
