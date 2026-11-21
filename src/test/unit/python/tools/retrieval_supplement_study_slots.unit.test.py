"""Synthetic unit coverage for per-slot vector and hybrid prerequisites."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from repomap_test_support import retrieval_supplement_study_slots as slots
from retrieval_evaluation_grading import ExecutionState
from retrieval_public_text_request import ARM_PTX, ARM_V1
from retrieval_supplement_declaration import SlotIdentity
from retrieval_supplement_report import SlotResult
from retrieval_supplement_study_contracts import StudyRefusal
from retrieval_supplement_values import ARMS


def _stub_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def make_unadmitted(_task: Any, arm_id: str, repetition: int,
                        state: ExecutionState, reason: str) -> dict[str, str]:
        calls.append((arm_id, repetition, state, reason))
        return {"state": state.value, "reason": reason}

    monkeypatch.setattr(slots, "make_unadmitted_operation", make_unadmitted)
    monkeypatch.setattr(
        slots, "bind_operation_capture",
        lambda *_args, **_kwargs: SimpleNamespace(raw=b"capture"),
    )
    monkeypatch.setattr(
        slots, "execute_after_admission",
        lambda *_args, **_kwargs: (SimpleNamespace(), True),
    )
    monkeypatch.setattr(slots, "public_text_applicability", lambda _task: None)
    monkeypatch.setattr(slots, "selected_public_binding_ids", lambda _task: ("bind1:test",))
    return calls


def _execute(
    monkeypatch: pytest.MonkeyPatch,
    *,
    arm: str,
    lexical_outcome: tuple[ExecutionState, object] | None = None,
    cached_v2_obs: object | None = None,
) -> tuple[list[tuple[Any, ...]], SlotResult, object | None]:
    calls = _stub_dispatch(monkeypatch)
    task = SimpleNamespace(task_id="task-1")
    slot = SlotIdentity(task.task_id, arm, 1)
    names = SimpleNamespace(batch_id=lambda _generation, _arm: "batch-1")
    study_catalog = SimpleNamespace(names=names)
    profiles = {
        ARM_V1: SimpleNamespace(identity="sha256:" + "1" * 64),
        ARM_PTX: SimpleNamespace(identity="sha256:" + "2" * 64),
    }
    execute_slot: Any = slots.execute_vector_or_fusion_slot
    result, observation = execute_slot(
        task,
        slot,
        declaration=object(),
        pg_handle=object(),
        study_catalog=study_catalog,
        arm_profiles=profiles,
        candidate_pub=object(),
        pg_pin=SimpleNamespace(generation=1),
        policy=object(),
        v1_input=object(),
        v1_artifact=SimpleNamespace(complete_generation=True, queries=()),
        public_text=object(),
        v2_seal=SimpleNamespace(records=()),
        scope=SimpleNamespace(markers=()),
        lexical_outcome=lexical_outcome,
        cached_v2_obs=cached_v2_obs,
        delivery_kind="functional_synthetic",
    )
    return calls, result, observation


@pytest.mark.parametrize("arm", (ARMS[3], ARMS[4]))
def test_missing_admitted_query_vector_is_retained_unavailable(
    monkeypatch: pytest.MonkeyPatch, arm: str,
) -> None:
    calls, result, observation = _execute(monkeypatch, arm=arm)

    assert calls == [(
        "exact_vector", 1, ExecutionState.UNAVAILABLE,
        "study_pg:missing_admitted_query_vector",
    )]
    assert result.capture_record_raw
    assert observation is None


def test_missing_v2_cache_is_retained_unavailable_for_hybrid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, result, observation = _execute(
        monkeypatch,
        arm=ARMS[5],
        lexical_outcome=(ExecutionState.COMPLETED, object()),
    )

    assert calls == [(
        "hybrid_rrf", 1, ExecutionState.UNAVAILABLE,
        "study_pg:hybrid_prerequisites_missing",
    )]
    assert result.capture_record_raw
    assert observation is None


def test_missing_lexical_prerequisite_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(StudyRefusal) as raised:
        _execute(monkeypatch, arm=ARMS[5], cached_v2_obs=object())

    assert raised.value.code == "study_pg:hybrid_lexical_prerequisite_missing"


@pytest.mark.parametrize("statement", ("SELECT 1", "SELECT 2"))
def test_exact_read_digest_binds_the_executed_statement(
    monkeypatch: pytest.MonkeyPatch, statement: str,
) -> None:
    import hashlib
    from repomap_test_support import w5_public_text_postgres as pg
    from typing import cast
    from retrieval_public_text_study_queries import StudyQuery
    from retrieval_semantic_provenance import VerifiedPublicationPin
    from repomap_test_support.w5_experiment_postgres import PostgresHandle
    observed: list[str] = []
    monkeypatch.setattr(pg, "scoped_snapshot", lambda *_args: "snapshot")
    monkeypatch.setattr(pg, "exact_sql", lambda _query: statement)
    def execute(_postgres: object, sql: str) -> str:
        observed.append(sql)
        return '{"status":"ok","candidates":[]}'
    monkeypatch.setattr(pg, "run_study_sql", execute)
    query = cast(StudyQuery, SimpleNamespace(publication=None, source_binding_id="binding", limit=20))
    pin = cast(VerifiedPublicationPin, SimpleNamespace(revalidate_current=lambda: None))
    read = pg.exact_read(cast(PostgresHandle, object()), query, pin)
    assert read.state == "completed" and observed == [statement]
    assert read.query_descriptor_sha256 == "sha256:" + hashlib.sha256(statement.encode()).hexdigest()


def test_completed_vector_capture_joins_binding_descriptors() -> None:
    from repomap_test_support.w5_semantic_support import (
        BIND_CORE, SNAP_CORE_G1, make_w5_calibration_task, make_w5_verified_pin,
    )
    from retrieval_semantic_fusion import AdmittedArmObservation, FileIdentityTuple, fuse_arm_observations_rrf
    from retrieval_supplement_values import digest_json
    task = make_w5_calibration_task(selected_bindings=(BIND_CORE,))
    pin = make_w5_verified_pin(backend="postgresql", selected_binding_ids=(BIND_CORE,))
    observation = AdmittedArmObservation(
        arm_id="exact_vector", mode="ranked_discovery", verified_pin=pin,
        selected_binding_ids=(BIND_CORE,),
        raw_file_hits=((FileIdentityTuple(BIND_CORE, SNAP_CORE_G1, "pkg/a.py"), 1, "chunk:1"),),
    )
    fused = fuse_arm_observations_rrf((observation,), expected_pin=pin, allow_explicit_single_arm_fallback=True)
    descriptor = "sha256:" + "a" * 64
    record = slots._vector_operation_record(
        task, SlotIdentity(task.task_id, ARMS[3], 1), kind="exact_vector", operation="exact_vector_sql",
        pg_pin=pin, profile_sha=descriptor, rebuild_id="batch-1", selected=(BIND_CORE,),
        per_binding=[{"source_binding_id": BIND_CORE, "state": "completed", "reason": None,
                      "elapsed_ns": 1, "candidate_count": 1, "query_descriptor_sha256": descriptor}], fused=fused,
    )
    actual: Any = record["actual"]
    assert actual["input_descriptor_sha256"] == digest_json({"queries": [[BIND_CORE, descriptor]]})


@pytest.mark.parametrize("arm,descriptor,refused", (
    (ARMS[3], "sha256:" + "a" * 64, False),
    (ARMS[4], "sha256:" + "a" * 64, False),
    (ARMS[5], None, False),
    (ARMS[3], None, True),
))
def test_real_slot_fusion_and_validator_after_completed_read(
    monkeypatch: pytest.MonkeyPatch, arm: str, descriptor: str | None, refused: bool,
) -> None:
    from repomap_test_support.w5_semantic_support import (
        BIND_CORE, SNAP_CORE_G1, make_w5_calibration_task, make_w5_verified_pin,
    )
    from retrieval_semantic_fusion import AdmittedArmObservation, FileIdentityTuple
    from retrieval_semantic_experiment_contracts import ExperimentRefusal
    task = make_w5_calibration_task(selected_bindings=(BIND_CORE,))
    pin = make_w5_verified_pin(backend="postgresql", selected_binding_ids=(BIND_CORE,))
    hit = ((FileIdentityTuple(BIND_CORE, SNAP_CORE_G1, "pkg/a.py"), 1, "chunk:1"),)
    representation = ARM_V1 if arm == ARMS[3] else ARM_PTX
    vector = AdmittedArmObservation(
        arm_id="exact_vector:" + representation, mode="vector", verified_pin=pin,
        selected_binding_ids=(BIND_CORE,), raw_file_hits=hit,
    )
    lexical = AdmittedArmObservation(
        arm_id=slots.LEXICAL_ARM, mode="ranked_discovery", verified_pin=pin,
        selected_binding_ids=(BIND_CORE,), raw_file_hits=hit,
    )
    monkeypatch.setattr(slots, "execute_after_admission", lambda *_a, **_k: (SimpleNamespace(), True))
    monkeypatch.setattr(slots, "public_text_applicability", lambda _task: None)
    monkeypatch.setattr(slots, "exact_read", lambda *_a: SimpleNamespace(
        state="completed", reason=None, rows=(), query_descriptor_sha256=descriptor))
    monkeypatch.setattr(slots, "v1_vector_arm", lambda *_a, **_k: vector)
    monkeypatch.setattr(slots, "project_public_text_candidates", lambda *_a, **_k: object())
    monkeypatch.setattr(slots, "public_text_vector_arm", lambda *_a, **_k: vector)
    monkeypatch.setattr(slots, "AdmittedArmObservation", lambda **_k: lexical)
    monkeypatch.setattr(slots, "bind_operation_capture", lambda *_a, **_k: SimpleNamespace(raw=b"capture"))
    execute: Any = slots.execute_vector_or_fusion_slot
    def run() -> tuple[SlotResult, object | None]:
        return execute(
            task, SlotIdentity(task.task_id, arm, 1), declaration=object(), pg_handle=object(),
            study_catalog=SimpleNamespace(names=SimpleNamespace(batch_id=lambda *_a: "batch-1")),
            arm_profiles={ARM_V1: SimpleNamespace(identity="sha256:" + "1" * 64),
                          ARM_PTX: SimpleNamespace(identity="sha256:" + "2" * 64)},
            candidate_pub=object(), pg_pin=pin, policy=object(), v1_input=object(),
            v1_artifact=SimpleNamespace(complete_generation=True,
                queries=(SimpleNamespace(task_id=task.task_id, vector=(1., 0., 0.)),)),
            public_text=object(), v2_seal=SimpleNamespace(records=(
                SimpleNamespace(record_id="ptx.q." + task.task_id, vector=(1., 0., 0.)),)),
            scope=SimpleNamespace(markers=()), lexical_outcome=(ExecutionState.COMPLETED, object()),
            cached_v2_obs=vector, delivery_kind="functional_synthetic",
        )
    if refused:
        with pytest.raises(ExperimentRefusal, match="exact_query_descriptor_missing"):
            run()
    else:
        result, _observation = run()
        assert result.fusion_result is not None
        assert result.fusion_result.execution_state is ExecutionState.COMPLETED
        from retrieval_supplement_values import strict_json
        record = strict_json(result.capture_record_raw)
        assert record["state"] == "completed"
        if arm in (ARMS[3], ARMS[4]):
            assert record["work"]["per_binding"][0]["query_descriptor_sha256"] == descriptor
            assert record["actual"]["input_descriptor_sha256"] is not None
        else:
            assert record["actual"]["input_descriptor_sha256"] is None
