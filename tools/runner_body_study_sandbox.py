"""Body-only SDK adapter over unchanged offline containment primitives."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, cast

from offline_qualification_values import ENV_OFFLINE_PLAN, INNER_PLAN, SDK_IMAGE, refuse
from retrieval_body_study_contracts import BODY_ROLE, BODY_SDK_SECONDS, MAX_REPORT_ARCHIVE_BYTES
from runner_pgvector_sandbox import OfflineSandbox, checked_image
from runner_pgvector_transport import send_file, send_plan


class BodyOfflineSandbox(OfflineSandbox):
    """One native body SDK; no old cohort can gain its lifetime or allocation."""

    def __init__(self, context: Any, runner: Callable, captured: Callable) -> None:
        from runner_body_study import validate_body_runner_plan
        validate_body_runner_plan(context.plan)
        refuse(context.plan["role"] == BODY_ROLE, "body SDK requires body cohort")
        super().__init__(context, runner, captured)
        self.deadline = time.monotonic() + BODY_SDK_SECONDS

    def outer_command(self, command: list[str]) -> list[str]:
        from retrieval_body_study_inner_read_reservation import prepay_inner_reads
        self.context.fixed()
        plan = self.context.plan
        prepay_inner_reads(plan, self.context.allocation(), self.context.repo_root)
        refuse(command.count(SDK_IMAGE) == 1, "body exact SDK required")
        refuse(command.count("--shm-size") == 0, "body SDK shared memory must be owned here")
        with self.context.allocation().session() as meter:
            refuse(meter.used["sdk_body_study"] == 0 and "body_study_result" not in meter.records
                   and all(meter.used[key] == 0 for key in (
                       "native_runtime_loads", "native_lifecycles", "body_study_workers",
                       "runtime_exports", "materializations", "builds", "pulls")),
                   "body SDK already spent or forbidden work attempted")
            meter.charge("sdk_body_study", 1)
        command = self._bounded_sdk_scratch(command)
        index = command.index(SDK_IMAGE)
        return [*command[:index], "--pull=never", "--network", "none", "--shm-size", "64m",
                "--env", "GOTOOLCHAIN=local", "--env", "GOPROXY=off",
                "--env", "PIP_NO_INDEX=1", "--env", ENV_OFFLINE_PLAN + "=" + INNER_PLAN,
                *command[index:]]

    def read_report_archive(self, container: str, required: bool) -> bytes | None:
        """Keep BODY original observations inside the canonical bounded export."""
        from runner_pgvector_sandbox import _ReportProcess
        from test_sandbox_execution import read_container_report_archive
        self.context.fixed()
        with self.context.allocation().session() as meter:
            reader: _ReportProcess | None = None
            def popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                nonlocal reader
                refuse(reader is None, "duplicate body report reader")
                process: subprocess.Popen[bytes] = subprocess.Popen(*args, **kwargs)
                reader = _ReportProcess(process, MAX_REPORT_ARCHIVE_BYTES)
                return cast(Any, reader)
            def read(maximum: int) -> bytes:
                archive = read_container_report_archive(container, required,
                    popen=popen, max_archive_bytes=maximum)
                if archive is None:
                    refuse(not required, "body report unavailable")
                    refuse(reader is None or reader.remaining == MAX_REPORT_ARCHIVE_BYTES,
                           "body optional report consumed bytes before failure")
                    return b""
                return archive
            return meter.read(read, MAX_REPORT_ARCHIVE_BYTES, transfer=True) or None

    def prepare(self, _runner: Callable, outer: str) -> None:
        from retrieval_body_study_plan import (
            project_inner_body_plan, require_reservation_admitted_for_runner,
        )
        context, plan = self.context, self.context.plan
        context.fixed()
        architecture = self.command(outer, "docker", "version", "--format", "{{.Server.Arch}}").stdout.strip()
        refuse(architecture in {"arm64", "aarch64"}, "native ARM daemon required")
        driver = self.command(outer, "docker", "info", "--format", "{{.Driver}}").stdout.strip()
        refuse(driver == "overlay2", "qualified graphdriver store required")
        existing = self.command(outer, "docker", "image", "ls", "--all", "--quiet", "--no-trunc").stdout
        refuse(not existing.strip(), "fresh inner image inventory required")
        self.command(outer, "python3", "-c", "\n".join((
            "import os, stat; from pathlib import Path",
            "for name in ('/sandbox-scratch/pgvector', '/sandbox-report'):",
            " p = Path(name)",
            " if not p.exists(): p.mkdir(mode=0o700)",
            " s = p.lstat()",
            " if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or any(p.iterdir()):",
            "  raise RuntimeError('body fresh SDK directory custody')",
            " p.chmod(0o700)",
        )))
        with context.allocation().session() as meter:
            descriptor = plan["archive"]
            meter.charge("native_runtime_loads", 1)
            meter.charge("native_lifecycles", 1)
            send_file(outer, Path(descriptor["path"]), "image.tar", descriptor, meter)
            meter.charge("read_bytes", descriptor["bytes"])
            self.command(outer, "docker", "image", "load", "--quiet", "--input",
                         "/sandbox-scratch/pgvector/image.tar")
            identity = plan["derived"]["runtime_config"]
            ids = self.command(outer, "docker", "image", "ls", "--all", "--quiet", "--no-trunc").stdout
            refuse(set(ids.split()) == {identity}, "unexpected body inner imported image")
            raw = self.command(outer, "docker", "image", "inspect", identity).stdout
            observed, = json.loads(raw)
            checked_image(observed, plan)
            self.probe_image = identity
            admission = require_reservation_admitted_for_runner(plan, plan_path=context.plan_path)
            inner = project_inner_body_plan(plan, admission=admission)
            send_plan(outer, inner, context.plan_path.with_suffix(".inner.json"), meter)
            self.command(outer, "rm", "/sandbox-scratch/pgvector/image.tar")
        context.fixed()

    def finished(self, result: int) -> None:
        from offline_qualification_values import digest, json_bytes, safe_path
        from runner_pgvector_context import verify_checkout
        self.context.fixed()
        plan = self.context.plan
        count = sum((self.context.repo_root / safe_path(name)).lstat().st_size
                    for name, row in plan["candidate_snapshot"].items() if "symlink" not in row)
        with self.context.allocation().session() as meter:
            meter.charge("read_bytes", count)
        verify_checkout(self.context.repo_root, plan["candidate_snapshot"], plan["candidate_head"])
        with self.context.allocation().session() as meter:
            meter.bind("body_study_sdk_result", {"exit": result, "cleanup_proven": result == 0,
                "candidate_head": plan["candidate_head"],
                "candidate_snapshot_sha256": digest(json_bytes(plan["candidate_snapshot"]))})


def admit_body_study_inner(argv: list[str], environment: dict[str, str],
                           execute: Callable[..., subprocess.CompletedProcess[str]]) -> dict[str, Any]:
    from runner_pgvector_context import inner_plan
    from retrieval_body_study_plan import admission_from_inner_plan
    plan = inner_plan(argv, environment)
    refuse(plan["role"] == BODY_ROLE, "body-study cohort required")
    admission = admission_from_inner_plan(plan)
    identity = plan["derived"]["runtime_config"]
    observed, = json.loads(execute("offline_runtime_identity",
                                  ["docker", "image", "inspect", identity]).stdout)
    checked_image(observed, plan)
    environment.update(REPOMAP_TEST_PG_IMAGE=identity, REPOMAP_TEST_PG_PROFILE="pgvector",
                       REPOMAP_TEST_PHASE=plan["phase"])
    return {"pgvector_setup": "imported_verified_offline_body_study", "resulting_engine_id": identity,
            "observed_platform": "linux/arm64", "image_built": False,
            "automatic_native_lifecycles": 0, "runtime_qualification": "owned by exact body-study test",
            "sdk_loaded_into_inner_daemon": False, "runtime_exports": 0,
            "body_attempt_id": admission.attempt_id}
