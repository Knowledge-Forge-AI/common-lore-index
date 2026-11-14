"""Inert prospective preparation of supplement document/query exchanges.

``prepare_supplement`` re-checks every scope at call time (corpus/catalog binding,
expectations versus policy-derived applicability, exposure identities, denial,
reachability and caps) and returns serializable descriptors only. Each eligible
query appears once per representation; the three repetitions *reference* that one
prepared exchange instead of multiplying inference. Fusion references the
public-text-v2 exchange; no HNSW or metadata representation is substituted.
Nothing is executed, embedded, tokenized or authorized here.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from retrieval_supplement_applicability import (
    CandidateExposure,
    binding_denial,
    check_catalog_applicability,
    expected_applicability,
    reachability_report,
)
from retrieval_supplement_catalog import SupplementCatalog
from retrieval_supplement_corpus import SupplementCorpus, check_catalog_corpus
from retrieval_supplement_tasks import task_digests
from retrieval_supplement_values import (
    ARMS,
    AUTHORED_TASK_CAP,
    CANDIDATE_PAIR_CAP,
    COMBINED_RECORD_CAP,
    FUSION_ARM,
    MAX_EXCERPT_BYTES,
    MAX_TOKENS,
    PREPARATION_TASK_CAP,
    REPETITIONS,
    V1_ARM,
    V2_ARM,
    integer,
    require,
)

PREPARATION_SCHEMA = "cloe-calibration-supplement-preparation-v1"
REPRESENTATIONS: tuple[tuple[str, str], ...] = (
    (V1_ARM, "canonical-node-v1"),
    (V2_ARM, "canonical-node-public-text-v2"),
)
COUNT_ALGEBRA = "document_count + query_count == record_count == embedding_count"


def combined_record_count(v1_documents: int, v1_queries: int, v2_documents: int, v2_queries: int) -> int:
    """Check the independent worker record budget; per-class limits stay separate."""
    counts = (v1_documents, v1_queries, v2_documents, v2_queries)
    for count in counts:
        integer(count, "preparation.record_count", 0, COMBINED_RECORD_CAP)
    combined = sum(counts)
    require(combined <= COMBINED_RECORD_CAP, "preparation:combined_record_cap_exceeded")
    return combined


def _validated_exposures(
    exposures: Iterable[CandidateExposure],
    corpus: SupplementCorpus,
    policy: BodyProjectionPolicy,
) -> tuple[CandidateExposure, ...]:
    items = tuple(exposures)
    require(all(isinstance(e, CandidateExposure) for e in items), "preparation:exposure_type")
    identities = [e.identity for e in items]
    require(len(set(identities)) == len(identities), "preparation:duplicate_exposure")
    known_files = corpus.file_keys()
    for exposure in items:
        binding_denial(policy, exposure.binding_id)
        require((exposure.binding_id, exposure.path) in known_files, "preparation:unknown_exposure")
    return items


def _documents(
    items: tuple[CandidateExposure, ...],
    arm: str,
    corpus: SupplementCorpus,
    policy: BodyProjectionPolicy,
) -> list[dict[str, Any]]:
    denied_files = corpus.denied_binding_ids
    documents: dict[tuple[str, str], dict[str, Any]] = {}
    for exposure in items:
        if exposure.arm != arm:
            continue
        require(
            binding_denial(policy, exposure.binding_id) is None
            and exposure.binding_id not in denied_files,
            "preparation:denied_eligible_document",
        )
        if arm == V2_ARM:
            require(exposure.spanned, "preparation:public_text_span_required")
        key = (exposure.binding_id, exposure.canonical_key)
        require(key not in documents, "preparation:duplicate_document_pair")
        record = exposure.to_dict()
        del record["arm"]
        documents[key] = record
    require(len(documents) <= CANDIDATE_PAIR_CAP, "preparation:candidate_pair_cap_exceeded")
    return [documents[key] for key in sorted(documents)]


def _check_fusion(items: tuple[CandidateExposure, ...]) -> None:
    public_text = {
        (e.binding_id, e.canonical_key): (e.path, e.start_line, e.end_line)
        for e in items if e.arm == V2_ARM
    }
    for exposure in items:
        if exposure.arm != FUSION_ARM:
            continue
        key = (exposure.binding_id, exposure.canonical_key)
        require(
            public_text.get(key) == (exposure.path, exposure.start_line, exposure.end_line),
            "preparation:fusion_substitution",
        )


def _eligible(catalog: SupplementCatalog, arm: str, policy: BodyProjectionPolicy) -> list[str]:
    """Task IDs whose authored expectation is *completed* and exactly policy-derived."""
    eligible: list[str] = []
    for case in catalog.cases:
        task = case.task
        authored = case.expectations[arm]
        if authored["execution"] != "completed":
            continue
        if authored == expected_applicability(task, arm, policy):
            eligible.append(task.task_id)
    return eligible


def prepare_supplement(
    catalog: SupplementCatalog,
    corpus: SupplementCorpus,
    *,
    policy: BodyProjectionPolicy,
    exposures: Iterable[CandidateExposure],
) -> dict[str, Any]:
    """Return inert prospective exchanges, repetition references and count algebra."""
    require(isinstance(catalog, SupplementCatalog), "catalog:required")
    require(isinstance(corpus, SupplementCorpus), "corpus:required")
    require(isinstance(policy, BodyProjectionPolicy), "policy:required")
    policy.validate()
    identity = catalog.identity
    require(policy.graph_id == identity.graph_id, "preparation:policy_graph_mismatch")
    tasks = catalog.tasks
    require(len(tasks) <= AUTHORED_TASK_CAP, "preparation:authored_cap_exceeded")
    require(len(tasks) <= PREPARATION_TASK_CAP, "preparation:task_cap_exceeded")
    check_catalog_corpus(catalog, corpus)
    applicability = check_catalog_applicability(catalog, policy)
    require(applicability["mismatch_count"] == 0, "preparation:expectation_mismatch")
    items = _validated_exposures(exposures, corpus, policy)
    _check_fusion(items)
    reachability = reachability_report(catalog, items, policy)
    require(reachability["gap_count"] == 0, "preparation:gold_reachability_missing")
    require(reachability["floor_met"], "preparation:common_floor_not_met")

    by_id = {task.task_id: task for task in tasks}
    digests = {task.task_id: task_digests(task) for task in tasks}
    exchanges: list[dict[str, Any]] = []
    exchange_ids: dict[str, str] = {}
    eligible_by_arm: dict[str, list[str]] = {}
    for arm, representation in REPRESENTATIONS:
        documents = _documents(items, arm, corpus, policy)
        eligible = _eligible(catalog, arm, policy)
        eligible_by_arm[arm] = eligible
        exchange_id = f"{identity.supplement_id}:{representation}"
        exchange_ids[arm] = exchange_id
        queries = [
            {
                "query_id": f"{exchange_id}:{task_id}",
                "task_id": task_id,
                "text": by_id[task_id].question,
                **digests[task_id],
            }
            for task_id in eligible
        ]
        exchanges.append({
            "exchange_id": exchange_id,
            "representation": representation,
            "arm": arm,
            "documents": documents,
            "queries": queries,
            "document_count": len(documents),
            "query_count": len(queries),
            "record_count": len(documents) + len(queries),
            "embedding_count_expected": len(documents) + len(queries),
        })
    combined = combined_record_count(exchanges[0]["document_count"], exchanges[0]["query_count"],
                                     exchanges[1]["document_count"], exchanges[1]["query_count"])

    fusion_eligible = _eligible(catalog, FUSION_ARM, policy)
    require(
        set(fusion_eligible) <= set(eligible_by_arm[V2_ARM]),
        "preparation:fusion_without_public_text_query",
    )
    eligible_by_arm[FUSION_ARM] = fusion_eligible
    exchange_ids[FUSION_ARM] = exchange_ids[V2_ARM]
    references = [
        {
            "arm": arm,
            "task_id": task_id,
            "repetition": repetition,
            "exchange_id": exchange_ids[arm],
            "query_id": f"{exchange_ids[arm]}:{task_id}",
        }
        for arm in (V1_ARM, V2_ARM, FUSION_ARM)
        for task_id in eligible_by_arm[arm]
        for repetition in range(1, REPETITIONS + 1)
    ]
    return {
        "schema": PREPARATION_SCHEMA,
        "supplement_id": identity.supplement_id,
        "corpus_id": identity.corpus_id,
        "phase": identity.phase,
        "catalog_sha256": catalog.digest,
        "corpus_sha256": corpus.digest,
        "policy_digest": policy.policy_digest(),
        "ordered_task_ids": [task.task_id for task in tasks],
        "task_digests": digests,
        "arms": list(ARMS),
        "repetitions": REPETITIONS,
        "slot_count": len(tasks) * len(ARMS) * REPETITIONS,
        "exchanges": exchanges,
        "references": references,
        "reachability": {
            "common_positive_count": reachability["common_positive_count"],
            "family_counts": reachability["family_counts"],
            "floor_met": reachability["floor_met"],
        },
        "encodings": {
            "count_algebra": COUNT_ALGEBRA,
            "combined_records": combined,
            "combined_record_cap": COMBINED_RECORD_CAP,
            "candidate_pair_cap": CANDIDATE_PAIR_CAP,
            "preparation_task_cap": PREPARATION_TASK_CAP,
            "authored_task_cap": AUTHORED_TASK_CAP,
            "max_tokens": MAX_TOKENS,
            "max_excerpt_bytes": MAX_EXCERPT_BYTES,
            "token_counts": None,
            "truncation": None,
        },
        "tokenization": "unknown",
        "queries_executed": False,
        "model_workers_started": 0,
        "native_qualified": False,
        "execution_authorized": False,
    }


__all__ = ["PREPARATION_SCHEMA", "prepare_supplement", "combined_record_count"]
