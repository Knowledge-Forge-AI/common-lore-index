"""Prove matched synthetic literal and refusal slots against disposable stores."""

from dataclasses import replace
from pathlib import Path

import pytest

from common_lore_index.ops.config_local import load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_service import build_graph_lexical_generation
from common_lore_index.retrieval.query_contracts import QueryMode
from common_lore_index_test_support.postgres_harness import temporary_postgres
from common_lore_index_test_support.retrieval_body_fixtures import make_body_identity
from common_lore_index_test_support.retrieval_body_publication_fixture import BodyStoreFixture, publish_body_fixture, write_body_home
from common_lore_index_test_support.retrieval_w5_matched_fixture import (
    EVALUATOR, GRADE, make_synthetic_archives, ready_fit, synthetic_policy, synthetic_result,
)
from retrieval_evaluation_grading import ExecutionState
from retrieval_evaluation_views import materialize_native_search_view
from retrieval_public_text_fusion import ARM_PTX, study_arm_id
from retrieval_semantic_fusion import AdmittedArmObservation
from retrieval_w5_matched_attempt import MatchedAttempt, RankCoverage, SlotPlan
import retrieval_w5_matched_baselines as baselines
from retrieval_w5_matched_baselines import execute_matched_fusion, execute_matched_lexical, execute_matched_native
from retrieval_w5_matched_design import Arm, ObservationState, Outcome, Postflight
from retrieval_w5_matched_grading import grade_closed_ranking
from retrieval_w5_matched_inputs import load_matched_input
import retrieval_w5_matched_postgres as matched_pg
from retrieval_w5_matched_postgres import MatchedPostgres, bind_matched_postgres
from retrieval_w5_matched_publications import bind_matched_publications, full_policy_digest, read_native_capture


def test_disposable_publications_execute_literal_slots_and_never_call_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    custody = tmp_path / "custody"
    commitment = make_synthetic_archives().write(custody)
    inputs = load_matched_input(custody, EVALUATOR, commitment)
    policy, bindings = synthetic_policy()
    identity = make_body_identity()
    source = tmp_path / "captured"
    # Physical publications have only public source roots. The full evaluator
    # policy still describes both deny controls and both factory pins prove it.
    published_bindings = {alias: bindings[alias] for alias in inputs.public_bindings}
    published_policy = replace(policy, bindings={key: policy.bindings[key] for key in published_bindings.values()})
    for alias in published_bindings:
        (source / alias).mkdir(parents=True)
    for row in inputs.files:
        (source / row.binding / row.path).write_bytes(row.content)

    sqlite_home = write_body_home(
        tmp_path / "sqlite", source, identity=identity, bindings=published_bindings, policy=published_policy,
    )
    sqlite = BodyStoreFixture(publish_body_fixture(sqlite_home, identity=identity, policy=policy))
    view = materialize_native_search_view(
        tmp_path / "native", manifest=sqlite.published.manifest, bundle=sqlite.published.bundle,
        store=sqlite.published.store, policy=policy, selected_aliases=inputs.public_bindings,
        include_extracted_metadata_for_non_body=False,
    )
    with temporary_postgres() as database:
        postgres_home = write_body_home(
            tmp_path / "postgres", source, identity=identity, bindings=published_bindings, policy=published_policy, postgres=database,
        )
        postgres = BodyStoreFixture(publish_body_fixture(
            postgres_home, identity=identity, policy=policy, psql_command=database.psql_command,
        ), psql_command=database.psql_command)
        config = load_graph_registry_config_home(postgres_home)
        assert isinstance(config, OpsConfig)
        lexical = build_graph_lexical_generation(
            config, identity.graph_id, authorize_body=True, selected_binding_ids=tuple(sorted(policy.selected_binding_ids)),
        )
        assert lexical.result == "published" and lexical.published is not None, lexical.refusal_code
        generation = lexical.published.lexical_generation_id
        pg_reader = bind_matched_postgres(config, pin=postgres.pin, policy=policy,
                                          read_vector_generation=lambda: "unit-model-unused")
        pair = bind_matched_publications(
            inputs, sqlite=sqlite.pin, postgres=postgres.pin, sqlite_policy=policy, postgres_policy=policy,
            expected_policy_sha256=full_policy_digest(policy),
            read_sqlite_capture=lambda: read_native_capture(view, sqlite.pin, policy),
            read_postgres_capture=pg_reader,
        )
        ledger = tmp_path / "ledger"
        ledger.mkdir(mode=0o700)
        attempt = MatchedAttempt(pair, ready_fit(pair), journal_root=ledger)
        actual = []
        ranked_baselines = []
        active_plans: dict[tuple[int, Arm], SlotPlan] = {}
        vector_reads = []

        def inert_vector(plan: SlotPlan):
            vector_reads.append(plan.slot.ordinal)
            assert plan.lexical_request is None
            synthetic = synthetic_result(pair, plan.slot)
            return AdmittedArmObservation(
                study_arm_id("exact_vector", ARM_PTX), "vector", verified_pin=pair.postgres,
                raw_file_hits=tuple((hit, rank, f"vector-fixture-{rank}") for rank, hit in enumerate(synthetic.hits, 1)),
                selected_binding_ids=plan.selected_binding_ids,
            ), synthetic.publication

        def execute(plan: SlotPlan):
            assert plan.slot.ordinal not in (21, 22)
            if plan.slot.ordinal in (1, 23):
                active_plans[(plan.slot.ordinal, plan.slot.arm)] = plan
            if plan.slot.ordinal == 1 and plan.slot.arm is Arm.FUSION:
                result = execute_matched_fusion(plan, pair, pg_reader, read_exact_v2=inert_vector)
                assert result is not None and result.hits
                assert result.publication.lexical_generation_id == generation
                assert result.publication.vector_generation_id == "unit-model-unused"
                return result
            if (plan.slot.ordinal, plan.slot.arm) in {(1, Arm.NATIVE), (1, Arm.LEXICAL), (13, Arm.LEXICAL)}:
                result = (execute_matched_native(plan, pair, view) if plan.slot.arm is Arm.NATIVE
                          else execute_matched_lexical(plan, pair, pg_reader))
                assert result is not None
                assert result.fallback is (plan.slot.arm is Arm.NATIVE)
                ranked_baselines.append((plan.slot.ordinal, plan.slot.arm))
                return result
            if plan.slot.ordinal <= 20:
                # These fixtures exercise the journal shape. Literal slots below
                # exercise actual backend routes without loading any model.
                return synthetic_result(pair, plan.slot)
            if plan.slot.arm is Arm.NATIVE:
                result = execute_matched_native(plan, pair, view)
            elif plan.slot.arm is Arm.LEXICAL:
                result = execute_matched_lexical(plan, pair, pg_reader)
            else:
                pytest.fail("literal fusion reached an execution callback")
            assert result is not None
            if plan.slot.arm is Arm.LEXICAL:
                assert result.publication.lexical_generation_id == generation
                assert result.publication.accepted_run_id == postgres.pin.run_id
            actual.append((plan.slot.ordinal, plan.slot.arm, len(result.hits), result.coverage))
            return result

        while attempt.run_next(execute, check_owner=lambda: None):
            pass
        assert vector_reads == [1]
        assert ranked_baselines == [(1, Arm.NATIVE), (1, Arm.LEXICAL), (13, Arm.LEXICAL)]
        assert set(actual) == {
            (23, Arm.NATIVE, 1, RankCoverage.COMPLETE), (23, Arm.LEXICAL, 1, RankCoverage.COMPLETE),
            (24, Arm.NATIVE, 0, RankCoverage.COMPLETE), (24, Arm.LEXICAL, 0, RankCoverage.COMPLETE),
        }
        result = attempt.close(postflight=Postflight.VERIFIED)
        graded = grade_closed_ranking(result, pair, grade_root=custody, archive_name=GRADE)
        assert graded.outcome is Outcome.NOT_DEMONSTRATED
        assert sum(row.state is ObservationState.CONTROL_CHECKED for row in result.events) == 8

        native = active_plans[(1, Arm.NATIVE)]
        with monkeypatch.context() as patch:
            patch.setattr(baselines, "execute_native_baseline_task_with_breakdown",
                          lambda *args: (ExecutionState.REFUSED, None, 0, None, None))
            with pytest.raises(ValueError, match="native_refused"):
                execute_matched_native(native, pair, view)

        def mutate_view(*_args):
            (view.view_root / view.selected_relative_paths[0]).write_bytes(b"changed")
            raise OSError("synthetic native error")

        first_file = view.view_root / view.selected_relative_paths[0]
        preserved = first_file.read_bytes()
        with monkeypatch.context() as patch:
            patch.setattr(baselines, "execute_native_baseline_task_with_breakdown", mutate_view)
            with pytest.raises(ValueError, match="mismatch|too_large|changed"):
                execute_matched_native(native, pair, view)
        first_file.write_bytes(preserved)

        fusion = active_plans[(1, Arm.FUSION)]
        vector_arm, identity = inert_vector(fusion)
        with pytest.raises(ValueError, match="vector_accepted_drift"):
            execute_matched_fusion(
                fusion, pair, pg_reader, read_exact_v2=lambda _plan: (
                    replace(vector_arm, execution_state=ExecutionState.UNAVAILABLE,
                            observed_accepted_publication={"run_id": pair.postgres.run_id + 1}), identity,
                ),
            )
        with pytest.raises(ValueError, match="vector_refused_or_truncated"):
            execute_matched_fusion(
                fusion, pair, pg_reader, read_exact_v2=lambda _plan: (
                    replace(vector_arm, truncated=True), replace(identity, vector_generation_id=None),
                ),
            )

        # A lexical privacy or provenance violation outranks a missing
        # identity and prevents even entering the vector reader.
        literal_request = active_plans[(23, Arm.LEXICAL)].lexical_request
        assert literal_request is not None
        literal = pg_reader.query(literal_request)
        hit, = literal.hits
        provenance = {key: value for key, value in literal.provenance.items()
                      if key not in ("accepted_run_id", "lexical_generation_id")}
        ranked = replace(literal, requested_mode=QueryMode.RANKED_DISCOVERY, effective_mode=QueryMode.RANKED_DISCOVERY,
                         provenance=provenance, scope=replace(literal.scope, publication_bundle_id=None))
        marker = "SYNTHETIC_PRIVATE_MARKER"
        leak = replace(hit, locator=replace(hit.locator, canonical_key="ordinary", evidence_key=marker))
        for corrupt, reason in (
            (replace(ranked, hits=(leak,)), "privacy_refused"),
            (replace(ranked, provenance=provenance | {"generation": 2}), "fusion_lexical_provenance"),
        ):
            with monkeypatch.context() as patch:
                patch.setattr(MatchedPostgres, "query", lambda *_args: corrupt)
                with pytest.raises(ValueError, match=reason):
                    execute_matched_fusion(fusion, pair, pg_reader, private_markers=(marker,),
                                           read_exact_v2=lambda _: pytest.fail("vector reached"))

        with monkeypatch.context() as patch:
            patch.setattr(matched_pg, "read_accepted_graph_publication", lambda *_args, **_kwargs: None)
            with pytest.raises(ValueError, match="postgres_accepted_missing"):
                pg_reader()

        # Losing all projected documents under the active generation is
        # an integrity violation, including when the authority row still exists.
        assert database.psql_scalar("""
            WITH removed AS (
                DELETE FROM cloe_lexical.documents RETURNING 1
            ) SELECT count(*) FROM removed
        """) == "24"
        with pytest.raises(ValueError, match="postgres_capture_content"):
            pg_reader()

        # A different generation policy receipt is a contradiction even when
        # the active publication, file census and content still match.
        assert database.psql_scalar("""
            WITH changed AS (
                UPDATE cloe_lexical.generations SET binding_policy_digests_json = '{}'
                WHERE state = 'published'
                RETURNING 1
            ) SELECT count(*) FROM changed
        """) == "1"
        with pytest.raises(ValueError, match="postgres_capture_identity"):
            pg_reader()

        config_path = postgres_home / config.config_files[0]
        original = config_path.read_bytes()
        config_path.write_bytes(original + b"\n")
        with pytest.raises(ValueError, match="postgres_config_drift"):
            pg_reader()
