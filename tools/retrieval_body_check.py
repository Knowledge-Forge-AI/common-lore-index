"""Check-only developer front end for W5 body-class source preparation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from retrieval_body_freeze import parse_body_source_freeze, verify_body_source_freeze
from retrieval_body_pin import load_registered_body_inputs
from repomap_test_support.retrieval_body_source import body_source_snapshot
from retrieval_body_values import digest_bytes, require
from retrieval_body_witnesses import verify_body_witnesses
from retrieval_body_input_metadata import validate_static_inventory
from retrieval_supplement_files import read_member

STATIC_MEMBER = "static-inventory-v1.json"
MAX_JSON_BYTES = 512 * 1024


def _registered(args: argparse.Namespace):
    return load_registered_body_inputs(Path(args.input_root), predecessor_root=Path(args.predecessor_root))


def _base_report(inputs: Any, source: Any) -> dict[str, Any]:
    return {
        "schema": "cloe-body-check-report-v1",
        "class_id": inputs.catalog.identity.class_id,
        "corpus_id": inputs.catalog.identity.corpus_id,
        "phase": inputs.catalog.identity.phase,
        "input_pin_sha256": inputs.pin_sha256,
        "catalog_sha256": inputs.catalog.digest,
        "corpus_sha256": inputs.corpus.digest,
        "source_closure_sha256": source.identity,
        "source_member_count": len(source.files),
        "source_bytes": source.total_bytes,
        "case_count": len(inputs.catalog.cases),
        "prospective_slot_count": len(inputs.catalog.cases) * 6 * 3,
        "execution_state": "not_executed",
        "queries_executed": False,
        "model_loaded": False,
        "tokenizer_observed": False,
        "native_qualified": False,
        "scientific_criteria_selected": False,
        "accepted_publication_or_native_qualification": False,
        "held_out_executed": False,
        "production_admitted": False,
        "unresolved_criteria": None,
    }


def check_registered(*, input_root: Path, predecessor_root: Path, source_root: Path) -> dict[str, Any]:
    """Revalidate registered inputs, static witnesses, floor and source closure."""
    inputs = load_registered_body_inputs(input_root, predecessor_root=predecessor_root)
    witness = verify_body_witnesses(inputs.catalog, inputs.corpus)
    static_raw = read_member(input_root, STATIC_MEMBER, max_bytes=MAX_JSON_BYTES)
    validate_static_inventory(static_raw, catalog=inputs.catalog, corpus=inputs.corpus)
    source = body_source_snapshot(source_root)
    report = _base_report(inputs, source)
    report.update({
        "public_v1_pairs": witness["public_v1_pairs"],
        "public_v2_span_pairs": witness["public_v2_span_pairs"],
        "common_positive_count": witness["distinct_intents"],
        "family_counts": witness["family_counts"],
        "body_floor_met": witness["body_floor"]["met"],
        "witness_reached": witness["body_file_witnesses_statically_reached"],
    })
    require(report["body_floor_met"] is True, "body.check:floor_not_met")
    require(report["witness_reached"] is True, "body.check:witnesses_not_reached")
    return report


def verify_freeze(
    *, input_root: Path, predecessor_root: Path, source_root: Path,
    freeze_root: Path, freeze_member: str,
) -> dict[str, Any]:
    inputs = load_registered_body_inputs(input_root, predecessor_root=predecessor_root)
    raw = read_member(freeze_root, freeze_member, max_bytes=MAX_JSON_BYTES)
    parsed = parse_body_source_freeze(raw)
    verify_body_source_freeze(source_root, parsed, inputs)
    return {
        "schema": "cloe-body-freeze-verification-v1",
        "status": "verified",
        "input_pin_sha256": inputs.pin_sha256,
        "freeze_sha256": digest_bytes(raw),
        "source_closure_sha256": parsed["source_closure_sha256"],
        "execution_state": "not_executed",
        "execution_authority_granted": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retrieval_body_check")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate registered inputs and source closure")
    check.add_argument("--input-root", required=True)
    check.add_argument("--predecessor-root", required=True)
    check.add_argument("--source-root", required=True)
    frozen = sub.add_parser("verify-freeze", help="verify an existing source freeze")
    frozen.add_argument("--input-root", required=True)
    frozen.add_argument("--predecessor-root", required=True)
    frozen.add_argument("--source-root", required=True)
    frozen.add_argument("--freeze-root", required=True)
    frozen.add_argument("--freeze-member", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "check":
        result = check_registered(
            input_root=Path(args.input_root), predecessor_root=Path(args.predecessor_root),
            source_root=Path(args.source_root),
        )
    else:
        result = verify_freeze(
            input_root=Path(args.input_root), predecessor_root=Path(args.predecessor_root),
            source_root=Path(args.source_root), freeze_root=Path(args.freeze_root),
            freeze_member=args.freeze_member,
        )
    print(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["check_registered", "main", "verify_freeze"]
