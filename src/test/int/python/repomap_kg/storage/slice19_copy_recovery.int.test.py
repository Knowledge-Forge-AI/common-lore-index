"""COPY refusals roll back unpublished rows while preserving committed evidence."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from common_lore_index.observations import RawObservation
from common_lore_index.storage import apply_migrations, default_rdbms_root, staged_ingestion
from common_lore_index.storage.errors import StorageSchemaError
from common_lore_index.storage.publication_readback import read_run_publication
from common_lore_index.storage.readback_driver import _psycopg_connection_params_from_psql_args
from common_lore_index.storage.row_spool import RowSpool
from common_lore_index.storage.staged_ingestion import IngestionAuthority, new_direct_authority, run_staged_full_refresh
from common_lore_index.storage.staged_rows import PreparedStageRows
from common_lore_index.storage.staged_validation import validate_stage
from common_lore_index.storage.staging_copy import CopyContractError, STAGING_COPY_TABLES, copy_stage_rows
from common_lore_index.storage.staging_family_contracts import STAGING_FAMILY_DESCRIPTORS
from repomap_test_support.canonical_merge_reference import (
    copy_diagnostic_fixture,
    create_repository_run_stage,
    observe_staged_statement,
)
from repomap_test_support.postgres_harness import require_postgres_binaries, temporary_postgres


@pytest.mark.parametrize("invalid_metadata", [{1: "invalid-key"}, {"nested": [{"bad": object()}]}],
                         ids=["non-text-key", "nested-unserializable-value"])
def test_late_copy_refusal_preserves_committed_rows_and_allows_recovery(invalid_metadata):
    require_postgres_binaries()
    with temporary_postgres() as postgres:
        apply_migrations(default_rdbms_root(), postgres.psql_args, psql_command=postgres.psql_command)
        with psycopg.connect(host=postgres.host, port=postgres.port, user=postgres.user,
                             dbname=postgres.database, password=postgres.password) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO repositories(name, root_path) VALUES ('slice19', 'fixture/slice19') RETURNING id")
                repository = cursor.fetchone()
                assert repository is not None
                cursor.execute("""INSERT INTO ingestion_stages
                    (stage_id, repository_id, operation_id, attempt, execution_mode,
                     source_generation, config_generation, extractor_generation,
                     canonicalizer_generation, state, validation_status, expires_at)
                    VALUES ('slice19-stage', %s, 'slice19-copy', 1, 'direct',
                            'sg1:fixture', 'cg1:fixture', 'eg1:fixture', 'kg1:fixture',
                            'loading', 'not_started', now() + interval '1 hour')""", (repository[0],))
            base = dict(stage_id="slice19-stage", family_ordinal=0, path="base.py", language="python",
                        role="source", confidence="extracted", content_hash="a" * 64,
                        executable=False, generated=False, metadata_json={"values": [None, True, 1.5]})
            table = STAGING_COPY_TABLES["files"]
            copied = copy_stage_rows(connection, table, [base], expected_stage_id="slice19-stage")
            assert copied.row_count == 1
            connection.commit()

            def replacement():
                yield {**base, "family_ordinal": 1, "path": "first.py"}
                yield {**base, "family_ordinal": 2, "path": "last.py", "metadata_json": invalid_metadata}

            with pytest.raises(CopyContractError, match="JSON value is invalid"):
                copy_stage_rows(connection, table, replacement(), expected_stage_id="slice19-stage")
            connection.rollback()
            with connection.cursor() as cursor:
                cursor.execute("SELECT path, metadata_json FROM stage_files WHERE stage_id = 'slice19-stage'")
                assert cursor.fetchall() == [("base.py", base["metadata_json"])]
            recovered = copy_stage_rows(connection, table,
                                        [{**base, "family_ordinal": 1, "path": "recovered.py"}],
                                        expected_stage_id="slice19-stage")
            assert recovered.row_count == 1
            connection.commit()
            with connection.cursor() as cursor:
                cursor.execute("SELECT path FROM stage_files WHERE stage_id = 'slice19-stage' ORDER BY path")
                assert cursor.fetchall() == [("base.py",), ("recovered.py",)]


@pytest.mark.parametrize("excess", [False, True], ids=["short-spool", "overlong-spool"])
def test_prepared_copy_refusal_rolls_back_before_statistics_and_preserves_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, excess: bool,
) -> None:
    require_postgres_binaries()
    prior_path = "accepted.py"
    next_path = "candidate.py"
    paths = (prior_path, next_path)
    current = tuple(RawObservation(
        kind="file", source_id=path, path=path, confidence="manual", extractor="copy-fixture",
        extractor_version="1.0.0", metadata={"language": "python", "role": "source"},
    ) for path in paths)

    def authority() -> IngestionAuthority:
        return new_direct_authority(
            source_generation="sg1:copy", config_generation="cg1:copy",
            extractor_generation="eg1:copy", canonicalizer_generation="kg1:copy",
        )

    with temporary_postgres() as postgres:
        apply_migrations(default_rdbms_root(), postgres.psql_args, psql_command=postgres.psql_command)
        repo, root = "copy-custody", "fixtures/copy-custody"
        accepted = run_staged_full_refresh(postgres.psql_args, current[:1], authority=authority(),
                                          stage_id="prior-copy-stage", repository_name=repo, root_path=root)
        original = staged_ingestion._resolve_prepared_rows
        statistics = staged_ingestion._refresh_canonical_node_evidence_statistics
        files: list[RowSpool] = []
        statistics_calls = 0

        def skew_counts(*args: Any, **kwargs: Any) -> PreparedStageRows:
            prepared = original(*args, **kwargs)
            reference = tuple(prepared.family_rows["files"])
            assert prepared.row_counts["files"] == len(reference) == 1
            rows = (*reference, {**reference[0], "family_ordinal": 1, "path": "surplus.py"}) if excess else ()
            spool = RowSpool.from_rows(rows, dir=tmp_path)
            files.append(spool)
            return replace(prepared, family_rows=dict(prepared.family_rows, files=spool))

        def observe_statistics(connection: Any) -> None:
            nonlocal statistics_calls
            statistics_calls += 1
            statistics(connection)

        with monkeypatch.context() as patch:
            patch.setattr(staged_ingestion, "_resolve_prepared_rows", skew_counts)
            patch.setattr(staged_ingestion, "_refresh_canonical_node_evidence_statistics", observe_statistics)
            with pytest.raises(StorageSchemaError, match="staged family completeness failed"):
                run_staged_full_refresh(postgres.psql_args, current[1:], authority=authority(),
                                       stage_id="mismatch-stage", repository_name=repo, root_path=root)
        assert len(files) == 1 and not files[0].path.exists()
        assert statistics_calls == 0
        params = _psycopg_connection_params_from_psql_args(postgres.psql_args)
        with psycopg.connect(make_conninfo(**params)) as connection:
            assert connection.execute(
                "SELECT state, cleanup_eligibility FROM ingestion_stages WHERE stage_id = 'mismatch-stage'"
            ).fetchone() == ("failed", "eligible")
            assert connection.execute(
                "SELECT count(*) FROM stage_files WHERE stage_id = 'mismatch-stage'"
            ).fetchone() == (0,)
            assert connection.execute("SELECT path FROM files").fetchall() == [(prior_path,)]
        receipt = accepted.publication_receipt
        assert receipt is not None
        persisted = read_run_publication(postgres.psql_args, job_id=receipt.attempt.job_id,
                                         attempt=receipt.attempt.attempt, psql_command=postgres.psql_command)
        assert persisted is not None and persisted.run_id == accepted.run_id and persisted.receipt == receipt
        recovered = run_staged_full_refresh(postgres.psql_args, current[1:], authority=authority(),
                                           stage_id="recovered-stage", repository_name=repo, root_path=root)
        assert recovered.run_id != accepted.run_id and recovered.files == 1


def test_completeness_explain_matches_ordinary_validation(tmp_path: Path) -> None:
    require_postgres_binaries()
    count, stage = 50, "stage-validation-explain"
    expected_counts: dict[str, int] = {family: 0 for family in STAGING_FAMILY_DESCRIPTORS}
    expected_counts.update({
        "canonical_nodes": count + 1, "canonical_edges": count, "canonical_evidence": count,
        "canonical_edge_evidence": 2 * count, "canonical_node_evidence": count,
    })
    with temporary_postgres() as postgres:
        apply_migrations(default_rdbms_root(), postgres.psql_args, psql_command=postgres.psql_command)
        create_repository_run_stage(postgres, stage)
        conninfo = make_conninfo(**_psycopg_connection_params_from_psql_args(postgres.psql_args))
        with psycopg.connect(conninfo) as connection, psycopg.connect(conninfo, autocommit=True) as observer:
            copy_diagnostic_fixture(connection, stage, count=count)
            connection.commit()
            observed: list[tuple[str, tuple[str, ...], dict[str, Any]]] = []

            class ExplainedValidation:
                def execute(self, statement: str, arguments: tuple[str, ...]) -> psycopg.Cursor[tuple[Any, ...]]:
                    _, measurement = observe_staged_statement(connection, observer, statement, arguments)
                    observed.append((statement, arguments, measurement))
                    return connection.execute(statement, arguments)

            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                validate_stage(ExplainedValidation(), stage, expected_counts)
                validate_stage(connection, stage, expected_counts)

            families = {}
            for (family, descriptor), (statement, arguments, measurement) in zip(
                STAGING_FAMILY_DESCRIPTORS.items(), observed, strict=True,
            ):
                assert statement == f"SELECT count(*) FROM {descriptor.stage_table} WHERE stage_id = %s"
                assert arguments == (stage,)
                assert measurement["statement_root"]["WAL Bytes"] == 0
                assert measurement["statement_root"]["Temp Written Blocks"] == 0
                families[family] = {"expected_rows": expected_counts[family], **measurement}
            payload = {"server_version": connection.info.server_version, "read_only": True, "stage_id": stage,
                       "ordinary_validation_passed": True, "families": families}
            print(f"W7_STAGED_SQL_VALIDATION {json.dumps(payload, sort_keys=True)}")
            (tmp_path / "w7_validation_diagnostics.json").write_text(json.dumps(payload, indent=2))
