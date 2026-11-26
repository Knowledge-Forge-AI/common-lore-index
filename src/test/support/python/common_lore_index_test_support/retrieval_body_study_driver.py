from __future__ import annotations
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from retrieval_body_study_driver import BodyStudyOutcome, run_actual_body_study
from retrieval_body_study_prepare import BodyPublicationPreparation
from retrieval_body_study_preflight import WorkerEpochReceipt, receipt_from_verified_epoch
from retrieval_body_study_requests import BodyStudyRequest, BodyStudyRequests
from retrieval_body_study_values import STUDY_PHASE, require
from common_lore_index_test_support.retrieval_body_study_snapshot_driver import current_snapshot_counter, make_pg_byte_charger, wrap_authenticated_body_study
INNER_GUARD_LIMIT = 512
INNER_SOURCE_READ_BYTES = 12 * 1024**3

def _source_budget(guard: Any, closure: Mapping[str, Any], guard_count: int) -> dict[str, Any]:
    source = guard_count * int(closure["regular_file_bytes"]); active = guard_count * int(guard.active_file_bytes)
    checkpoint = current_snapshot_counter().checkpoint()
    snapshots = checkpoint.reserved_count * int(guard.registered_snapshot_file_bytes); total = source + snapshots
    require(total <= INNER_SOURCE_READ_BYTES, "body.inner:source_byte_budget")
    return {"source_guard_read_bytes": source, "active_guard_read_bytes": active, "active_guard_read_bytes_kind": "informational_subset", "registered_snapshot_count": checkpoint.observed_count, "registered_snapshot_count_reserved": checkpoint.reserved_count, "registered_snapshot_count_kind": "measured_interim_reserved_budget", "registered_snapshot_file_bytes": int(guard.registered_snapshot_file_bytes), "registered_snapshot_read_bytes": snapshots, "registered_snapshot_read_bytes_kind": "reserved_upper_bound", "source_guard_total_read_bytes": total, "source_guard_total_read_bytes_kind": "source_plus_snapshot_reserved_upper_bound", "source_guard_byte_limit": INNER_SOURCE_READ_BYTES}
def _require_preparation_against_inputs(preparation: Any, current: Any) -> dict[str, str]:
    from retrieval_body_catalog import BodyCatalog
    from retrieval_body_study_join import prepare_bound_body_study
    from retrieval_body_study_values import REGISTERED_INPUT_PIN_SHA256, SLOT_COUNT, digest_value
    require(preparation.phase == STUDY_PHASE, "body.inner:preparation_phase")
    require(current.pin_sha256 == REGISTERED_INPUT_PIN_SHA256, "body.inner:input_pin")
    require(current.policy.to_dict() == preparation.policy.to_dict(), "body.inner:policy")
    from retrieval_body_pin import RegisteredBodyInputs
    require(RegisteredBodyInputs.is_factory_issued(preparation.inputs),
            "body.inner:preparation_input")
    require(BodyCatalog.is_factory_issued(preparation.catalog), "body.inner:catalog_origin")
    require(current.catalog.digest == preparation.catalog.digest, "body.inner:catalog_drift")
    for pin, backend in ((preparation.sqlite_pin, "sqlite"),
                         (preparation.postgres_pin, "postgresql")):
        require(pin.is_factory_issued() and pin.backend == backend, "body.inner:publication_pin")
        pin.revalidate_current()
    require(preparation.sqlite_pin.generation == preparation.postgres_pin.generation,
            "body.inner:generation")
    require(preparation.sqlite_pin.snapshot_vector == preparation.postgres_pin.snapshot_vector,
            "body.inner:snapshots")
    require(preparation.matrix.get("planned_slot_count") == SLOT_COUNT, "body.inner:slot_count")
    rebuilt = prepare_bound_body_study(
        current.catalog,
        sqlite_decisions=preparation.sqlite_decisions,
        postgres_decisions=preparation.postgres_decisions,
        sqlite_pin=preparation.sqlite_pin,
        postgres_pin=preparation.postgres_pin,
        policy=preparation.policy,
        frozen_policy=preparation.frozen_policy,
    )
    require(digest_value(rebuilt) == digest_value(preparation.matrix), "body.inner:matrix_drift")
    return {
        "input_pin_sha256": current.pin_sha256,
        "preparation_identity": digest_value({
            "phase": preparation.phase,
            "catalog_sha256": preparation.catalog.digest,
            "input_pin_sha256": current.pin_sha256,
            "sqlite_pin": preparation.sqlite_pin.to_dict(),
            "postgres_pin": preparation.postgres_pin.to_dict(),
            "matrix": preparation.matrix.get("selected_rows"),
        }),
    }
def run_body_study_inner(
    *,
    preparation: BodyPublicationPreparation,
    requests: BodyStudyRequests,
    worker_exchange: Callable[[BodyStudyRequest], object],
    slot_executor: Callable[[Mapping[str, Any], Any], Mapping[str, Any]],
    journal: Any,
    sqlite_pin: Any,
    postgres_pin: Any,
    cleanup: Mapping[str, Any],
    private_markers: Sequence[str] = (),
) -> BodyStudyOutcome:
    require(preparation.phase == STUDY_PHASE, "body.inner:phase")
    require(preparation.sqlite_pin is sqlite_pin and preparation.postgres_pin is postgres_pin,
            "body.inner:publication_pin_join")
    return run_actual_body_study(
        preparation=preparation,
        requests=requests,
        exchange=worker_exchange,
        pins={"sqlite": sqlite_pin, "postgresql": postgres_pin},
        execute_slot=slot_executor,
        journal=journal,
        cleanup=cleanup,
        private_markers=private_markers,
    )
def exchange_from_setup(
    request: BodyStudyRequest,
    *,
    worker_digests: dict[str, str],
    delivery: Any,
    evidence_root: Path,
    evidence: Any,
    require_current: Callable[[], None],
) -> object:
    from common_lore_index_test_support.retrieval_body_study_setup import run_exchange_epoch
    descriptor = dict(request.descriptor)
    descriptor["wire_request"] = dict(request.wire)
    return run_exchange_epoch(
        descriptor, request.raw, worker_digests=worker_digests,
        delivery=delivery, representation=request.representation,
        evidence_root=evidence_root, evidence=evidence,
        require_current=require_current,
    )
def run_authenticated_body_study(admission: Any) -> BodyStudyOutcome:
    from runner_pgvector_prebuilt import INNER_PLAN
    from retrieval_body_study_closure import build_closure
    from retrieval_body_study_worker_closure import fresh_worker_closure
    from retrieval_body_study_inputs import load_body_study_inputs
    from retrieval_body_study_registered_guard import BodyRegisteredDataGuard
    from retrieval_body_study_plan import BodyStudyAdmission, admission_from_inner_plan, plan_digest, read_inner_body_plan, require_admission_current
    from retrieval_body_study_inner_reads import read_receipt, reserve_inner_source_verification, read_inner_source_closure_receipt, reserve_inner_source_closure_guard
    from common_lore_index_test_support.retrieval_body_study_pg import disposable_socket_postgres, publish_registered_body_stores
    from common_lore_index_test_support.retrieval_body_study_pg_lifecycle import MAX_BODY_LIFECYCLE_RESERVED_BYTES
    from common_lore_index_test_support.retrieval_body_study_requests import build_body_study_requests, seal_body_v2_worker_response
    from common_lore_index_test_support.retrieval_body_study_slots import import_body_vector_batches, make_actual_body_slot_executor
    from retrieval_public_text_contracts import PrivateMarkerScope
    from retrieval_semantic_generator_output import seal_generation_result
    require(type(admission) is BodyStudyAdmission, "body.inner:admission_type")
    plan_path = Path(INNER_PLAN)
    plan = read_inner_body_plan(plan_path)
    expected = admission_from_inner_plan(plan); require(type(admission) is type(expected), "body.inner:admission_type")
    require(admission.to_dict() == expected.to_dict(), "body.inner:admission_join")
    require_admission_current(admission, plan)
    repo_root = Path("/workspace")
    workspace = Path(plan["inner"]["scratch_dir"])
    report_root = Path(plan["inner"]["report_dir"]).parent
    from retrieval_body_study_contracts import INNER_BODY_REPORT_DIR
    require(report_root == Path(INNER_BODY_REPORT_DIR).parent and workspace.name == "pgvector", "body.inner:path_join")
    workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_receipt = reserve_inner_source_verification(plan, repo_root, ledger_root=workspace)
    from offline_qualification_checkout import verify_checkout
    verify_checkout(repo_root, dict(plan["candidate_snapshot"]), plan["candidate_head"])
    frame = load_body_study_inputs(
        repo_root / "src/test/fixtures/retrieval_evaluation/body-v2",
        predecessor_root=repo_root / "src/test/fixtures/retrieval_evaluation",
        validate=False,
    )
    registered_guard = BodyRegisteredDataGuard.from_registered_inputs(frame.inputs)
    closure_receipt = reserve_inner_source_closure_guard(
        plan, repo_root, ledger_root=workspace,
    )
    source = build_closure(repo_root, plan["source_members"], name="body-source")
    treatment = build_closure(repo_root, plan["treatment_members"], name="body-treatment")
    worker = fresh_worker_closure(repo_root)
    require(source["identity"] == plan["source_closure_sha256"], "body.inner:source_closure")
    require(treatment["identity"] == plan["treatment_sha256"], "body.inner:treatment_closure")
    require(worker["identity"] == plan["worker_closure_sha256"], "body.inner:worker_closure")
    source_receipt = read_receipt(plan, ledger_root=workspace, receipt=source_receipt)
    closure_receipt = read_inner_source_closure_receipt(
        plan, ledger_root=workspace, receipt=closure_receipt,
    )
    preparation: Any = None
    stores: Any = None
    final_source_exported = False
    inner_guard_calls = 1
    guard_state: dict[str, Any] = {
        "closure_receipt": closure_receipt, "guarded": False,
    }
    def require_current(*, semantic: bool = False) -> Any:
        nonlocal inner_guard_calls
        closure_receipt = reserve_inner_source_closure_guard(
            plan, repo_root, ledger_root=workspace,
        )
        current = read_inner_body_plan(plan_path)
        require(plan_digest(current) == plan_digest(plan), "body.inner:plan_drift")
        require_admission_current(admission, current)
        inner_guard_calls += 1
        require(inner_guard_calls <= INNER_GUARD_LIMIT, "body.inner:source_guard_budget")
        fresh: dict[str, Any] = {}
        def verify_code() -> None:
            fresh["source"], fresh["treatment"] = (build_closure(repo_root, current["source_members"], name="body-source"), build_closure(repo_root, current["treatment_members"], name="body-treatment"))
            fresh["worker"] = fresh_worker_closure(repo_root)
            require(fresh["source"]["identity"] == source["identity"], "body.inner:source_drift")
            require(fresh["treatment"]["identity"] == treatment["identity"], "body.inner:treatment_drift")
            require(fresh["worker"]["identity"] == worker["identity"], "body.inner:worker_drift")
        if semantic:
            current_inputs = registered_guard.require_semantic_snapshot(verify_code)
            bound: dict[str, str] = {"input_pin_sha256": current_inputs.pin_sha256}
            guard_state["current_inputs"] = current_inputs
        else:
            registered_guard.require_current(verify_code)
            bound = {"input_pin_sha256": registered_guard.input_pin_sha256}
        if preparation is not None and semantic:
            bound = _require_preparation_against_inputs(preparation, current_inputs)
        elif preparation is not None:
            require(registered_guard.catalog_sha256 == preparation.catalog.digest,
                    "body.inner:catalog_drift")
            require(registered_guard.policy_sha256 == preparation.policy.policy_digest(),
                    "body.inner:policy_drift")
        if stores is not None:
            stores.sqlite_store.pin.revalidate_current()
            stores.postgres_store.pin.revalidate_current()
        closure_receipt = read_inner_source_closure_receipt(
            current, ledger_root=workspace, receipt=closure_receipt,
        )
        guard_state.update(closure_receipt=closure_receipt, guarded=True,
                           worker_digests=dict(fresh["worker"]["digests"]), **bound)
        return guard_state.get("current_inputs")
    require_current()
    cleanup: dict[str, Any] = {
        "plan_sha256": plan["host_plan_sha256"],
        "evaluator_closure_sha256": plan["evaluator_closure_sha256"], "protocol_sha256": plan["protocol_sha256"], "operator_decision_sha256": plan["operator_decision_sha256"],
        "source_closure_sha256": source["identity"], "treatment_sha256": treatment["identity"], "worker_closure_sha256": worker["identity"],
        "input_pin_sha256": guard_state["input_pin_sha256"],
        "registered_data_guard_identity": registered_guard.identity, "active_guard_bytes_per_check": registered_guard.active_file_bytes,
        "source_read_receipt": source_receipt,
        "source_guard_receipt": closure_receipt,
        "relations_clean": False, "container_absent": False,
        "ownership_ok": None, "integrity_ok": True,
    }
    pg_byte_charger, pg_auxiliary = make_pg_byte_charger(MAX_BODY_LIFECYCLE_RESERVED_BYTES)
    cleanup.update({"pg_auxiliary_read_bytes_limit": pg_auxiliary["limit"], "pg_auxiliary_read_bytes_reserved": 0, "pg_auxiliary_read_bytes_match": False, "pg_auxiliary_read_bytes_kind": "body_worker_pre_reserved"})
    pg_context = disposable_socket_postgres(
        str(plan["derived"]["runtime_config"]), scratch_root=workspace,
        byte_charger=pg_byte_charger,
    )
    pg_handle: Any = None; pg_lifecycle: dict[str, Any] = {}
    pg_closed = False
    study_catalog: Any = None; study_names: Any = None
    profiles: Any = None; pg_entered = False
    finalize_cleanup_fn: Callable[[], None] | None = None
    try:
        pg_handle, pg_lifecycle = pg_context.__enter__()
        pg_entered = True
        require_current(semantic=True)
        store_inputs = guard_state["current_inputs"]
        stores = publish_registered_body_stores(
            workspace / "body-study-stores", inputs=frame.inputs, policy=store_inputs.policy,
            postgres=pg_handle,
        )
        scope = PrivateMarkerScope.create((str(workspace), str(report_root)))
        require_current(semantic=True)
        prep_inputs = guard_state["current_inputs"]
        from retrieval_body_study_prepare import prepare_body_publications
        preparation = prepare_body_publications(
            frame.inputs, sqlite_pin=stores.sqlite_store.pin, postgres_pin=stores.postgres_store.pin,
            policy=prep_inputs.policy, frozen_policy=prep_inputs.policy, validate=False,
        )
        require(tuple((row["task_id"], row["arm"], repetition, row["generation"]) for row in preparation.matrix["selected_rows"] for repetition in (1, 2, 3)) == tuple((row["task_id"], row["arm"], row["repetition"], row["generation"]) for row in plan["declaration"]["slot_manifest"]), "body.inner:slot_declaration")
        require_current(semantic=True)
        cleanup.update({key: guard_state[key] for key in ("input_pin_sha256", "preparation_identity")})
        requests = build_body_study_requests(
            preparation, postgres_store=stores.postgres_store, scope=scope,
            attempt_id=admission.attempt_id,
            artifact_profile_identity=plan["artifact_source_identity_sha256"],
            validate=False,
            input_pin_sha256=guard_state["input_pin_sha256"],
        )
        require_current()
        cleanup.update({key: guard_state[key] for key in ("input_pin_sha256", "preparation_identity")})
        from retrieval_body_study_attempt_journal import BodyAttemptJournal
        identity = {
            "attempt_id": admission.attempt_id,
            "input_pin_sha256": guard_state["input_pin_sha256"],
            "source_closure_sha256": source["identity"],
            "treatment_sha256": treatment["identity"],
            "protocol_sha256": plan["protocol_sha256"],
            "candidate_snapshot_sha256": plan["candidate_snapshot_sha256"],
            "plan_sha256": plan["host_plan_sha256"],
        }
        journal = BodyAttemptJournal.create(
            report_root / "attempt-journal", identity=identity,
            task_ids=tuple(case.task.task_id for case in preparation.catalog.cases),
        )
        from retrieval_semantic_generator_inputs import prepare_public_generation_input
        v1_input = prepare_public_generation_input(
            stores.postgres_store.representations(),
            tuple(case.task for case in preparation.catalog.cases),
            read_current=lambda: stores.postgres_store.pin.revalidate_current(),
            read_representations=stores.postgres_store.representations,
        )
        public_text = stores.postgres_store.public_text(scope)
        v1_artifact: dict[str, Any] = {}
        v2_seal: dict[str, Any] = {}
        executor: dict[str, Any] = {}
        def exchange(request: BodyStudyRequest) -> object:
            require_current()
            return exchange_from_setup(
                request, worker_digests=dict(guard_state["worker_digests"]),
                delivery=None, evidence_root=report_root, evidence=None,
                require_current=require_current,
            )
        def adapt(request: BodyStudyRequest, evidence: object, prepared: Any) -> WorkerEpochReceipt:
            nonlocal study_catalog, profiles, study_names
            from retrieval_semantic_generator_output_receipt import VerifiedWorkerResponse
            require_current()
            receipt = receipt_from_verified_epoch(request, evidence, preparation=prepared)
            response = getattr(evidence, "response", None)
            require(type(response) is VerifiedWorkerResponse, "body.inner:verified_response_type")
            assert isinstance(response, VerifiedWorkerResponse)
            if request.ordinal == 1:
                v1_artifact["value"] = seal_generation_result(v1_input, response)
            else:
                v2_seal["value"] = seal_body_v2_worker_response(
                    request, response, preparation=prepared, public_text=public_text,
                    fresh_public_text=lambda: stores.postgres_store.public_text(scope), scope=scope,
                )
                study_catalog, profiles, study_names = import_body_vector_batches(
                    pg_handle, attempt_id=admission.attempt_id,
                    artifact_identity=plan["artifact_source_identity_sha256"],
                    v1_artifact=v1_artifact["value"], v2_seal=v2_seal["value"],
                    public_text=public_text, pg_pin=stores.postgres_store.pin,
                    candidate_pub=stores.candidate_pub,
                )
                executor["value"] = make_actual_body_slot_executor(
                    preparation=prepared, stores=stores, pg_handle=pg_handle, policy=preparation.policy,
                    scope=scope, v1_input=v1_input, v1_artifact=v1_artifact["value"],
                    public_text=public_text, v2_seal=v2_seal["value"],
                    study_catalog=study_catalog, profiles=profiles, candidate_pub=stores.candidate_pub,
                )
            return receipt
        def slot_executor(row: Mapping[str, Any], case: Any) -> Mapping[str, Any]:
            require("value" in executor, "body.inner:slot_executor_unbound")
            return executor["value"](row, case)
        def finalize_cleanup() -> None:
            nonlocal final_source_exported, pg_closed, source_receipt
            primary: BaseException | None = None
            try:
                if not final_source_exported:
                    if not guard_state["guarded"]:
                        require_current()
                    source_receipt = read_receipt(
                        plan, ledger_root=workspace, receipt=source_receipt,
                    )
                    closure_receipt = read_inner_source_closure_receipt(
                        plan, ledger_root=workspace,
                        receipt=guard_state["closure_receipt"],
                    )
                    cleanup["source_read_receipt"] = source_receipt
                    cleanup["source_guard_receipt"] = closure_receipt
                    cleanup["source_guard_count"] = inner_guard_calls
                    cleanup["source_read_count"] = int(source_receipt["consumed_reads"])
                    cleanup["source_guard_limit"] = INNER_GUARD_LIMIT
                    budget_values = _source_budget(registered_guard, closure_receipt,
                                                   inner_guard_calls)
                    cleanup.update(budget_values)
                    budget = {"schema": "cloe-body-study-source-read-budget-v1",
                              "source_read_receipt": source_receipt,
                              "source_guard_receipt": closure_receipt,
                              "source_guard_count": inner_guard_calls,
                              "source_guard_limit": INNER_GUARD_LIMIT, **budget_values}
                    cleanup["source_read_budget_pending"] = budget
                    final_source_exported = True
            except BaseException as error:
                primary = error
                cleanup["integrity_ok"] = False
            try:
                if study_catalog is not None and not cleanup["relations_clean"]:
                    from common_lore_index_test_support.w5_public_text_postgres import cleanup_study
                    cleaned = cleanup_study(pg_handle, study_names, study_catalog)
                    cleanup["relations_clean"] = getattr(cleaned, "status", None) == "clean"
                    cleanup["study_cleanup"] = cleaned.to_dict() if hasattr(cleaned, "to_dict") else str(cleaned)
                elif study_catalog is None:
                    cleanup["relations_clean"] = True
            except BaseException as error:
                if primary is None:
                    primary = error
            finally:
                try:
                    if not pg_closed:
                        pg_context.__exit__(None, None, None)
                        pg_closed = True
                except BaseException as error:
                    if primary is None:
                        primary = error
                relations_clean = cleanup["relations_clean"]; cleanup.update(pg_lifecycle); cleanup["relations_clean"] = relations_clean
                cleanup["pg_auxiliary_read_bytes_reserved"] = pg_auxiliary["reserved"]; cleanup["pg_auxiliary_read_bytes_match"] = type(pg_lifecycle.get("command_reserved_bytes")) is int and pg_lifecycle["command_reserved_bytes"] == pg_auxiliary["reserved"]; cleanup["integrity_ok"] = cleanup["integrity_ok"] and cleanup["pg_auxiliary_read_bytes_match"]; primary = RuntimeError("body.inner:pg_budget_join") if not cleanup["pg_auxiliary_read_bytes_match"] and primary is None else primary
                cleanup["container_absent"] = bool(pg_lifecycle.get("container_absent"))
                cleanup["ownership_ok"] = cleanup["relations_clean"] and cleanup["container_absent"]
                cleanup["cleanup_ok"] = cleanup["relations_clean"] and cleanup["container_absent"]
            if primary is not None:
                raise primary
        finalize_cleanup_fn = finalize_cleanup
        cleanup["_finalize_cleanup"] = finalize_cleanup
        outcome = run_actual_body_study(
            preparation=preparation, requests=requests, exchange=exchange,
            pins={"sqlite": stores.sqlite_store.pin, "postgresql": stores.postgres_store.pin},
            execute_slot=slot_executor, journal=journal, cleanup=cleanup,
            private_markers=scope.markers, response_adapter=adapt,
            source_guard=require_current,
            report_guard=lambda: require_current(semantic=True),
        )
        require(outcome.cleanup_proven is True, "body.inner:cleanup_not_proven")
        from retrieval_body_study_report_helpers import write_body_report
        report_sha = write_body_report(report_root, outcome.report)
        require(report_sha == outcome.report_sha256, "body.inner:report_identity")
        return outcome
    finally:
        cleanup["pg_auxiliary_read_bytes_reserved"] = pg_auxiliary["reserved"]; cleanup["pg_auxiliary_read_bytes_match"] = type(pg_lifecycle.get("command_reserved_bytes")) is int and pg_lifecycle["command_reserved_bytes"] == pg_auxiliary["reserved"]; cleanup["integrity_ok"] = cleanup["integrity_ok"] and cleanup["pg_auxiliary_read_bytes_match"]
        if finalize_cleanup_fn is not None:
            finalize_cleanup_fn()
        elif pg_entered and not pg_closed:
            try:
                pg_context.__exit__(None, None, None)
            finally:
                pg_closed = True
run_authenticated_body_study = wrap_authenticated_body_study(run_authenticated_body_study)
__all__ = ["BodyStudyOutcome", "exchange_from_setup", "run_authenticated_body_study", "run_body_study_inner"]
