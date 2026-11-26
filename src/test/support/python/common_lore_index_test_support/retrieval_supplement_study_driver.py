"""Inner SDK orchestration for the selected W5 supplement native study (ADR 0203)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets
from typing import Any

from common_lore_index_test_support.retrieval_supplement_study_pg import (
    SocketPostgresHandle,
    cleanup_study,
    disposable_socket_postgres,
    publish_study_stores,
)
from common_lore_index_test_support.retrieval_supplement_study_setup import (
    build_supplement_declaration,
    run_exchange_epoch,
    slot_outcome_row,
    store_join_maps,
)
from common_lore_index_test_support.retrieval_supplement_study_pg_lifecycle import cleanup_container_absent
from retrieval_supplement_study_inner_evidence import (
    InnerEvidence, attach_slot_metrics, begin_preparation_epoch, build_v1_request_stage,
    build_v2_live_requests, byte_forecast_evidence, candidate_snapshot_bytes, error_code,
    failure_receipt, report_payload, settle_cleanup,
)
from common_lore_index_test_support.retrieval_supplement_study_slots import (
    execute_baseline_slot,
    execute_vector_or_fusion_slot,
    import_study_vector_batches,
)
from common_lore_index_test_support.w5_public_text_functional import SyntheticDelivery
from retrieval_evaluation_grading import ExecutionState
from retrieval_public_text_contracts import PrivateMarkerScope
from retrieval_public_text_sources import require_unchanged, worker_closure
from retrieval_public_text_study_schema import StudyNames, make_study_identity
from retrieval_semantic_arm_validation import AdmittedArmObservation
from retrieval_semantic_generator_inputs import prepare_public_generation_input
from retrieval_semantic_generator_output import seal_generation_result
from retrieval_semantic_provenance import verify_publication_provenance_from_adapter
from retrieval_supplement_corpus import materialize_corpus
from retrieval_supplement_files import publish_member
from retrieval_supplement_operation import PhaseRecorder
from retrieval_supplement_operation_values import PhaseIdentity
from retrieval_supplement_report import SlotResult, report_supplement
from retrieval_supplement_study_closure import discover_study_evaluator_closure
from retrieval_supplement_study_contracts import (
    ARTIFACT_SOURCE_IDENTITY_SHA256,
    INPUT_PIN_SHA256,
    SLOT_COUNT,
    STUDY_SDK_SECONDS,
    TOTAL_RECORDS,
    WORKER_AGGREGATE_SECONDS,
    WORKER_CLOSURE_SHA256,
    refuse,
)
from retrieval_supplement_study_live_requests import seal_v2_worker_response
from retrieval_supplement_study_inputs import require_study_manifest
from retrieval_supplement_study_plan import (
    admission_from_inner_plan,
    require_admission_current,
    read_inner_study_plan,
)
from retrieval_supplement_study_preflight import (
    SlotOutcome,
    analyze_slots,
    extract_worker_token_evidence,
    parse_token_evidence,
    require_store_joins,
)
from retrieval_supplement_study_schedule import (
    CooperativeBudget,
    build_schedule,
)
from retrieval_supplement_values import (
    ARMS,
    canonical_bytes,
    digest_bytes,
    digest_json,
)

@dataclass(frozen=True)
class SupplementStudyOutcome:
    status: str
    attempt_id: str
    observed_slots: int
    unstarted_slots: int
    token_records: int
    cleanup_proven: bool
    report_sha256: str | None
    paired_analysis: dict[str, Any] | None
    failure_code: str | None
    secondary_errors: tuple[str, ...] = ()

def run_supplement_study_inner(
    *,
    inner_plan_path: Path,
    repo_root: Path,
    workspace: Path,
    report_root: Path,
    functional_only: bool = False,
    external_pg: SocketPostgresHandle | None = None,
) -> SupplementStudyOutcome:
    """Execute the full inner study lifecycle and export report or failure receipt."""
    refuse(not (external_pg is not None and not functional_only),
           "study_driver:external_pg_forbidden_in_native_mode")
    plan = read_inner_study_plan(inner_plan_path)
    admission = admission_from_inner_plan(plan)
    require_admission_current(admission, plan)
    schedule = build_schedule()
    refuse(plan["schedule_sha256"] == schedule["schedule_sha256"], "study_driver:schedule_drift")

    budget = CooperativeBudget(STUDY_SDK_SECONDS)
    budget.begin_phase("sdk_import_and_closures", 240)
    closure = discover_study_evaluator_closure(repo_root)
    refuse(closure["evaluator_closure_sha256"] == plan["evaluator_closure_sha256"],
           "study_driver:evaluator_closure_mismatch")
    w_closure = worker_closure(repo_root)
    refuse(w_closure.identity == WORKER_CLOSURE_SHA256, "study_driver:worker_closure_mismatch")
    identity, bindings, catalog, corpus, policy, preparation, declaration = build_supplement_declaration(repo_root, admission.attempt_id)
    declared_slots = require_study_manifest(plan, catalog)
    evidence = InnerEvidence()
    candidate_bytes = 0
    def require_current() -> None:
        require_admission_current(admission, plan)
        current = discover_study_evaluator_closure(repo_root)
        refuse(current["evaluator_closure_sha256"] == closure["evaluator_closure_sha256"],
               "study_driver:evaluator_closure_drift")
        require_unchanged(repo_root, w_closure)

    require_current()
    budget.checkpoint()

    workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    delivery = SyntheticDelivery(workspace / "synthetic-model", w_closure) if functional_only else None
    delivery_kind = "functional_synthetic" if functional_only else "external_unqualified"
    study_id = make_study_identity(
        task_pins_identity=INPUT_PIN_SHA256,
        q_source_closure=closure["evaluator_closure_sha256"],
        nonce=secrets.token_hex(16),
    )
    names = StudyNames(study_id)
    slot_results: list[SlotResult] = []
    slot_outcomes: list[SlotOutcome] = []
    token_count = 0
    failure_code: str | None = None
    pg_relations_clean, pg_container_absent = False, external_pg is not None
    lifecycle_info: dict[str, Any] = {"absent_after_rm": external_pg is not None}
    report_sha: str | None = None
    paired: dict[str, Any] | None = None
    cleanup_receipt: dict[str, Any] | None = None
    pending_report: dict[str, Any] | None = None

    @dataclass
    class _Holder:
        cat: Any = None

    holder = _Holder()

    def _execute_with_pg(pg_handle: SocketPostgresHandle) -> None:
        nonlocal token_count, pg_relations_clean, paired, cleanup_receipt, pending_report
        primary: BaseException | None = None
        try:
            budget.begin_phase("pg_socket_cluster_and_publish", 60)
            working_copy = workspace / "working-copy"
            working_copy.mkdir(mode=0o700)
            materialize_corpus(corpus, working_copy)
            stores = publish_study_stores(
                workspace, working_copy, identity=identity, bindings=bindings,
                policy=policy, postgres=pg_handle.database, psql_command=pg_handle.psql_command,
            )
            budget.checkpoint()

            budget.begin_phase("pre_request_v1_prepare_and_publish", 110)
            begin_preparation_epoch(
                attempt_id=admission.attempt_id, ordinal=1,
                evaluator_closure_sha256=closure["evaluator_closure_sha256"],
                functional_only=functional_only, evidence_root=report_root,
                require_current=require_current, charge=lambda _value: None, ledger=evidence,
            )
            scope = PrivateMarkerScope.create((str(workspace), str(report_root)))
            reps = stores.postgres_store.representations()
            v1_input = prepare_public_generation_input(
                reps, catalog.tasks,
                read_current=lambda: verify_publication_provenance_from_adapter(
                    stores.postgres_store.adapter, policy=policy),
                read_representations=stores.postgres_store.representations,
            )
            stage = build_v1_request_stage(
                preparation, v1_input=v1_input,
                attempt_id=admission.attempt_id,
                artifact_profile_identity=ARTIFACT_SOURCE_IDENTITY_SHA256,
            )
            budget.checkpoint()

            budget.begin_phase("worker_epoch_v1", WORKER_AGGREGATE_SECONDS)
            worker1 = run_exchange_epoch(
                dict(stage.request), stage.request_raw,
                worker_digests=dict(w_closure.digests), delivery=delivery,
                representation="canonical-node-v1", evidence_root=report_root,
                evidence=evidence, require_current=require_current,
            )
            resp1 = worker1.response
            refuse(resp1 is not None, "study_driver:worker_v1_response_missing")
            v1_artifact = seal_generation_result(v1_input, resp1)
            budget.checkpoint()

            budget.begin_phase("pre_request_v2_recheck_and_publish", 110)
            begin_preparation_epoch(
                attempt_id=admission.attempt_id, ordinal=2,
                evaluator_closure_sha256=closure["evaluator_closure_sha256"],
                functional_only=functional_only, evidence_root=report_root,
                require_current=require_current, charge=lambda _value: None, ledger=evidence,
            )
            pub_text = stores.postgres_store.public_text(scope)
            golds, acc_nodes, pos_ids = store_join_maps(catalog, v1_input, pub_text)
            require_store_joins(
                ordered_task_ids=[t.task_id for t in catalog.tasks],
                gold_targets=golds, accepted_nodes=acc_nodes,
                publication_identity=pub_text.publication_identity,
                expected_publication_identity=digest_json(stores.postgres_store.pin.to_dict()),
                positive_task_ids=pos_ids,
            )
            live = build_v2_live_requests(
                stage, public_text=pub_text, tasks=catalog.tasks,
                attempt_id=admission.attempt_id,
                artifact_profile_identity=ARTIFACT_SOURCE_IDENTITY_SHA256,
            )
            forecast = byte_forecast_evidence(
                plan, source_bytes=int(closure["total_bytes"]),
                request_bytes=(len(stage.request_raw), len(live.v2_request_raw)),
                candidate_bytes=candidate_bytes,
            )
            stores.postgres_store.pin.revalidate_current()
            budget.checkpoint()

            budget.begin_phase("worker_epoch_v2", WORKER_AGGREGATE_SECONDS)
            worker2 = run_exchange_epoch(
                dict(live.v2_request), live.v2_request_raw,
                worker_digests=dict(w_closure.digests), delivery=delivery,
                representation="canonical-node-public-text-v2", evidence_root=report_root,
                evidence=evidence, require_current=require_current,
            )
            resp2 = worker2.response
            refuse(resp2 is not None, "study_driver:worker_v2_response_missing")
            v2_seal = seal_v2_worker_response(
                live, resp2, public_text=pub_text,
                fresh_public_text=lambda: stores.postgres_store.public_text(scope), scope=scope,
            )
            budget.checkpoint()

            budget.begin_phase("vector_batch_import_and_joins", 80)
            require_current()
            tok1 = extract_worker_token_evidence("canonical-node-v1", resp1, id_prefix="v1.")
            tok2 = extract_worker_token_evidence("canonical-node-public-text-v2", resp2)
            token_map = parse_token_evidence([tok1, tok2], expected_total=TOTAL_RECORDS)
            token_count = len(token_map)
            expected_tok_ids = (
                [f"v1.{r.record_id}" for r in v1_input.records]
                + [r.record_id for r in live.v2_records]
            )
            require_store_joins(
                ordered_task_ids=[t.task_id for t in catalog.tasks],
                gold_targets=golds, accepted_nodes=acc_nodes,
                publication_identity=pub_text.publication_identity,
                expected_publication_identity=digest_json(stores.postgres_store.pin.to_dict()),
                positive_task_ids=pos_ids,
                expected_token_record_ids=expected_tok_ids, token_evidence=token_map,
            )
            holder.cat, arm_profiles = import_study_vector_batches(
                pg_handle, names, v1_artifact=v1_artifact, v2_seal=v2_seal,
                public_text=pub_text, pg_pin=stores.postgres_store.pin,
                candidate_pub=stores.candidate_pub,
                artifact_profile_identity=ARTIFACT_SOURCE_IDENTITY_SHA256,
            )
            budget.checkpoint()

            budget.begin_phase("query_slots_432", 300)
            cases_by_id = {c.task.task_id: c for c in catalog.cases}
            lex_cache: dict[tuple[str, int], tuple[ExecutionState, Any]] = {}
            v2_obs_cache: dict[tuple[str, int], AdmittedArmObservation | None] = {}
            recorder = PhaseRecorder()
            declared = declaration.to_dict()
            for slot in declared_slots:
                case = cases_by_id[slot.task_id]
                p_id = PhaseIdentity(
                    declared["supplement_id"], declared["input_pin_sha256"],
                    declared["freeze_sha256"], declared["source_closure_sha256"],
                    declared["attempt_id"],
                    f"slot-{slot.task_id}-{slot.arm}-{slot.repetition}", "query",
                    task_id=slot.task_id, arm=slot.arm,
                    repetition=slot.repetition, generation=slot.generation,
                )
                with recorder.scope(p_id):
                    if slot.arm in ARMS[:3]:
                        res, lex_pair = execute_baseline_slot(
                            case.task, slot, declaration=declaration,
                            stores=stores, policy=policy, delivery_kind=delivery_kind,
                        )
                        if slot.arm == ARMS[2]:
                            lex_cache[(slot.task_id, slot.repetition)] = lex_pair
                    else:
                        res, maybe_v2 = execute_vector_or_fusion_slot(
                            case.task, slot, declaration=declaration, pg_handle=pg_handle,
                            study_catalog=holder.cat, arm_profiles=arm_profiles,
                            candidate_pub=stores.candidate_pub, pg_pin=stores.postgres_store.pin,
                            policy=policy, v1_input=v1_input, v1_artifact=v1_artifact,
                            public_text=pub_text, v2_seal=v2_seal, scope=scope,
                            lexical_outcome=lex_cache.get((slot.task_id, slot.repetition)),
                            cached_v2_obs=v2_obs_cache.get((slot.task_id, slot.repetition)),
                            delivery_kind=delivery_kind,
                        )
                        if slot.arm == ARMS[4]:
                            v2_obs_cache[(slot.task_id, slot.repetition)] = maybe_v2
                slot_results.append(res)
                slot_outcomes.append(slot_outcome_row(case, res, live, v1_input, pub_text))
            require_current()
            budget.checkpoint()

            budget.begin_phase("grading_and_report_export", 120)
            sup_report = report_supplement(
                catalog, declaration, slot_results, phase_records=recorder.records,
            )
            slot_outcomes[:] = list(attach_slot_metrics(slot_outcomes, sup_report))
            paired = analyze_slots(
                slot_outcomes, evidence=token_map,
                ordered_task_ids=[t.task_id for t in catalog.tasks],
                cost_summary=sup_report["phase_costs"],
            )
            pending_report = report_payload(
                attempt_id=admission.attempt_id, functional_only=functional_only,
                declaration_source_closure=declaration.to_dict()["source_closure_sha256"],
                evaluator_closure=closure["evaluator_closure_sha256"],
                worker_closure=w_closure.identity, workers=(worker1, worker2),
                observed_slots=len(slot_outcomes), token_records=token_count,
                candidate_bytes=candidate_bytes, byte_forecast=forecast, evidence=evidence,
                supplement_report=sup_report, paired_analysis=paired,
            )
            budget.checkpoint()
        except BaseException as error:
            primary = error
        else:
            primary = None
        primary, cleaned = settle_cleanup(
            primary, lambda: cleanup_study(pg_handle, names, holder.cat), evidence,
        )
        if cleaned is not None:
            cleanup_receipt = dict(cleaned.to_dict())
            pg_relations_clean = cleaned.status == "clean"
        if primary is not None:
            raise primary

    try:
        candidate_bytes = candidate_snapshot_bytes(repo_root, plan["candidate_snapshot"])
        if external_pg is not None:
            _execute_with_pg(external_pg)
        else:
            with disposable_socket_postgres(str(plan["derived"]["runtime_config"])) as (pg_h, lifecycle_info):
                _execute_with_pg(pg_h)
            pg_container_absent = cleanup_container_absent(lifecycle_info, pg_container_absent)
        cleanup_ok = pg_relations_clean and pg_container_absent
        refuse(cleanup_ok, "study_driver:cleanup_not_proven")
        if pending_report is None:
            raise RuntimeError("study_driver:report_missing")
        pending_report["cleanup"] = {
            "relations_clean": pg_relations_clean, "container_absent": pg_container_absent,
            "receipt": cleanup_receipt,
        }
        bundle_bytes = canonical_bytes(pending_report)
        publish_member(report_root, "supplement-study-report-v1.json", bundle_bytes)
        report_sha = digest_bytes(bundle_bytes)
    except BaseException as exc:
        pg_container_absent = cleanup_container_absent(lifecycle_info, pg_container_absent)
        failure_code = error_code(exc, type(exc).__name__)
        receipt_bytes = failure_receipt(
            attempt_id=admission.attempt_id, failure_code=failure_code,
            catalog=catalog, outcomes=slot_outcomes, relations_clean=pg_relations_clean,
            container_absent=pg_container_absent, cleanup_receipt=cleanup_receipt,
            evidence=evidence, evaluator_closure=closure["evaluator_closure_sha256"],
            worker_closure=w_closure.identity,
        )
        if not (report_root / "supplement-study-failure-v1.json").exists():
            publish_member(report_root, "supplement-study-failure-v1.json", receipt_bytes)
        raise

    return SupplementStudyOutcome(
        status="completed",
        attempt_id=admission.attempt_id,
        observed_slots=len(slot_outcomes),
        unstarted_slots=SLOT_COUNT - len(slot_outcomes),
        token_records=token_count,
        cleanup_proven=cleanup_ok,
        report_sha256=report_sha,
        paired_analysis=paired,
        failure_code=failure_code,
        secondary_errors=tuple(evidence.secondary_errors),
    )
__all__ = ["SupplementStudyOutcome", "run_supplement_study_inner"]
