"""Qualify the exact synthetic controller entry through the disposable runner."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from common_lore_index.ops.config_local import load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_service import build_graph_lexical_generation
from repomap_test_support.postgres_harness import temporary_postgres
from repomap_test_support.retrieval_body_fixtures import make_body_identity
from repomap_test_support.retrieval_body_publication_fixture import BodyStoreFixture, publish_body_fixture, write_body_home
from repomap_test_support.retrieval_w5_matched_fixture import make_synthetic_archives, ready_fit, synthetic_policy, synthetic_result, synthetic_tar
from retrieval_body_study_journal_io import root_identity
from retrieval_evaluation_views import materialize_native_search_view
from retrieval_public_text_fusion import ARM_PTX, study_arm_id
from retrieval_semantic_fusion import AdmittedArmObservation
from retrieval_w5_matched_attempt import RankedObservation, SlotPlan
from retrieval_w5_matched_baselines import execute_matched_fusion, execute_matched_lexical, execute_matched_native
from retrieval_w5_matched_design import Arm
from retrieval_w5_matched_dry import EVALUATOR_MEMBER, GRADING_MEMBER, DryRunPlan, DryRuntime, OfflineClosure, run_synthetic_dry
from retrieval_w5_matched_inputs import MatchedInput
from retrieval_w5_matched_postgres import bind_matched_postgres
from retrieval_w5_matched_publications import bind_matched_publications, full_policy_digest, read_native_capture


@pytest.mark.parametrize("interrupt", (False, True), ids=("complete", "cleanup-after-lost-owner"))
def test_synthetic_matched_controller(tmp_path: Path, interrupt: bool) -> None:
    evaluator, grade, attempt = (tmp_path / name for name in ("evaluator", "custodian", "attempt"))
    for root in (evaluator, grade, attempt):
        root.mkdir(mode=0o700)
    fixtures = make_synthetic_archives()
    commitment = fixtures.write(tmp_path / "author", include_grade=False)
    (evaluator / EVALUATOR_MEMBER).write_bytes(synthetic_tar(fixtures.evaluator))
    (grade / GRADING_MEMBER).write_bytes(synthetic_tar(fixtures.grader))
    # The synthetic launcher supplies a fixed inert closure. The host handoff
    # independently identifies the retained offline deployment artifacts.
    closure = OfflineClosure("a" * 64, "b" * 64, "c" * 64, "d" * 64)
    plan = DryRunPlan(commitment, "e" * 64, "f" * 64, closure, root_identity(attempt))
    cleanup = False
    runtime_root: Path | None = None
    runtime_entries = 0
    called: list[tuple[int, Arm]] = []

    @contextmanager
    def open_runtime(inputs: MatchedInput) -> Iterator[DryRuntime]:
        nonlocal runtime_entries, cleanup, runtime_root
        runtime_entries += 1
        policy, bindings = synthetic_policy()
        published_bindings = {alias: bindings[alias] for alias in inputs.public_bindings}
        published_policy = replace(policy, bindings={key: policy.bindings[key] for key in published_bindings.values()})
        identity = make_body_identity()
        with TemporaryDirectory(dir=tmp_path) as temporary:
            workspace = Path(temporary)
            runtime_root = workspace
            owned_identity = root_identity(workspace)
            captured = workspace / "source"
            for alias in published_bindings:
                (captured / alias).mkdir(parents=True)
            for row in inputs.files:
                (captured / row.binding / row.path).write_bytes(row.content)
            sqlite_home = write_body_home(
                workspace / "sqlite", captured, identity=identity, bindings=published_bindings, policy=published_policy,
            )
            sqlite = BodyStoreFixture(publish_body_fixture(sqlite_home, identity=identity, policy=policy))
            view = materialize_native_search_view(
                workspace / "native", manifest=sqlite.published.manifest, bundle=sqlite.published.bundle,
                store=sqlite.published.store, policy=policy, selected_aliases=inputs.public_bindings,
                include_extracted_metadata_for_non_body=False,
            )
            with temporary_postgres() as database:
                try:
                    postgres_home = write_body_home(
                        workspace / "postgres", captured, identity=identity, bindings=published_bindings,
                        policy=published_policy, postgres=database,
                    )
                    postgres = BodyStoreFixture(publish_body_fixture(
                        postgres_home, identity=identity, policy=policy, psql_command=database.psql_command,
                    ), psql_command=database.psql_command)
                    config = load_graph_registry_config_home(postgres_home)
                    assert isinstance(config, OpsConfig)
                    lexical = build_graph_lexical_generation(
                        config, identity.graph_id, authorize_body=True,
                        selected_binding_ids=tuple(sorted(policy.selected_binding_ids)),
                    )
                    assert lexical.result == "published"
                    reader = bind_matched_postgres(
                        config, pin=postgres.pin, policy=policy, read_vector_generation=lambda: "synthetic-vector",
                    )
                    pair = bind_matched_publications(
                        inputs, sqlite=sqlite.pin, postgres=postgres.pin, sqlite_policy=policy, postgres_policy=policy,
                        expected_policy_sha256=full_policy_digest(policy),
                        read_sqlite_capture=lambda: read_native_capture(view, sqlite.pin, policy),
                        read_postgres_capture=reader,
                    )

                    def inert_vector(slot: SlotPlan):
                        result = synthetic_result(pair, slot.slot)
                        return AdmittedArmObservation(
                            study_arm_id("exact_vector", ARM_PTX), "vector", verified_pin=pair.postgres,
                            raw_file_hits=tuple((hit, rank, f"fixture-{rank}") for rank, hit in enumerate(result.hits, 1)),
                            selected_binding_ids=slot.selected_binding_ids,
                        ), result.publication

                    def check_owner() -> None:
                        if root_identity(workspace) != owned_identity or (interrupt and len(called) == 1):
                            raise OSError("synthetic owner lost")

                    def execute(slot: SlotPlan) -> RankedObservation | None:
                        called.append((slot.slot.ordinal, slot.slot.arm))
                        if slot.slot.arm is Arm.NATIVE:
                            return execute_matched_native(slot, pair, view)
                        if slot.slot.arm is Arm.LEXICAL:
                            return execute_matched_lexical(slot, pair, reader)
                        return execute_matched_fusion(slot, pair, reader, read_exact_v2=inert_vector)

                    yield DryRuntime(pair, ready_fit(pair), execute, check_owner)
                finally:
                    empty = database.psql_scalar("""
                        DROP SCHEMA IF EXISTS cloe_lexical CASCADE;
                        DROP SCHEMA public CASCADE;
                        CREATE SCHEMA public;
                        SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname IN ('public', 'cloe_lexical');
                    """) == "0"
                    cleanup = empty
        # TemporaryDirectory removes only its named, owned fixture tree.
        assert not workspace.exists()

    def launch():
        return run_synthetic_dry(
            plan, expected_plan_sha256=plan.identity(), observed_closure=closure, evaluator_root=evaluator,
            grade_root=grade, attempt_root=attempt, open_runtime=open_runtime,
            cleanup_verified=lambda: cleanup and runtime_root is not None and not runtime_root.exists(),
        )

    if interrupt:
        with pytest.raises(OSError, match="owner lost"):
            launch()
        assert called == [(1, Arm.NATIVE)]
        terminal = json.loads((attempt / "dry-terminal.json").read_bytes())
        assert terminal["report"]["cleanup"] == "proven"
        assert terminal["report"]["status"] == "technical_failure"
    else:
        report = launch()
        assert (report.status, report.cleanup, report.planned_slots) == ("qualified", "proven", 72)
        assert (report.ranked_slots, report.refused_slots, len(called)) == (64, 8, 64)
        assert (attempt / "dry-terminal.json").read_bytes() == report.document("cleanup")
    with pytest.raises(ValueError, match="destination_exists"):
        launch()
    assert runtime_entries == 1
