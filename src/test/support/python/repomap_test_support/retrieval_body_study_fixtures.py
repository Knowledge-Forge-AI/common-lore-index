"""Synthetic inert fixtures for body-study unit owners (no execution)."""
from __future__ import annotations

from typing import Any

from repomap_test_support.retrieval_body_fixtures import (
    make_body_bindings, make_body_catalog_payload, make_body_identity,
)
from retrieval_body_capabilities import expected_capabilities
from retrieval_body_catalog import BodyCatalog, parse_functional_catalog
from retrieval_body_report import build_body_report
from retrieval_body_study_values import (
    ARM_REPRESENTATIONS, ARMS, BODY_FAMILIES, COMPARISON_ARMS, FUSION_ARM, GRAPH_ARM, V1_ARM,
)
from retrieval_body_study_common import select_common_positives
from retrieval_body_values import canonical_bytes, digest_bytes
from retrieval_supplement_tasks import task_digests

PIN = digest_bytes(b"unit-body-study-input-pin")
SQLITE_PUB = {"graph_id": make_body_identity().graph_id, "generation": 1, "backend": "sqlite", "run_id": 1}
PG_PUB = {"graph_id": make_body_identity().graph_id, "generation": 1, "backend": "postgresql", "run_id": 2}


def study_catalog() -> BodyCatalog:
    return parse_functional_catalog(
        canonical_bytes(make_body_catalog_payload()),
        identity=make_body_identity(), binding_ids=tuple(make_body_bindings().values()))


def study_decisions(catalog: BodyCatalog, publication: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in catalog.cases:
        digests = task_digests(case.task)
        caps = expected_capabilities(case.task, case.kind)
        for arm in ARMS:
            cap = caps[arm]
            rows.append({
                "class_id": catalog.identity.class_id, "catalog_sha256": catalog.digest,
                "case_sha256": case.digest, "input_pin_sha256": PIN,
                "task_id": case.task.task_id, **digests, "generation": case.task.generation,
                "arm": arm, "preparation": cap["preparation"], "reason": cap["reason"],
                "actual_surface": cap["actual_surface"], "fallback_policy": cap["fallback_policy"],
                "publication_identity": publication,
            })
    return rows


def study_reports(catalog: BodyCatalog | None = None) -> tuple[BodyCatalog, dict, dict]:
    resolved = catalog or study_catalog()
    return (resolved, build_body_report(resolved, study_decisions(resolved, SQLITE_PUB)),
            build_body_report(resolved, study_decisions(resolved, PG_PUB)))


def quality_inputs(*, fusion_rank: int = 1, comparator_rank: int = 3,
                   common_count: int | None = None):
    """Closed synthetic 24-task/432-slot quality inputs with known ranks."""
    meta: dict[str, dict[str, Any]] = {}
    golds: dict[str, list] = {}
    universes: dict[str, int | None] = {}
    body_ids: list[str] = []
    for index in range(1, 13):
        task_id = f"body-{index:02d}"
        body_ids.append(task_id)
        meta[task_id] = {"case_kind": "body_positive",
                         "family": BODY_FAMILIES[(index - 1) // 4],
                         "intent_id": f"intent-{index}", "wording_stratum": "literal_clue",
                         "expected_arm_outcomes": {
                             arm: {"state": "unavailable" if arm in {GRAPH_ARM, V1_ARM} else "completed",
                                   "refusal_code": None} for arm in ARMS}}
        golds[task_id] = [["bind-a", "src/gold.py", 3]]
        universes[task_id] = 22
    for index in range(1, 5):
        task_id = f"anchor-{index}"
        meta[task_id] = {"case_kind": "metadata_anchor", "family": "exact_symbol_and_partial_name",
                         "intent_id": task_id, "wording_stratum": "not_applicable",
                         "expected_arm_outcomes": {
                             arm: {"state": "completed", "refusal_code": None} for arm in ARMS}}
        golds[task_id] = [["bind-a", "src/gold.py", 3]]
        universes[task_id] = 11
    for kind, grade, count in (("absent", 0, 0), ("ambiguous", 2, 2), ("privacy", 0, 0),
                               ("unsupported", 1, 2)):
        for index in range(1, 3):
            task_id = f"{kind}-{index}"
            meta[task_id] = {"case_kind": kind, "family": "control", "intent_id": task_id,
                             "wording_stratum": "not_applicable",
                             "expected_arm_outcomes": {
                                 arm: {"state": "refused", "refusal_code": "refused_expected"}
                                 for arm in ARMS
                             } if kind in {"privacy", "unsupported"} else {
                                 arm: {"state": "unavailable" if arm in {GRAPH_ARM, V1_ARM} else "completed",
                                       "refusal_code": None} for arm in ARMS
                             },
                             **({"expected_refusal_code": "refused_expected"}
                                if kind in {"privacy", "unsupported"} else {})}
            golds[task_id] = [["bind-a", f"src/g{i}.py", grade] for i in range(count)]
            universes[task_id] = None if kind in {"privacy", "unsupported"} else 22

    def ranked(rank: int) -> list[list[str]]:
        files = [["bind-a", f"src/other{i}.py"] for i in range(rank - 1)]
        files.append(["bind-a", "src/gold.py"])
        # Duplicate chunk of the gold file must not double-count.
        files.append(["bind-a", "src/gold.py"])
        return files

    slots: list[dict[str, Any]] = []
    for task_id, item in meta.items():
        for arm in ARMS:
            for repetition in (1, 2, 3):
                slot: dict[str, Any] = {"task_id": task_id, "arm": arm,
                                        "repetition": repetition,
                                        "execution_state": "completed", "ranked_files": []}
                if item["case_kind"] in {"privacy", "unsupported"}:
                    slot.update(execution_state="refused", refusal_code="refused_expected")
                elif item["expected_arm_outcomes"][arm]["state"] == "unavailable":
                    slot.update(execution_state="unavailable")
                elif item["case_kind"] in {"body_positive", "metadata_anchor"}:
                    slot["ranked_files"] = ranked(
                        fusion_rank if arm == FUSION_ARM else comparator_rank)
                if item["case_kind"] == "absent" and slot["execution_state"] == "completed":
                    slot.update(capability_state="available", scope_complete=True)
                slots.append(slot)
    planned = [
        {"task_id": task_id, **{field: meta[task_id][field] for field in
         ("family", "intent_id", "wording_stratum", "case_kind")}}
        for task_id in body_ids
    ]
    if common_count is not None:
        # A low-floor fixture models preflight-only work: candidate scopes are
        # not observed and must not be fabricated as positive universe sizes.
        universes = {task_id: None for task_id in universes}
    eligibility = [
        {"task_id": task_id, "arm": arm, "admitted": True,
         "captured_text_reachable": True, "token_count": 10,
         "truncated": common_count is not None and body_ids.index(task_id) >= common_count,
         "representation_identity": ARM_REPRESENTATIONS[arm]}
        for task_id in body_ids for arm in COMPARISON_ARMS
    ]
    common = select_common_positives(planned, eligibility)
    return meta, golds, universes, common, slots


__all__ = ["PG_PUB", "PIN", "SQLITE_PUB", "quality_inputs", "study_catalog",
           "study_decisions", "study_reports"]
