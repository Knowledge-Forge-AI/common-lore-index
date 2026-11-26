"""Reference pre-Variant-F canonical edge-evidence merge SQL and test helpers."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from common_lore_index.storage.authority import AttemptNumber, OperationId
from common_lore_index.storage.canonical_staging_merge import build_canonical_merge_statements
from common_lore_index.storage.staging_copy import STAGING_COPY_TABLES, copy_stage_rows
from common_lore_index.storage.staging_merge import MergeContext
from common_lore_index.storage.staging_ownership import StageOwner
from common_lore_index.storage.staging_resource_observability import capture_staging_resources

__all__ = (
    "build_reference_edge_evidence_merge_statement",
    "create_repository_run_stage",
    "merge_context",
    "execute_merge",
    "copy_canonical_fixture",
    "copy_multi_run_canonical_fixtures",
    "copy_diagnostic_fixture",
    "insert_raw_fixture",
    "query_canonical_edge_evidence",
    "inspect_plan_nodes",
    "measure_fixture_cardinalities",
    "check_temp_tables_analyzed",
    "extract_plan_triggers",
    "insert_diagnostic_raw_fixture",
    "observe_staged_statement",
    "prepare_edge_evidence_insert",
)


def build_reference_edge_evidence_merge_statement(
    stage: str,
    repository: str,
    run: str,
) -> str:
    """Return pre-Variant-F canonical edge-evidence merge SQL for parity verification."""
    return f"""WITH proposals AS (
    SELECT DISTINCT ON (
        s.graph_key_version, s.source_canonical_key, s.edge_kind,
        s.target_canonical_key, s.identity_metadata_hash,
        s.evidence_key, s.link_kind
    )
        edge.id AS canonical_edge_id,
        evidence.id AS canonical_evidence_id,
        s.link_kind
    FROM stage_canonical_edge_evidence s
    JOIN canonical_edges edge
      ON edge.repository_id = {repository}
     AND edge.graph_key_version = s.graph_key_version
     AND edge.source_canonical_key = s.source_canonical_key
     AND edge.edge_kind = s.edge_kind
     AND edge.target_canonical_key = s.target_canonical_key
     AND edge.identity_metadata_hash = s.identity_metadata_hash
    JOIN canonical_evidence evidence
      ON evidence.repository_id = {repository}
     AND evidence.run_id = {run}
     AND evidence.graph_key_version = s.graph_key_version
     AND evidence.evidence_key = s.evidence_key
    WHERE s.stage_id = {stage}
    ORDER BY s.graph_key_version, s.source_canonical_key, s.edge_kind,
             s.target_canonical_key, s.identity_metadata_hash,
             s.evidence_key, s.link_kind, s.family_ordinal
)
INSERT INTO canonical_edge_evidence(
    canonical_edge_id, canonical_evidence_id, link_kind
)
SELECT canonical_edge_id, canonical_evidence_id, link_kind
FROM proposals
ON CONFLICT DO NOTHING;"""


def create_repository_run_stage(
    postgres: Any,
    stage_id: str,
    root_suffix: str = "",
    repository_id: str | None = None,
) -> tuple[str, str]:
    if repository_id is None:
        root_path = f"fixture-root{root_suffix}"
        repository_id = postgres.psql_scalar(
            f"INSERT INTO repositories(name, root_path) VALUES ('fixture{root_suffix}', '{root_path}'); "
            f"SELECT id FROM repositories WHERE root_path = '{root_path}';"
        )
    run_id = postgres.psql_scalar(
        f"INSERT INTO runs(repository_id, git_commit) VALUES ({repository_id}, 'commit-{stage_id}'); "
        f"SELECT id FROM runs WHERE repository_id = {repository_id} AND git_commit = 'commit-{stage_id}';"
    )
    postgres.psql_scalar(
        f"""INSERT INTO ingestion_stages(
            stage_id, repository_id, operation_id, attempt, execution_mode,
            source_generation, config_generation, extractor_generation,
            canonicalizer_generation, state, validation_status, expires_at
        ) VALUES (
            '{stage_id}', {repository_id}, 'operation-{stage_id}', 1, 'direct',
            'sg1:source', 'cg1:config', 'eg1:extractor', 'kg1:canonicalizer',
            'validated', 'passed', now() + interval '1 hour'
        );"""
    )
    return str(repository_id), str(run_id)


def merge_context(repository_id: str, run_id: str, stage_id: str) -> MergeContext:
    return MergeContext(
        stage_id=stage_id,
        owner=StageOwner(
            repository_id=int(repository_id), operation_id=OperationId(f"operation-{stage_id}"),
            attempt=AttemptNumber(1), execution_mode="direct", source_generation="sg1:source",
            config_generation="cg1:config", extractor_generation="eg1:extractor",
            canonicalizer_generation="kg1:canonicalizer",
        ),
        run_id=int(run_id),
    )


def execute_merge(connection: Any, repository_id: str, run_id: str, stage_id: str) -> None:
    statements = build_canonical_merge_statements(merge_context(repository_id, run_id, stage_id))
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def copy_canonical_fixture(connection: Any, stage_id: str) -> None:
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_nodes"], [
        {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "canonical_key": "node:source",
         "kind": "function", "display_name": "source", "metadata_json": {"fixture": "source"}, "confidence": "extracted", "conflict": False},
        {"stage_id": stage_id, "family_ordinal": 1, "graph_key_version": 1, "canonical_key": "node:target",
         "kind": "function", "display_name": "target", "metadata_json": {"fixture": "target"}, "confidence": "extracted", "conflict": False},
    ], expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_evidence"], [
        {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "evidence_key": "evidence:source",
         "raw_observation_ordinal": 0, "raw_schema_version": 1, "raw_kind": "file", "raw_source_id": "fixture:source",
         "path": "fixture/root", "start_line": 1, "end_line": 3, "extractor": "fixture-extractor",
         "extractor_version": "fixture-1", "confidence": "extracted", "metadata_json": {"fixture": True}},
    ], expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_edges"], [
        {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "source_canonical_key": "node:source",
         "edge_kind": "calls", "target_canonical_key": "node:target", "identity_metadata_json": {"fixture": "identity"},
         "identity_metadata_hash": "a" * 64, "metadata_json": {"fixture": "edge"}, "confidence": "extracted", "conflict": False},
    ], expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_node_evidence"], [
        {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "canonical_key": "node:source",
         "evidence_key": "evidence:source", "link_kind": "definition"},
    ], expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_edge_evidence"], [
        {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "source_canonical_key": "node:source",
         "edge_kind": "calls", "target_canonical_key": "node:target", "identity_metadata_hash": "a" * 64,
         "evidence_key": "evidence:source", "link_kind": "call-site"},
    ], expected_stage_id=stage_id)


def copy_multi_run_canonical_fixtures(connection: Any, stage_id: str, run_index: int = 1) -> None:
    nodes = [
        {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "canonical_key": "node:n1", "kind": "func", "display_name": "n1", "metadata_json": {}, "confidence": "extracted", "conflict": False},
        {"stage_id": stage_id, "family_ordinal": 1, "graph_key_version": 1, "canonical_key": "node:n2", "kind": "func", "display_name": "n2", "metadata_json": {}, "confidence": "extracted", "conflict": False},
        {"stage_id": stage_id, "family_ordinal": 2, "graph_key_version": 1, "canonical_key": "node:n3", "kind": "func", "display_name": "n3", "metadata_json": {}, "confidence": "extracted", "conflict": False},
    ]
    edges = [
        {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "source_canonical_key": "node:n1", "edge_kind": "calls", "target_canonical_key": "node:n2", "identity_metadata_json": {}, "identity_metadata_hash": "a" * 64, "metadata_json": {}, "confidence": "extracted", "conflict": False},
        {"stage_id": stage_id, "family_ordinal": 1, "graph_key_version": 1, "source_canonical_key": "node:n2", "edge_kind": "calls", "target_canonical_key": "node:n3", "identity_metadata_json": {}, "identity_metadata_hash": "b" * 64, "metadata_json": {}, "confidence": "extracted", "conflict": False},
    ]
    if run_index == 1:
        evidence = [
            {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "evidence_key": "ev:1", "raw_observation_ordinal": 0, "raw_schema_version": 1, "raw_kind": "file", "raw_source_id": "fixture:source", "path": "fixture/root", "start_line": 1, "end_line": 3, "extractor": "ext", "extractor_version": "1", "confidence": "extracted", "metadata_json": {}},
            {"stage_id": stage_id, "family_ordinal": 1, "graph_key_version": 1, "evidence_key": "ev:2", "raw_observation_ordinal": 1, "raw_schema_version": 1, "raw_kind": "file", "raw_source_id": "fixture:source", "path": "fixture/root", "start_line": 4, "end_line": 6, "extractor": "ext", "extractor_version": "1", "confidence": "extracted", "metadata_json": {}},
        ]
        node_ev = [
            {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "canonical_key": "node:n1", "evidence_key": "ev:1", "link_kind": "definition"},
            {"stage_id": stage_id, "family_ordinal": 1, "graph_key_version": 1, "canonical_key": "node:n2", "evidence_key": "ev:2", "link_kind": "definition"},
        ]
        edge_ev = [
            {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "source_canonical_key": "node:n1", "edge_kind": "calls", "target_canonical_key": "node:n2", "identity_metadata_hash": "a" * 64, "evidence_key": "ev:1", "link_kind": "call-site"},
            {"stage_id": stage_id, "family_ordinal": 1, "graph_key_version": 1, "source_canonical_key": "node:n1", "edge_kind": "calls", "target_canonical_key": "node:n2", "identity_metadata_hash": "a" * 64, "evidence_key": "ev:1", "link_kind": "call-site"},
            {"stage_id": stage_id, "family_ordinal": 2, "graph_key_version": 1, "source_canonical_key": "node:n1", "edge_kind": "calls", "target_canonical_key": "node:n2", "identity_metadata_hash": "a" * 64, "evidence_key": "ev:1", "link_kind": "type-ref"},
            {"stage_id": stage_id, "family_ordinal": 3, "graph_key_version": 1, "source_canonical_key": "node:n2", "edge_kind": "calls", "target_canonical_key": "node:n3", "identity_metadata_hash": "b" * 64, "evidence_key": "ev:2", "link_kind": "call-site"},
        ]
    else:
        evidence = [
            {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "evidence_key": "ev:3", "raw_observation_ordinal": 2, "raw_schema_version": 1, "raw_kind": "file", "raw_source_id": "fixture:source", "path": "fixture/root", "start_line": 7, "end_line": 9, "extractor": "ext", "extractor_version": "1", "confidence": "extracted", "metadata_json": {}},
            {"stage_id": stage_id, "family_ordinal": 1, "graph_key_version": 1, "evidence_key": "ev:4", "raw_observation_ordinal": 3, "raw_schema_version": 1, "raw_kind": "file", "raw_source_id": "fixture:source", "path": "fixture/root", "start_line": 10, "end_line": 12, "extractor": "ext", "extractor_version": "1", "confidence": "extracted", "metadata_json": {}},
        ]
        node_ev = [
            {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "canonical_key": "node:n1", "evidence_key": "ev:3", "link_kind": "definition"},
            {"stage_id": stage_id, "family_ordinal": 1, "graph_key_version": 1, "canonical_key": "node:n3", "evidence_key": "ev:4", "link_kind": "definition"},
        ]
        edge_ev = [
            {"stage_id": stage_id, "family_ordinal": 0, "graph_key_version": 1, "source_canonical_key": "node:n1", "edge_kind": "calls", "target_canonical_key": "node:n2", "identity_metadata_hash": "a" * 64, "evidence_key": "ev:3", "link_kind": "call-site"},
            {"stage_id": stage_id, "family_ordinal": 1, "graph_key_version": 1, "source_canonical_key": "node:n2", "edge_kind": "calls", "target_canonical_key": "node:n3", "identity_metadata_hash": "b" * 64, "evidence_key": "ev:4", "link_kind": "type-ref"},
        ]
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_nodes"], nodes, expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_edges"], edges, expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_evidence"], evidence, expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_node_evidence"], node_ev, expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_edge_evidence"], edge_ev, expected_stage_id=stage_id)


def copy_diagnostic_fixture(connection: Any, stage_id: str, count: int = 50) -> None:
    nodes = [
        {"stage_id": stage_id, "family_ordinal": i, "graph_key_version": 1,
         "canonical_key": f"node:r{i}", "kind": "func", "display_name": f"r{i}",
         "metadata_json": {}, "confidence": "extracted", "conflict": False}
        for i in range(count + 1)
    ]
    edges = [
        {"stage_id": stage_id, "family_ordinal": i, "graph_key_version": 1,
         "source_canonical_key": f"node:r{i}", "edge_kind": "calls",
         "target_canonical_key": f"node:r{i+1}", "identity_metadata_json": {},
         "identity_metadata_hash": f"{i:064x}", "metadata_json": {},
         "confidence": "extracted", "conflict": False}
        for i in range(count)
    ]
    evidence = [
        {"stage_id": stage_id, "family_ordinal": i, "graph_key_version": 1,
         "evidence_key": f"ev:r{i}", "raw_observation_ordinal": i, "raw_schema_version": 1,
         "raw_kind": "file", "raw_source_id": f"fixture:{i}", "path": f"fixture/r{i}",
         "start_line": 1, "end_line": 5, "extractor": "ext", "extractor_version": "1",
         "confidence": "extracted", "metadata_json": {}}
        for i in range(count)
    ]
    node_ev = [
        {"stage_id": stage_id, "family_ordinal": i, "graph_key_version": 1,
         "canonical_key": f"node:r{i}", "evidence_key": f"ev:r{i}", "link_kind": "def"}
        for i in range(count)
    ]
    edge_ev: list[dict[str, Any]] = []
    for i in range(count):
        edge_ev.append({
            "stage_id": stage_id, "family_ordinal": len(edge_ev), "graph_key_version": 1,
            "source_canonical_key": f"node:r{i}", "edge_kind": "calls",
            "target_canonical_key": f"node:r{i+1}", "identity_metadata_hash": f"{i:064x}",
            "evidence_key": f"ev:r{i}", "link_kind": "call-site"
        })
        edge_ev.append({
            "stage_id": stage_id, "family_ordinal": len(edge_ev), "graph_key_version": 1,
            "source_canonical_key": f"node:r{i}", "edge_kind": "calls",
            "target_canonical_key": f"node:r{i+1}", "identity_metadata_hash": f"{i:064x}",
            "evidence_key": f"ev:r{i}", "link_kind": "call-site"
        })
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_nodes"], nodes, expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_edges"], edges, expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_evidence"], evidence, expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_node_evidence"], node_ev, expected_stage_id=stage_id)
    copy_stage_rows(connection, STAGING_COPY_TABLES["canonical_edge_evidence"], edge_ev, expected_stage_id=stage_id)


def insert_raw_fixture(
    postgres: Any,
    repository_id: str,
    run_id: str,
    ordinals: tuple[int, ...] = (0,),
) -> None:
    for ord_idx in ordinals:
        postgres.psql_scalar(
            f"""INSERT INTO raw_observations(
                repository_id, run_id, ordinal, schema_version, kind, source_id, path, payload_json, payload_hash
            ) VALUES (
                {repository_id}, {run_id}, {ord_idx}, 1, 'file', 'fixture:source', 'fixture/root',
                '{{"kind":"file","path":"fixture/root"}}'::jsonb, '{format(ord_idx, "064x")}'
            ) ON CONFLICT DO NOTHING;"""
        )


def insert_diagnostic_raw_fixture(connection: Any, repo: str, run: str, count: int) -> None:
    connection.execute("""
INSERT INTO raw_observations(
    repository_id, run_id, ordinal, schema_version, kind, source_id, path, payload_json, payload_hash
)
SELECT %s, %s, i, 1, 'file', 'fixture:' || i, 'fixture/r' || i,
       jsonb_build_object('kind', 'file', 'path', 'fixture/r' || i), lpad(to_hex(i), 64, '0')
FROM generate_series(0, %s) AS series(i)
""", (int(repo), int(run), count - 1))


def prepare_edge_evidence_insert(connection: Any, repo: str, run: str, stage_id: str) -> tuple[str, str]:
    statements = build_canonical_merge_statements(merge_context(repo, run, stage_id))
    for statement in statements[:-1]:
        connection.execute(statement)
    setup, insert = statements[-1].split("WITH staged_links AS", 1)
    subject, teardown = insert.split("DROP TABLE IF EXISTS", 1)
    connection.execute(setup)
    return "WITH staged_links AS" + subject, "DROP TABLE IF EXISTS" + teardown


def observe_staged_statement(
    connection: Any, observer: Any, statement: str, params: tuple[str, ...] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    before = capture_staging_resources(observer)
    row = connection.execute(f"EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON) {statement}", params).fetchone()
    after = capture_staging_resources(observer)
    assert row is not None
    plan = row[0][0]
    root = plan["Plan"]
    # The root contains inclusive totals. Adding descendants would double-count work.
    keys = (
        "Shared Hit Blocks", "Shared Read Blocks", "Shared Dirtied Blocks", "Shared Written Blocks",
        "Local Hit Blocks", "Local Read Blocks", "Local Dirtied Blocks", "Local Written Blocks",
        "Temp Read Blocks", "Temp Written Blocks", "WAL Records", "WAL FPI", "WAL Bytes",
    )

    def delta(start: int | None, end: int | None) -> int | None:
        return end - start if start is not None and end is not None and end >= start else None

    same_database = (before.temporary_counter_identity is not None
                     and before.temporary_counter_identity == after.temporary_counter_identity)
    return plan, {
        "statement_sha256": sha256(statement.encode()).hexdigest(),
        "statement_root": {key: root[key] for key in keys},
        "shared_interval": {
            "wal_position_bytes_delta": delta(before.wal_position, after.wal_position),
            "database_temporary_bytes_delta": delta(before.temporary_bytes, after.temporary_bytes) if same_database else None,
        },
    }


def query_canonical_edge_evidence(connection: Any, repository_id: str, run_id: str | None = None) -> list[tuple[Any, ...]]:
    query = """
SELECT e.source_canonical_key, e.edge_kind, e.target_canonical_key,
       ev.evidence_key, ee.link_kind
FROM canonical_edge_evidence ee
JOIN canonical_edges e ON e.id = ee.canonical_edge_id
JOIN canonical_evidence ev ON ev.id = ee.canonical_evidence_id
WHERE e.repository_id = %s
"""
    params: list[Any] = [int(repository_id)]
    if run_id is not None:
        query += " AND ev.run_id = %s"
        params.append(int(run_id))
    query += " ORDER BY e.source_canonical_key, e.target_canonical_key, ev.evidence_key, ee.link_kind"
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params))
        return cursor.fetchall()


def inspect_plan_nodes(plan: dict[str, Any]) -> tuple[list[str], list[str]]:
    node_types: list[str] = [plan.get("Node Type", "")]
    relations: list[str] = [plan.get("Relation Name", "")] if "Relation Name" in plan else []
    for sub in plan.get("Plans", []):
        sub_types, sub_rels = inspect_plan_nodes(sub)
        node_types.extend(sub_types)
        relations.extend(sub_rels)
    return node_types, relations


def measure_fixture_cardinalities(connection: Any, stage_id: str) -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM stage_canonical_nodes WHERE stage_id = %s", (stage_id,))
        nodes = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM stage_canonical_edges WHERE stage_id = %s", (stage_id,))
        edges = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM stage_canonical_evidence WHERE stage_id = %s", (stage_id,))
        evidence = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM stage_canonical_node_evidence WHERE stage_id = %s", (stage_id,))
        node_evidence = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM stage_canonical_edge_evidence WHERE stage_id = %s", (stage_id,))
        edge_evidence = cursor.fetchone()[0]
    return {
        "stage_canonical_nodes": int(nodes),
        "stage_canonical_edges": int(edges),
        "stage_canonical_evidence": int(evidence),
        "stage_canonical_node_evidence": int(node_evidence),
        "stage_canonical_edge_evidence": int(edge_evidence),
    }


def check_temp_tables_analyzed(connection: Any) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename, count(attname) FROM pg_stats "
            "WHERE schemaname = (SELECT nspname FROM pg_namespace WHERE oid = pg_my_temp_schema()) "
            "AND tablename IN ('temp_canonical_edge_map', 'temp_canonical_evidence_map') "
            "GROUP BY tablename"
        )
        rows = cursor.fetchall()
    return len(rows) == 2 and all(r[1] > 0 for r in rows)


def extract_plan_triggers(plan_entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"trigger_name": t.get("Trigger Name"), "calls": t.get("Calls", 0), "time_ms": t.get("Time", 0.0)}
        for t in plan_entry.get("Triggers", [])
    ]
