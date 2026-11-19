"""Hosted staging qualification with exact artifact, population and measurement closure."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ci import hosted_staging
from hosted_staging_coverage import merge_go, merge_python
from hosted_staging_parts import MEASURED_PARTS, PARTS, invocation_id, nodes_for_part
from runner_staging_discovery_contract import OPTIONAL_NODE_IDS, OPTIONAL_REASON
from staging_report_contract import POLICY, SCHEMA, _declarations, digest_partition, load_report, persist_report, qualifies


def _python(covered: int) -> dict[str, object]:
    return {"lines": [1, 2, 3], "missing_lines": [i for i in range(1, 4) if i != covered],
            "arcs": [[1, 2], [1, 3], [2, -1]], "missing_arcs": [
                list(arc) for i, arc in enumerate(((1, 2), (1, 3), (2, -1)), 1) if i != covered
            ]}


def _go(covered: int) -> str:
    return "mode: atomic\n" + "\n".join(f"fixture.go:{i}.1,{i}.2 1 {int(i == covered)}" for i in range(1, 4))


def _record(node: str) -> dict[str, Any]:
    return {"test_id": node, "test_file": node.split("::")[0], "status": "passed",
            "duration_seconds": 0.1, "message": ""}


def _leg(nodes: list[str], part: str, roles: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "passed", "pytest_exit": 0, "planned": nodes,
            "records": [_record(node) for node in nodes], "completed": nodes, "cleanup": "passed",
            "measurement": {"state": "unavailable" if part == "a" else "measured", "valid": True,
                            "summary": None, "session_id": "session-" + part},
            "abrupt_roles": roles if part == "a" else []}


def _inputs(root: Path, *, change: Callable[[str, dict[str, Any]], None] | None = None
            ) -> tuple[Path, dict[str, Any]]:
    input_root = root / "input"
    input_root.mkdir()
    commit = "a" * 40
    binding = {"run": "15", "attempt": "1", "invocation_id": invocation_id("15", "1", commit),
               "candidate": {"commit": commit, "source_sha256": "b" * 64}}
    python = root / "src/main/python/example.py"
    python.parent.mkdir(parents=True)
    python.write_text("x = 1\nx = 2\nx = 3\n")
    measured = [f"test_{i}.py::test_a" for i in range(20)]
    abrupt = [row["nodeid"] for row in _declarations()]
    roles = [
        {"nodeid": row["nodeid"], "role": row["role"], "checkpoint": row["checkpoint"],
         "invocation_id": binding["invocation_id"], "source_sha256": "b" * 64,
         "launch_id": f"launch-{i}", "pid": 10 + i, "parent_pid": 1,
         "started_ns": 1, "settled_ns": 2, "returncode": row["expected_returncode"],
         "measurement": "unavailable", "cleanup": True}
        for i, row in enumerate(_declarations())
    ]
    partition = {
        "collected": measured + abrupt, "required": measured + abrupt, "measured": measured, "abrupt": abrupt,
        "deferred": [], "optional": {"excluded": [{"nodeid": OPTIONAL_NODE_IDS[0], "raw_index": len(measured + abrupt)}],
                                     "count": 1, "raw_count": len(measured + abrupt) + 1, "reason": OPTIONAL_REASON},
    }
    partition["sha256"] = digest_partition(partition)
    for part in PARTS:
        path = input_root / hosted_staging.artifact_name(binding, part)
        if part == "smoke":
            persist_report(path / "smoke.json", {"candidate": binding["candidate"],
                           "invocation_id": binding["invocation_id"], "smoke": "passed"})
        else:
            key = "A" if part == "a" else "M"
            record = {"schema": SCHEMA, "policy": POLICY, "candidate": binding["candidate"],
                      "invocation_id": binding["invocation_id"], "coverage_enabled": True, "scoped": False,
                      "smoke": "not_required", "partition": partition, "go_coverage": None, "errors": [],
                      "legs": {key: _leg(list(nodes_for_part(part, measured, abrupt)), part, roles)}}
            if change is not None:
                change(part, record)
            persist_report(path / "part-report.json", record)
            if part in MEASURED_PARTS:
                index = MEASURED_PARTS.index(part) + 1
                persist_report(path / "python-coverage.json", {python.relative_to(root).as_posix(): _python(index)})
                (path / "go.txt").write_text(_go(index))
        members = {name: (path / name).read_bytes() for name in hosted_staging.part_members(part)}
        if part == "m0":
            leaf = "first-use-workload/fixture/index.json"
            hosted_staging._write_member(path, leaf, b"{}\n")
            members[leaf] = b"{}\n"
        persist_report(path / "receipt.json", hosted_staging._receipt(binding, part, members))
    return input_root, binding


def test_aggregate_joins_all_parts_and_reuses_canonical_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, identity = _inputs(tmp_path)
    monkeypatch.setattr(hosted_staging, "ROOT", tmp_path)
    output = tmp_path / "output"
    assert hosted_staging.aggregate(root, output, identity) == 0
    report = load_report(output / "staging/latest/staging_contract_report.json")
    assert qualifies(report)
    assert report["legs"]["M"]["measurement"]["summary"]["covered_branches"] == 3
    assert report["go_coverage"]["covered_statements"] == 3
    assert (output / "first-use-workload/fixture/index.json").read_bytes() == b"{}\n"


@pytest.mark.parametrize("fault", ["missing_job", "cross_attempt", "changed_member"])
def test_artifact_custody_refuses_missing_cross_attempt_and_changed_members(tmp_path: Path, fault: str) -> None:
    root, identity = _inputs(tmp_path)
    part = root / hosted_staging.artifact_name(identity, "m0")
    if fault == "missing_job":
        part.rename(tmp_path / "missing")
    elif fault == "cross_attempt":
        doc = load_report(part / "receipt.json")
        persist_report(part / "receipt.json", doc | {"attempt": "2"})
    else:
        (part / "go.txt").write_text(_go(3))
    with pytest.raises(ValueError, match="artifact|identity"):
        hosted_staging.aggregate(root, tmp_path / "result", identity)


@pytest.mark.parametrize("fault", ["population", "cleanup", "overlap", "stale_source"])
def test_aggregate_refuses_invalid_part_even_with_new_receipt(tmp_path: Path, fault: str) -> None:
    def change(part: str, report: dict[str, Any]) -> None:
        if part != "m0":
            return
        leg = report["legs"]["M"]
        if fault == "population":
            report["partition"] = report["partition"] | {"measured": []}
        elif fault == "cleanup":
            leg["cleanup"] = "unknown"
        elif fault == "overlap":
            leg["completed"].append(leg["planned"][0])
        else:
            report["candidate"] = report["candidate"] | {"source_sha256": "f" * 64}

    root, identity = _inputs(tmp_path, change=change)
    with pytest.raises(ValueError, match="population|cleanup|identity"):
        hosted_staging.aggregate(root, tmp_path / "result", identity)


def test_coverage_needs_exact_denominators_and_uses_global_floor(tmp_path: Path) -> None:
    path = tmp_path / "src/main/python/test.py"
    path.parent.mkdir(parents=True)
    path.touch()
    name = path.relative_to(tmp_path).as_posix()
    one, two = {name: _python(1)}, {name: _python(2)}
    assert not merge_python(tmp_path, [one, two]).passed
    assert merge_python(tmp_path, [one, two, {name: _python(3)}]).passed
    with pytest.raises(ValueError, match="denominators"):
        merge_python(tmp_path, [one, {name: _python(2) | {"lines": [1, 2, 3, 4]}}])
    assert merge_go([_go(1), "", _go(2), _go(3)]).statement_percent == 100
    with pytest.raises(ValueError, match="denominators"):
        merge_go([_go(1), _go(2).replace("fixture.go:3", "fixture.go:4")])
