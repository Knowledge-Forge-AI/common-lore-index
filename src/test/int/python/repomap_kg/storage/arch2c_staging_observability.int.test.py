from __future__ import annotations

from dataclasses import replace
import json

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from common_lore_index.observations import RawObservation
from common_lore_index.ops.resource_view._projections import project_staging_resources
from common_lore_index.storage import apply_migrations, default_rdbms_root, staged_ingestion
from common_lore_index.storage.authority import AttemptNumber, OperationId
from common_lore_index.storage.readback_driver import (
    _psycopg_connection_params_from_psql_args,
)
from common_lore_index.storage.staged_ingestion import (
    IngestionAuthority,
    run_staged_full_refresh,
)
from common_lore_index.storage.staging_family_contracts import STAGING_FAMILY_DESCRIPTORS
from common_lore_index.storage.staging_observability import (
    StagingMeasurementAvailability,
    StagingMeasurementCategory,
    StagingMeasurementEvent,
    StagingMeasurements,
)
from common_lore_index.storage.staging_resource_observability import (
    StagingResourceSnapshot,
    capture_staging_resources,
    emit_staging_resource_measurements,
)
from repomap_test_support.postgres_harness import (
    require_postgres_binaries,
    temporary_postgres,
)


def _authority() -> IngestionAuthority:
    return IngestionAuthority(
        operation_id=OperationId("private-arch2c-operation-marker"),
        attempt=AttemptNumber(1),
        execution_mode="direct",
        source_generation="sg1:arch2c-source",
        config_generation="cg1:arch2c-config",
        extractor_generation="eg1:arch2c-extractor",
        canonicalizer_generation="kg1:arch2c-canonicalizer",
    )


def _observations() -> tuple[RawObservation, ...]:
    return (
        RawObservation(
            kind="file",
            source_id="src/private-fixture.py",
            path="src/private-fixture.py",
            confidence="manual",
            extractor="arch2c-fixture",
            extractor_version="1.0.0",
            metadata={"language": "python", "role": "source"},
        ),
    )


def _migrate(postgres) -> None:
    apply_migrations(
        default_rdbms_root(),
        postgres.psql_args,
        psql_command=postgres.psql_command,
    )


def _published_state(postgres, root_path: str) -> tuple[object, ...]:
    params = _psycopg_connection_params_from_psql_args(postgres.psql_args)
    with psycopg.connect(
        host=params["host"],
        port=int(params["port"]),
        user=params["user"],
        dbname=params["dbname"],
    ) as connection:
        row = connection.execute(
                """
SELECT (SELECT count(*) FROM files f JOIN repositories r
        ON r.id = f.repository_id WHERE r.root_path = %s),
       (SELECT count(*) FROM raw_observations o JOIN repositories r
        ON r.id = o.repository_id WHERE r.root_path = %s),
       (SELECT count(*) FROM canonical_nodes n JOIN repositories r
        ON r.id = n.repository_id WHERE r.root_path = %s),
       run.status, run.publication_job_id, run.publication_attempt,
       stage.state, stage.merge_status, stage.publication_reconciliation_state
FROM runs run
JOIN repositories repository ON repository.id = run.repository_id
JOIN ingestion_stages stage ON stage.repository_id = repository.id
WHERE repository.root_path = %s
""",
                (root_path,) * 4,
            ).fetchone()
        assert row is not None
        return tuple(row)


class _RecordedCursor:
    def __init__(self, cursor, operations: list[str]) -> None:
        self._cursor = cursor
        self._operations = operations

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self._cursor.__exit__(*args)

    def execute(self, *args, **kwargs):
        self._operations.append("execute")
        return self._cursor.execute(*args, **kwargs)

    def copy(self, *args, **kwargs):
        self._operations.append("copy")
        return self._cursor.copy(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)


class _RecordedConnection:
    def __init__(self, connection, operations: list[str]) -> None:
        self._connection = connection
        self._operations = operations

    def execute(self, query, *args, **kwargs):
        normalized = " ".join(query.split()) if isinstance(query, str) else ""
        count_prefixes = ("SELECT (SELECT count(*) FROM stage_", "SELECT count(*) FROM stage_")
        self._operations.append(normalized if normalized.startswith(count_prefixes) else "execute")
        return self._connection.execute(query, *args, **kwargs)

    def cursor(self, *args, **kwargs):
        return _RecordedCursor(self._connection.cursor(*args, **kwargs), self._operations)

    def commit(self) -> None:
        self._operations.append("commit")
        self._connection.commit()

    def rollback(self) -> None:
        self._operations.append("rollback")
        self._connection.rollback()

    def close(self) -> None:
        self._operations.append("close")
        self._connection.close()

    def __getattr__(self, name: str):
        return getattr(self._connection, name)


def _recording_connect(operations: list[str]):
    def connect(**params):
        return _RecordedConnection(psycopg.connect(**params), operations)

    return connect


def test_arch2c_measurements_are_public_safe_bounded_and_result_neutral() -> None:
    require_postgres_binaries()
    root_path = "private-arch2c-root-marker"
    with temporary_postgres() as baseline_postgres:
        _migrate(baseline_postgres)
        baseline_operations: list[str] = []
        baseline_summary = run_staged_full_refresh(
            baseline_postgres.psql_args,
            _observations(),
            repository_name="private-arch2c-repository-marker",
            root_path=root_path,
            authority=_authority(),
            connect=_recording_connect(baseline_operations),
        )
        baseline_state = _published_state(baseline_postgres, root_path)

    with temporary_postgres() as measured_postgres:
        _migrate(measured_postgres)
        events: list[StagingMeasurementEvent] = []
        measured_operations: list[str] = []
        measured_summary = run_staged_full_refresh(
            measured_postgres.psql_args,
            _observations(),
            repository_name="private-arch2c-repository-marker",
            root_path=root_path,
            authority=_authority(),
            connect=_recording_connect(measured_operations),
            staging_measurements=StagingMeasurements(events.append),
        )
        measured_state = _published_state(measured_postgres, root_path)

    assert measured_summary == baseline_summary
    assert measured_state == baseline_state
    family_counts = [
        f"SELECT count(*) FROM {descriptor.stage_table} WHERE stage_id = %s"
        for descriptor in STAGING_FAMILY_DESCRIPTORS.values()
    ]
    batched_counts = "SELECT " + ", ".join(f"({sql})" for sql in family_counts)
    assert [op for op in baseline_operations if op.startswith("SELECT ")] == [batched_counts]
    assert [op for op in measured_operations if op.startswith("SELECT ")] == family_counts
    assert len(measured_operations) - len(family_counts) == len(baseline_operations) - 1 + 4
    assert len(events) <= 96

    family_categories = {
        StagingMeasurementCategory.FAMILY_PREPARATION,
        StagingMeasurementCategory.FAMILY_ROW_COUNT,
        StagingMeasurementCategory.NORMALIZED_BYTES,
        StagingMeasurementCategory.SPOOL_BYTES,
        StagingMeasurementCategory.SPOOL_ALLOCATED_BYTES,
        StagingMeasurementCategory.CHECKSUM,
        StagingMeasurementCategory.COPY,
    }
    for family in STAGING_FAMILY_DESCRIPTORS:
        assert family_categories <= {
            event.category for event in events if event.family == family
        }

    global_categories = {
        StagingMeasurementCategory.STATISTICS,
        StagingMeasurementCategory.COMPLETENESS_VALIDATION,
        StagingMeasurementCategory.SEMANTIC_GUARD,
        StagingMeasurementCategory.MERGE,
        StagingMeasurementCategory.RECEIPT,
        StagingMeasurementCategory.WAL_UPPER_BOUND,
        StagingMeasurementCategory.TEMPORARY_BYTE_UPPER_BOUND,
        StagingMeasurementCategory.CLIENT_MEMORY,
        StagingMeasurementCategory.POSTGRESQL_MEMORY,
    }
    assert global_categories <= {event.category for event in events}
    for event in events:
        if event.category in {
            StagingMeasurementCategory.WAL_UPPER_BOUND,
            StagingMeasurementCategory.TEMPORARY_BYTE_UPPER_BOUND,
            StagingMeasurementCategory.CLIENT_MEMORY,
        }:
            assert event.upper_bound is True
        if event.category is StagingMeasurementCategory.POSTGRESQL_MEMORY:
            assert event.availability in {
                StagingMeasurementAvailability.MEASURED,
                StagingMeasurementAvailability.UNAVAILABLE,
            }

    serialized = json.dumps([event.to_payload() for event in events], sort_keys=True)
    for forbidden in (
        "private-arch2c-root-marker",
        "private-arch2c-operation-marker",
        "private-arch2c-repository-marker",
        "src/private-fixture.py",
        "backend_pid",
        "database",
        "credential",
        "raw_sql",
        "query",
        "statement",
    ):
        assert forbidden not in serialized


def test_staging_temporary_bytes_are_unavailable_across_owned_database_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_postgres_binaries()
    root_path = "staged-counter-reset-fixture"
    authority = _authority()
    samples: list[StagingResourceSnapshot] = []
    events: list[StagingMeasurementEvent] = []

    def observe(connection: psycopg.Connection) -> StagingResourceSnapshot:
        sample = capture_staging_resources(connection)
        samples.append(sample)
        return sample

    monkeypatch.setattr(staged_ingestion, "capture_staging_resources", observe)
    with temporary_postgres() as postgres:
        _migrate(postgres)
        params = _psycopg_connection_params_from_psql_args(postgres.psql_args)

        def reset() -> None:
            with psycopg.connect(make_conninfo(**params)) as resetter:
                resetter.execute("SELECT pg_stat_reset()")

        measurements = StagingMeasurements(events.append)
        summary = run_staged_full_refresh(
            postgres.psql_args,
            _observations(),
            repository_name=root_path,
            root_path=root_path,
            authority=replace(authority, before_publication=reset),
            staging_measurements=measurements,
        )
        assert summary.publication_receipt == authority.receipt()
        stored = _published_state(postgres, root_path)
        assert stored[3:7] == ("complete", authority.receipt().attempt.job_id, 1, "published")

    before, after = samples
    assert before.temporary_counter_identity is not None
    assert after.temporary_counter_identity is not None
    assert before.temporary_counter_identity[0] == after.temporary_counter_identity[0]
    assert after.temporary_counter_identity[1] is not None
    assert before.temporary_counter_identity[1] != after.temporary_counter_identity[1]
    assert before.temporary_bytes is not None and after.temporary_bytes is not None
    temporary, = (event for event in events
                  if event.category is StagingMeasurementCategory.TEMPORARY_BYTE_UPPER_BOUND)
    assert temporary.value is None
    assert temporary.availability is StagingMeasurementAvailability.UNAVAILABLE
    projected = {metric.name: metric for metric in project_staging_resources(
        events, measurements_failed=measurements.failed,
    )}
    metric = projected["temporary_byte_upper_bound"]
    assert (metric.availability, metric.reason, metric.value) == ("unavailable", "not_observed", None)
    assert projected["wal_upper_bound"].availability == "measured"
    repeated = project_staging_resources(
        (event for _ in range(10) for event in events), measurements_failed=False,
    )
    assert all(metric.reason == "ambiguous_repeat" and metric.value is None for metric in repeated)


def test_staging_samples_and_statement_counters_on_disposable_workload() -> None:
    require_postgres_binaries()
    with temporary_postgres() as postgres:
        params = _psycopg_connection_params_from_psql_args(postgres.psql_args)
        dsn = make_conninfo(**params)
        with psycopg.connect(dsn, autocommit=True) as observer:
            version = observer.info.server_version
            with psycopg.connect(dsn, autocommit=True) as worker:
                worker.execute("CREATE TABLE w7_observation (id integer, body text)")
                worker.execute(
                    "INSERT INTO w7_observation SELECT id, md5(id::text) "
                    "FROM generate_series(1, 16384) AS source(id)"
                )
                before_write = capture_staging_resources(observer)
                with worker.transaction(force_rollback=True):
                    insert = _statement_counters(
                        worker,
                        "INSERT INTO w7_observation SELECT id + 16384, body FROM w7_observation",
                    )
                after_write = capture_staging_resources(observer)
                count = worker.execute("SELECT count(*) FROM w7_observation").fetchone()
                assert count == (16384,)
                with worker.transaction():
                    worker.execute("SET LOCAL work_mem = '64kB'")
                    worker.execute("SET LOCAL max_parallel_workers_per_gather = 0")
                    sort = _statement_counters(worker, "SELECT body FROM w7_observation ORDER BY body")
                after_sort = capture_staging_resources(observer)
            after_worker_exit = capture_staging_resources(observer)

    assert insert["WAL Records"] > 0 and insert["WAL Bytes"] > 0
    assert sort["Actual Rows"] == 16384
    assert sort["Temp Read Blocks"] > 0 and sort["Temp Written Blocks"] > 0
    assert sort["WAL Bytes"] == 0
    snapshots = (before_write, after_write, after_sort, after_worker_exit)
    assert all(sample.wal_position is not None for sample in snapshots)
    assert all(sample.temporary_bytes is not None for sample in snapshots)
    assert before_write.temporary_counter_identity is not None
    assert all(sample.temporary_counter_identity == before_write.temporary_counter_identity
               for sample in snapshots)
    write = _sampled_byte_deltas(before_write, after_write)
    assert write["wal_upper_bound"] is not None and write["wal_upper_bound"] > 0
    evidence = {
        "server_version": version,
        "statement_insert": insert,
        "statement_sort": sort,
        "sampled_insert": write,
        "sampled_sort": _sampled_byte_deltas(after_write, after_sort),
        "sampled_sort_through_worker_exit": _sampled_byte_deltas(after_write, after_worker_exit),
    }
    print("W7-STATEMENT-OBSERVATION " + json.dumps(evidence, sort_keys=True))


def _statement_counters(connection: psycopg.Connection, statement: str) -> dict[str, int]:
    result = connection.execute(
        "EXPLAIN (ANALYZE, BUFFERS, WAL, TIMING OFF, SUMMARY OFF, FORMAT JSON) " + statement
    ).fetchone()
    assert result is not None
    plan = result[0][0]["Plan"]
    return {
        name: int(plan[name])
        for name in (
            "Actual Rows", "Shared Hit Blocks", "Shared Read Blocks",
            "Temp Read Blocks", "Temp Written Blocks", "WAL Records", "WAL Bytes",
        )
    }


def _sampled_byte_deltas(
    before: StagingResourceSnapshot, after: StagingResourceSnapshot,
) -> dict[str, int | None]:
    events: list[StagingMeasurementEvent] = []
    emit_staging_resource_measurements(StagingMeasurements(events.append), before, after)
    return {
        event.category.value: event.value
        for event in events
        if event.category in (
            StagingMeasurementCategory.WAL_UPPER_BOUND,
            StagingMeasurementCategory.TEMPORARY_BYTE_UPPER_BOUND,
        )
    }
