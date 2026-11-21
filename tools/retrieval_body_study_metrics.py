"""Unit-only metric adapter for the inert W5 body-study quality policy.

This adapter is limited to already-projected synthetic unit pairs.  It keeps
binding-qualified identity while delegating arithmetic to the maintained
ranking owner; an actual controller must use the pin-bound file owner.
"""
from __future__ import annotations

from collections.abc import Sequence
import math
from typing import Any

from common_lore_index.retrieval.query_contracts import EvaluationLocator
from retrieval_evaluation_grading import compute_ranking_metrics
from retrieval_evaluation_schema import GoldTarget
from retrieval_body_study_values import CUTOFF, require

FileKey = tuple[str, str]
METRIC_NAMES: tuple[str, ...] = (
    "file_recall@10",
    "file_mrr@10",
    "file_ndcg@10",
)


def _file_key(value: object, code: str) -> FileKey:
    if hasattr(value, "file_identity_key") and callable(value.file_identity_key):
        value = value.file_identity_key()
    elif hasattr(value, "file_key") and callable(value.file_key):
        value = value.file_key()
    require(
        isinstance(value, (tuple, list)) and len(value) == 2
        and type(value[0]) is str and type(value[1]) is str
        and bool(value[0]) and bool(value[1]),
        code,
    )
    assert isinstance(value, (tuple, list))
    return str(value[0]), str(value[1])


def dedupe_ranked_files(ranked: Sequence[Sequence[str]]) -> tuple[FileKey, ...]:
    """Collapse duplicate chunks without collapsing same paths across bindings."""
    require(
        isinstance(ranked, Sequence) and not isinstance(ranked, (str, bytes, bytearray)),
        "body_study:ranked_sequence",
    )
    seen: set[FileKey] = set()
    result: list[FileKey] = []
    for item in ranked:
        key = _file_key(item, "body_study:ranked_file_identity")
        if key not in seen:
            seen.add(key)
            result.append(key)
    return tuple(result)


def collapse_gold_files(golds: Sequence[Sequence[Any]]) -> dict[FileKey, int]:
    """Collapse ``(binding_id, path, grade)`` golds by file using max grade."""
    require(
        isinstance(golds, Sequence) and not isinstance(golds, (str, bytes, bytearray)),
        "body_study:gold_sequence",
    )
    result: dict[FileKey, int] = {}
    for gold in golds:
        if hasattr(gold, "locator") and hasattr(gold, "relevance_grade"):
            key = _file_key(gold.locator, "body_study:gold_identity")
            grade = gold.relevance_grade
        else:
            require(
                isinstance(gold, (tuple, list)) and len(gold) == 3,
                "body_study:gold_shape",
            )
            key = _file_key(gold[:2], "body_study:gold_identity")
            grade = gold[2]
        require(type(grade) is int and 0 <= grade <= 3, "body_study:gold_grade")
        result[key] = max(int(grade), result.get(key, 0))
    return {key: grade for key, grade in result.items() if grade > 0}


def _shared_file_metrics(
    ranked: Sequence[Sequence[str]], golds: Sequence[Sequence[Any]]
) -> dict[str, float]:
    """Adapt already-projected files to the maintained metric owner.

    This unit-only adapter receives already-projected binding/path identities.
    A controller with snapshot-bearing identities must call the maintained
    ``compute_file_level_ranking_metrics`` owner directly.
    """
    files = dedupe_ranked_files(ranked)
    positive = collapse_gold_files(golds)
    returned = tuple(EvaluationLocator(binding_id, path) for binding_id, path in files)
    gold_targets = tuple(
        GoldTarget(EvaluationLocator(binding_id, path), grade, "body-study-file-gold")
        for (binding_id, path), grade in positive.items()
    )
    metrics = compute_ranking_metrics(returned, gold_targets, cutoffs=(CUTOFF,))
    return {
        "file_recall@10": metrics["recall@10"],
        "file_mrr@10": metrics["mrr@10"],
        "file_ndcg@10": metrics["ndcg@10"],
    }


def file_metrics_at_10(
    ranked: Sequence[Sequence[str]], golds: Sequence[Sequence[Any]]
) -> dict[str, float]:
    """Return shared arithmetic for an already-projected unit-test pair list."""
    return _shared_file_metrics(ranked, golds)


def expected_random_baseline(universe_size: int | None, gold_count: int = 1) -> dict[str, Any]:
    """Return analytic random context only for the protocol's single-gold case."""
    require(universe_size is None or type(universe_size) is int,
            "body_study:universe_size")
    require(type(gold_count) is int and gold_count >= 0, "body_study:universe_gold_count")
    if universe_size is None or universe_size <= 0:
        return {
            "status": "unavailable",
            "reason": "universe_unavailable",
            "gold_count": gold_count,
            "expected_recall@10": None,
            "expected_ndcg@10": None,
        }
    if gold_count != 1:
        return {
            "status": "unavailable",
            "reason": "analytic_single_gold_only",
            "gold_count": gold_count,
            "expected_recall@10": None,
            "expected_ndcg@10": None,
        }
    top = min(CUTOFF, universe_size)
    return {
        "status": "available",
        "gold_count": 1,
        "expected_recall@10": round(top / universe_size, 6),
        "expected_ndcg@10": round(
            sum(1.0 / math.log2(rank + 1.0) for rank in range(1, top + 1))
            / universe_size,
            6,
        ),
    }


__all__ = [
    "FileKey",
    "METRIC_NAMES",
    "collapse_gold_files",
    "dedupe_ranked_files",
    "expected_random_baseline",
    "file_metrics_at_10",
]
