"""Registered inner preparation joined to real SQLite/PostgreSQL pins.

The request exchange and 432-slot callback are deliberately functional-only:
they exercise request, freeze, capture, journal, and report joins without a
worker, tokenizer, native query, or scientific-acceptance claim.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from common_lore_index.retrieval.query_contracts import (
    QueryMode,
    QueryScope,
    unavailable_envelope,
)
from repomap_test_support.postgres_harness import temporary_postgres
from repomap_test_support.retrieval_body_study_pg import (
    BodySocketPostgresHandle, publish_registered_body_stores,
)
from retrieval_body_admission import BodyTaskAdmission
from retrieval_body_pin import RegisteredBodyInputs
from retrieval_body_study_attempt_journal import BodyAttemptJournal
from retrieval_body_study_driver import run_actual_body_study
from retrieval_body_study_inputs import load_body_study_inputs
from retrieval_body_study_prepare import prepare_body_publications, selected_admissions
from retrieval_body_study_queries import require_query_admission_row
from retrieval_body_study_registered_guard import BodyRegisteredDataGuard
from retrieval_body_study_requests import build_body_study_requests
from retrieval_body_study_values import (
    ARM_REPRESENTATIONS,
    COMPARISON_ARMS,
    NATIVE_ARM,
    STUDY_PHASE,
    V2_ARM,
    digest_value,
)
from retrieval_public_text_contracts import PrivateMarkerScope


def _assert_captured_vector_admission(preparation: Any, pin: Any, policy: Any,
                                      scope: PrivateMarkerScope, monkeypatch: Any) -> None:
    from repomap_test_support import w5_public_text_postgres
    from repomap_test_support.retrieval_body_study_slots import _admission_terminal, _vector
    from retrieval_body_study_slots import captured_v2_slot_matches
    from retrieval_body_study_values import V1_ARM
    from retrieval_public_text_request import ARM_PTX
    from retrieval_semantic_experiment_scope import selected_public_binding_ids

    cases = {case.task.task_id: case for case in preparation.catalog.cases}
    rows = [row for row in preparation.matrix["selected_rows"] if row["arm"] == V2_ARM]
    eligible = [row for row in rows if captured_v2_slot_matches(row, cases[row["task_id"]].task, pin)]
    assert len(eligible) == 16
    assert sum(cases[row["task_id"]].kind == "body_positive" for row in eligible) == 12
    assert sum(cases[row["task_id"]].task.grading_rule == "absent_answer" for row in eligible) == 2

    calls: list[tuple[Any, Any]] = []

    def exact_read(_handle: Any, request: Any, actual_pin: Any) -> Any:
        calls.append((request, actual_pin))
        return SimpleNamespace(state="refused", reason="functional_lookup_stop")

    with monkeypatch.context() as patches:
        patches.setattr(w5_public_text_postgres, "exact_read", exact_read)
        catalog = SimpleNamespace(names=SimpleNamespace(batch_id=lambda _generation, _arm: "fixture"))
        options: dict[str, Any] = dict(pg_handle=None, study_catalog=catalog, profiles={ARM_PTX: None},
                                       candidate_pub=None, pg_pin=pin, v1_input=None, v1_artifact=None,
                                       public_text=None, scope=scope)
        for row in eligible:
            task = cases[row["task_id"]].task
            assert _admission_terminal(task, pin=pin, policy=policy) is None
            records = (SimpleNamespace(record_id="query:" + task.task_id, vector=(1.0,) * 384),)
            options["v2_seal"] = SimpleNamespace(records=records)
            result, observed = _vector(row, task, policy=policy, **options)
            assert result["refusal_code"] == "functional_lookup_stop" and observed is None
            assert calls[-1][0].source_binding_id == selected_public_binding_ids(task)[0]
            assert calls[-1][1] is pin

        row = eligible[-1]
        task = cases[row["task_id"]].task
        binding = selected_public_binding_ids(task)[0]
        missing = replace(policy, bindings={key: value for key, value in policy.bindings.items() if key != binding})
        denied = replace(policy, bindings={**policy.bindings, binding:
                         replace(policy.bindings[binding], resolved_evidence_retention="no-content")})
        for altered in (missing, denied):
            result, _ = _vector(row, task, policy=altered, **options)
            assert result["execution_state"] == "refused"
        for changed in ({"arm": V1_ARM}, {"preparation": "unavailable"}, {"task_id": "other"},
                        {"representation_identity": "canonical-node-v1"},
                        {"publication_identity": {**row["publication_identity"], "run_id": -1}}):
            result, _ = _vector({**row, **changed}, task, policy=policy, **options)
            assert result["unavailable_reason"] == "metadata_representation_not_comparable"
        unavailable, _ = _vector(row, replace(task, requested_mode="literal_captured_text"),
                                 policy=policy, **options)
        assert unavailable["unavailable_reason"] == "metadata_representation_not_comparable"
        options["v2_seal"] = SimpleNamespace(records=())
        missing_vector, _ = _vector(row, task, policy=policy, **options)
        assert missing_vector["unavailable_reason"] == "body_query_vector_missing"
        metadata = next(item for item in rows if item["preparation"] == "ready"
                        and cases[item["task_id"]].task.comparability_stratum == "metadata")
        metadata_v1, _ = _vector({**metadata, "arm": V1_ARM}, cases[metadata["task_id"]].task,
                                 policy=policy, **options)
        assert metadata_v1["refusal_code"] == "v1_own_record_truncated"
        assert len(calls) == 16


def _functional_response(request: Any, preparation: Any) -> Mapping[str, Any]:
    tokens = []
    for record in request.records:
        mode = getattr(record.mode, "value", str(record.mode))
        tokens.append({
            "record_id": record.record_id,
            "mode": mode,
            "token_count": 1,
            "admitted": True,
            "captured_text_reachable": True,
            "truncated": False,
            "representation_identity": request.representation,
        })
    eligibility = []
    if request.ordinal == 2:
        for case in preparation.catalog.cases:
            if case.kind != "body_positive":
                continue
            for arm in COMPARISON_ARMS:
                eligibility.append({
                    "task_id": case.task.task_id,
                    "arm": arm,
                    "admitted": True,
                    "captured_text_reachable": True,
                    "token_count": 1,
                    "truncated": False,
                    "representation_identity": ARM_REPRESENTATIONS[arm],
                })
    identity = request.identity
    return {
        "phase": STUDY_PHASE,
        "representation": request.representation,
        "request_identity": identity,
        "status": "completed",
        "response_identity": digest_value({"request": identity, "functional": True}),
        "admission_identity": digest_value({"request": identity, "admission": "functional"}),
        "reachability_identity": digest_value({"request": identity, "reachability": "functional"}),
        "token_receipts": tokens,
        "eligibility_receipts": eligibility,
    }


def _unavailable_slot(
    row: Mapping[str, Any], case: Any, *, sqlite_pin: Any, postgres_pin: Any,
) -> Mapping[str, Any]:
    task = case.task
    pin = sqlite_pin if row["arm"] == NATIVE_ARM else postgres_pin
    scope = QueryScope(
        graph_id=task.graph_id,
        backend=pin.backend,
        publication_bundle_id=pin.publication_bundle_id,
        snapshot_manifest_id=pin.snapshot_manifest_id,
        selected_binding_ids=tuple(task.selected_bindings),
        source_path_prefix=task.source_path_prefix,
    )
    reason = "functional_only_no_worker"
    envelope = unavailable_envelope(
        requested_mode=QueryMode(task.requested_mode), scope=scope, reason=reason,
    )
    return {
        "execution_state": "unavailable",
        "envelope": envelope,
        "snapshot_hits": (),
        "unavailable_reason": reason,
    }


def _assert_owned_preparation_handshake(
    requests: Any, root: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from repomap_test_support.retrieval_body_study_host_cases import body_plan
    import retrieval_body_study_exchange as exchange
    from retrieval_body_study_epoch import (
        await_acknowledgement, prepare_epoch, publish_acknowledgement,
    )
    from retrieval_body_study_contracts import BODY_ADMISSION_SCHEMA
    from retrieval_body_study_plan import plan_digest, project_inner_body_plan
    from retrieval_body_study_worker_closure import fresh_worker_closure

    plan = body_plan()
    plan["attempt_id"] = requests.v1.study_attempt_id
    admission = {"schema": BODY_ADMISSION_SCHEMA, "plan_sha256": plan_digest(plan),
        "reservation_sha256": digest_value({"fixture": "epoch"}),
        **{key: plan[key] for key in ("attempt_id", "source_closure_sha256",
            "treatment_sha256", "protocol_sha256", "operator_decision_sha256")}}
    inner_path = root / "fixture-inner-plan.json"
    inner_path.write_text(json.dumps(project_inner_body_plan(plan, admission=admission)))
    inner_path.chmod(0o600)
    epochs = root / "epochs"
    acknowledgements: list[int] = []

    def prepare(**fields: Any) -> Any:
        return prepare_epoch(root=epochs, **fields)

    def acknowledge(epoch: Any, **fields: Any) -> None:
        publish_acknowledgement(epoch, root=epochs, charge=lambda _bytes: None)
        await_acknowledgement(epoch, root=epochs, **fields)
        acknowledgements.append(epoch.ordinal)

    publications: list[str] = []

    def stop_before_readiness(*, attempt_id: str, **_fields: Any) -> Any:
        ordinal = len(publications) + 1
        assert acknowledgements == list(range(1, ordinal + 1))
        assert attempt_id == f"{plan['attempt_id']}-v{ordinal}"
        publications.append(attempt_id)
        raise RuntimeError("fixture stops before worker readiness")

    monkeypatch.setattr(exchange, "INNER_PLAN", str(inner_path))
    monkeypatch.setattr(exchange, "prepare_epoch", prepare)
    monkeypatch.setattr(exchange, "await_acknowledgement", acknowledge)
    monkeypatch.setattr(exchange.ComponentExchange, "publish_request", stop_before_readiness)
    worker_digests = fresh_worker_closure(repo_root)["digests"]
    for request in requests.ordered():
        with pytest.raises(exchange.BodyExchangeError, match="request_publish"):
            exchange.run_exchange_epoch(
                {**dict(request.descriptor), "wire_request": dict(request.wire)}, request.raw,
                worker_digests=worker_digests, delivery=None, representation=request.representation,
                evidence_root=root, evidence=None, require_current=lambda: None,
            )
    assert len(publications) == 2


def test_registered_preparation_guard_and_functional_driver_stay_inert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real publication pins do not turn functional receipts into origin proof."""
    repo_root = Path(__file__).resolve().parents[5]
    frame = load_body_study_inputs(
        repo_root / "src/test/fixtures/retrieval_evaluation/body-v2",
        predecessor_root=repo_root / "src/test/fixtures/retrieval_evaluation",
        validate=True,
    )
    inputs = frame.inputs
    assert RegisteredBodyInputs.is_factory_issued(inputs)
    current = frame.require_current()
    guard = BodyRegisteredDataGuard.from_registered_inputs(inputs)
    guard.require_current(frame.require_current)
    semantic = guard.require_semantic_snapshot(frame.require_current)
    assert semantic.pin_sha256 == current.pin_sha256
    assert guard.input_pin_sha256 == current.pin_sha256

    copied_active = tmp_path / "copied-active"
    shutil.copytree(inputs.root, copied_active)
    with pytest.raises(ValueError, match="body.guard:input_roots"):
        replace(guard, active_root=copied_active)
    alias_active = tmp_path / "active-alias"
    alias_active.symlink_to(inputs.root, target_is_directory=True)
    with pytest.raises(ValueError, match="body.guard:input_roots"):
        replace(guard, active_root=alias_active)

    with temporary_postgres() as database:
        stores = publish_registered_body_stores(
            tmp_path / "registered-stores",
            inputs=inputs,
            policy=current.policy,
            postgres=BodySocketPostgresHandle(
                database, "functional-unattested-container", database.psql_command),
        )
        assert stores.ops_config.postgres.host == database.host
        assert stores.ops_config.runtime.postgres.bind_host == "127.0.0.1"
        sqlite_pin, postgres_pin = stores.sqlite_store.pin, stores.postgres_store.pin
        assert sqlite_pin.backend == "sqlite"
        assert postgres_pin.backend == "postgresql"
        assert sqlite_pin.is_factory_issued() and postgres_pin.is_factory_issued()
        preparation = prepare_body_publications(
            inputs,
            sqlite_pin=sqlite_pin,
            postgres_pin=postgres_pin,
            policy=current.policy,
            frozen_policy=current.policy,
            validate=True,
        )
        assert preparation.matrix["selected_row_count"] == 144
        assert preparation.matrix["planned_slot_count"] == 432
        assert preparation.matrix["functional_only"] is False
        assert preparation.matrix["origin_proven"] is False
        assert preparation.matrix["execution_authority_granted"] is False
        admissions = selected_admissions(preparation, V2_ARM)
        assert len(admissions) == 24
        assert all(BodyTaskAdmission.is_factory_issued(item) for item in admissions)

        scope = PrivateMarkerScope.create((str(tmp_path),))
        _assert_captured_vector_admission(preparation, postgres_pin, current.policy, scope, monkeypatch)
        from retrieval_body_study_model_contract import ARTIFACT_PROFILE_ID
        requests = build_body_study_requests(
            preparation,
            postgres_store=stores.postgres_store,
            scope=scope,
            attempt_id="body-study-registered-functional",
            artifact_profile_identity=ARTIFACT_PROFILE_ID,
            validate=True,
        )
        _assert_owned_preparation_handshake(requests, tmp_path, repo_root, monkeypatch)
        matrix = {(row["task_id"], row["arm"]): row for row in preparation.matrix["selected_rows"]}
        request_rows = requests.v2.actual_query_rows()
        assert len(request_rows) == 24
        for row in request_rows:
            selected = matrix[(row["task_id"], row["arm"])]
            assert "policy_digest" in row and "policy_digest" not in selected
            require_query_admission_row(selected, row)
        functional_pin = digest_value({"functional": "registered-prep-only"})
        journal = BodyAttemptJournal.create(
            tmp_path / "functional-journal",
            identity={
                "attempt_id": "body-study-registered-functional",
                "input_pin_sha256": functional_pin,
                "source_closure_sha256": digest_value({"source": "functional"}),
                "treatment_sha256": digest_value({"treatment": "functional"}),
                "protocol_sha256": digest_value({"protocol": "functional"}),
                "candidate_snapshot_sha256": digest_value({"candidate": "functional"}),
                "plan_sha256": digest_value({"plan": "functional"}),
            },
            task_ids=tuple(case.task.task_id for case in preparation.catalog.cases),
            functional_only=True,
        )
        cleanup = {
            # This owner creates no worker evidence or study relations; the
            # enclosing temporary database owns disposal of its publication.
            "cleanup_ok": True,
            "ownership_ok": True,
            "integrity_ok": True,
            "input_pin_sha256": functional_pin,
        }
        outcome = run_actual_body_study(
            preparation=preparation,
            requests=requests,
            exchange=lambda request: _functional_response(request, preparation),
            pins={"sqlite": sqlite_pin, "postgresql": postgres_pin},
            execute_slot=lambda row, case: _unavailable_slot(
                row, case, sqlite_pin=sqlite_pin, postgres_pin=postgres_pin,
            ),
            journal=journal,
            cleanup=cleanup,
            report_guard=lambda: guard.require_current(frame.require_current),
        )

        assert outcome.status == "functional"
        assert outcome.token_records == 134
        assert outcome.observed_slots == 432
        assert outcome.unstarted_slots == 0
        assert outcome.report["functional_only"] is True
        assert outcome.report["origin_proven"] is False
        assert outcome.report["scientific_acceptance"] is False
        assert outcome.report["scientific_acceptance_claimed"] is False
        assert outcome.report["inert_preparation"]["matrix"]["sqlite_report"] == preparation.sqlite_report
        assert outcome.report["inert_preparation"]["matrix"]["postgres_report"] == preparation.postgres_report
        assert outcome.report["inert_preparation"]["origin_proven"] is False
        assert outcome.report["inert_preparation"]["execution_authority_granted"] is False
        assert all(row["execution_state"] == "unavailable" for row in outcome.report["slots"])
        assert outcome.journal_receipt["functional_only"] is True
        assert outcome.journal_receipt["origin_proven"] is False
        assert outcome.journal_receipt["execution_authority_granted"] is False
