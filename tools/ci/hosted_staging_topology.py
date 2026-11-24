"""Topology required to preserve the stable staging gate across hosted jobs."""

from __future__ import annotations

from ci.workflow_model import Workflow
from hosted_staging_parts import INTEGRATION_PARTS

SMOKE_JOB = "staging-smoke"
PARTS_JOB = "staging-integration-parts"
GATE_JOB = "staging-integration-gate"
SOURCE_JOB = "source-and-export-policy"
PREFIX = "cloe-staging-${{ github.run_id }}-a${{ github.run_attempt }}-"
STAGING_TOOL = "python3 tools/ci/hosted_staging.py"
STAGING_RUN = f'{STAGING_TOOL} run'
SMOKE_COMMAND = f'{STAGING_RUN} smoke "${{RUNNER_TEMP}}/staging-part"'
PART_COMMAND = f'{STAGING_RUN} "${{PART}}" "${{RUNNER_TEMP}}/staging-part"'
AGGREGATE_COMMAND = f'{STAGING_TOOL} aggregate "${{RUNNER_TEMP}}/staging-parts" "${{RUNNER_TEMP}}/ci-staging-report"'


def validate_hosted_staging(workflow: Workflow) -> list[str]:
    result = []
    for name, timeout, dependencies in (
        (SMOKE_JOB, 30, [SOURCE_JOB]),
        (PARTS_JOB, 240, [SOURCE_JOB, SMOKE_JOB]),
        (GATE_JOB, 20, [SOURCE_JOB, SMOKE_JOB, PARTS_JOB]),
    ):
        job = workflow.jobs.get(name) or {}
        if job.get("timeout-minutes") != timeout or type(job.get("timeout-minutes")) is not int:
            result.append(f"{name}: staging lane timeout must be {timeout} minutes")
        if job.get("needs") != dependencies:
            result.append(f"{name}: staging producer dependencies are invalid")
        if job.get("runs-on") != "ubuntu-latest":
            result.append(f"{name}: staging job must use a separate GitHub-hosted runner")
    part_job = workflow.jobs.get(PARTS_JOB) or {}
    strategy = part_job.get("strategy")
    if (not isinstance(strategy, dict) or strategy != {
        "fail-fast": False, "matrix": {"part": list(INTEGRATION_PARTS)}
    } or strategy["fail-fast"] is not False):
        result.append("hosted staging matrix must run all four parts independently")
    gate_job = workflow.jobs.get(GATE_JOB) or {}
    if gate_job.get("if") != "${{ always() }}" or gate_job.get("name") != GATE_JOB:
        result.append("stable hosted staging aggregate must run even after a producer failure")
    gate = workflow.job_steps(GATE_JOB)
    guard = gate[0] if gate else {}
    expected = {
        "SOURCE_RESULT": "${{ needs.source-and-export-policy.result }}",
        "SMOKE_RESULT": "${{ needs.staging-smoke.result }}",
        "INTEGRATION_RESULT": "${{ needs.staging-integration-parts.result }}",
    }
    if (guard.get("env") != expected or guard.get("if") is not None
            or str(guard.get("run", "")).splitlines() != [f'test "${{{name}}}" = success' for name in expected]):
        result.append("hosted staging aggregate must refuse failed or skipped producers before checkout")
    for name, command in ((SMOKE_JOB, SMOKE_COMMAND), (PARTS_JOB, PART_COMMAND), (GATE_JOB, AGGREGATE_COMMAND)):
        steps = workflow.job_steps(name)
        commands = [step for step in steps if STAGING_TOOL in str(step.get("run", ""))]
        if len(commands) != 1 or commands[0].get("run") != command or commands[0].get("if") is not None:
            result.append(f"{name}: requires one exact hosted staging invocation")
        elif name == PARTS_JOB and commands[0].get("env") != {"PART": "${{ matrix.part }}"}:
            result.append("integration part selection must come from the closed matrix")
    for name, suffix in ((SMOKE_JOB, "smoke"), (PARTS_JOB, "${{ matrix.part }}")):
        upload = [step for step in workflow.job_steps(name) if str(step.get("uses", "")).startswith("actions/upload-artifact@")]
        if len(upload) != 1 or upload[0].get("if") is not None or upload[0].get("with") != {
            "name": PREFIX + suffix, "path": "${{ runner.temp }}/staging-part",
            "if-no-files-found": "error", "retention-days": 14,
        }:
            result.append(f"{name}: requires one exact attempt-bound staging upload")
    download = [step for step in gate if str(step.get("uses", "")).startswith("actions/download-artifact@")]
    if len(download) != 1 or download[0].get("with") != {
        "pattern": PREFIX + "*", "path": "${{ runner.temp }}/staging-parts",
    } or download[0].get("if") is not None:
        result.append("staging aggregate must download separate same-attempt artifacts")
    elif not gate or gate.index(download[0]) == 0:
        result.append("staging artifact preparation must follow the producer status guard")
    if sum(str(step.get("with", {}).get("name", "")) == "repomap-staging-gate-report"
           for step in gate if str(step.get("uses", "")).startswith("actions/upload-artifact@")) != 1:
        result.append("staging aggregate must retain the canonical report artifact")
    return result
