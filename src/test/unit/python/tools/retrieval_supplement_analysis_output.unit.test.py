"""Deterministic output, Markdown tables, frozen-catalog loader and inert CLI entry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repomap_test_support.retrieval_supplement_analysis_fixtures import (
    SYNTHETIC_ANALYZER,
    native_shaped_provenance,
    synthetic_bundle,
    synthetic_bytes,
    synthetic_counted_tokens,
    synthetic_provenance,
    synthetic_tasks,
)
from retrieval_supplement_analysis import (
    AnalysisRefusal,
    analyze_retained,
    compute_analyzer_source_sha256,
    load_registered_task_descriptors,
    main,
    render_report,
)
from retrieval_supplement_files import file_inventory, publish_member
from retrieval_supplement_primitives import canonical_bytes, digest_bytes


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def test_registered_task_descriptors_and_analyzer_closure_hash_are_reproducible() -> None:
    repo = _repo_root()
    descriptors = load_registered_task_descriptors(repo)
    assert len(descriptors) == 24
    assert [d.task_id for d in descriptors] == [f"sup1-{idx:02d}" for idx in range(1, 25)]
    positives = [d for d in descriptors if d.kind == "positive"]
    controls = [d for d in descriptors if d.kind != "positive"]
    assert len(positives) == 16 and len(controls) == 8
    assert all(d.arm_expectations is not None for d in descriptors)

    first_hash = compute_analyzer_source_sha256(repo)
    second_hash = compute_analyzer_source_sha256(repo)
    assert first_hash == second_hash
    assert first_hash.startswith("sha256:")


def test_output_json_and_markdown_tables_carry_denominators_and_bindings() -> None:
    raw = synthetic_bytes(emit_slot_query_costs=True)
    prov = synthetic_provenance(raw)
    tasks = synthetic_tasks()
    first = analyze_retained(
        raw, provenance=prov, tasks=tasks, analyzer_source_sha256=SYNTHETIC_ANALYZER
    )
    second = analyze_retained(
        raw, provenance=prov, tasks=tasks, analyzer_source_sha256=SYNTHETIC_ANALYZER
    )
    assert first == second

    without_output_hash = {k: v for k, v in first.items() if k != "output_sha256"}
    assert first["output_sha256"] == digest_bytes(canonical_bytes(without_output_hash))

    report = render_report(first)
    assert render_report(first) == report
    # Headline table explicitly includes task denominators and absent span status.
    assert "(n=4/4)" in report
    assert "span_namespace_not_recorded_in_bundle" in report
    assert "## Pairwise arm comparisons" in report
    assert "## Family breakdown" in report
    assert "## Controls" in report
    assert "## Layered costs" in report
    assert "## Limitations" in report
    assert "Operator criteria decision: pending" in report


def test_cli_main_publishes_native_shaped_bundle_and_refuses_clobber_with_safe_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo_root()
    # Build a synthetic bundle using the real pinned 24 registered descriptors'
    # public task IDs/digests/expectations and invented synthetic metric values.
    descriptors = load_registered_task_descriptors(repo)
    bundle = synthetic_bundle(tasks=descriptors, native=True)
    tokens = synthetic_counted_tokens(bundle, native=True)
    raw = synthetic_bytes(bundle)
    prov = native_shaped_provenance(raw)

    in_dir = tmp_path / "in"
    tok_dir = tmp_path / "tok"
    out_dir = tmp_path / "out"
    in_dir.mkdir(mode=0o700)
    tok_dir.mkdir(mode=0o700)
    out_dir.mkdir(mode=0o700)

    publish_member(in_dir, "retained/bundle.json", raw)
    publish_member(
        tok_dir,
        "workers/counted_tokens.json",
        json.dumps(list(tokens), sort_keys=True).encode("utf-8"),
    )

    argv = [
        "--repo-root", str(repo),
        "--input-root", str(in_dir),
        "--input-member", "retained/bundle.json",
        "--output-root", str(out_dir),
        "--json-member", "analysis/analysis.json",
        "--report-member", "analysis/analysis.md",
        "--expected-raw-sha256", prov.expected_raw_sha256,
        "--attempt-id", prov.attempt_id,
        "--evaluator-closure-sha256", prov.evaluator_closure_sha256,
        "--declaration-source-closure-sha256", str(prov.declaration_source_closure_sha256),
        "--predecessor-failure", "observed_operation_exact_query_descriptor_missing",
        "--counted-tokens-root", str(tok_dir),
        "--counted-tokens-member", "workers/counted_tokens.json",
    ]
    rc = main(argv)
    captured = capsys.readouterr()
    assert rc == 0
    summary = json.loads(captured.out)
    assert summary["output_sha256"].startswith("sha256:")
    assert file_inventory(out_dir) == ("analysis-bundle.zip",)

    # Second run against the same output destination refuses without clobbering or leaking paths.
    rc_repeat = main(argv)
    err_repeat = capsys.readouterr().err
    assert rc_repeat == 2
    assert "analysis:output_destination_exists" in err_repeat
    assert str(out_dir) not in err_repeat

    # Wrong expected hash refuses before creating any output in a fresh directory.
    out_dir2 = tmp_path / "out2"
    out_dir2.mkdir(mode=0o700)
    bad_argv = [
        "--repo-root", str(repo),
        "--input-root", str(in_dir),
        "--input-member", "retained/bundle.json",
        "--output-root", str(out_dir2),
        "--json-member", "analysis/analysis.json",
        "--report-member", "analysis/analysis.md",
        "--expected-raw-sha256", "sha256:" + "0" * 64,
        "--attempt-id", prov.attempt_id,
        "--evaluator-closure-sha256", prov.evaluator_closure_sha256,
    ]
    assert main(bad_argv) == 2
    err_bad = capsys.readouterr().err
    assert "analysis:raw_digest_mismatch" in err_bad
    assert file_inventory(out_dir2) == ()


def test_cli_rejects_foreign_origin_or_malformed_token_argument() -> None:
    with pytest.raises(AnalysisRefusal):
        native_shaped_provenance(b"{}", attempt_id="/unsafe/path")


def test_cli_argument_errors_do_not_echo_untrusted_values(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--foreign-option", "private-host.example"]) == 2
    assert capsys.readouterr().err == "analysis:cli_arguments\n"


def test_cli_does_not_follow_symlinked_repository_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    alias = tmp_path / "repo-alias"
    alias.symlink_to(_repo_root(), target_is_directory=True)
    assert main([
        "--repo-root", str(alias), "--input-root", str(tmp_path),
        "--output-root", str(tmp_path), "--input-member", "input.json",
        "--json-member", "analysis.json", "--report-member", "analysis.md",
        "--expected-raw-sha256", "sha256:" + "0" * 64,
        "--attempt-id", "synthetic", "--evaluator-closure-sha256", "sha256:" + "1" * 64,
    ]) == 2
    assert capsys.readouterr().err == "analysis:analyzer_source_custody\n"
