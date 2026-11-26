"""Real PostgreSQL CLI children: v2 refresh reports, budget refusal and v1 default.

Each command is an observed canonical ``-m common_lore_index`` child. CLI JSON
remains the authority for publication state; no report value is copied from it.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import psycopg
import pytest

from common_lore_index.cli.main import main
from common_lore_index.coordinator.local_lifecycle import initialize_coordinator_control
from common_lore_index.ops.config_local import load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.storage import apply_migrations, default_rdbms_root
from common_lore_index.storage import staged_ingestion
from common_lore_index.storage.staging_observability import StagingMeasurementCategory, StagingMeasurements
from common_lore_index.storage.staging_resource_observability import StagingResourceSnapshot
from common_lore_index_test_support.postgres_harness import temporary_postgres
from common_lore_index_test_support.resource_report_cli_files import (
    limit, metric, private_directory, report_leftovers, report_payload, run_cloe,
)
from common_lore_index_test_support.retrieval_evaluation_publication import read_postgres_accepted_publication
from common_lore_index_test_support.retrieval_evaluation_fixture import (
    MIXED_V1_GRAPH_ID,
    materialize_mixed_v1_working_copy,
    write_mixed_v1_postgres_home,
)


def _home(tmp_path: Path, postgres: Any) -> tuple[Path, Path]:
    working_copy = materialize_mixed_v1_working_copy(tmp_path / "working-copy")
    home = write_mixed_v1_postgres_home(
        tmp_path / "postgres-home", working_copy,
        host=postgres.host, port=postgres.port, database=postgres.database,
        user=postgres.user, password=postgres.password, declared_aliases=("core", "companion"),
    )
    config = load_graph_registry_config_home(home)
    assert isinstance(config, OpsConfig)
    apply_migrations(
        default_rdbms_root(), config.postgres.psql_args_for_database(postgres.database),
        psql_command=postgres.psql_command,
    )
    assert initialize_coordinator_control(home)["result"] == "ready"
    return home, working_copy


def _refresh(home: Path, postgres: Any, tmp_path: Path, *extra: str, codes=frozenset({0})) -> dict[str, Any]:
    completed = run_cloe(
        "ops", "refresh-graph", "--repo-map-home", str(home), "--graph", MIXED_V1_GRAPH_ID,
        "--psql-command", postgres.psql_command, "--json", *extra, cwd=tmp_path,
    )
    assert completed.returncode in codes, completed.stderr
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return payload


def test_server_v2_committed_refused_and_v1_default_reports(tmp_path: Path) -> None:
    reports = private_directory(tmp_path / "reports")
    with temporary_postgres() as postgres:
        home, working_copy = _home(tmp_path, postgres)
        forbidden = (home, working_copy, postgres.password, MIXED_V1_GRAPH_ID)

        committed = _refresh(home, postgres, tmp_path, "--resource-report", str(reports / "v2.json"),
                             "--resource-report-schema", "2")
        assert committed["result"] == "success"
        v2 = report_payload(reports / "v2.json", operation="server_refresh", backend="postgresql",
                            outcome="committed", forbidden=forbidden)
        assert v2["schema"] == "cloe-resource-view-v2" and len(v2["metrics"]) == 30
        sealed = metric(v2, "sealed_manifest_bytes")["value"]
        assert metric(v2, "sealing_copy_read_bytes")["value"] == sealed
        assert metric(v2, "sealing_read_bytes")["value"] > 4 * sealed
        assert metric(v2, "sealing_read_bytes")["basis"] == "call_meter_exact"
        assert metric(v2, "worker_launches")["value"] == 1
        for name in ("worker_cpu", "worker_user_cpu", "worker_system_cpu", "worker_peak_rss"):
            row = metric(v2, name)
            assert row["availability"] == "measured" and row["value"] >= 0
        assert metric(v2, "worker_cpu")["value"] > 0 and metric(v2, "worker_peak_rss")["value"] > 0
        assert metric(v2, "worker_cpu")["value"] == (
            metric(v2, "worker_user_cpu")["value"] + metric(v2, "worker_system_cpu")["value"]
        )
        group = metric(v2, "worker_group_members_after_reap")
        assert group["availability"] == "measured" and group["value"] in (0, 1)
        assert group["basis"] == "process_group_probe"
        assert metric(v2, "publication_elapsed")["availability"] == "measured"

        config = load_graph_registry_config_home(home)
        assert isinstance(config, OpsConfig)
        accepted_before = read_postgres_accepted_publication(
            config, MIXED_V1_GRAPH_ID, psql_command=postgres.psql_command,
        )

        refused = _refresh(
            home, postgres, tmp_path, "--sealing-max-write-bytes", "1", "--resource-report",
            str(reports / "refused.json"), "--resource-report-schema", "2", codes=frozenset({1}),
        )
        result = refused["graphs"][0] if "graphs" in refused else refused
        assert result["result"] == "failure"
        assert (result["publication_state"], result["error_category"]) == ("not_started", "resource_budget_exceeded")
        codes = [item["code"] for item in result["diagnostics"]]
        assert codes == ["sealing-budget-exceeded"]
        assert result["diagnostics"][0]["message"] == "sealing budget exceeded: planned_write_bytes"
        refusal = report_payload(reports / "refused.json", operation="server_refresh", backend="postgresql",
                                 outcome="refused", instrumentation="incomplete", forbidden=forbidden)
        assert metric(refusal, "worker_launches")["value"] == 0
        assert limit(refusal, "sealing_max_write_bytes")["value"] == 1

        assert read_postgres_accepted_publication(
            config, MIXED_V1_GRAPH_ID, psql_command=postgres.psql_command,
        ) == accepted_before
        following = _refresh(home, postgres, tmp_path, "--resource-report", str(reports / "v1.json"))
        assert following["result"] == "success"
        v1 = report_payload(reports / "v1.json", operation="server_refresh", backend="postgresql",
                            outcome="committed", forbidden=forbidden)
        assert v1["schema"] == "cloe-resource-view-v1" and len(v1["metrics"]) == 11
        assert v1["limits"][-1]["name"] == "sealing_io_limit"

        plain = _refresh(home, postgres, tmp_path)
        assert set(plain) == set(following)
    assert report_leftovers(reports) == []


@pytest.mark.parametrize("fault", ("first_sample", "last_sample", "emit_partial"))
def test_server_v3_sampling_failure_preserves_publication(
    fault: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    reports = private_directory(tmp_path / "reports")
    target = reports / "v3.json"
    sentinel = "private sample callback failure"
    capture = staged_ingestion.capture_staging_resources
    attempts = 0
    faults: list[str] = []

    def sample(connection: Any) -> StagingResourceSnapshot:
        nonlocal attempts
        attempts += 1
        if (attempts == 1 and fault == "first_sample") or (attempts == 2 and fault == "last_sample"):
            faults.append(fault)
            raise RuntimeError(sentinel)
        return capture(connection)

    def emit_partial(
        measurements: StagingMeasurements, _before: StagingResourceSnapshot, _after: StagingResourceSnapshot,
    ) -> None:
        measurements.record_bytes(StagingMeasurementCategory.WAL_UPPER_BOUND, 91)
        faults.append(fault)
        raise RuntimeError(sentinel)

    monkeypatch.setattr(staged_ingestion, "capture_staging_resources", sample)
    if fault == "emit_partial":
        monkeypatch.setattr(staged_ingestion, "emit_staging_resource_measurements", emit_partial)
    with temporary_postgres() as postgres:
        home, working_copy = _home(tmp_path, postgres)
        config = load_graph_registry_config_home(home)
        assert isinstance(config, OpsConfig)
        result_code = main([
            "ops", "refresh-graph", "--repo-map-home", str(home), "--graph", MIXED_V1_GRAPH_ID,
            "--psql-command", postgres.psql_command, "--resource-report", str(target),
            "--resource-report-schema", "3", "--json",
        ])
        captured = capsys.readouterr()
        assert faults == [fault]
        assert result_code == 0, captured
        assert attempts == (1 if fault == "first_sample" else 2)
        cli = json.loads(captured.out)
        result = cli["graphs"][0] if "graphs" in cli else cli
        assert (result["result"], result["publication_state"]) == ("success", "committed")
        accepted, _ = read_postgres_accepted_publication(config, MIXED_V1_GRAPH_ID,
                                                         psql_command=postgres.psql_command)
        assert str(accepted.run_id) == str(result["run_id"])
        payload = report_payload(target, operation="server_refresh", backend="postgresql",
                                 outcome="committed", forbidden=(home, working_copy, postgres.password,
                                                                  MIXED_V1_GRAPH_ID, sentinel))
        for name in ("staging_wal_position_delta", "staging_database_temporary_bytes_delta"):
            row = metric(payload, name)
            assert (row["value"], row["availability"], row["reason"]) == (None, "unavailable", "observer_failed")
        assert sentinel not in captured.out + captured.err
    assert report_leftovers(reports) == []


@pytest.mark.parametrize("sample", ("normal", "reset", "query_failure", "wal_backwards", "temp_backwards"))
def test_server_v3_reports_shared_samples_and_preserves_actual_commit(
    sample: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    reports = private_directory(tmp_path / "reports")
    target = reports / "v3.json"
    with temporary_postgres() as postgres:
        home, working_copy = _home(tmp_path, postgres)
        params = dict(host=postgres.host, port=postgres.port, dbname=postgres.database,
                      user=postgres.user, password=postgres.password)
        capture = staged_ingestion.capture_staging_resources
        observations: list[StagingResourceSnapshot] = []

        class FailedSamplingQuery:
            def __init__(self, connection: Any) -> None:
                self._connection = connection

            def transaction(self) -> Any:
                return self._connection.transaction()

            def execute(self, statement: str) -> Any:
                if "FROM pg_stat_database" in statement:
                    raise psycopg.OperationalError("private sampling error")
                return self._connection.execute(statement)

        def observe(connection: Any) -> StagingResourceSnapshot:
            if observations and sample == "query_failure":
                connection = FailedSamplingQuery(connection)
            result = capture(connection)
            if observations and sample == "wal_backwards":
                first_position = observations[0].wal_position
                assert first_position is not None and first_position > 0
                result = replace(result, wal_position=first_position - 1)
            if sample == "temp_backwards":
                assert result.temporary_counter_identity is not None
                result = replace(result, temporary_bytes=0 if observations else 1)
            observations.append(result)
            return result

        def reset(*_args: Any) -> None:
            with psycopg.connect(**params, autocommit=True) as connection:
                connection.execute("SELECT pg_stat_reset()")

        monkeypatch.setattr(staged_ingestion, "capture_staging_resources", observe)
        if sample == "reset":
            monkeypatch.setattr(staged_ingestion, "_run_system_test_publication_pause", reset)
        assert main([
            "ops", "refresh-graph", "--repo-map-home", str(home), "--graph", MIXED_V1_GRAPH_ID,
            "--psql-command", postgres.psql_command, "--resource-report", str(target),
            "--resource-report-schema", "3", "--json",
        ]) == 0
        cli = json.loads(capsys.readouterr().out)
        assert cli["result"] == "success" and len(observations) == 2
        result = cli["graphs"][0] if "graphs" in cli else cli
        assert result["publication_state"] == "committed"
        config = load_graph_registry_config_home(home)
        assert isinstance(config, OpsConfig)
        accepted = read_postgres_accepted_publication(config, MIXED_V1_GRAPH_ID, psql_command=postgres.psql_command)
        assert accepted
        payload = report_payload(target, operation="server_refresh", backend="postgresql",
                                 outcome="committed", forbidden=(home, working_copy, postgres.password,
                                                                  MIXED_V1_GRAPH_ID, "private sampling error"))
        assert payload["schema"] == "cloe-resource-view-v3" and len(payload["metrics"]) == 13
        wal = metric(payload, "staging_wal_position_delta")
        temporary = metric(payload, "staging_database_temporary_bytes_delta")
        assert wal["basis"] == temporary["basis"] == "shared_sample_delta"
        if sample in {"query_failure", "wal_backwards"}:
            assert (wal["value"], wal["reason"]) == (None, "not_observed")
        else:
            assert wal["availability"] == "measured" and wal["value"] > 0
        if sample in {"normal", "wal_backwards"}:
            assert temporary["availability"] == "measured" and temporary["value"] >= 0
        else:
            assert (temporary["value"], temporary["reason"]) == (None, "not_observed")
        if sample in {"reset", "temp_backwards"}:
            first, second = (item.temporary_counter_identity for item in observations)
            assert first is not None and second is not None and first[0] == second[0]
            assert (first[1] != second[1]) == (sample == "reset")
        checked = run_cloe("ops", "resource-report", "show", "--input", str(target), "--json", cwd=tmp_path)
        assert checked.returncode == 0 and json.loads(checked.stdout) == payload
    assert report_leftovers(reports) == []
