"""Pin-aware actual grading for body-study snapshot-bearing hits."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from retrieval_body_study_metrics import METRIC_NAMES, file_metrics_at_10
from retrieval_body_study_values import digest_value, require
from retrieval_semantic_file_projection import (
    collapse_gold_targets_to_files,
    compute_file_level_ranking_metrics,
    verify_and_project_returned_files,
)
from retrieval_semantic_provenance import VerifiedPublicationPin


def _hit_provenance(value: object) -> dict[str, Any]:
    """Retain only public identity fields from one snapshot-bearing hit."""
    if hasattr(value, "to_dict") and callable(value.to_dict):
        raw = value.to_dict()
        if isinstance(raw, Mapping):
            value = raw
    if isinstance(value, Mapping):
        loc = value.get("locator", value.get("file_identity", value))
    else:
        loc = getattr(value, "locator", getattr(value, "file_identity", value))
    binding = getattr(loc, "binding_id", getattr(loc, "source_binding_id", None))
    snapshot = getattr(loc, "snapshot_id", None)
    path = getattr(loc, "source_relative_path", None)
    if isinstance(loc, Mapping):
        binding = loc.get("binding_id", loc.get("source_binding_id", binding))
        snapshot = loc.get("snapshot_id", snapshot)
        path = loc.get("source_relative_path", path)
    require(type(binding) is str and type(snapshot) is str and type(path) is str,
            "body.grading:hit_identity")
    return {"binding_id": binding, "snapshot_id": snapshot, "source_relative_path": path}


@dataclass(frozen=True, slots=True)
class ActualGrade:
    """Actual measurement plus the pair-shaped arithmetic audit."""

    pin_identity: str
    projected_files: tuple[tuple[str, str], ...]
    ranked_files: tuple[tuple[str, str], ...]
    snapshot_hits: tuple[Mapping[str, Any], ...]
    normalized_gold_files: tuple[tuple[str, str, int], ...]
    metrics: Mapping[str, float]
    inert_metrics: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pin_identity": self.pin_identity,
            "projected_files": [list(item) for item in self.projected_files],
            "ranked_files": [list(item) for item in self.ranked_files],
            "snapshot_hits": [dict(item) for item in self.snapshot_hits],
            "normalized_gold_files": [list(item) for item in self.normalized_gold_files],
            "metrics": dict(self.metrics),
            "inert_metrics": dict(self.inert_metrics),
            "metric_keys_exact": tuple(sorted(self.metrics)) == tuple(sorted(METRIC_NAMES)),
            "six_decimal_equal": dict(self.metrics) == dict(self.inert_metrics),
        }


def grade_snapshot_hits(
    *,
    returned_items: Sequence[Any],
    gold_targets: Sequence[Any],
    verified_pin: VerifiedPublicationPin,
    task: Any,
    private_markers: Sequence[str] = (),
) -> ActualGrade:
    """Grade through maintained pin-aware metrics before invoking inert arithmetic."""
    require(type(verified_pin) is VerifiedPublicationPin, "body.grading:pin_type")
    require(verified_pin.is_factory_issued(), "body.grading:pin_issued")
    verified_pin.revalidate_current()
    projected = verify_and_project_returned_files(
        tuple(returned_items), verified_pin=verified_pin, task=task,
        private_markers=private_markers,
    )
    actual = compute_file_level_ranking_metrics(
        tuple(returned_items), tuple(gold_targets), verified_pin=verified_pin, task=task,
        cutoffs=(10,), private_markers=private_markers,
    )
    collapsed = collapse_gold_targets_to_files(tuple(gold_targets), graph_id=verified_pin.graph_id)
    inert_golds = tuple(
        (item.binding_id, item.source_relative_path, item.relevance_grade)
        for item in collapsed if item.relevance_grade > 0
    )
    inert = file_metrics_at_10(projected, inert_golds)
    require(set(actual) == set(METRIC_NAMES), "body.grading:actual_metric_keys")
    require(set(inert) == set(METRIC_NAMES), "body.grading:inert_metric_keys")
    for name in METRIC_NAMES:
        require(round(float(actual[name]), 6) == round(float(inert[name]), 6),
                "body.grading:metric_mismatch")
    hits = tuple(_hit_provenance(item) for item in returned_items)
    result = ActualGrade(
        digest_value(verified_pin.to_dict()),
        tuple(projected), tuple(projected), hits, inert_golds,
        {name: round(float(actual[name]), 6) for name in METRIC_NAMES},
        {name: round(float(inert[name]), 6) for name in METRIC_NAMES},
    )
    require(result.to_dict()["metric_keys_exact"] and result.to_dict()["six_decimal_equal"],
            "body.grading:audit_flags")
    return result


__all__ = ["ActualGrade", "grade_snapshot_hits"]
