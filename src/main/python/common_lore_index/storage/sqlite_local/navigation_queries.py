"""Bounded direct-evidence scan; no JSON SQL extension or raw payload reads."""

from __future__ import annotations

import sqlite3
import json
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any

from common_lore_index.storage.navigation_search import (
    ADMISSION_BYTE_LIMIT, ADMISSION_CHECK_LIMIT, CODE_KINDS, DOC_KINDS,
    EVIDENCE_SCAN_LIMIT, NODE_SCAN_LIMIT, NavigationSearchQuery, unavailable,
)
from common_lore_index.storage.graph_readback_sql import escape_readback_like_pattern
from common_lore_index.storage.sqlite_local.node_location_queries import (
    ContextVectorReader,
    _EVIDENCE_ROLE_SQL,
    _context_vector_reader,
    _location_from_vector,
)
from common_lore_index.storage.sqlite_local.schema import SqliteFunctionReadConnection, accepted_identity


def candidate_predicate(query: NavigationSearchQuery) -> tuple[str, dict[str, Any]]:
    filters = ["subject.graph_key_version = 1", "(subject.canonical_key LIKE :pattern ESCAPE '\\' "
               "OR subject.kind LIKE :pattern ESCAPE '\\' OR subject.display_name LIKE :pattern ESCAPE '\\')"]
    params: dict[str, Any] = {"pattern": "%" + escape_readback_like_pattern(query.query) + "%"}
    if query.kind is not None:
        filters.append("subject.kind = :kind")
        params["kind"] = query.kind
    kinds = CODE_KINDS if query.scope == "code" else DOC_KINDS if query.scope == "docs" else ()
    if kinds:
        filters.append("subject.kind IN (" + ",".join(f":family{i}" for i in range(len(kinds))) + ")")
        params.update({f"family{i}": kind for i, kind in enumerate(kinds)})
    return " AND ".join(filters), params


def evidence_sql(
    predicate: str, *, role_sql: str = _EVIDENCE_ROLE_SQL, refuse_overflow: bool = False,
) -> str:
    # Run selection deliberately precedes role selection, just like the locator.
    # Materialize the bounded relational identity/sort window before joining wide
    # evidence metadata, run vectors, or raw observation payload hashes; post-limit
    # runs parity relies on schema FK canonical_evidence.run_id -> runs(id).
    window_state, source, join, overflow = "", "bounded", "CROSS JOIN", ""
    if refuse_overflow:
        # Keep a single narrow sentinel on empty or overflow. No wide rows are joined.
        window_state = ", window_state AS (SELECT count(*) = :evidence_fetch AS overflow FROM bounded)"
        source = "window_state LEFT JOIN bounded ON NOT window_state.overflow"
        join = "LEFT JOIN"
        overflow = ", window_state.overflow"
    return f"""WITH selected AS (
        SELECT subject.id AS node_id,
               coalesce(max(CASE WHEN e.run_id = :accepted THEN e.run_id END), max(e.run_id)) AS run_id
        FROM canonical_nodes subject
        JOIN canonical_node_evidence link ON link.canonical_node_id = subject.id
        JOIN canonical_evidence e ON e.id = link.canonical_evidence_id AND e.graph_key_version = 1
        WHERE {predicate} GROUP BY subject.id
    ),
    bounded AS MATERIALIZED (
        SELECT subject.id AS node_id,
               subject.canonical_key AS canonical_key,
               e.id AS evidence_id,
               e.run_id AS run_id,
               e.raw_observation_ordinal AS raw_observation_ordinal,
               e.evidence_key AS evidence_key,
               link.link_kind AS link_kind,
               {role_sql} AS evidence_role
        FROM selected
        CROSS JOIN canonical_nodes subject ON subject.id = selected.node_id
        CROSS JOIN canonical_node_evidence link ON link.canonical_node_id = subject.id
        CROSS JOIN canonical_evidence e ON e.id = link.canonical_evidence_id
                                       AND e.run_id = selected.run_id AND e.graph_key_version = 1
        WHERE ({role_sql}) = 'direct'
        ORDER BY subject.canonical_key, e.raw_observation_ordinal, e.evidence_key, link.link_kind
        LIMIT :evidence_fetch
    ){window_state}
    SELECT bounded.evidence_key AS evidence_key,
           bounded.link_kind AS link_kind,
           bounded.run_id AS run_id,
           bounded.raw_observation_ordinal AS raw_observation_ordinal,
           raw.payload_hash, e.raw_kind, e.raw_source_id, e.path, e.start_line, e.end_line,
           e.extractor, e.extractor_version, e.confidence, e.metadata_json,
           r.publication_bundle_id, r.snapshot_manifest_id, r.extraction_receipt_id,
           r.graph_candidate_id, r.snapshot_vector_json,
           bounded.evidence_role, bounded.node_id AS id{overflow}
    FROM {source}
    {join} canonical_evidence e ON e.id = bounded.evidence_id
    {join} runs r ON r.id = bounded.run_id
    LEFT JOIN raw_observations raw
      ON raw.run_id = bounded.run_id AND raw.ordinal = bounded.raw_observation_ordinal
    ORDER BY bounded.canonical_key, bounded.raw_observation_ordinal,
             bounded.evidence_key, bounded.link_kind"""


def _direct_location(row: Any, accepted_run_id: int, *,
                     snapshot_vector: list[Any] | None = None,
                     read_context_vector: ContextVectorReader | None = None) -> dict[str, Any]:
    if read_context_vector is not None:
        # Validate the persisted row even when the caller supplies a separately
        # validated vector for resolving this location's binding.
        persisted = read_context_vector(row[18])
        vector = persisted if snapshot_vector is None else snapshot_vector
    else:
        vector = json.loads(row[18]) if snapshot_vector is None else snapshot_vector
    return _direct_location_from_vector(row, accepted_run_id, vector)


def _direct_location_from_vector(row: Any, accepted_run_id: int, vector: list[Any]) -> dict[str, Any]:
    location = _location_from_vector(row, accepted_run_id, vector)
    source = location["source"]
    vector = [item for item in vector if item[0] == source.get("binding_id")]
    location["source_identity_status"] = (
        "verified_vector" if len(vector) == 1 and vector[0][1] == source.get("binding_revision")
        and vector[0][2] == source.get("snapshot_id") else "unbound_or_conflicting_vector"
    )
    return location


@dataclass
class _Admission:
    admits_location: Callable[[dict[str, Any]], bool]
    read_context_vector: ContextVectorReader | None = None
    checks: int = 0
    decoded_bytes: int = 0
    input_bytes: int = 0
    vector_bytes: int = 0
    vectors: dict[int, list[Any]] = field(default_factory=dict)
    exhausted: bool = False
    error: Exception | None = None

    def matches(self, run_id: int, path: str | None, metadata: str, vector: str) -> bool:
        metadata_size, vector_size = len(metadata.encode("utf-8")), len(vector.encode("utf-8"))
        self.input_bytes += metadata_size + vector_size
        size = metadata_size + (vector_size if run_id not in self.vectors else 0)
        if self.checks >= ADMISSION_CHECK_LIMIT or self.decoded_bytes + size > ADMISSION_BYTE_LIMIT:
            self.exhausted = True
            raise ValueError("admission_work_limit")
        self.checks += 1
        self.decoded_bytes += size
        try:
            if run_id not in self.vectors:
                self.vectors[run_id] = (self.read_context_vector or json.loads)(vector)
                self.vector_bytes += vector_size
            # Identity fields only; no payload/source or corpus-sized collection.
            row = (None, "observed", 0, None, None, None, None, path, None, None,
                   None, None, None, metadata, None, None, None, None, vector, "direct")
            return self.admits_location(_direct_location_from_vector(row, 0, self.vectors[run_id]))
        except Exception as error:
            # sqlite3 erases callback causes. Re-raise outside its SQL boundary.
            self.error = error
            raise

    def diagnostics(self) -> dict[str, Any]:
        return {"basis": "selected_run_direct_public_predicate", "checks": self.checks,
                "decoded_bytes": self.decoded_bytes, "input_bytes": self.input_bytes,
                "vector_decodes": len(self.vectors), "vector_bytes": self.vector_bytes,
                "check_limit": ADMISSION_CHECK_LIMIT,
                "byte_limit": ADMISSION_BYTE_LIMIT, "budget_exhausted": self.exhausted}


def _scope_predicate(predicate: str, *, role_sql: str = _EVIDENCE_ROLE_SQL) -> str:
    # Correlated relational EXISTS, not a backend query per subject. Select the
    # run from ANY link before selecting direct roles and source facts.
    return predicate + f""" AND EXISTS (
        SELECT 1 FROM canonical_node_evidence link
        JOIN canonical_evidence e ON e.id = link.canonical_evidence_id AND e.graph_key_version = 1
        JOIN runs r ON r.id = e.run_id
        WHERE link.canonical_node_id = subject.id AND e.run_id = (
            SELECT coalesce(max(CASE WHEN chosen.run_id = :accepted THEN chosen.run_id END), max(chosen.run_id))
            FROM canonical_node_evidence chosen_link
            JOIN canonical_evidence chosen ON chosen.id = chosen_link.canonical_evidence_id
                                         AND chosen.graph_key_version = 1
            WHERE chosen_link.canonical_node_id = subject.id
        ) AND ({role_sql}) = 'direct'
        AND repomap_navigation_admits(e.run_id, e.path, e.metadata_json, r.snapshot_vector_json)
    )"""


def navigation_candidates(connection: SqliteFunctionReadConnection, query: NavigationSearchQuery, *,
                          admits_location: Callable[[dict[str, Any]], bool] | None = None,
                          role_sql: str = _EVIDENCE_ROLE_SQL,
                          context_identity: bool = False) -> dict[str, Any]:
    query.validate()
    accepted = accepted_identity(connection)
    if accepted is None:
        return unavailable("publication_absent")
    predicate, params = candidate_predicate(query)
    read_context_vector = _context_vector_reader() if context_identity else None
    admission = None
    if query.source_binding_id is not None:
        if admits_location is None:
            return unavailable("source_scope_predicate_unavailable")
        admission = _Admission(admits_location, read_context_vector=read_context_vector)
        predicate = _scope_predicate(predicate, role_sql=role_sql)
    rows = []
    try:
        if admission is not None:
            connection.create_function("repomap_navigation_admits", 4, admission.matches)
        with closing(connection.execute(
            "SELECT subject.id, subject.canonical_key, subject.kind, subject.display_name, "
            "subject.first_seen_run_id, subject.last_seen_run_id FROM canonical_nodes subject WHERE "
            + predicate + " ORDER BY subject.canonical_key LIMIT :node_fetch",
            {**params, "accepted": accepted.run_id, "node_fetch": NODE_SCAN_LIMIT + 1},
        )) as candidates:
            rows = candidates.fetchall()
    except sqlite3.OperationalError:
        if admission is not None and admission.error is not None:
            raise admission.error from None
        if admission is None or not admission.exhausted:
            raise
    finally:
        if admission is not None:
            connection.create_function("repomap_navigation_admits", 4, None)
    result: dict[str, Any] = {
        "status": "ok", "reason": None, "nodes": [],
        "accepted_publication": {"generation": accepted.generation, "run_id": accepted.run_id,
                                 "publication_bundle_id": accepted.publication_bundle_id},
        "scan": {"complete": False, "nodes": min(len(rows), NODE_SCAN_LIMIT), "evidence": 0,
                 "node_limit": NODE_SCAN_LIMIT, "evidence_limit": EVIDENCE_SCAN_LIMIT},
    }
    if admission is not None:
        result["scan"]["admission"] = admission.diagnostics()
        if admission.exhausted:
            return {**result, "status": "incomplete", "reason": "admission_work_limit"}
    if len(rows) > NODE_SCAN_LIMIT:
        return {**result, "status": "incomplete", "reason": "node_scan_limit"}
    # A fixed number of relational reads, irrespective of candidate/member count.
    # The trusted integer ID set is at most NODE_SCAN_LIMIT, independent of
    # SQLite's bound-variable ceiling. Admit subjects, never prune alternatives.
    evidence_predicate = "subject.id IN (" + ",".join(str(int(row[0])) for row in rows) + ")" if rows else "0"
    nodes = {row[0]: {"canonical_key": row[1], "kind": row[2], "display_name": row[3],
                      "first_seen_run_id": row[4], "last_seen_run_id": row[5], "locations": []}
             for row in rows}
    vectors = dict(admission.vectors) if admission is not None else {}
    sql = evidence_sql(evidence_predicate, role_sql=role_sql, refuse_overflow=True)
    with closing(connection.execute(sql, {
        **params, "accepted": accepted.run_id, "evidence_fetch": EVIDENCE_SCAN_LIMIT + 1,
    })) as evidence:
        for row in evidence:
            if row[21]:
                result["scan"]["evidence"] = EVIDENCE_SCAN_LIMIT
                return {**result, "status": "incomplete", "reason": "evidence_scan_limit"}
            if row[20] is None:
                break
            if read_context_vector is None and row[2] not in vectors:
                vectors[row[2]] = json.loads(row[18])
            location = _direct_location(
                row[:20], accepted.run_id, snapshot_vector=vectors.get(row[2]),
                read_context_vector=read_context_vector,
            )
            nodes[row[20]]["locations"].append(location)
            result["scan"]["evidence"] += 1
    result["nodes"] = list(nodes.values())
    result["scan"]["complete"] = True
    return result
