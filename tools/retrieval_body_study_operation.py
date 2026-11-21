"""Inert operation summaries and recovery policy; actual journal is separate."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from retrieval_body_study_contracts import BODY_OPERATION_SCHEMA, refuse
from retrieval_body_study_outcomes import slot_exposes_quality
from retrieval_body_study_recovery import classify_successor, select_primary_attempt


def build_operation(*, attempt_id: str, slots: Sequence[Mapping[str, Any]],
                    integrity_ok: bool | None, cleanup_ok: bool | None,
                    ownership_ok: bool | None = None) -> dict[str, Any]:
    refuse(type(attempt_id) is str and bool(attempt_id), "body operation attempt identity")
    refuse(all(value is None or type(value) is bool for value in (integrity_ok, cleanup_ok, ownership_ok)),
           "body operation flags")
    exposed = any(slot_exposes_quality(s, str(s.get("case_kind", ""))) for s in slots)
    return {"schema": BODY_OPERATION_SCHEMA, "attempt_id": attempt_id,
            "exposed_to_quality": exposed, "slot_count": len(slots),
            "integrity_ok": integrity_ok, "cleanup_ok": cleanup_ok,
            "ownership_ok": ownership_ok,
            "successor_blocked": any(value is not True for value in (integrity_ok, cleanup_ok, ownership_ok)),
            "origin_proven": False, "execution_authority_granted": False}


def exposed_successor_allowed(*, defect_class: str, rank_blind_review: bool,
                              treatment_unchanged: bool, integrity_ok: bool | None,
                              ownership_ok: bool | None = None,
                              cleanup_ok: bool | None = None) -> str:
    """Return policy only; caller-claimed booleans do not establish authority."""
    return classify_successor(outcome="incomplete_technical", exposed_to_scores=True,
        defect_class=defect_class, scientific_fingerprints_identical=treatment_unchanged,
        integrity_restored=integrity_ok, ownership_restored=ownership_ok,
        cleanup_proven=cleanup_ok, rank_blind_review_done=rank_blind_review,
        operator_disposition=False)


def primary_attempt(attempts: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    idx = select_primary_attempt(attempts)
    return attempts[idx] if idx is not None else None


__all__ = ["build_operation", "exposed_successor_allowed", "primary_attempt"]
