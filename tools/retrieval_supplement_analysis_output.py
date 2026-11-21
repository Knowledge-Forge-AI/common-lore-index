"""Closed-allowlist analysis artifacts, Markdown tables and atomic archive publication.

Output contains only registered/caller-bound public task/arm/family labels,
original and analyzer hashes, numeric summaries and closed safe categories. No
raw query, SQL, vector, body, path, endpoint, runtime name or attached string
can flow through. Publication delegates to ``publish_analysis_pair``: both logical
members become visible together in one exclusive archive. All acceptance flags remain false and the
operator criteria decision remains pending.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from retrieval_supplement_analysis_admission import admit_bundle
from retrieval_supplement_analysis_costs import cost_analysis
from retrieval_supplement_analysis_custody import publish_analysis_pair, read_retained_bundle
from retrieval_supplement_analysis_paired import paired_analysis
from retrieval_supplement_analysis_values import (
    ANALYSIS_SCHEMA,
    MAX_BUNDLE_BYTES,
    MAX_OUTPUT_JSON_BYTES,
    MAX_REPORT_BYTES,
    ExpectedProvenance,
    TaskDescriptor,
    as_digest,
    refuse,
)
from retrieval_supplement_primitives import (
    ARMS,
    POSITIVE_FAMILIES,
    canonical_bytes,
    digest_bytes,
)

_HEADLINE_METRICS = (
    "file_recall@5",
    "file_mrr@5",
    "file_ndcg@5",
    "file_recall@10",
    "file_mrr@10",
    "file_ndcg@10",
)


def analyze_retained(
    raw: bytes,
    *,
    provenance: ExpectedProvenance,
    tasks: Sequence[TaskDescriptor],
    analyzer_source_sha256: str,
    counted_worker_tokens: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Admit retained bytes and build the bounded decision artifact."""
    analyzer = as_digest(analyzer_source_sha256, "analyzer_source")
    admitted = admit_bundle(
        raw,
        provenance=provenance,
        tasks=tasks,
        counted_worker_tokens=counted_worker_tokens,
    )
    refuse(
        analyzer not in {
            provenance.evaluator_closure_sha256,
            provenance.worker_closure_sha256,
            provenance.input_pin_sha256,
            admitted.declaration_source_closure_sha256,
        },
        "analysis:analyzer_identity_must_be_separate",
    )
    paired = paired_analysis(admitted)
    costs = cost_analysis(admitted)
    result: dict[str, Any] = {
        "schema": ANALYSIS_SCHEMA,
        "origin": admitted.origin,
        "native_observation_bound": admitted.native_qualified,
        "bindings": {
            "input_raw_sha256": admitted.raw_sha256,
            "input_pin_sha256": provenance.input_pin_sha256,
            "attempt_id_sha256": digest_bytes(provenance.attempt_id.encode("utf-8")),
            "declaration_source_closure_sha256": admitted.declaration_source_closure_sha256,
            "evaluator_closure_sha256": provenance.evaluator_closure_sha256,
            "worker_closure_sha256": provenance.worker_closure_sha256,
            "analyzer_source_sha256": analyzer,
            "counted_worker_tokens_verified": admitted.counted_worker_tokens_verified,
        },
        "counts": {
            "tasks": admitted.task_count,
            "positive_tasks": len(paired["positive_task_ids"]),
            "arms": len(ARMS),
            "repetitions": 3,
            "slots": len(admitted.slots),
            "states": dict(admitted.state_counts),
            "comparable_positive_tasks": len(admitted.comparable_positive_tasks),
            "family_counts": dict(admitted.family_counts),
        },
        "paired": paired,
        "costs": costs,
        "predecessor_failure_sha256": [
            digest_bytes(code.encode("utf-8")) for code in provenance.predecessor_failure_codes
        ],
        "predecessors_pooled_into_results": False,
        "decision": {
            "operator_criteria_decision": "pending",
            "criteria_proposal_allowed": admitted.floor_met,
            "threshold_selected": False,
            "preferred_arm_selected": False,
            "evidence_summary": (
                "floor_met" if admitted.floor_met else "below_comparable_positive_floor"
            ),
        },
        "acceptance": {
            "scientific_acceptance": False,
            "heldout_acceptance": False,
            "full_w5_acceptance": False,
            "production_acceptance": False,
            "native_platform_acceptance": False,
        },
        "limitations": [
            "repetitions_share_embeddings_not_independent_samples",
            "descriptive_paired_observation_only_no_significance_claim",
            "unknown_values_are_never_zero",
            "shared_costs_not_allocated_and_layers_not_additive",
            "physical_postgresql_and_rss_costs_unknown",
            "small_calibration_set_no_generalization_claim",
        ],
    }
    payload = canonical_bytes(result)
    refuse(len(payload) <= MAX_OUTPUT_JSON_BYTES, "analysis:output_too_large")
    result["output_sha256"] = digest_bytes(payload)
    return result


def _fmt(value: Any) -> str:
    return "unavailable" if value is None else f"{float(value):.6f}"


def _fmt_with_denom(mean: Any, denom: int, total: int) -> str:
    return f"{_fmt(mean)} (n={denom}/{total})"


def render_report(analysis: dict[str, Any]) -> str:
    """Deterministic operator-reviewable Markdown tables over the closed JSON artifact."""
    counts = analysis["counts"]
    bindings = analysis["bindings"]
    paired = analysis["paired"]
    ns_status = paired["namespace_status"]
    span_info = ns_status["span"]
    span_label = (
        "available"
        if span_info["status"] == "available"
        else f"unavailable ({span_info['reason']})"
    )
    pos_total = int(counts["positive_tasks"])
    comp_total = int(counts["comparable_positive_tasks"])

    lines: list[str] = [
        "# Retained supplement analysis",
        "",
        f"- Origin: `{analysis['origin']}` (native observation bound: `{analysis['native_observation_bound']}`)",
        f"- Attempt identity SHA-256: `{bindings['attempt_id_sha256']}`; input: `{bindings['input_raw_sha256']}`",
        f"- Input pin: `{bindings['input_pin_sha256']}`; "
        f"evaluator closure: `{bindings['evaluator_closure_sha256']}`",
        f"- Worker closure: `{bindings['worker_closure_sha256']}`; "
        f"analyzer source: `{bindings['analyzer_source_sha256']}`",
        f"- Counted worker tokens co-verified: `{bindings['counted_worker_tokens_verified']}`",
        f"- Grid: tasks={counts['tasks']} (positives={pos_total}), arms={counts['arms']}, "
        f"repetitions=3, slots={counts['slots']}",
        f"- Slot states: `{counts['states']}`",
        f"- Metric namespaces: `file=available`, `span={span_label}`",
        "",
        "## Family breakdown",
        "",
        "| Positive family | Comparable untruncated positives | Floor (>=2) |",
        "|---|---:|---|",
    ]
    for family in POSITIVE_FAMILIES:
        f_cnt = int(counts["family_counts"][family])
        lines.append(f"| `{family}` | {f_cnt} | {'met' if f_cnt >= 2 else 'below_floor'} |")
    lines += [
        f"| **Total comparable** | **{comp_total} / {pos_total}** | "
        f"**{'met' if paired['floor_met'] else 'below_floor'} (>=8)** |",
        "",
        "## Task-weighted headline metrics (common comparable positives)",
        "",
        "| Metric | " + " | ".join(ARMS) + " |",
        "|---|" + "---|" * len(ARMS),
    ]
    for metric in _HEADLINE_METRICS:
        summaries = paired["metrics"][metric]["arm_summaries"]
        cells = [
            _fmt_with_denom(
                summaries[arm]["comparable_task_weighted_mean"],
                int(summaries[arm]["comparable_task_denominator"]),
                pos_total,
            )
            for arm in ARMS
        ]
        lines.append(f"| `{metric}` | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Task-weighted headline metrics (all observed positives per arm)",
        "",
        "| Metric | " + " | ".join(ARMS) + " |",
        "|---|" + "---|" * len(ARMS),
    ]
    for metric in _HEADLINE_METRICS:
        summaries = paired["metrics"][metric]["arm_summaries"]
        cells = [
            _fmt_with_denom(
                summaries[arm]["task_weighted_mean"],
                int(summaries[arm]["task_denominator"]),
                pos_total,
            )
            for arm in ARMS
        ]
        lines.append(f"| `{metric}` | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Pairwise arm comparisons (`file_ndcg@10`, common comparable positives)",
        "",
        "| Left arm | Right arm | Paired N / Positives | Excluded | Mean diff | W / T / L | Status |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for comp in paired["metrics"]["file_ndcg@10"]["comparisons"]:
        denom = int(comp["paired_task_denominator"])
        excl = len(comp["excluded_positive_tasks"])
        wtl = f"{comp['win']} / {comp['tie']} / {comp['loss']}"
        status = (
            "available"
            if comp["status"] == "available"
            else f"unavailable ({comp['reason']})"
        )
        lines.append(
            f"| `{comp['left_arm']}` | `{comp['right_arm']}` | {denom} / {pos_total} | "
            f"{excl} | {_fmt(comp['mean_signed_difference'])} | {wtl} | {status} |"
        )

    lines += [
        "",
        "## Controls",
        "",
        "| Control kind | Tasks | Slots | States | Safety refusal (T/F/U) | "
        "Absent answer (T/F/U) | Capability match (T/F) |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for kind in sorted(paired["controls"]):
        row = paired["controls"][kind]
        sr = row["safety_refusal"]
        aa = row["absent_answer"]
        cm = row["capability_expectation_matches"]
        lines.append(
            f"| `{kind}` | {row['task_count']} | {row['slot_count']} | `{row['states']}` | "
            f"{sr['true']}/{sr['false']}/{sr['unknown']} | "
            f"{aa['true']}/{aa['false']}/{aa['unknown']} | {cm['true']}/{cm['false']} |"
        )

    costs = analysis["costs"]
    lines += [
        "",
        "## Layered costs",
        "",
        f"Phase records: {costs['record_count']} (shared unallocated: "
        f"{costs['shared_observation_count']}); phases: `{costs['phases_observed']}`; "
        f"unknown elapsed: {costs['unknown_elapsed_count']}; "
        f"unknown field samples: {costs['unknown_sample_count']}.",
        "Shared costs are retained once, never allocated to arms; layers are not additive.",
        "",
        "| Arm | Query status | Elapsed N / Obs | Unknown | Min ns | Mean ns | Max ns |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        q = costs["per_arm_query"][arm]
        q_status = (
            "available" if q["status"] == "available" else f"unavailable ({q['reason']})"
        )
        min_ns = "unavailable" if q["minimum_elapsed_ns"] is None else str(q["minimum_elapsed_ns"])
        max_ns = "unavailable" if q["maximum_elapsed_ns"] is None else str(q["maximum_elapsed_ns"])
        lines.append(
            f"| `{arm}` | {q_status} | {q['elapsed_denominator']} / "
            f"{q['observation_denominator']} | {q['unknown_elapsed_count']} | "
            f"{min_ns} | {_fmt(q['mean_elapsed_ns'])} | {max_ns} |"
        )

    lines += ["", "## Limitations", ""]
    lines += [f"- `{item}`" for item in analysis["limitations"]]
    lines += [
        "",
        "## Decision boundary",
        "",
        "Operator criteria decision: pending. No threshold or preferred arm is selected.",
        "Scientific, held-out, full-W5, production and native-platform acceptance: false.",
        f"Predecessor failure identities retained, never pooled: `{analysis['predecessor_failure_sha256']}`.",
        "",
    ]
    text = "\n".join(lines)
    refuse(len(text.encode("utf-8")) <= MAX_REPORT_BYTES, "analysis:report_too_large")
    return text


def publish_analysis(
    output_root: Path,
    *,
    json_member: str,
    report_member: str,
    analysis: dict[str, Any],
) -> dict[str, str]:
    """Atomically publish the JSON/Markdown archive; refuse an existing archive."""
    json_raw = canonical_bytes(analysis)
    refuse(len(json_raw) <= MAX_OUTPUT_JSON_BYTES, "analysis:output_too_large")
    report_raw = render_report(analysis).encode("utf-8")
    return publish_analysis_pair(
        output_root,
        json_member=json_member,
        report_member=report_member,
        json_raw=json_raw,
        report_raw=report_raw,
    )


def analyze_retained_member(
    input_root: Path,
    member: str,
    output_root: Path,
    *,
    provenance: ExpectedProvenance,
    tasks: Sequence[TaskDescriptor],
    analyzer_source_sha256: str,
    json_member: str,
    report_member: str,
    counted_worker_tokens: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    """Read retained bytes under dir-fd custody, analyze, publish atomically."""
    raw = read_retained_bundle(
        input_root,
        member,
        expected_sha256=provenance.expected_raw_sha256,
        max_bytes=MAX_BUNDLE_BYTES,
    )
    analysis = analyze_retained(
        raw,
        provenance=provenance,
        tasks=tasks,
        analyzer_source_sha256=analyzer_source_sha256,
        counted_worker_tokens=counted_worker_tokens,
    )
    published = publish_analysis(
        output_root,
        json_member=json_member,
        report_member=report_member,
        analysis=analysis,
    )
    return {
        **published,
        "input_sha256": provenance.expected_raw_sha256,
        "output_sha256": str(analysis["output_sha256"]),
    }


__all__ = [
    "analyze_retained",
    "analyze_retained_member",
    "publish_analysis",
    "render_report",
]
