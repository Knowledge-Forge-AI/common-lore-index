"""Public-safe synthetic fixtures for supplement-study source proof.

All identities use a synthetic ``studytest-`` namespace except the frozen
public constants copied from the selected contract; no private path, task,
gold or held-out value appears here. Doubles are functional only and never
claim native/model execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from common_lore_index_test_support.retrieval_supplement_runtime_fixture import runtime_provenance_fixture
from retrieval_supplement_preparation import PREPARATION_SCHEMA
from retrieval_supplement_study_contracts import (
    ARTIFACT_PIN_DOCUMENT_SHA256,
    ARTIFACT_SOURCE_IDENTITY_SHA256,
    INPUT_PIN_SHA256,
    REPETITIONS,
    SLOT_COUNT,
    STUDY_PHASE,
    STUDY_PLAN_SCHEMA,
    STUDY_PROFILE,
    STUDY_ROLE,
    STUDY_SELECTOR,
    TASK_COUNT,
    WORKER_CLOSURE_SHA256,
)
from retrieval_supplement_study_schedule import build_byte_forecast, build_schedule
from retrieval_supplement_values import ARMS, digest_bytes, digest_json
from runner_pgvector_prebuilt import (
    BASE_CONFIG,
    PROVIDER_SHA256,
    SDK_IMAGE,
)

FAKE_SHA = "sha256:" + "a" * 64
FAKE_SHA_B = "sha256:" + "b" * 64
FAKE_SHA_C = "sha256:" + "c" * 64
FAKE_CID = "c" * 64


def make_study_plan(tmp_root: Path, *, evaluator_closure: str = FAKE_SHA) -> dict[str, Any]:
    schedule = build_schedule()
    forecast_source_bytes: int = 1024
    forecast_request_bytes: list[int] = [8 * 1024**2, 8 * 1024**2]
    forecast_candidate_bytes: int = 4096
    forecast_binding = {
        "source_bytes": forecast_source_bytes,
        "request_bytes": forecast_request_bytes,
        "candidate_bytes": forecast_candidate_bytes,
    }
    forecast = build_byte_forecast(
        source_bytes=forecast_source_bytes,
        request_bytes=tuple(forecast_request_bytes),
        candidate_bytes=forecast_candidate_bytes,
    )
    # Keep the host-side failure grid closed even when no SDK/model worker starts.
    # The real declaration carries the same ordered identity set; this synthetic
    # wrapper adds the explicit inert flags needed by the plan contract.
    slot_manifest = [
        {"task_id": f"task-{task}", "arm": arm, "repetition": repetition, "generation": 1}
        for task in range(TASK_COUNT) for arm in ARMS for repetition in range(1, REPETITIONS + 1)
    ]
    assert len(slot_manifest) == SLOT_COUNT
    declaration = {
        "schema": "cloe-calibration-supplement-declaration-v1",
        "generation": 1,
        "repetitions": REPETITIONS,
        "status": "inert",
        "execution_authorized": False,
        "queries_executed": False,
        "model_workers_started": 0,
        "slot_order": "task_arm_repetition",
        "slot_count": SLOT_COUNT,
        "slot_manifest": slot_manifest,
        "slot_set_sha256": digest_json(slot_manifest),
        "queries_authorized": 0,
        "model_workers_authorized": 0,
        "publication_authorized": False,
        "scientific_criteria_accepted": False,
        "held_out_authorized": False,
    }
    return {
        "schema": STUDY_PLAN_SCHEMA, "phase": STUDY_PHASE, "role": STUDY_ROLE,
        "profile": STUDY_PROFILE, "selector": STUDY_SELECTOR,
        "attempt_id": "studytest-attempt-1",
        "input_pin_sha256": INPUT_PIN_SHA256,
        "artifact_source_identity_sha256": ARTIFACT_SOURCE_IDENTITY_SHA256,
        "artifact_pin_document_sha256": ARTIFACT_PIN_DOCUMENT_SHA256,
        "worker_closure_sha256": WORKER_CLOSURE_SHA256,
        "evaluator_closure_sha256": evaluator_closure,
        "sdk_image": SDK_IMAGE, "provider_sha256": PROVIDER_SHA256,
        "candidate_head": "0" * 40,
        "candidate_snapshot": {
            f"synthetic/member-{index:03d}.py": {"sha256": "c" * 64, "mode": 0o644}
            for index in range(120)
        },
        "archive": {
            "path": str(tmp_root / "runtime.tar"),
            "bytes": 502_569_984,
            "sha256": "efa131b8123911f523845d4b36411936a2e63a66c15457cdc5ac86cd4c7a3a66",
        },
        "allocation_path": str(tmp_root / "allocation.json"),
        "reservation_path": str(tmp_root / "reservation.json"),
        "declaration": declaration,
        "schedule_sha256": schedule["schedule_sha256"],
        "byte_forecast_sha256": forecast["byte_forecast_sha256"],
        "derived": {
            "base_config": BASE_CONFIG,
            **runtime_provenance_fixture(),
            "byte_forecast_binding": forecast_binding,
        },
        "review1_receipt_sha256": FAKE_SHA_B,
        "operator_decision_sha256": FAKE_SHA_C,
    }


def make_preparation(
    *, v1: tuple[int, int] = (2, 2), v2: tuple[int, int] = (1, 2), slots: int = 18,
) -> dict[str, Any]:
    def exchange(arm: str, representation: str, counts: tuple[int, int], tag: str) -> dict[str, Any]:
        ex_id = f"studytest:{representation}"
        documents = [
            {"binding_id": f"studytest-bind-{n}", "canonical_key": f"node:{tag}:{n}",
             "path": f"synthetic/{tag}.py"}
            for n in range(counts[0])
        ]
        queries = [
            {"query_id": f"{ex_id}:task-{n}", "task_id": f"task-{n}",
             "text": f"synthetic question {n}"}
            for n in range(counts[1])
        ]
        return {
            "exchange_id": ex_id, "representation": representation, "arm": arm,
            "documents": documents, "queries": queries,
            "document_count": counts[0], "query_count": counts[1],
            "record_count": sum(counts), "embedding_count_expected": sum(counts),
        }

    exchanges = [
        exchange(ARMS[3], "canonical-node-v1", v1, "v1"),
        exchange(ARMS[4], "canonical-node-public-text-v2", v2, "v2"),
    ]
    v1_id = exchanges[0]["exchange_id"]
    v2_id = exchanges[1]["exchange_id"]
    references: list[dict[str, Any]] = []
    for arm, ex_id, q_count in (
        (ARMS[3], v1_id, v1[1]),
        (ARMS[4], v2_id, v2[1]),
        (ARMS[5], v2_id, v2[1]),
    ):
        for n in range(q_count):
            for rep in (1, 2, 3):
                references.append({
                    "arm": arm, "task_id": f"task-{n}", "repetition": rep,
                    "exchange_id": ex_id, "query_id": f"{ex_id}:task-{n}",
                })
    max_tasks = max(v1[1], v2[1], 3)
    return {
        "schema": PREPARATION_SCHEMA,
        "ordered_task_ids": [f"task-{n}" for n in range(max_tasks)],
        "execution_authorized": False, "queries_executed": False,
        "model_workers_started": 0, "arms": list(ARMS), "repetitions": 3,
        "slot_count": slots, "exchanges": exchanges, "references": references,
        "digest": digest_json({"synthetic": "preparation"}),
    }


def fake_make_inputs(
    representation: str,
    documents: tuple[Mapping[str, Any], ...],
    queries: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    return {
        "representation": representation,
        "records": [
            {"record_id": f"doc:{row['canonical_key']}", "mode": "document",
             "text": f"doc text {row['canonical_key']}"}
            for row in documents
        ] + [
            {"record_id": str(row["query_id"]), "mode": "query", "text": str(row["text"])}
            for row in queries
        ],
    }


def fake_build_request(
    inputs: Mapping[str, Any], *, attempt_id: str, artifact_profile_identity: str,
) -> dict[str, Any]:
    return {
        "schema": "synthetic-worker-request", "attempt_id": attempt_id,
        "artifact_profile_identity": artifact_profile_identity,
        "records": list(inputs["records"]),
        "digest": digest_bytes(attempt_id.encode()),
    }


__all__ = [
    "FAKE_CID", "FAKE_SHA", "FAKE_SHA_B", "FAKE_SHA_C", "fake_build_request", "fake_make_inputs",
    "make_preparation", "make_study_plan",
]
