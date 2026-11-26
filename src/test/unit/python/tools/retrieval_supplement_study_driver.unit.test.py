"""Source-level unit coverage for inner epoch, evidence and cleanup seams."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from common_lore_index_test_support import retrieval_supplement_study_pg as study_pg
from common_lore_index.ops.config_local import load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.retrieval.lexical_policy import build_lexical_generation_policy
from common_lore_index_test_support.postgres_container import PostgresContainerDatabase
from common_lore_index_test_support.retrieval_supplement_fixtures import (
    make_test_bindings, make_test_identity, make_test_policy,
)

from retrieval_supplement_study_contracts import StudyRefusal
from retrieval_supplement_study_inner_evidence import (
    InnerEvidence,
    attach_slot_metrics,
    begin_preparation_epoch,
    settle_cleanup,
)
from common_lore_index_test_support.retrieval_supplement_study_pg_lifecycle import cleanup_container_absent


@dataclass(frozen=True)
class _Outcome:
    task_id: str
    arm: str
    repetition: int
    metrics: object = None


def test_functional_epoch_is_recorded_without_host_ack(tmp_path: Path) -> None:
    ledger = InnerEvidence()
    epoch = begin_preparation_epoch(
        attempt_id="study-test-epoch",
        ordinal=1,
        evaluator_closure_sha256="sha256:" + "a" * 64,
        functional_only=True,
        evidence_root=tmp_path,
        require_current=lambda: None,
        charge=lambda _value: None,
        ledger=ledger,
    )
    assert epoch.request_attempt_id == "study-test-epoch-v1"
    assert ledger.epochs[0].acknowledged is True
    assert (tmp_path / "epochs/v1/ack.json").is_file()


def test_report_metrics_replace_only_by_exact_slot_identity() -> None:
    outcomes = (_Outcome("task-1", "arm-a", 1), _Outcome("task-2", "arm-b", 1))
    report = {"slots": [
        {"slot": {"task_id": "task-2", "arm": "arm-b", "repetition": 1},
         "grading": {"metrics": {"recall@5": 0.5}}},
    ]}
    enriched = attach_slot_metrics(outcomes, report)
    assert enriched[0].metrics is None
    assert enriched[1].metrics == {"recall@5": 0.5}


def test_cleanup_preserves_primary_and_records_secondary_failure() -> None:
    ledger = InnerEvidence()
    primary = RuntimeError("primary")

    def cleanup() -> None:
        raise OSError("secondary")

    retained, receipt = settle_cleanup(primary, cleanup, ledger)
    assert retained is primary
    assert receipt is None
    assert ledger.secondary_errors == ["cleanup_failed"]


def test_cleanup_failure_is_primary_when_body_completed() -> None:
    ledger = InnerEvidence()

    def cleanup() -> None:
        raise OSError("cleanup")

    retained, receipt = settle_cleanup(None, cleanup, ledger)
    assert isinstance(retained, OSError)
    assert receipt is None


def test_cleanup_absence_fact_survives_primary_and_secondary_failure() -> None:
    ledger = InnerEvidence()
    primary = RuntimeError("primary")
    lifecycle = {"absent_after_rm": False}

    def cleanup() -> None:
        lifecycle["absent_after_rm"] = True
        raise OSError("secondary")

    retained, receipt = settle_cleanup(primary, cleanup, ledger)
    assert retained is primary
    assert receipt is None
    assert cleanup_container_absent(lifecycle) is True
    assert ledger.secondary_errors == ["cleanup_failed"]


@pytest.mark.parametrize("existing", [False, True])
def test_store_workspace_exists_before_either_backend_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool,
) -> None:
    workspace = tmp_path / "stores"
    if existing:
        workspace.mkdir(mode=0o700)
    reached: list[Path] = []

    def first_home(home: Path, *_args: object, **_kwargs: object) -> Path:
        assert workspace.is_dir()
        assert workspace.stat().st_mode & 0o777 == 0o700
        reached.append(home)
        raise RuntimeError("first-publication-reached")

    monkeypatch.setattr(study_pg, "write_study_supplement_home", first_home)
    with pytest.raises(RuntimeError, match="first-publication-reached"):
        study_pg.publish_study_stores(
            workspace, tmp_path, identity=make_test_identity(),
            bindings=make_test_bindings(), policy=make_test_policy(),
            postgres=PostgresContainerDatabase(
                host=str(tmp_path / "socket"), port=55433,
                database="repomap_study", user="postgres", psql_command="/sdk/psql",
            ),
        )
    assert reached == [workspace / "sqlite-home"]


def test_store_workspace_symlink_is_refused_without_publication(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    workspace = tmp_path / "stores"
    workspace.symlink_to(target, target_is_directory=True)
    with pytest.raises(StudyRefusal, match="workspace_symlink"):
        study_pg.publish_study_stores(
            workspace, tmp_path, identity=make_test_identity(),
            bindings=make_test_bindings(), policy=make_test_policy(),
            postgres=PostgresContainerDatabase(
                host=str(tmp_path / "socket"), port=55433, database="repomap_study",
                user="postgres", psql_command="/sdk/psql",
            ),
        )
    assert not tuple(target.iterdir())


@pytest.mark.parametrize("socket_host", [False, True])
def test_durable_home_contains_only_public_bindings_without_reclassifying_controls(
    tmp_path: Path, socket_host: bool,
) -> None:
    identity, bindings, policy = make_test_identity(), make_test_bindings(), make_test_policy()
    original_policy = policy.policy_digest()
    working_copy = tmp_path / "working-copy"
    working_copy.mkdir(mode=0o700)
    for alias in bindings:
        (working_copy / alias).mkdir(mode=0o700)
    home = study_pg.write_study_supplement_home(
        tmp_path / "home", working_copy, identity=identity, bindings=bindings, policy=policy,
        postgres=PostgresContainerDatabase(
            host=str(tmp_path / "socket") if socket_host else "127.0.0.1", port=55433,
            database="repomap_study", user="postgres", psql_command="/sdk/psql",
        ),
    )
    config = load_graph_registry_config_home(home)
    assert isinstance(config, OpsConfig)
    graph = next(graph for graph in config.graphs if graph.id == identity.graph_id)
    public_ids = {key for key, value in policy.bindings.items()
                  if value.resolved_binding_privacy == "public-dev"}
    private_ids = set(bindings.values()) - public_ids
    assert private_ids and graph.privacy == "public-dev"
    assert {binding.binding_id for binding in graph.effective_source_bindings} == public_ids
    lexical = build_lexical_generation_policy(graph)
    assert lexical.resolved_graph_privacy == "public-dev"
    assert set(lexical.admitted_binding_ids) == public_ids
    assert policy.policy_digest() == original_policy
    assert all(policy.bindings[key].resolved_binding_privacy == "private-memory" for key in private_ids)
