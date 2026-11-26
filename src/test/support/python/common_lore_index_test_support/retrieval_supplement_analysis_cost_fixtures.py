"""Synthetic provenance, cost-record and counted-token builders for analysis proof.

All digests, record identifiers and sample values are invented for tests only.
"""

from __future__ import annotations

import hashlib
from typing import Any

from retrieval_supplement_analysis_values import COST_RECORD_SCHEMA, ExpectedProvenance
from retrieval_supplement_primitives import REGISTERED_INPUT_PIN_SHA256, digest_json
from retrieval_supplement_study_contracts import WORKER_CLOSURE_SHA256

SYNTHETIC_PIN = "sha256:" + "1" * 64
SYNTHETIC_EVALUATOR = "sha256:" + "2" * 64
SYNTHETIC_WORKER = "sha256:" + "3" * 64
SYNTHETIC_ANALYZER = "sha256:" + "4" * 64
SYNTHETIC_DECLARATION = "sha256:" + "5" * 64
SYNTHETIC_FREEZE = "sha256:" + "6" * 64
ATTEMPT = "synthetic-analysis-attempt-1"
NATIVE_SYNTHETIC_ATTEMPT = "synthetic-native-shape-attempt-1"


def digest_seed(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def synthetic_provenance(raw: bytes, **overrides: Any) -> ExpectedProvenance:
    values: dict[str, Any] = {
        "expected_raw_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "input_pin_sha256": SYNTHETIC_PIN,
        "attempt_id": ATTEMPT,
        "evaluator_closure_sha256": SYNTHETIC_EVALUATOR,
        "worker_closure_sha256": SYNTHETIC_WORKER,
        "origin": "synthetic",
        "predecessor_failure_codes": ("synthetic_predecessor_transport_failure",),
        "declaration_source_closure_sha256": SYNTHETIC_DECLARATION,
    }
    values.update(overrides)
    return ExpectedProvenance(**values)


def native_shaped_provenance(raw: bytes, **overrides: Any) -> ExpectedProvenance:
    values: dict[str, Any] = {
        "expected_raw_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "input_pin_sha256": REGISTERED_INPUT_PIN_SHA256,
        "attempt_id": NATIVE_SYNTHETIC_ATTEMPT,
        "evaluator_closure_sha256": SYNTHETIC_EVALUATOR,
        "worker_closure_sha256": WORKER_CLOSURE_SHA256,
        "origin": "native",
        "predecessor_failure_codes": ("observed_operation_exact_query_descriptor_missing",),
        "declaration_source_closure_sha256": SYNTHETIC_DECLARATION,
    }
    values.update(overrides)
    return ExpectedProvenance(**values)


def synthetic_cost_record(
    *,
    arm: str | None = None,
    task_id: str | None = "syn-an-01",
    repetition: int | None = 1,
    shared_owner: str | None = None,
    phase: str = "query",
    elapsed_ns: int | None = 1250,
    samples: tuple[dict[str, Any], ...] | None = None,
    sample_state: str | None = None,
    sample_name: str = "query_ns",
    operation_id: str = "synthetic-operation-1",
    supplement_id: str = "syn-an-supplement",
    input_pin_sha256: str = SYNTHETIC_PIN,
    freeze_sha256: str = SYNTHETIC_FREEZE,
    source_closure_sha256: str = SYNTHETIC_DECLARATION,
    attempt_id: str = ATTEMPT,
    errors: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    identity = {
        "supplement_id": supplement_id,
        "input_pin_sha256": input_pin_sha256,
        "freeze_sha256": freeze_sha256,
        "source_closure_sha256": source_closure_sha256,
        "attempt_id": attempt_id,
        "operation_id": operation_id,
        "phase": phase,
        "generation": 1,
        "task_id": None if shared_owner else task_id,
        "arm": None if shared_owner else arm,
        "repetition": None if shared_owner else repetition,
        "shared_owner": shared_owner,
    }
    if samples is not None:
        sample_list = [dict(item) for item in samples]
    elif sample_state is not None:
        sample_list = [{
            "name": sample_name,
            "units": "ns",
            "value": None if sample_state in {"unknown", "unattributed"} else 1000,
            "state": sample_state,
            "layer": "model_worker" if shared_owner else "caller",
            "shared_owner": shared_owner if sample_state == "shared" else None,
            "additive": False,
        }]
    else:
        sample_list = []
    err_map = {"body": None, "clock": None, "capture": None} if errors is None else dict(errors)
    failed = any(err_map.values())
    record: dict[str, Any] = {
        "schema": COST_RECORD_SCHEMA,
        "identity": identity,
        "state": "failed" if failed else "completed",
        "elapsed_ns": elapsed_ns,
        "elapsed_units": "ns",
        "elapsed_observation": "unknown" if elapsed_ns is None else "caller_phase_boundary",
        "capture_after_clock_stop": True,
        "additive_with_other_layers": False,
        "samples": sample_list,
        "errors": err_map,
        "observation_complete": not failed,
        "generation_ledger_defaults_imported": False,
        "origin_or_execution_qualification": False,
    }
    record["record_sha256"] = digest_json(record)
    return record


def synthetic_counted_tokens(
    bundle: dict[str, Any],
    *,
    native: bool = False,
    truncated_records: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], ...]:
    """Build counted-worker token outputs matching ``bundle``'s raw slot IDs."""
    v1_ids: set[str] = set()
    v2_ids: set[str] = set()
    for row in bundle["paired_analysis"]["raw_slots"]:
        for rec_id in [row["query_record_id"], *row["target_record_ids"]]:
            if not rec_id:
                continue
            if rec_id.startswith(("v1.", "syn-v1-")):
                v1_ids.add(rec_id)
            else:
                v2_ids.add(rec_id)
    # Both synthetic bundle sizes declare146 token records. Pad unused invented
    # records as well, so truncation tests reach the token/slot join guard.
    v1_target = 86
    v2_target = 60
    idx = 0
    while len(v1_ids) < v1_target:
        idx += 1
        pad = hashlib.sha256(f"pad-v1-{idx}".encode("utf-8")).hexdigest()
        v1_ids.add(f"v1.document:{pad}" if native else f"syn-v1-pad:{idx:03d}")
    idx = 0
    while len(v2_ids) < v2_target:
        idx += 1
        v2_ids.add(f"ptx.d.{900 + idx:04d}" if native else f"syn-ptx-pad:{idx:03d}")

    def _rows(record_ids: set[str]) -> list[dict[str, Any]]:
        return [
            {
                "record_id": rec_id,
                "token_count": 512 if rec_id in truncated_records else 48,
                "token_count_before_limit": 640 if rec_id in truncated_records else 48,
                "overflow_chunks": 1 if rec_id in truncated_records else 0,
                "truncated": rec_id in truncated_records,
            }
            for rec_id in sorted(record_ids)
        ]

    v1_rows, v2_rows = _rows(v1_ids), _rows(v2_ids)
    attempt = str(bundle["attempt_id"])
    return (
        {
            "representation": "canonical-node-v1",
            "attempt_id": f"{attempt}-v1",
            "record_count": len(v1_rows),
            "token_evidence": v1_rows,
        },
        {
            "representation": "canonical-node-public-text-v2",
            "attempt_id": f"{attempt}-v2",
            "record_count": len(v2_rows),
            "token_evidence": v2_rows,
        },
    )


__all__ = [
    "ATTEMPT",
    "NATIVE_SYNTHETIC_ATTEMPT",
    "SYNTHETIC_ANALYZER",
    "SYNTHETIC_DECLARATION",
    "SYNTHETIC_EVALUATOR",
    "SYNTHETIC_FREEZE",
    "SYNTHETIC_PIN",
    "SYNTHETIC_WORKER",
    "digest_seed",
    "native_shaped_provenance",
    "synthetic_cost_record",
    "synthetic_counted_tokens",
    "synthetic_provenance",
]
