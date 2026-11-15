"""Offline callbacks for the two exact canonical SDK lifetimes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import subprocess
import time
from typing import Any, Callable, cast

from runner_pgvector_budget import refuse
from runner_pgvector_context import OfflineContext, inner_plan
from runner_pgvector_export import export_runtime
from runner_pgvector_lineage import require_attempt
from runner_pgvector_prebuilt import BASE_CONFIG, ENV_OFFLINE_PLAN, INNER_PLAN, SDK_IMAGE, digest, json_bytes
from runner_pgvector_transport import send_file, send_plan
from test_sandbox_arguments import SandboxArgumentsResult

Captured = Callable[..., subprocess.CompletedProcess[str]]


class _ReportProcess:
    """Bound the exact owned reader, including EOF probing and process settlement."""

    def __init__(self, process: subprocess.Popen[bytes], maximum: int) -> None:
        if process.stdout is None:
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError("pgvector prebuilt refused: offline report pipe unavailable")
        self.process, self.remaining = process, maximum
        self.deadline = time.monotonic() + 300

    @property
    def stdout(self) -> _ReportProcess:
        return self

    def read(self, count: int) -> bytes:
        refuse(self.remaining > 0, "offline report cap reached before proved EOF")
        stream = self.process.stdout
        assert stream is not None
        timeout = self.deadline - time.monotonic()
        refuse(timeout > 0, "offline report timeout")
        ready, _, _ = select.select([stream], [], [], timeout)
        refuse(bool(ready), "offline report timeout")
        raw = os.read(stream.fileno(), min(count, self.remaining))
        self.remaining -= len(raw)
        return raw

    def poll(self) -> int | None:
        return self.process.poll()

    def kill(self) -> None:
        self.process.kill()

    def wait(self) -> int:
        return self.process.wait(timeout=max(1.0, self.deadline - time.monotonic()))


def checked_image(observed: dict[str, Any], plan: dict[str, Any], *, base: bool = False) -> None:
    expected = plan["derived"]
    config = expected["base_config_document"] if base else expected["runtime_config_document"]
    identity = BASE_CONFIG if base else expected["runtime_config"]
    refuse(observed.get("Id") == identity
           and (observed.get("Os"), observed.get("Architecture")) == ("linux", "arm64")
           and observed.get("RootFS", {}).get("Layers") == config["rootfs"]["diff_ids"]
           and observed.get("Config", {}).get("Entrypoint") == config["config"].get("Entrypoint")
           and observed.get("Config", {}).get("Cmd") == ["postgres"], "imported image identity")
    if not base:
        refuse(observed["Config"].get("Labels") == config["config"].get("Labels"),
               "imported runtime labels")


class OfflineSandbox:
    """Only this admitted cohort can replace ordinary prerequisite acquisition."""

    def __init__(self, context: OfflineContext, runner: Callable, captured: Captured) -> None:
        self.context, self.runner, self.captured = context, runner, captured
        self.deadline = time.monotonic() + 1200
        self.probe_image: str | None = None

    def outer(self, **kwargs: Any) -> list[str]:
        from test_sandbox import outer_run_command
        return self.outer_command(outer_run_command(**kwargs))

    def report_arguments(self, argv: list[str], *, repo_root: Path) -> SandboxArgumentsResult:
        self.context.fixed()
        from test_sandbox_arguments import sandbox_report_arguments
        return sandbox_report_arguments(argv, repo_root=repo_root, export_report=True)

    def read_report_archive(self, container: str, required: bool) -> bytes | None:
        from test_sandbox_execution import read_container_report_archive
        self.context.fixed()
        with self.context.allocation().session() as meter:
            reader: _ReportProcess | None = None
            def popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
                nonlocal reader
                refuse(reader is None, "duplicate offline report reader")
                process: subprocess.Popen[bytes] = subprocess.Popen(*args, **kwargs)
                # The existing reader consumes precisely this stdout/read/poll/
                # kill/wait interface. No other Popen surface is substituted.
                reader = _ReportProcess(process, 4 * 1024**2)
                return cast(Any, reader)
            def read(maximum: int) -> bytes:
                archive = read_container_report_archive(container, required,
                    popen=popen, max_archive_bytes=maximum)
                if archive is None:
                    refuse(not required, "offline report unavailable")
                    refuse(reader is None or reader.remaining == 4 * 1024**2,
                           "offline optional report consumed bytes before failure")
                    return b""
                return archive
            # Reserve both controlled read and transfer before the foreign reader.
            # Its own stream cap is identical; unknown failure retains the reservation.
            return meter.read(read, 4 * 1024**2, transfer=True) or None

    def boundary(self, runner: Callable, *, container_id: str, token: str) -> None:
        # Keep the canonical socket, toolchain, Git, real nested-bind and volume
        # proofs. The PostgreSQL image gets only public synthetic scratch input,
        # never the protected checkout. Ordinary cohorts retain the old probe.
        import test_sandbox as facade
        refuse(self.probe_image is not None, "offline probe image unavailable")
        probe_image = cast(str, self.probe_image)
        probe = "/sandbox-scratch/cloe-public-probe"
        self.command(container_id, "python3", "-c",
            "import os; from pathlib import Path; "
            f"p=Path({probe!r}); p.mkdir(mode=0o755); p.chmod(0o755); "
            "f=p/'pyproject.toml'; f.write_text('# public synthetic bind marker\\n'); f.chmod(0o444); "
            "o=os.open('/sandbox-scratch/bind-proof', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o666); "
            "os.fchmod(o,0o666); os.close(o)")
        probe_child: str | None = None
        def require(command_runner, outer, command, *, timeout=60):
            nonlocal probe_child
            if command[:3] == ("docker", "run", "-d"):
                refuse(command[-4] == probe_image and command[-3:-1] == ("sh", "-c"),
                       "canonical bind probe shape changed")
                options = [token.replace("src=/workspace,dst=/workspace,readonly",
                    "src=" + probe + ",dst=" + probe + ",readonly") for token in command[3:-3]]
                script = command[-1].replace("/workspace/pyproject.toml", probe + "/pyproject.toml")
                command = ("docker", "run", "-d", "--pull=never", "--entrypoint", "/bin/sh",
                    "--network", "none", "--user", "postgres", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--read-only", "--memory", "2g",
                    "--cpus", "2", "--pids-limit", "256", "--shm-size", "64m",
                    "--tmpfs", "/tmp:rw,mode=1777,size=32m", "--tmpfs",
                    "/var/lib/postgresql/data:rw,mode=1777,size=160m", *options, "-c", script)
                result = facade._require_inner_command(command_runner, outer, command, timeout=timeout)
                probe_child = result.stdout.strip()
                return result
            if command == ("cat", "/sandbox-scratch/bind-proof"):
                refuse(probe_child is not None, "bind probe identity unavailable")
                waited = facade._require_inner_command(command_runner, outer,
                    ("docker", "wait", cast(str, probe_child)), timeout=30)
                refuse(waited.stdout.strip() == "0", "bind probe child failed")
            return facade._require_inner_command(command_runner, outer, command, timeout=timeout)
        facade._inner.prove_inner_boundary(
            runner, container_id=container_id, token=token, require_inner_command=require,
            captured=facade._captured, inner_exec=facade._inner_exec,
            remove_container=facade._remove_inner_container, probe_volume=facade._probe_inner_volume,
            exact_container_id=facade.EXACT_CONTAINER_ID, alpine_probe_image=probe_image)

    def command(self, outer: str, *args: str) -> subprocess.CompletedProcess[str]:
        remaining = self.deadline - time.monotonic()
        refuse(remaining > 0, "offline SDK lifetime expired")
        result = self.captured(self.runner, ["docker", "exec", outer, *args],
                               timeout=min(300, remaining))
        refuse(result.returncode == 0, "offline SDK command failed")
        return result

    def outer_command(self, command: list[str]) -> list[str]:
        self.context.fixed()
        refuse(command.count(SDK_IMAGE) == 1, "offline exact SDK required")
        with self.context.allocation().session() as meter:
            plan = self.context.plan
            attempt = require_attempt(meter, plan)
            refuse(meter.records.get("derived_archive") == plan["derived"]
                   and meter.used["materializations"] == 1, "unbound materialization")
            if plan["role"] == "native":
                exported = meter.records.get("core_export", {})
                result = meter.records.get("core_result", {})
                refuse(meter.used["sdk_core"] == meter.used["base_loads"] == meter.used["runtime_exports"] == attempt
                       and result == {"exit": 0, "cleanup_proven": True,
                                      "candidate_head": plan["candidate_head"],
                                      "candidate_snapshot_sha256": digest(json_bytes(plan["candidate_snapshot"]))}
                       and exported.get("bytes") == plan["archive"]["bytes"]
                       and exported.get("sha256") == plan["archive"]["sha256"]
                       and exported.get("runtime_config") == plan["derived"]["runtime_config"],
                       "core proof/export prerequisite missing")
            else:
                refuse(meter.used["sdk_core"] == meter.used["base_loads"]
                       == meter.used["runtime_exports"] == attempt - 1,
                       "core attempt already spent or predecessor incomplete")
                refuse(plan["archive"]["bytes"] == plan["derived"]["bytes"]
                       and plan["archive"]["sha256"] == plan["derived"]["sha256"],
                       "unbound materialization archive")
            meter.charge("sdk_" + self.context.plan["role"], 1)
        index = command.index(SDK_IMAGE)
        return [*command[:index], "--pull=never", "--network", "none",
                "--env", "GOTOOLCHAIN=local", "--env", "GOPROXY=off", "--env", "PIP_NO_INDEX=1",
                "--env", ENV_OFFLINE_PLAN + "=" + INNER_PLAN,
                *command[index:]]

    def prepare(self, _runner: Callable, outer: str) -> None:
        context, plan = self.context, self.context.plan
        context.fixed()
        architecture = self.command(outer, "docker", "version", "--format", "{{.Server.Arch}}").stdout.strip()
        refuse(architecture in {"arm64", "aarch64"}, "native ARM daemon required")
        driver = self.command(outer, "docker", "info", "--format", "{{.Driver}}").stdout.strip()
        refuse(driver == "overlay2", "qualified graphdriver store required")
        existing = self.command(outer, "docker", "image", "ls", "--all", "--quiet", "--no-trunc").stdout
        refuse(not existing.strip(), "fresh inner image inventory required")
        self.command(outer, "python3", "-c",
                     "import os; os.mkdir('/sandbox-scratch/pgvector', mode=0o700)")
        with context.allocation().session() as meter:
            descriptor = plan["archive"]
            if plan["role"] == "core":
                meter.charge("base_loads", 1)
            else:
                meter.charge("native_runtime_loads", 1)
                meter.charge("native_lifecycles", 1)
            send_file(outer, Path(descriptor["path"]), "image.tar", descriptor, meter)
            # Reserve the entire child file read before invoking its loader.
            meter.charge("read_bytes", descriptor["bytes"])
            self.command(outer, "docker", "image", "load", "--quiet", "--input",
                         "/sandbox-scratch/pgvector/image.tar")
            expected_ids = {plan["derived"]["runtime_config"]}
            if plan["role"] == "core":
                expected_ids.add(BASE_CONFIG)
            ids = self.command(outer, "docker", "image", "ls", "--all", "--quiet", "--no-trunc").stdout
            refuse(set(ids.split()) == expected_ids, "unexpected inner imported image")
            for identity in sorted(expected_ids):
                raw = self.command(outer, "docker", "image", "inspect", identity).stdout
                observed, = json.loads(raw)
                checked_image(observed, plan, base=identity == BASE_CONFIG)
            self.probe_image = BASE_CONFIG if plan["role"] == "core" else plan["derived"]["runtime_config"]
            inner = {key: plan[key] for key in (
                "schema", "phase", "role", "provider_sha256", "sdk_image", "candidate_head",
                "candidate_snapshot", "review1_receipt_sha256", "operator_decision_sha256", "derived",
                "diagnostic_declaration", "diagnostic_declaration_sha256")}
            for key in ("core_attempt", "recovery_parent_sha256"):
                if key in plan:
                    inner[key] = plan[key]
            send_plan(outer, inner, context.plan_path.with_suffix(".inner.json"), meter)
            if plan["role"] == "core":
                output = Path(plan["export_path"])
                refuse(output.is_absolute() and not output.exists()
                       and not any(p.is_symlink() for p in output.parents), "export destination")
                receipt = export_runtime(outer, output, plan["derived"], meter)
                meter.bind("core_export", receipt)
                with output.with_suffix(".receipt.json").open("xb") as target:
                    target.write(json_bytes(receipt))
                output.with_suffix(".receipt.json").chmod(0o600)
            self.command(outer, "rm", "/sandbox-scratch/pgvector/image.tar")
        context.fixed()

    def finished(self, result: int) -> None:
        # Called only after canonical exact outer removal and host readback.
        self.context.fixed()
        from runner_pgvector_context import verify_checkout
        plan = self.context.plan
        verify_checkout(self.context.repo_root, plan["candidate_snapshot"], plan["candidate_head"])
        with self.context.allocation().session() as meter:
            meter.bind(plan["role"] + "_result", {"exit": result, "cleanup_proven": result == 0,
                "candidate_head": plan["candidate_head"],
                "candidate_snapshot_sha256": digest(json_bytes(plan["candidate_snapshot"]))})

    def run_tests(self, _runner: Callable, outer: str) -> int:
        self.command(outer, "touch", "/sandbox-scratch/start-tests")
        # Every external command stays bounded; no six-hour docker wait.
        while time.monotonic() < self.deadline:
            result = self.captured(self.runner, ["docker", "container", "inspect", outer,
                "--format", "{{.State.Running}} {{.State.ExitCode}}"], timeout=10)
            refuse(result.returncode == 0, "offline SDK result unavailable")
            running, code = result.stdout.strip().split()
            if running == "false":
                return int(code)
            refuse(running == "true", "offline SDK state")
            time.sleep(0.25)
        raise RuntimeError("pgvector prebuilt refused: offline SDK lifetime expired")


def admit_inner(argv: list[str], environment: dict[str, str],
                execute: Callable[..., subprocess.CompletedProcess[str]]) -> dict[str, Any]:
    plan = inner_plan(argv, environment)
    refuse(plan["role"] == "native", "native cohort required")
    identity = plan["derived"]["runtime_config"]
    observed, = json.loads(execute("offline_runtime_identity",
                                  ["docker", "image", "inspect", identity]).stdout)
    checked_image(observed, plan)
    environment["REPOMAP_TEST_PG_IMAGE"] = identity
    environment["REPOMAP_TEST_PG_PROFILE"] = "pgvector"
    return {"pgvector_setup": "imported_verified_offline", "resulting_engine_id": identity,
            "observed_platform": "linux/arm64", "image_built": False,
            "automatic_native_lifecycles": 0, "runtime_qualification": "owned by exact native test"}
