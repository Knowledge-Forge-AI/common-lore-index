"""Layered cost summary over already-admitted ``PhaseRecorder`` observations.

Shared observations stay once under their shared owner category. Nothing is
divided among arms, overlapping layers are never summed, and missing physical
PostgreSQL or RSS work remains unknown. Per-arm query figures consume both the
``PhaseRecorder`` caller phase-boundary ``elapsed_ns`` and any explicitly
arm-scoped ``FieldSample`` observations without combining layers.
"""

from __future__ import annotations

from typing import Any

from retrieval_supplement_analysis_admission import AdmittedBundle
from retrieval_supplement_analysis_values import DISPLAY_DIGITS
from retrieval_supplement_primitives import ARMS


def _round(value: float) -> float:
    return round(value, DISPLAY_DIGITS)


def _field_sample_summaries(arm_samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for sample in arm_samples:
        key = f"{sample['name']}:{sample['layer']}:{sample['units']}"
        buckets.setdefault(key, []).append(sample)
    out: dict[str, dict[str, Any]] = {}
    for key in sorted(buckets):
        rows = buckets[key]
        observed = [
            float(r["value"])
            for r in rows
            if r["value"] is not None and r["state"] not in {"unknown", "unattributed"}
        ]
        unknown_cnt = sum(
            r["value"] is None or r["state"] in {"unknown", "unattributed"} for r in rows
        )
        out[key] = {
            "name": rows[0]["name"],
            "layer": rows[0]["layer"],
            "units": rows[0]["units"],
            "status": "available" if observed else "unavailable",
            "observed_denominator": len(observed),
            "unknown_denominator": unknown_cnt,
            "mean": _round(sum(observed) / len(observed)) if observed else None,
            "minimum": min(observed) if observed else None,
            "maximum": max(observed) if observed else None,
            "additive_with_other_layers": False,
        }
    return out


def cost_analysis(admitted: AdmittedBundle) -> dict[str, Any]:
    """Summarize phase-boundary elapsed times, explicit field samples and unknowns."""
    observations: list[dict[str, Any]] = []
    shared_observations: list[dict[str, Any]] = []
    unknown_sample_count = 0
    unknown_elapsed_count = 0
    per_arm: dict[str, dict[str, Any]] = {}

    for arm in ARMS:
        arm_obs = [
            obs for obs in admitted.costs
            if obs.scope == "per_arm_query" and obs.arm == arm
        ]
        elapsed_vals = [
            obs.elapsed_ns
            for obs in arm_obs
            if obs.elapsed_ns is not None and obs.elapsed_observation == "caller_phase_boundary"
        ]
        arm_unknown_elapsed = sum(
            obs.elapsed_ns is None or obs.elapsed_observation == "unknown"
            for obs in arm_obs
        )
        arm_failed = sum(obs.state == "failed" for obs in arm_obs)
        flat_samples = [
            dict(sample)
            for obs in arm_obs
            for sample in obs.samples
            if sample["state"] != "shared"
        ]
        sample_groups = _field_sample_summaries(flat_samples)
        any_sample_available = any(g["status"] == "available" for g in sample_groups.values())
        available = bool(elapsed_vals) or any_sample_available
        reason: str | None
        if available:
            reason = None
        elif arm_obs:
            reason = "per_arm_query_values_unknown"
        else:
            reason = "no_per_arm_query_observations"

        per_arm[arm] = {
            "status": "available" if available else "unavailable",
            "reason": reason,
            "observation_denominator": len(arm_obs),
            "sample_denominator": len(elapsed_vals),
            "elapsed_denominator": len(elapsed_vals),
            "unknown_elapsed_count": arm_unknown_elapsed,
            "failed_record_count": arm_failed,
            "elapsed_layer": "caller_phase_boundary",
            "elapsed_units": "ns",
            "mean_elapsed_ns": (
                _round(sum(elapsed_vals) / len(elapsed_vals)) if elapsed_vals else None
            ),
            "minimum_elapsed_ns": min(elapsed_vals) if elapsed_vals else None,
            "maximum_elapsed_ns": max(elapsed_vals) if elapsed_vals else None,
            "field_samples": sample_groups,
            "additive_with_field_samples": False,
        }

    for obs in admitted.costs:
        if obs.elapsed_ns is None or obs.elapsed_observation == "unknown":
            unknown_elapsed_count += 1
        samples_out: list[dict[str, Any]] = []
        for sample in obs.samples:
            if sample["state"] in {"unknown", "unattributed"} or sample["value"] is None:
                unknown_sample_count += 1
            samples_out.append({
                "name": sample["name"],
                "units": sample["units"],
                "value": sample["value"],
                "state": sample["state"],
                "layer": sample["layer"],
                "shared_owner_category": sample["shared_owner_category"],
                "additive": False,
            })
        row = {
            "phase": obs.phase,
            "scope": obs.scope,
            "task_id": obs.task_id,
            "arm": obs.arm,
            "repetition": obs.repetition,
            "shared_owner_category": obs.shared_owner_category,
            "state": obs.state,
            "observation_complete": obs.observation_complete,
            "elapsed_ns": obs.elapsed_ns,
            "elapsed_units": obs.elapsed_units,
            "elapsed_observation": obs.elapsed_observation,
            "error_flags": dict(obs.error_flags),
            "samples": samples_out,
        }
        observations.append(row)
        if obs.scope == "shared":
            shared_observations.append(row)

    phases = sorted({obs.phase for obs in admitted.costs})
    return {
        "record_count": admitted.cost_record_count,
        "cost_completeness": admitted.cost_completeness,
        "phase_presence_is_not_per_slot_cost_coverage": True,
        "phases_observed": phases,
        "shared_observation_count": len(shared_observations),
        "observations": observations,
        "per_arm_query": per_arm,
        "unknown_sample_count": unknown_sample_count,
        "unknown_elapsed_count": unknown_elapsed_count,
        "shared_observations_retained_once": True,
        "shared_cost_allocated_to_arms": False,
        "cross_layer_sum": None,
        "non_additive_across_layers": True,
        "physical_postgresql_cost": "unknown",
        "physical_rss_cost": "unknown",
        "cost_completeness_upgraded": False,
        "omitted_private_fields": [
            "raw_sql", "vectors", "queries", "source_bodies", "paths",
            "endpoints", "runtime_names", "journal_payloads", "attached_strings",
        ],
    }


__all__ = ["cost_analysis"]
