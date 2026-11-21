"""Inert maintained entry point for retained supplement analysis.

Importing this module performs no filesystem, model, database or network work.
Callers supply retained bytes (or dir-fd custody roots), expected hashes and
frozen task descriptors explicitly, or invoke ``main`` for one bounded read-only
retained analysis; see ADR0204 and ``docs/specs/cloe-w5-supplement-analysis-v1.md``.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import sys
from typing import Any, Never

from retrieval_supplement_analysis_admission import (
    admit_bundle as admit_bundle,
    parse_bounded_json,
)
from retrieval_supplement_analysis_sources import source_closure_sha256
from retrieval_supplement_analysis_output import (
    analyze_retained as analyze_retained,
    analyze_retained_member as analyze_retained_member,
    publish_analysis as publish_analysis,
    render_report as render_report,
)
from retrieval_supplement_analysis_values import (
    AnalysisRefusal as AnalysisRefusal,
    ExpectedProvenance as ExpectedProvenance,
    TaskDescriptor as TaskDescriptor,
    as_digest,
    as_int,
    as_str,
    exact_keys,
    fail,
    refuse,
)
from retrieval_supplement_files import read_member
from retrieval_supplement_primitives import (
    ARMS,
    CATALOG_SCHEMA,
    INPUT_PIN_SCHEMA,
    REGISTERED_INPUT_PIN_SHA256,
    SupplementError,
    digest_json,
    strict_json,
)
from retrieval_supplement_study_contracts import WORKER_CLOSURE_SHA256

ANALYZER_SOURCE_OWNERS: tuple[str, ...] = (
    "tools/retrieval_supplement_analysis.py",
    "tools/retrieval_supplement_analysis_admission.py",
    "tools/retrieval_supplement_analysis_costs.py",
    "tools/retrieval_supplement_analysis_custody.py",
    "tools/retrieval_supplement_analysis_joins.py",
    "tools/retrieval_supplement_analysis_output.py",
    "tools/retrieval_supplement_analysis_paired.py",
    "tools/retrieval_supplement_analysis_slots.py",
    "tools/retrieval_supplement_analysis_values.py",
)
_PINNED_SUPPLEMENT_REL = "src/test/fixtures/retrieval_evaluation/supplement-v1"
_EXPECTATION_FIELDS = frozenset({"execution", "reason", "actual_surface", "fallback_policy"})


def compute_analyzer_source_sha256(repo_root: Path) -> str:
    """Bind the exact maintained analyzer source closure without touching study closures."""
    refuse(isinstance(repo_root, Path), "analysis:repo_root_required")
    return source_closure_sha256(repo_root, ANALYZER_SOURCE_OWNERS)


def load_registered_task_descriptors(repo_root: Path) -> tuple[TaskDescriptor, ...]:
    """Load and pin-verify the 24 frozen ``sup1-*`` descriptors without kernel imports."""
    refuse(isinstance(repo_root, Path), "analysis:repo_root_required")
    fixture_root = repo_root / _PINNED_SUPPLEMENT_REL
    try:
        pin_raw = read_member(
            fixture_root,
            "input-pin-v1.json",
            max_bytes=512 * 1024,
            expected_sha256=REGISTERED_INPUT_PIN_SHA256,
        )
        pin = strict_json(pin_raw)
    except SupplementError:
        fail("analysis:frozen_pin_custody")
    if not isinstance(pin, dict) or pin.get("schema") != INPUT_PIN_SCHEMA:
        fail("analysis:frozen_pin_schema")
    input_files = pin.get("input_files")
    pin_digests = pin.get("task_digests")
    if not isinstance(input_files, dict) or not isinstance(pin_digests, dict):
        fail("analysis:frozen_pin_shape")
    cat_entry = input_files.get("catalog-v1.json")
    if not isinstance(cat_entry, dict):
        fail("analysis:frozen_catalog_entry")
    cat_bytes = as_int(cat_entry.get("bytes"), "catalog_bytes", 1, 512 * 1024)
    cat_sha = as_digest(cat_entry.get("sha256"), "catalog_sha256")
    try:
        cat_raw = read_member(
            fixture_root, "catalog-v1.json", max_bytes=cat_bytes, expected_sha256=cat_sha
        )
        refuse(len(cat_raw) == cat_bytes, "analysis:frozen_catalog_size")
        catalog = strict_json(cat_raw)
    except SupplementError:
        fail("analysis:frozen_catalog_custody")
    if not isinstance(catalog, dict) or catalog.get("schema") != CATALOG_SCHEMA:
        fail("analysis:frozen_catalog_schema")
    cases = catalog.get("cases")
    if not isinstance(cases, list) or len(cases) != 24:
        fail("analysis:frozen_catalog_cases")
    descriptors: list[TaskDescriptor] = []
    seen_digests: dict[str, dict[str, str]] = {}
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            fail("analysis:frozen_case_shape")
        kind = as_str(raw_case.get("case_kind"), "case_kind")
        task = raw_case.get("task")
        expectations_raw = raw_case.get("expectations")
        if not isinstance(task, dict) or not isinstance(expectations_raw, dict):
            fail("analysis:frozen_task_shape")
        task_id = as_str(task.get("task_id"), "task_id")
        family = as_str(task.get("family"), "family")
        rule = as_str(task.get("grading_rule"), "grading_rule")
        recomputed = {
            "retained_task_sha256": digest_json(task, ascii_only=True),
            "task_sha256": digest_json(task, ascii_only=False),
        }
        refuse(pin_digests.get(task_id) == recomputed, "analysis:frozen_task_digest_mismatch")
        seen_digests[task_id] = recomputed
        exp_map = exact_keys(expectations_raw, frozenset(ARMS), "expectations")
        checked_exp = {
            arm: exact_keys(exp_map[arm], _EXPECTATION_FIELDS, "arm_expectation")
            for arm in ARMS
        }
        descriptors.append(TaskDescriptor(
            task_id=task_id,
            family=family,
            kind=kind,
            grading_rule=rule,
            task_digests=recomputed,
            arm_expectations=checked_exp,
        ))
    refuse(seen_digests == pin_digests, "analysis:frozen_pin_task_set_mismatch")
    return tuple(descriptors)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        fail("analysis:cli_arguments")


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeParser(prog="retained-supplement-analysis", description=__doc__, add_help=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--input-member", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--json-member", required=True)
    parser.add_argument("--report-member", required=True)
    parser.add_argument("--expected-raw-sha256", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--evaluator-closure-sha256", required=True)
    parser.add_argument("--input-pin-sha256", default=REGISTERED_INPUT_PIN_SHA256)
    parser.add_argument("--worker-closure-sha256", default=WORKER_CLOSURE_SHA256)
    parser.add_argument("--declaration-source-closure-sha256", default=None)
    parser.add_argument("--predecessor-failure", action="append", default=[])
    parser.add_argument("--counted-tokens-root", type=Path, default=None)
    parser.add_argument("--counted-tokens-member", default=None)
    return parser


def _load_optional_tokens(
    tokens_root: Path | None, tokens_member: str | None
) -> Sequence[Mapping[str, Any]] | None:
    if tokens_root is None and tokens_member is None:
        return None
    if tokens_root is None or tokens_member is None:
        fail("analysis:counted_tokens_arguments_incomplete")
    try:
        raw = read_member(tokens_root, tokens_member, max_bytes=2 * 1024 * 1024)
    except SupplementError:
        fail("analysis:counted_tokens_custody")
    parsed = parse_bounded_json(raw)
    if isinstance(parsed, dict) and isinstance(parsed.get("workers"), list):
        items: list[Mapping[str, Any]] = []
        for row in parsed["workers"]:
            if not isinstance(row, dict):
                fail("analysis:counted_tokens_terminal_shape")
            tok_ev = row.get("token_evidence")
            if not isinstance(tok_ev, dict):
                fail("analysis:counted_tokens_terminal_shape")
            items.append(tok_ev)
        return items
    if isinstance(parsed, list):
        direct: list[Mapping[str, Any]] = []
        for row in parsed:
            if not isinstance(row, dict):
                fail("analysis:counted_tokens_shape")
            direct.append(row)
        return direct
    fail("analysis:counted_tokens_shape")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded native retained-bundle analysis; emit public-safe codes only."""
    try:
        args = _build_parser().parse_args(list(argv) if argv is not None else None)
    except AnalysisRefusal as refusal:
        print(refusal.code, file=sys.stderr)
        return 2
    except SystemExit as exc:
        return 2 if exc.code != 0 else 0
    try:
        repo_root = args.repo_root
        for root in (repo_root, args.input_root, args.output_root, args.counted_tokens_root):
            if root is not None:
                refuse(root.is_absolute() and not any(ord(c) < 32 for c in str(root)),
                       "analysis:cli_root_invalid")
        analyzer_sha = compute_analyzer_source_sha256(repo_root)
        descriptors = load_registered_task_descriptors(repo_root)
        tokens = _load_optional_tokens(args.counted_tokens_root, args.counted_tokens_member)
        provenance = ExpectedProvenance(
            expected_raw_sha256=args.expected_raw_sha256,
            input_pin_sha256=args.input_pin_sha256,
            attempt_id=args.attempt_id,
            evaluator_closure_sha256=args.evaluator_closure_sha256,
            worker_closure_sha256=args.worker_closure_sha256,
            origin="native",
            predecessor_failure_codes=tuple(args.predecessor_failure),
            declaration_source_closure_sha256=args.declaration_source_closure_sha256,
        )
        published = analyze_retained_member(
            args.input_root,
            args.input_member,
            args.output_root,
            provenance=provenance,
            tasks=descriptors,
            analyzer_source_sha256=analyzer_sha,
            json_member=args.json_member,
            report_member=args.report_member,
            counted_worker_tokens=tokens,
        )
        print(json.dumps(published, sort_keys=True, separators=(",", ":")))
        return 0
    except AnalysisRefusal as refusal:
        print(refusal.code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ANALYZER_SOURCE_OWNERS",
    "AnalysisRefusal",
    "ExpectedProvenance",
    "TaskDescriptor",
    "admit_bundle",
    "analyze_retained",
    "analyze_retained_member",
    "compute_analyzer_source_sha256",
    "load_registered_task_descriptors",
    "main",
    "publish_analysis",
    "render_report",
]
