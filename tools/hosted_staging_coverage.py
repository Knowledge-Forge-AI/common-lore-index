"""Export and combine measured staging coverage at exact source identities."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import subprocess
from typing import Any

from go_runner_coverage import DEFAULT_GO_INT_STATEMENT_FLOOR, parse_textfmt_blocks
from runner_coverage_reports import coverage_policy_for_suite, percentage
from staging_report_contract import persist_report
from test_report import CoverageFileRecord, CoverageSummary, GoCoverageSummary


def export_python_part(collector: Any, source_root: Path, repo_root: Path, output: Path) -> None:
    rows = {}
    for path in sorted(source_root.rglob("*.py")):
        analysis = collector._analyze(str(path))
        branches = analysis.branch_stats()
        eligible = {
            (start, end) for start, end in analysis.arc_possibilities
            if start in branches and start not in analysis.excluded and end not in analysis.excluded
        }
        missing = {
            (start, end) for start, destinations in analysis.missing_branch_arcs().items()
            for end in destinations
        }
        numbers = analysis.numbers
        if (
            len(eligible) != numbers.n_branches or len(missing) != numbers.n_missing_branches
            or not missing <= eligible
            or len(analysis.statements) != numbers.n_statements or len(analysis.missing) != numbers.n_missing
        ):
            raise RuntimeError("staging coverage identities disagree with the measured counts")
        rows[path.relative_to(repo_root).as_posix()] = {
            "lines": sorted(analysis.statements), "missing_lines": sorted(analysis.missing),
            "arcs": sorted(eligible), "missing_arcs": sorted(missing),
        }
    persist_report(output, rows)


def export_go_part(output: Path) -> None:
    path = Path(os.environ["GOCOVERDIR"])
    output.parent.mkdir(parents=True, exist_ok=True)
    if not any(entry.name.startswith("covcounters.") for entry in path.iterdir()):
        output.write_text("", encoding="utf-8")
        return
    subprocess.run(
        ["go", "tool", "covdata", "textfmt", f"-i={path}", f"-o={output}"],
        check=True, capture_output=True, timeout=30,
    )
    if not parse_textfmt_blocks(output.read_text(encoding="utf-8")):
        raise ValueError("measured Go part has no profile blocks")


def _integers(raw: object) -> set[int]:
    if not isinstance(raw, list) or any(type(value) is not int or value <= 0 for value in raw):
        raise ValueError("invalid coverage line set")
    values = set(raw)
    if len(values) != len(raw):
        raise ValueError("duplicate coverage lines")
    return values


def _arcs(raw: object) -> set[tuple[int, int]]:
    if not isinstance(raw, list):
        raise ValueError("invalid coverage arc set")
    arcs: set[tuple[int, int]] = set()
    for item in raw:
        if (not isinstance(item, list) or len(item) != 2
                or any(type(endpoint) is not int or endpoint == 0 for endpoint in item) or item[0] < 0):
            raise ValueError("invalid coverage arc")
        arcs.add((item[0], item[1]))
    if len(arcs) != len(raw):
        raise ValueError("duplicate coverage arcs")
    return arcs


def _coverage_row(raw: object) -> tuple[set[int], set[int], set[tuple[int, int]], set[tuple[int, int]]]:
    if not isinstance(raw, dict) or raw.keys() != {"lines", "missing_lines", "arcs", "missing_arcs"}:
        raise ValueError("invalid measured Python record")
    lines, missing_lines = _integers(raw["lines"]), _integers(raw["missing_lines"])
    arcs, missing_arcs = _arcs(raw["arcs"]), _arcs(raw["missing_arcs"])
    if not missing_lines <= lines or not missing_arcs <= arcs:
        raise ValueError("missing coverage identities lie outside denominator")
    return lines, missing_lines, arcs, missing_arcs


def merge_python(repo_root: Path, parts: Sequence[Mapping[str, object]]) -> CoverageSummary:
    paths = sorted((repo_root / "src/main/python").rglob("*.py"))
    expected = {path.relative_to(repo_root).as_posix() for path in paths}
    if not paths or not parts or any(value.keys() != expected for value in parts):
        raise ValueError("staging coverage parts do not cover all product source files")
    files = []
    for path in paths:
        name = path.relative_to(repo_root).as_posix()
        lines, missing_lines, arcs, missing_arcs = _coverage_row(parts[0][name])
        for part in parts[1:]:
            other_lines, other_missing, other_arcs, other_missing_arcs = _coverage_row(part[name])
            if (lines, arcs) != (other_lines, other_arcs):
                raise ValueError("staging Python coverage denominators drifted across parts")
            missing_lines &= other_missing
            missing_arcs &= other_missing_arcs
        covered, covered_arcs = len(lines - missing_lines), len(arcs - missing_arcs)
        files.append(CoverageFileRecord(path, len(lines), covered, percentage(covered, len(lines)),
                                        len(arcs), covered_arcs, percentage(covered_arcs, len(arcs))))
    lines_count = sum(row.executable_lines for row in files)
    covered_count = sum(row.covered_lines for row in files)
    arc_count = sum(row.total_branches for row in files)
    covered_arc_count = sum(row.covered_branches for row in files)
    policy = coverage_policy_for_suite("staging")
    return CoverageSummary(
        "staging", policy.line_hard_threshold, policy.branch_hard_threshold,
        policy.line_warn_threshold, policy.branch_warn_threshold,
        lines_count, covered_count, percentage(covered_count, lines_count),
        arc_count, covered_arc_count, percentage(covered_arc_count, arc_count), tuple(files),
    )


def merge_go(profiles: Sequence[str]) -> GoCoverageSummary:
    blocks: dict[str, tuple[int, int]] = {}
    for text in profiles:
        if not text:
            continue
        next_blocks = parse_textfmt_blocks(text)
        if not next_blocks:
            raise ValueError("measured Go part contains an empty profile")
        if blocks and (blocks.keys() != next_blocks.keys() or any(
            count != next_blocks[block][0] for block, (count, _) in blocks.items()
        )):
            raise ValueError("staging Go coverage denominators drifted across parts")
        blocks = {block: (count, max(taken, blocks.get(block, (0, 0))[1]))
                  for block, (count, taken) in next_blocks.items()}
    total = sum(count for count, _ in blocks.values())
    if not total:
        raise ValueError("no measured Go statement denominator")
    covered = sum(count for count, taken in blocks.values() if taken > 0)
    return GoCoverageSummary("staging", DEFAULT_GO_INT_STATEMENT_FLOOR, total, covered,
                             100 * covered / total, "N/A", "none")
