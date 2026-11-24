"""Non-executing primary selection and successor disposition policy."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from retrieval_body_study_values import digest_value, is_digest, require

COMPLETE_ADMISSIBLE_OUTCOMES = frozenset({
    "calibration_signal_present",
    "signal_not_demonstrated",
})
VALID_RESULT_OUTCOMES = COMPLETE_ADMISSIBLE_OUTCOMES | {"inconclusive_floor"}
ROUTINE_DEFECTS = frozenset({"transport", "telemetry", "cleanup"})
INADMISSIBILITY_CAUSES = frozenset({
    "privacy_disclosure", "forbidden_work", "integrity", "ownership", "source", "other",
})


def select_primary_attempt(
    attempts: Sequence[Mapping[str, Any]], *, invalidations: Sequence[Mapping[str, Any]] = (),
) -> int | None:
    """Select chronologically, skipping only explicit, payload-bound invalidations.

    Receipt hashes here are inert references. The actual controller must verify
    their contents, authority and chronology before passing them to this policy.
    """
    require(
        isinstance(attempts, Sequence)
        and not isinstance(attempts, (str, bytes, bytearray)),
        "body_study:attempts_sequence",
    )
    require(all(isinstance(attempt, Mapping) for attempt in attempts), "body_study:attempt_mapping")
    invalidated: set[int] = set()
    require(isinstance(invalidations, Sequence) and not isinstance(invalidations, (str, bytes)),
            "body_study:invalidations_sequence")
    fields = {"attempt_index", "attempt_identity", "objective_defect_code",
              "rank_blind_review_done", "operator_disposition", "rank_blind_review_sha256",
              "operator_decision_sha256"}
    for row in invalidations:
        require(isinstance(row, Mapping) and set(row) == fields, "body_study:invalidation_fields")
        index = row["attempt_index"]
        require(type(index) is int and 0 <= index < len(attempts), "body_study:invalidation_index")
        require(index not in invalidated, "body_study:invalidation_duplicate")
        target = attempts[index]
        require(target.get("outcome") in VALID_RESULT_OUTCOMES, "body_study:invalidation_target")
        require(row["attempt_identity"] == digest_value(target), "body_study:invalidation_binding")
        require(row["rank_blind_review_done"] is True and row["operator_disposition"] is True,
                "body_study:invalidation_disposition")
        require(all(is_digest(row[name]) for name in ("rank_blind_review_sha256", "operator_decision_sha256")),
                "body_study:invalidation_receipts")
        code = row["objective_defect_code"]
        require(type(code) is str and 1 <= len(code) <= 128 and code != "unknown"
                and all(char.isascii() and (char.isalnum() or char in "._-") for char in code),
                "body_study:invalidation_objective_defect")
        invalidated.add(index)
    for index, attempt in enumerate(attempts):
        require(isinstance(attempt, Mapping), "body_study:attempt_mapping")
        if index not in invalidated and attempt.get("outcome") in VALID_RESULT_OUTCOMES:
            return index
    return None


def classify_successor(
    *,
    outcome: str,
    exposed_to_scores: bool,
    defect_class: str,
    scientific_fingerprints_identical: bool,
    integrity_restored: bool | None,
    rank_blind_review_done: bool,
    operator_disposition: bool,
    ownership_restored: bool | None = None,
    cleanup_proven: bool | None = None,
    invalidates_complete_primary: bool = False,
    inadmissibility_cause: str | None = None,
) -> str:
    """Return a disposition label; this function never grants execution authority."""
    require(type(outcome) is str and bool(outcome), "body_study:recovery_outcome")
    require(type(exposed_to_scores) is bool, "body_study:recovery_exposure")
    require(type(defect_class) is str, "body_study:recovery_defect_class")
    require(type(scientific_fingerprints_identical) is bool,
            "body_study:recovery_fingerprint_type")
    require(type(rank_blind_review_done) is bool, "body_study:recovery_review_type")
    require(type(operator_disposition) is bool, "body_study:recovery_disposition_type")
    require(type(invalidates_complete_primary) is bool,
            "body_study:recovery_invalidation_type")
    require(
        inadmissibility_cause is None
        or (type(inadmissibility_cause) is str and inadmissibility_cause in INADMISSIBILITY_CAUSES),
        "body_study:recovery_inadmissibility_cause",
    )
    for name, value in (("integrity_restored", integrity_restored),
                        ("ownership_restored", ownership_restored),
                        ("cleanup_proven", cleanup_proven)):
        require(value is None or type(value) is bool, f"body_study:recovery_{name}_type")

    if integrity_restored is False:
        return "refused_until_integrity_restored"
    if integrity_restored is not True or ownership_restored is not True or cleanup_proven is not True:
        return "refused_until_integrity_ownership_cleanup_proven"

    if invalidates_complete_primary:
        if outcome not in VALID_RESULT_OUTCOMES:
            return "invalid_primary_invalidation_target"
        if not rank_blind_review_done:
            return "rank_blind_review_required"
        if not defect_class or defect_class == "unknown":
            return "objective_defect_required"
        if not operator_disposition:
            return "operator_disposition_required"
        return "successor_may_replace_after_operator_disposition"

    if outcome in VALID_RESULT_OUTCOMES:
        return "no_successor_valid_result"

    if outcome not in {"incomplete_technical", "inadmissible"}:
        return "refused_unknown_outcome"

    if exposed_to_scores and not rank_blind_review_done:
        return "rank_blind_review_required"

    if outcome == "inadmissible" and (
        inadmissibility_cause in {"privacy_disclosure", "forbidden_work", "other"}
        or inadmissibility_cause is None
    ):
        return (
            "successor_permitted_after_operator_disposition"
            if operator_disposition
            else "operator_disposition_required"
        )

    if not exposed_to_scores and outcome == "incomplete_technical":
        if not defect_class or defect_class == "unknown":
            return "operator_disposition_required"
        return "diagnosed_correction_may_use_fresh_attempt"

    if defect_class in ROUTINE_DEFECTS and scientific_fingerprints_identical:
        if exposed_to_scores:
            return "routine_neutral_successor_permitted"
        return "diagnosed_correction_may_use_fresh_attempt"

    return (
        "operator_disposition_required"
        if not operator_disposition
        else "successor_permitted_after_operator_disposition"
    )


__all__ = ["classify_successor", "select_primary_attempt"]
