"""Produce and settle one exact public staging attempt across hosted jobs."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT / "tools"))

from hosted_staging_coverage import merge_go, merge_python
from hosted_staging_parts import INTEGRATION_PARTS, MEASURED_PARTS, PARTS, invocation_id, nodes_for_part
from runner_integration_execution import candidate_identity
from staging_report_contract import POLICY, SCHEMA, persist_report, qualifies, validate_report
from test_report import TestRecord, write_html_report
from test_report_coverage import CoverageStatus

_RECEIPT_SCHEMA = "cloe-hosted-staging-part-v1"
_MAX_MEMBER = 16 * 1024 * 1024
_MAX_MEMBERS = 4096
_PART_REPORT = "staging/latest/staging_part_report.json"


def part_members(part: str) -> tuple[str, ...]:
    if part == "smoke":
        return ("smoke.json",)
    if part in MEASURED_PARTS:
        return ("part-report.json", "python-coverage.json", "go.txt")
    if part == "a":
        return ("part-report.json",)
    raise ValueError("unknown staging part")


def _read(path: Path) -> bytes:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("staging artifact has a symlink")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_MEMBER:
            raise ValueError("staging artifact exceeds its member bound")
        raw = stream.read(_MAX_MEMBER + 1)
    if len(raw) > _MAX_MEMBER:
        raise ValueError("staging artifact exceeds its member bound")
    return raw


def _decode(raw: bytes) -> dict[str, Any]:
    def pairs(fields: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(fields)
        if len(result) != len(fields):
            raise ValueError("duplicate staging artifact key")
        return result

    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("expected a staging artifact object")
    return value


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _first_use_members(root: Path) -> list[str]:
    bundle = root / "first-use-workload"
    if bundle.is_symlink() or (bundle.exists() and not bundle.is_dir()):
        raise ValueError("invalid first-use report tree")
    members: list[str] = []
    for entry in bundle.rglob("*"):
        if entry.is_symlink() or (not entry.is_dir() and not entry.is_file()):
            raise ValueError("unsafe first-use report member")
        if entry.is_file():
            if entry.suffix != ".json" or len(members) >= _MAX_MEMBERS:
                raise ValueError("first-use report tree exceeds its closed membership")
            members.append(entry.relative_to(root).as_posix())
    return sorted(members)


def _write_member(root: Path, name: str, raw: bytes) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def workflow_binding() -> dict[str, Any]:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        raise ValueError("hosted staging requires a pull-request workflow")
    run = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ["GITHUB_RUN_ATTEMPT"]
    commit = os.environ["GITHUB_SHA"]
    identity = invocation_id(run, attempt, commit)
    candidate = candidate_identity(ROOT)
    if candidate["commit"] != commit:
        raise ValueError("hosted staging checkout does not match workflow commit")
    return {"run": run, "attempt": attempt, "invocation_id": identity, "candidate": candidate}


def artifact_name(binding: dict[str, Any], part: str) -> str:
    return f"cloe-staging-{binding['run']}-a{binding['attempt']}-{part}"


def _receipt(binding: dict[str, Any], part: str, blobs: dict[str, bytes]) -> dict[str, Any]:
    return {
        "schema": _RECEIPT_SCHEMA, "part": part, **binding, "terminal_cleanup": "passed",
        "members": {name: _digest(raw) for name, raw in blobs.items()},
    }


def run_part(part: str, output: Path, binding: dict[str, Any]) -> int:
    if output.exists() or output.is_symlink():
        raise ValueError("staging artifact output already exists")
    with tempfile.TemporaryDirectory(prefix="cloe-hosted-staging-", dir=os.environ["RUNNER_TEMP"]) as temp:
        reports = Path(temp) / "inner"
        command = [
            sys.executable, str(ROOT / "tools/run_tests.py"), "--suite", "staging",
            "--hygiene-profile", "exhaustive", "--declared-complete-gates", "1", "--operator-attest-exclusive",
            "--operator-attest-pressure-degradation", "--pg-container-port", "55433", "--sandbox",
            "--report", "--report-dir", str(reports), "--hosted-staging-part", part,
            "--hosted-staging-invocation", binding["invocation_id"],
        ]
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            return 1
        blobs = {name: _read(reports / (_PART_REPORT if name == "part-report.json" else name))
                 for name in part_members(part)}
        if part in MEASURED_PARTS:
            blobs.update({name: _read(reports / name) for name in _first_use_members(reports)})
    if workflow_binding() != binding:
        raise ValueError("hosted staging source changed during execution")
    output.mkdir()
    for name, blob in blobs.items():
        _write_member(output, name, blob)
    persist_report(output / "receipt.json", _receipt(binding, part, blobs))
    return 0


def _read_parts(directory: Path, binding: dict[str, Any]) -> dict[str, dict[str, bytes]]:
    expected = {artifact_name(binding, part) for part in PARTS}
    if {entry.name for entry in directory.iterdir()} != expected:
        raise ValueError("missing or unexpected hosted staging artifact")
    result = {}
    for part in PARTS:
        path = directory / artifact_name(binding, part)
        if path.is_symlink():
            raise ValueError("staging artifact directory cannot be a symlink")
        members = part_members(part) + (tuple(_first_use_members(path)) if part in MEASURED_PARTS else ())
        required_roots = {"receipt.json", *(name.split("/", 1)[0] for name in members)}
        if {child.name for child in path.iterdir()} != required_roots:
            raise ValueError("unexpected staging artifact member")
        blobs = {name: _read(path / name) for name in members}
        receipt = _decode(_read(path / "receipt.json"))
        if receipt != _receipt(binding, part, blobs):
            raise ValueError("staging part identity, member hash or cleanup is invalid")
        result[part] = blobs
    return result


def _part_leg(report: dict[str, Any], part: str, binding: dict[str, Any]) -> dict[str, Any]:
    kind = "A" if part == "a" else "M"
    if (report["schema"] != SCHEMA or report["policy"] != POLICY
            or report["candidate"] != binding["candidate"]
            or report["invocation_id"] != binding["invocation_id"]
            or report["coverage_enabled"] is not True or report["scoped"] is not False
            or report["smoke"] != "not_required" or report["go_coverage"] is not None
            or report["errors"] != [] or list(report["legs"]) != [kind]):
        raise ValueError("hosted staging part report scope or identity mismatch")
    partition = report["partition"]
    nodes = list(nodes_for_part(part, partition["measured"], partition["abrupt"]))
    leg = report["legs"][kind]
    measurement = leg["measurement"]
    if (not nodes or leg["planned"] != nodes or leg["completed"] != nodes
            or [row["test_id"] for row in leg["records"]] != nodes
            or leg["status"] != "passed" or leg["cleanup"] != "passed"
            or type(leg["pytest_exit"]) is not int or leg["pytest_exit"] != 0
            or measurement["state"] != ("unavailable" if part == "a" else "measured")
            or measurement["valid"] is not True or measurement["summary"] is not None
            or not isinstance(measurement["session_id"], str) or not measurement["session_id"]
            or (kind == "M" and leg["abrupt_roles"] != [])):
        raise ValueError("hosted staging part lacks exact execution, measurement or cleanup")
    return leg


def aggregate(directory: Path, output: Path, binding: dict[str, Any]) -> int:
    parts = _read_parts(directory, binding)
    if _decode(parts["smoke"]["smoke.json"]) != {
        "candidate": binding["candidate"], "invocation_id": binding["invocation_id"], "smoke": "passed",
    }:
        raise ValueError("hosted staging smoke evidence mismatch")
    reports = {part: _decode(parts[part]["part-report.json"]) for part in INTEGRATION_PARTS}
    report = reports["a"]
    partition = report["partition"]
    if any(part["partition"] != partition for part in reports.values()):
        raise ValueError("hosted staging populations drifted across parts")
    legs = {part: _part_leg(doc, part, binding) for part, doc in reports.items()}
    sessions = {part: leg["measurement"]["session_id"] for part, leg in legs.items()}
    if len(set(sessions.values())) != len(INTEGRATION_PARTS):
        raise ValueError("hosted staging reused a measurement session")
    all_records = [row for part in MEASURED_PARTS for row in legs[part]["records"]]
    records_by_id = {row["test_id"]: row for row in all_records}
    measured = partition["measured"]
    if len(records_by_id) != len(all_records) or set(records_by_id) != set(measured):
        raise ValueError("hosted staging measured parts are incomplete or overlap")
    coverage = merge_python(ROOT, [_decode(parts[part]["python-coverage.json"]) for part in MEASURED_PARTS])
    go = merge_go([parts[part]["go.txt"].decode() for part in MEASURED_PARTS])
    summary = asdict(coverage)
    summary["files"] = [
        row | {"path": row["path"].relative_to(ROOT).as_posix()} for row in summary["files"]
    ]
    ordinary = [records_by_id[node] for node in measured]
    report["legs"]["M"] = {
        "planned": measured, "records": ordinary, "completed": measured,
        "status": "passed", "cleanup": "passed", "abrupt_roles": [], "pytest_exit": 0,
        "measurement": {"state": "measured", "valid": True, "summary": summary,
                        "session_id": "aggregate-" + binding["invocation_id"]},
    }
    report["smoke"] = "passed"
    report["go_coverage"] = asdict(go)
    candidates = binding["candidate"]
    validate_report(report, expected_commit=candidates["commit"],
                    expected_source_sha256=candidates["source_sha256"], expected_invocation_id=binding["invocation_id"])
    accepted = qualifies(report)
    evidence_names = set()
    for part in MEASURED_PARTS:
        for name, raw in parts[part].items():
            if name.startswith("first-use-workload/"):
                if name in evidence_names:
                    raise ValueError("duplicate first-use report across staging parts")
                _write_member(output, name, raw)
                evidence_names.add(name)
    persist_report(output / "staging/latest/staging_contract_report.json", report)
    persist_report(output / "hosted-staging-closure.json", {
        **binding, "parts": {part: _receipt(binding, part, blobs) for part, blobs in parts.items()},
        "measurement_sessions": sessions, "partition_sha256": partition["sha256"], "qualified": accepted,
    })
    write_html_report(
        report_root=output, suite_name="staging", repo_root=ROOT,
        test_records=tuple(TestRecord(**row) for row in ordinary + legs["a"]["records"]),
        coverage=coverage, go_coverage=go, coverage_status=CoverageStatus("measured"), staging_obligations=report,
    )
    print(f"Staging coverage: Python lines {coverage.line_percent:.2f}%, "
          f"branches {coverage.branch_percent:.2f}%, Go statements {go.statement_percent:.2f}%")
    return 0 if accepted else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("part", choices=PARTS)
    run.add_argument("output", type=Path)
    combine = commands.add_parser("aggregate")
    combine.add_argument("artifacts", type=Path)
    combine.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        binding = workflow_binding()
        if args.command == "run":
            return run_part(args.part, args.output, binding)
        return aggregate(args.artifacts, args.output, binding)
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Hosted staging refused: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
