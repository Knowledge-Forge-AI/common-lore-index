"""Catalog-bound two-publication preparation; never execution admission.

Functional catalogs remain unregistered. Both complete reports and their
distinct publication identities survive selection of one 432-slot matrix.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from retrieval_body_admission import BodyTaskAdmission
from retrieval_body_admission_rules import complete_policy_identity, evaluate_body_case
from retrieval_body_capabilities import expected_capabilities
from retrieval_body_catalog import BodyCatalog
from retrieval_body_report import _FALSE_FLAGS, build_body_report
from retrieval_body_study_values import (
    ARM_BACKENDS, ARM_REPRESENTATIONS, ARMS, FTS_ARM, FUSION_ARM, INERT_FLAGS,
    NATIVE_ARM, PROTOCOL_ID, REGISTERED_INPUT_PIN_SHA256, REPETITIONS,
    ROW_COUNT, SLOT_COUNT, STUDY_CLASS_ID, STUDY_PHASE, STUDY_SCHEMA, V2_ARM,
    digest_value, is_digest, require, thaw,
)
from retrieval_evaluation_admission_w5 import _pin_identity
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_supplement_tasks import task_digests

_ROW_FIELDS = frozenset({
    "class_id", "catalog_sha256", "input_pin_sha256", "case_sha256", "task_id",
    "task_sha256", "retained_task_sha256", "generation", "arm", "case_kind", "family",
    "intent_id", "wording_stratum", "stratum", "preparation", "reason", "actual_surface",
    "fallback_policy", "admission_identity", "publication_identity", "representation_identity",
})


def _rows(report: Mapping[str, Any], label: str, catalog: BodyCatalog) -> list[dict[str, Any]]:
    require(isinstance(report, Mapping), f"body_study:{label}_report_mapping")
    require(report.get("schema") == "cloe-body-report-v1", f"body_study:{label}_report_schema")
    require(report.get("origin_proven") is False, f"body_study:{label}_origin_claim")
    require(report.get("execution_state") == "not_executed", f"body_study:{label}_execution_claim")
    require(all(report.get(field) is False for field in _FALSE_FLAGS),
            f"body_study:{label}_execution_claim")
    require(report.get("class_id") == catalog.identity.class_id, f"body_study:{label}_class")
    require(report.get("catalog_sha256") == catalog.digest, f"body_study:{label}_catalog")
    pin = report.get("input_pin_sha256")
    require(is_digest(pin), f"body_study:{label}_input_pin")
    if catalog.registered:
        require(pin == REGISTERED_INPUT_PIN_SHA256, f"body_study:{label}_registered_input_pin")
    cases = {case.task.task_id: case for case in catalog.cases}
    rows = report.get("preparation_rows")
    require(type(rows) is list and len(rows) == ROW_COUNT, f"body_study:{label}_row_count")
    assert isinstance(rows, list)
    require(report.get("rows") == rows, f"body_study:{label}_row_alias")
    for field, expected_count in (("preparation_row_count", ROW_COUNT),
                                  ("planned_slot_count", SLOT_COUNT), ("task_count", 24),
                                  ("arm_count", len(ARMS)), ("repetition_count", REPETITIONS)):
        require(type(report.get(field)) is int and report[field] == expected_count,
                f"body_study:{label}_declared_counts")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        require(isinstance(row, Mapping), f"body_study:{label}_row_mapping")
        require(set(row) == _ROW_FIELDS, f"body_study:{label}_row_fields")
        task_id, arm = row.get("task_id"), row.get("arm")
        require(type(task_id) is str and task_id in cases and type(arm) is str and arm in ARMS,
                f"body_study:{label}_row_identity")
        key = (str(task_id), str(arm))
        require(key not in seen, f"body_study:{label}_duplicate_row")
        seen.add(key)
        case = cases[str(task_id)]
        require(type(row.get("generation")) is int, f"body_study:{label}_row_generation")
        expected = {"class_id": catalog.identity.class_id, "catalog_sha256": catalog.digest,
                    "input_pin_sha256": pin, "case_sha256": case.digest,
                    **task_digests(case.task), "generation": case.task.generation,
                    "case_kind": case.kind, "family": case.task.family,
                    "intent_id": case.to_dict()["intent_id"],
                    "wording_stratum": case.to_dict()["wording_stratum"]}
        for field, value in expected.items():
            require(row.get(field) == value, f"body_study:{label}_row_{field}")
        require(row.get("representation_identity") == ARM_REPRESENTATIONS[str(arm)],
                f"body_study:{label}_representation")
        require(type(row.get("preparation")) is str
                and row["preparation"] in {"ready", "refused", "unavailable"},
                f"body_study:{label}_preparation")
        capability = expected_capabilities(case.task, case.kind)[str(arm)]
        if row["preparation"] == "ready":
            require(capability["preparation"] == "ready", f"body_study:{label}_forbidden_ready")
            require(all(row[field] == capability[field] for field in ("actual_surface", "fallback_policy")),
                    f"body_study:{label}_ready_surface")
        else:
            require(row["actual_surface"] == "none" and row["fallback_policy"] == "none",
                    f"body_study:{label}_inactive_surface")
        publication = row.get("publication_identity")
        if publication is None:
            require(row["preparation"] == "refused", f"body_study:{label}_missing_row_publication")
        else:
            require(digest_value(publication) == digest_value(report.get("publication_identity")),
                    f"body_study:{label}_mixed_row_publication")
    require(seen == {(task, arm) for task in cases for arm in ARMS},
            f"body_study:{label}_completeness")
    expected_slots = [
        {**row, "repetition": repetition, "execution_state": "not_executed",
         "refused_preparation_is_execution_evidence": False,
         "execution_refusal_evidence": False, **_FALSE_FLAGS}
        for row in rows for repetition in range(1, REPETITIONS + 1)
    ]
    require(report.get("planned_slots") == expected_slots
            and report.get("slots") == expected_slots, f"body_study:{label}_planned_slots")
    return [dict(row) for row in rows]


def _check_pair(sqlite: Mapping[str, Any], postgres: Mapping[str, Any], catalog: BodyCatalog) -> None:
    require(isinstance(sqlite, Mapping) and isinstance(postgres, Mapping), "body_study:report_pair")
    for field in ("catalog_sha256", "input_pin_sha256"):
        require(sqlite.get(field) == postgres.get(field) and is_digest(sqlite.get(field)),
                f"body_study:mixed_{field}")
    generations = {case.task.generation for case in catalog.cases}
    require(len(generations) == 1, "body_study:catalog_generations")
    for backend, report in (("sqlite", sqlite), ("postgresql", postgres)):
        pub = report.get("publication_identity")
        require(isinstance(pub, Mapping), "body_study:report_publication_required")
        assert isinstance(pub, Mapping)
        require(pub.get("backend") == backend, "body_study:report_backend")
        require(pub.get("graph_id") == catalog.identity.graph_id, "body_study:report_graph")
        require(type(pub.get("generation")) is int and pub["generation"] in generations,
                "body_study:report_generation")
    require(digest_value(sqlite["publication_identity"]) != digest_value(postgres["publication_identity"]),
            "body_study:same_publication_for_both_backends")


def select_body_study_matrix(
    sqlite_report: Mapping[str, Any], postgres_report: Mapping[str, Any], *, catalog: BodyCatalog,
) -> dict[str, Any]:
    """Select a catalog-exact matrix; both complete source reports stay inert."""
    require(BodyCatalog.is_factory_issued(catalog), "body_study:catalog_not_issued")
    _check_pair(sqlite_report, postgres_report, catalog)
    sqlite_rows = _rows(sqlite_report, "sqlite", catalog)
    pg_rows = _rows(postgres_report, "postgres", catalog)
    s_by = {(r["task_id"], r["arm"]): r for r in sqlite_rows}
    p_by = {(r["task_id"], r["arm"]): r for r in pg_rows}
    selected = []
    for case in catalog.cases:
        for arm in ARMS:
            key = (case.task.task_id, arm)
            row = dict(s_by[key] if arm == NATIVE_ARM else p_by[key])
            row.update({"selected_backend": ARM_BACKENDS[arm],
                        "observed_surface": "native_filesystem" if arm == NATIVE_ARM else None,
                        "study_class_id": STUDY_CLASS_ID, "source_publication": ARM_BACKENDS[arm]})
            if arm == FUSION_ARM and row["preparation"] == "ready":
                require(all(p_by[(key[0], dep)]["preparation"] == "ready" for dep in (FTS_ARM, V2_ARM)),
                        "body_study:fusion_dependency_not_ready")
            selected.append(row)
    slots = [{**row, "repetition": repetition, "execution_state": "unstarted"}
             for row in selected for repetition in range(1, REPETITIONS + 1)]
    require(len(selected) == ROW_COUNT and len(slots) == SLOT_COUNT, "body_study:selected_counts")
    return {"schema": STUDY_SCHEMA, "phase": STUDY_PHASE, "protocol": PROTOCOL_ID,
            "study_class_id": STUDY_CLASS_ID, "source_class_id": catalog.identity.class_id,
            "source_corpus_id": catalog.identity.corpus_id, "functional_only": not catalog.registered,
            "catalog_sha256": catalog.digest, "input_pin_sha256": sqlite_report["input_pin_sha256"],
            "sqlite_publication_identity": thaw(sqlite_report["publication_identity"]),
            "postgres_publication_identity": thaw(postgres_report["publication_identity"]),
            "combined_publication_digest": None, "sqlite_report": thaw(sqlite_report),
            "postgres_report": thaw(postgres_report), "selected_rows": selected, "planned_slots": slots,
            "selected_row_count": len(selected), "planned_slot_count": len(slots),
            "counterpart_substitution": False, "decision_rows_verified": False, **INERT_FLAGS}


def _require_pin(pin: object, backend: str, label: str) -> VerifiedPublicationPin:
    require(pin is not None, f"body_study:{label}_pin_missing")
    require(type(pin) is VerifiedPublicationPin, f"body_study:{label}_pin_type")
    assert isinstance(pin, VerifiedPublicationPin)
    require(pin.is_factory_issued(), f"body_study:{label}_pin_not_issued")
    require(pin.backend == backend, f"body_study:{label}_pin_backend")
    try:
        pin.revalidate_current()
    except Exception as exc:
        from retrieval_body_study_values import BodyStudyError
        raise BodyStudyError(f"body_study:{label}_pin_not_current") from exc
    return pin


def _pin_matches_report(pin: VerifiedPublicationPin, report: Mapping[str, Any], label: str) -> None:
    require(digest_value(report.get("publication_identity")) == digest_value(_pin_identity(pin)),
            f"body_study:{label}_report_pin_identity")


def _verify_decisions(
    catalog: BodyCatalog, decisions: Sequence[object], pin: VerifiedPublicationPin,
    policy: BodyProjectionPolicy, frozen_policy: BodyProjectionPolicy, backend: str,
) -> None:
    """Recompute each preparation from its actual issued pin, never a copied label."""
    require(len(decisions) == ROW_COUNT, "body_study:decision_count")
    cases = {case.task.task_id: case for case in catalog.cases}
    seen: set[tuple[str, str]] = set()
    for decision in decisions:
        if type(decision) is BodyTaskAdmission:
            require(BodyTaskAdmission.is_factory_issued(decision), "body_study:decision_not_issued")
            decision.require_current()
            row = decision.to_dict()
        else:
            require(isinstance(decision, Mapping), "body_study:decision_mapping")
            assert isinstance(decision, Mapping)
            row = dict(decision)
        task_id, arm = row.get("task_id"), row.get("arm")
        require(type(task_id) is str and task_id in cases and type(arm) is str and arm in ARMS,
                "body_study:decision_identity")
        key = (str(task_id), str(arm))
        require(key not in seen, "body_study:decision_duplicate")
        seen.add(key)
        input_pin = row.get("input_pin_sha256")
        require(is_digest(input_pin), "body_study:decision_input_pin")
        if catalog.registered:
            require(input_pin == REGISTERED_INPUT_PIN_SHA256, "body_study:decision_registered_pin")
        expected = evaluate_body_case(catalog, cases[str(task_id)], arm=str(arm), policy=policy,
                                     frozen_policy=frozen_policy, publication_pin=pin,
                                     observed_backend=backend)
        expected["input_pin_sha256"] = input_pin
        require(digest_value(row) == digest_value(expected), "body_study:decision_recomputed_mismatch")
    require(seen == {(task_id, arm) for task_id in cases for arm in ARMS}, "body_study:decision_completeness")


def prepare_bound_body_study(
    catalog: BodyCatalog, *, sqlite_decisions: Sequence[object], postgres_decisions: Sequence[object],
    sqlite_pin: VerifiedPublicationPin | None, postgres_pin: VerifiedPublicationPin | None,
    policy: BodyProjectionPolicy, frozen_policy: BodyProjectionPolicy,
) -> dict[str, Any]:
    """Check current issued publications and policies; return inert preparation.

    This does not claim registered-input admission. Functional catalogs are
    useful for real store tests but remain functional in every returned record.
    """
    require(BodyCatalog.is_factory_issued(catalog), "body_study:catalog_not_issued")
    sqlite = _require_pin(sqlite_pin, "sqlite", "sqlite")
    postgres = _require_pin(postgres_pin, "postgresql", "postgres")
    complete = complete_policy_identity(policy)
    require(complete == complete_policy_identity(frozen_policy), "body_study:policy_drift")
    require(policy.graph_id == catalog.identity.graph_id, "body_study:policy_graph")
    require(sqlite.graph_id == postgres.graph_id == catalog.identity.graph_id, "body_study:pin_graph_mismatch")
    require(sqlite.generation == postgres.generation, "body_study:pin_generation_mismatch")
    require(sqlite.snapshot_vector == postgres.snapshot_vector, "body_study:pin_snapshot_mismatch")
    require(sqlite.admitted_public_bindings == postgres.admitted_public_bindings,
            "body_study:pin_admitted_mismatch")
    require(dict(sqlite.denied_bindings) == dict(postgres.denied_bindings), "body_study:pin_denied_mismatch")
    require(sqlite.policy_digest == postgres.policy_digest == policy.policy_digest(), "body_study:pin_policy")
    require({case.task.generation for case in catalog.cases} == {sqlite.generation},
            "body_study:task_generation_mismatch")
    _verify_decisions(catalog, sqlite_decisions, sqlite, policy, frozen_policy, "sqlite")
    _verify_decisions(catalog, postgres_decisions, postgres, policy, frozen_policy, "postgresql")
    sqlite_report = build_body_report(catalog, sqlite_decisions)
    postgres_report = build_body_report(catalog, postgres_decisions)
    _pin_matches_report(sqlite, sqlite_report, "sqlite")
    _pin_matches_report(postgres, postgres_report, "postgres")
    matrix = select_body_study_matrix(sqlite_report, postgres_report, catalog=catalog)
    _require_pin(sqlite, "sqlite", "sqlite")
    _require_pin(postgres, "postgresql", "postgres")
    matrix.update({"bound_preparation": True, "decision_rows_verified": True, "complete_policy_sha256": complete,
                   "policy_digest_legacy": policy.policy_digest(), "max_projected_files": policy.max_projected_files,
                   "sqlite_pin_identity": digest_value(sqlite.to_dict()),
                   "postgres_pin_identity": digest_value(postgres.to_dict())})
    return matrix


__all__ = ["prepare_bound_body_study", "select_body_study_matrix"]
