"""Body-study support joins and body-specific model epoch adapter."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from retrieval_body_study_values import STUDY_PHASE, digest_value, require


@dataclass(frozen=True, slots=True)
class BodyStudyDeclaration:
    attempt_id: str
    input_pin_sha256: str
    source_closure_sha256: str
    treatment_sha256: str
    protocol_sha256: str
    candidate_snapshot_sha256: str
    plan_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "cloe-body-study-declaration-v1",
            "phase": STUDY_PHASE,
            "role": "body-study",
            "attempt_id": self.attempt_id,
            "input_pin_sha256": self.input_pin_sha256,
            "source_closure_sha256": self.source_closure_sha256,
            "treatment_sha256": self.treatment_sha256,
            "protocol_sha256": self.protocol_sha256,
            "candidate_snapshot_sha256": self.candidate_snapshot_sha256,
            "plan_sha256": self.plan_sha256,
        }

    @property
    def identity(self) -> str:
        return digest_value(self.to_dict())


def build_body_study_declaration(
    *,
    attempt_id: str,
    input_pin_sha256: str,
    source_closure_sha256: str,
    treatment_sha256: str,
    protocol_sha256: str,
    candidate_snapshot_sha256: str,
    plan_sha256: str,
) -> BodyStudyDeclaration:
    values = {
        "attempt_id": attempt_id,
        "input_pin_sha256": input_pin_sha256,
        "source_closure_sha256": source_closure_sha256,
        "treatment_sha256": treatment_sha256,
        "protocol_sha256": protocol_sha256,
        "candidate_snapshot_sha256": candidate_snapshot_sha256,
        "plan_sha256": plan_sha256,
    }
    require(all(type(value) is str and value for value in values.values()), "body.setup:declaration_fields")
    return BodyStudyDeclaration(**values)


def run_exchange_epoch(
    request_map: dict[str, Any],
    request_raw: bytes,
    *,
    worker_digests: dict[str, str],
    delivery: Any,
    representation: str,
    evidence_root: Path,
    evidence: Any,
    require_current: Callable[[], None] | None = None,
) -> Any:
    """Route only to the body sibling epoch/transport implementation."""
    require(isinstance(request_map, dict), "body.setup:request_descriptor")
    require(request_map.get("phase", STUDY_PHASE) in (STUDY_PHASE, None), "body.setup:phase")
    try:
        from retrieval_body_study_exchange import run_exchange_epoch as body_exchange
    except ImportError as exc:
        raise RuntimeError("body.setup:body_exchange_missing") from exc
    return body_exchange(
        request_map, request_raw, worker_digests=worker_digests, delivery=delivery,
        representation=representation, evidence_root=evidence_root, evidence=evidence,
        require_current=require_current,
    )


def store_join_maps(catalog: Any, v1_input: Any, public_text: Any) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]], list[str]]:
    """Join current v1/public-text descriptors without issuing admissions."""
    span_lookup = {
        (str(doc.descriptor["source_binding_id"]), str(doc.descriptor["source_relative_path"])):
        (int(doc.descriptor["start_line"]), int(doc.descriptor["end_line"]))
        for doc in public_text.documents
    }
    accepted_nodes: dict[str, dict[str, Any]] = {}
    for entry in v1_input.documents:
        rep = entry.representation
        for rel_path in rep.source_relative_paths:
            span = span_lookup.get((rep.source_binding_id, rel_path))
            if span is not None:
                accepted_nodes[f"{rep.source_binding_id}:{rel_path}"] = {
                    "publication_identity": public_text.publication_identity,
                    "span_start": span[0], "span_end": span[1],
                }
    golds: dict[str, list[str]] = {}
    positives: list[str] = []
    for case in catalog.cases:
        if case.kind == "body_positive":
            positives.append(case.task.task_id)
        targets = [
            f"{g.locator.binding_id}:{g.locator.source_relative_path}"
            for g in case.task.gold_targets if g.relevance_grade > 0
        ]
        if targets:
            golds[case.task.task_id] = targets
    return golds, accepted_nodes, positives


def slot_outcome_row(case: Any, observation: Any) -> dict[str, Any]:
    """Stable support projection for report joins; quality stays in the driver."""
    policy = observation.policy
    return {
        "task_id": case.task.task_id,
        "arm": policy["arm"],
        "repetition": policy["repetition"],
        "execution_state": policy["execution_state"],
        "returned_hit_count": policy.get("returned_hit_count", 0),
        "refusal_code": policy.get("refusal_code"),
    }


__all__ = [
    "BodyStudyDeclaration",
    "build_body_study_declaration",
    "run_exchange_epoch",
    "slot_outcome_row",
    "store_join_maps",
]
