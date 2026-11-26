"""Small public-safe portable publication custody boundaries.

These cases use distinct synthetic bundle identities and disposable stores. They
cover accepted SQLite generation/receipt parity, PostgreSQL authority refusal,
pre-publication interruption, and a retained partial-receipt reconciliation
hold. They do not qualify the complete integration population.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from common_lore_index.storage import StorageSchemaError, apply_migrations, default_rdbms_root
from common_lore_index.storage.publication import RunPublicationReceipt
from common_lore_index.storage.publication_fencing import MergeContext, PublicationHandoff
from common_lore_index.storage.publication_readback import (
    read_latest_receipt_bearing_publication,
    read_run_publication,
)
from common_lore_index.storage.sqlite_local.connection import (
    initialize_graph_database,
    read_transaction,
)
from common_lore_index.storage.sqlite_local.publisher import publish_generation
from common_lore_index.storage.sqlite_local.schema import accepted_identity
from common_lore_index.storage.staged_ingestion import (
    _psycopg_connection_params_from_psql_args,
    run_staged_portable_refresh,
    stage_id_for_authority,
)
from common_lore_index.storage.staged_publication import reconcile_commit_unknown
from common_lore_index_test_support.postgres_harness import require_postgres_binaries, temporary_postgres
from common_lore_index_test_support.portable_publication_fixtures import (
    _authority,
    _binding,
    _seven_family_bundle,
)
from common_lore_index_test_support.sqlite_local_fixtures import local_binding, publication_for


def _row(
    connection: psycopg.Connection[tuple[object, ...]],
    query: str,
    params: tuple[object, ...] = (),
) -> tuple[object, ...]:
    row = connection.execute(query, params).fetchone()
    assert row is not None
    return row


def test_sqlite_accepted_generation_and_receipt_readback_match(tmp_path: Path) -> None:
    """The accepted generation names the same synthetic portable receipt as its run."""
    binding = local_binding()
    database = tmp_path / "sqlite" / "portable-fixture.sqlite3"
    database.parent.mkdir()
    initialize_graph_database(database, binding, applied_at="2026-11-15T00:00:00Z")
    bundle = _seven_family_bundle(
        job_id="job-supplement-parity",
        file_path="supplement/parity.py",
        module_name="supplement.parity",
        function_name="publish",
    )
    publication = publication_for(bundle)

    result = publish_generation(
        database,
        publication,
        bundle.families,
        expected_generation=0,
    )

    with read_transaction(database, binding) as connection:
        accepted = accepted_identity(connection)
        assert accepted is not None
        run_row = connection.execute(
            "SELECT id, previous_run_id, status, publication_bundle_id, "
            "publication_job_id, publication_attempt FROM runs WHERE id = ?",
            (accepted.run_id,),
        ).fetchone()

    assert result.generation == accepted.generation == 1
    assert result.run_id == accepted.run_id
    assert result.previous_run_id is None and accepted.previous_run_id is None
    assert (
        accepted.publication_bundle_id,
        accepted.publication_job_id,
        accepted.publication_attempt,
    ) == (bundle.bundle_id, bundle.job_id, bundle.attempt)
    assert run_row == (
        result.run_id,
        None,
        "complete",
        bundle.bundle_id,
        bundle.job_id,
        bundle.attempt,
    )
    assert publication.receipt.portable is not None
    assert publication.receipt.portable.publication_bundle_id == accepted.publication_bundle_id


def test_portable_same_epoch_authority_identity_refusal_preserves_prior_receipt() -> None:
    """A same-epoch coordinator identity cannot replace a prior accepted publication."""
    require_postgres_binaries()
    accepted_bundle = _seven_family_bundle(
        job_id="job-supplement-authority-accepted",
        file_path="supplement/authority-accepted.py",
        module_name="supplement.authority_accepted",
        function_name="accept",
    )
    accepted_authority = replace(
        _authority(accepted_bundle), coordinator_instance_id="coord-supplement-accepted"
    )
    refused_bundle = _seven_family_bundle(
        job_id="job-supplement-authority-imposter",
        file_path="supplement/authority-imposter.py",
        module_name="supplement.authority_imposter",
        function_name="attempt",
    )
    refused_authority = replace(
        _authority(refused_bundle), coordinator_instance_id="coord-supplement-imposter"
    )

    with temporary_postgres() as postgres:
        apply_migrations(default_rdbms_root(), postgres.psql_args, psql_command=postgres.psql_command)
        accepted = run_staged_portable_refresh(
            postgres.psql_args,
            accepted_bundle,
            repository_name="supplement-custody",
            root_path="graph:supplement-custody",
            authority=accepted_authority,
            portable_binding=_binding(accepted_bundle, accepted_authority),
        )
        with pytest.raises(StorageSchemaError, match="staged PostgreSQL operation failed") as failure:
            run_staged_portable_refresh(
                postgres.psql_args,
                refused_bundle,
                repository_name="supplement-custody",
                root_path="graph:supplement-custody",
                authority=refused_authority,
                portable_binding=_binding(refused_bundle, refused_authority),
            )
        assert isinstance(failure.value.__cause__, psycopg.Error)
        assert "ARCH1C stale graph publication claim" in str(failure.value.__cause__)

        params = _psycopg_connection_params_from_psql_args(postgres.psql_args)
        with psycopg.connect(make_conninfo(**params)) as connection:
            assert _row(
                connection,
                "SELECT path FROM files WHERE last_seen_run_id = %s",
                (accepted.run_id,),
            ) == ("supplement/authority-accepted.py",)
            assert _row(
                connection,
                "SELECT count(*) FROM files WHERE path = %s",
                ("supplement/authority-imposter.py",),
            ) == (0,)
            assert connection.execute(
                "SELECT state FROM ingestion_stages WHERE stage_id = %s",
                (stage_id_for_authority(refused_authority),),
            ).fetchone() is None

        latest = read_latest_receipt_bearing_publication(
            postgres.psql_args, psql_command=postgres.psql_command
        )
        assert latest is not None and latest.run_id == accepted.run_id
        assert latest.receipt.attempt.job_id == accepted_authority.job_id
        assert read_run_publication(
            postgres.psql_args,
            job_id=str(refused_authority.job_id),
            attempt=int(refused_authority.attempt),
            psql_command=postgres.psql_command,
        ) is None


def test_portable_prepublication_interruption_marks_candidate_failed_without_replacing_receipt() -> None:
    """The documented pre-publication hook rolls back the candidate, not the accepted run."""
    require_postgres_binaries()
    accepted_bundle = _seven_family_bundle(
        job_id="job-supplement-interrupt-accepted",
        file_path="supplement/interruption-accepted.py",
        module_name="supplement.interruption_accepted",
        function_name="accept",
    )
    accepted_authority = replace(
        _authority(accepted_bundle),
        coordinator_instance_id="coord-supplement-interrupt",
    )
    interrupted_bundle = _seven_family_bundle(
        job_id="job-supplement-interrupt-candidate",
        file_path="supplement/interruption-candidate.py",
        module_name="supplement.interruption_candidate",
        function_name="attempt",
    )

    def interrupt_before_finalize() -> None:
        raise RuntimeError("supplement interruption before portable finalize")

    interrupted_authority = replace(
        _authority(interrupted_bundle),
        coordinator_instance_id="coord-supplement-interrupt",
        singleton_fencing_epoch=8,
        graph_lease_fencing_epoch=10,
        before_publication=interrupt_before_finalize,
    )

    with temporary_postgres() as postgres:
        apply_migrations(default_rdbms_root(), postgres.psql_args, psql_command=postgres.psql_command)
        accepted = run_staged_portable_refresh(
            postgres.psql_args,
            accepted_bundle,
            repository_name="supplement-interruption",
            root_path="graph:supplement-interruption",
            authority=accepted_authority,
            portable_binding=_binding(accepted_bundle, accepted_authority),
        )
        with pytest.raises(RuntimeError, match="supplement interruption before portable finalize"):
            run_staged_portable_refresh(
                postgres.psql_args,
                interrupted_bundle,
                repository_name="supplement-interruption",
                root_path="graph:supplement-interruption",
                authority=interrupted_authority,
                portable_binding=_binding(interrupted_bundle, interrupted_authority),
            )

        params = _psycopg_connection_params_from_psql_args(postgres.psql_args)
        with psycopg.connect(make_conninfo(**params)) as connection:
            assert _row(
                connection,
                "SELECT path FROM files WHERE last_seen_run_id = %s",
                (accepted.run_id,),
            ) == ("supplement/interruption-accepted.py",)
            assert _row(
                connection,
                "SELECT count(*) FROM files WHERE path = %s",
                ("supplement/interruption-candidate.py",),
            ) == (0,)
            failed_run = _row(
                connection,
                "SELECT status, publication_bundle_id, graph_candidate_id "
                "FROM runs ORDER BY id DESC LIMIT 1",
            )
            assert failed_run == ("failed", None, None)
            assert _row(
                connection,
                "SELECT state, merge_status, publication_reconciliation_state, cleanup_eligibility "
                "FROM ingestion_stages WHERE stage_id = %s",
                (stage_id_for_authority(interrupted_authority),),
            ) == ("failed", "rolled_back", "reconciled", "eligible")

        latest = read_latest_receipt_bearing_publication(
            postgres.psql_args, psql_command=postgres.psql_command
        )
        assert latest is not None and latest.run_id == accepted.run_id
        assert read_run_publication(
            postgres.psql_args,
            job_id=str(interrupted_authority.job_id),
            attempt=int(interrupted_authority.attempt),
            psql_command=postgres.psql_command,
        ) is None


def test_partial_portable_receipt_keeps_reconciliation_blocked_and_prior_latest_readable() -> None:
    """A retained partial portable receipt is held instead of being inferred as committed."""
    require_postgres_binaries()
    accepted_bundle = _seven_family_bundle(
        job_id="job-supplement-retention-accepted",
        file_path="supplement/retention-accepted.py",
        module_name="supplement.retention_accepted",
        function_name="accept",
    )
    accepted_authority = replace(
        _authority(accepted_bundle), coordinator_instance_id="coord-supplement-retention"
    )
    retained_bundle = _seven_family_bundle(
        job_id="job-supplement-retention-partial",
        file_path="supplement/retention-partial.py",
        module_name="supplement.retention_partial",
        function_name="hold",
    )
    retained_authority = replace(
        _authority(retained_bundle),
        coordinator_instance_id="coord-supplement-retention",
        singleton_fencing_epoch=8,
        graph_lease_fencing_epoch=10,
    )

    with temporary_postgres() as postgres:
        apply_migrations(default_rdbms_root(), postgres.psql_args, psql_command=postgres.psql_command)
        accepted = run_staged_portable_refresh(
            postgres.psql_args,
            accepted_bundle,
            repository_name="supplement-retention",
            root_path="graph:supplement-retention",
            authority=accepted_authority,
            portable_binding=_binding(accepted_bundle, accepted_authority),
        )
        params = _psycopg_connection_params_from_psql_args(postgres.psql_args)
        stage_id = stage_id_for_authority(retained_authority)
        owner = retained_authority.owner(accepted.repository_id)
        with psycopg.connect(make_conninfo(**params)) as connection:
            run_id = _row(
                connection,
                "INSERT INTO runs(repository_id, status) VALUES (%s, 'complete') RETURNING id",
                (accepted.repository_id,),
            )[0]
            assert isinstance(run_id, int)
            # Model a retained-row corruption after the schema's normal guard;
            # the reconciliation reader must still refuse to infer publication.
            connection.execute(
                "ALTER TABLE runs DROP CONSTRAINT runs_portable_publication_binding_all_or_none"
            )
            connection.execute(
                "UPDATE runs SET publication_bundle_id = %s WHERE id = %s",
                (retained_bundle.bundle_id, run_id),
            )
            connection.execute(
                "INSERT INTO ingestion_stages("
                "stage_id, repository_id, operation_id, job_id, attempt, execution_mode, "
                "coordinator_instance_id, singleton_fencing_epoch, graph_lease_fencing_epoch, "
                "source_generation, config_generation, extractor_generation, canonicalizer_generation, "
                "state, merge_status, publication_reconciliation_state, cleanup_eligibility, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "'commit_unknown', 'unknown', 'required', 'blocked', now() + interval '1 hour')",
                (
                    stage_id,
                    owner.repository_id,
                    owner.operation_id,
                    owner.job_id,
                    owner.attempt,
                    owner.execution_mode,
                    owner.coordinator_instance_id,
                    owner.singleton_fencing_epoch,
                    owner.graph_lease_fencing_epoch,
                    owner.source_generation,
                    owner.config_generation,
                    owner.extractor_generation,
                    owner.canonicalizer_generation,
                ),
            )
            connection.commit()

        handoff = PublicationHandoff(
            MergeContext(
                stage_id,
                owner,
                run_id,
            ),
            RunPublicationReceipt(
                retained_authority.receipt().attempt,
                retained_authority.receipt().generations,
                _binding(retained_bundle, retained_authority),
            ),
        ).validate()
        with pytest.raises(
            StorageSchemaError,
            match="portable publication identity must be present all-or-none",
        ):
            reconcile_commit_unknown(
                psycopg.connect,
                params,
                handoff,
                accepted.repository_id,
            )

        with psycopg.connect(make_conninfo(**params)) as connection:
            assert _row(
                connection,
                "SELECT state, merge_status, publication_reconciliation_state, cleanup_eligibility "
                "FROM ingestion_stages WHERE stage_id = %s",
                (stage_id,),
            ) == ("commit_unknown", "unknown", "required", "blocked")

        latest = read_latest_receipt_bearing_publication(
            postgres.psql_args, psql_command=postgres.psql_command
        )
        assert latest is not None and latest.run_id == accepted.run_id
        assert read_run_publication(
            postgres.psql_args,
            job_id=str(retained_authority.job_id),
            attempt=int(retained_authority.attempt),
            psql_command=postgres.psql_command,
        ) is None
