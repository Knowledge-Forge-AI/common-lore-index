"""Scoped pgvector materialization inside the canonical disposable sandbox."""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence

from runner_pgvector_profile import CORE_PROFILE, PGVECTOR_PROFILE, validate_profile_choice
from runner_pgvector_sandbox import admit_inner, admit_study_inner



class _SetupCommands:
    """Emit bounded setup evidence without exposing argv or environment values."""

    def __init__(self, environment: dict[str, str], runner: Callable | None) -> None:
        self.environment, self.runner = environment, runner or subprocess.run
        self.stage = "provider_facts"
        self.failed_stage: str | None = None

    def event(self, stage: str, status: str, **fields: object) -> None:
        self.stage = stage
        if status == "failed" and fields.get("terminal"):
            self.failed_stage = self.failed_stage or stage
        print(json.dumps({"event": "repomap-pgvector-setup-v1", "stage": stage,
                          "status": status, **fields}, sort_keys=True), flush=True)

    def safe_text(self, value: str | bytes | None) -> str:
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        for name, secret in self.environment.items():
            if secret and re.search(r"TOKEN|PASSWORD|SECRET|CREDENTIAL|API_KEY", name, re.I):
                text = text.replace(secret, "<redacted>")
        text = re.sub(r"(?:/Users/|/home/)[^\s\"']+", "<private-path>", text)
        text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1<redacted>@", text)
        text = re.sub(r"(?i)((?:token|password|secret|api_key)[=:]\s*)[^\s]+",
                      r"\1<redacted>", text)
        return text

    def execute(self, stage: str, cmd: list[str], *, check: bool = True,
                payload: bytes | None = None, timeout: int = 300) -> subprocess.CompletedProcess[str]:
        self.event(stage, "started")
        command_exception = None
        try:
            raw = self.runner(cmd, env=self.environment, capture_output=True,
                              text=payload is None, input=payload, timeout=timeout, check=False)
            def decoded(value: str | bytes | None) -> str:
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            result = subprocess.CompletedProcess(
                cmd, raw.returncode, decoded(raw.stdout), decoded(raw.stderr))
        except (OSError, subprocess.TimeoutExpired) as error:
            command_exception = type(error).__name__
            result = subprocess.CompletedProcess(
                cmd, 124 if isinstance(error, subprocess.TimeoutExpired) else 127,
                self.safe_text(getattr(error, "stdout", None)),
                self.safe_text(getattr(error, "stderr", None)) or type(error).__name__)
        output: dict[str, object] = {
            name: self.safe_text(getattr(result, name))[:4096] for name in ("stdout", "stderr")}
        output.update({name + "_bytes": len(getattr(result, name).encode())
                       for name in ("stdout", "stderr")})
        output["output_truncated"] = any(len(getattr(result, name)) > 4096
                                         for name in ("stdout", "stderr"))
        self.event(stage, "completed" if result.returncode == 0 else "failed",
                   returncode=result.returncode, command_exception=command_exception, **output)
        if check and result.returncode != 0:
            self.failed_stage = self.failed_stage or stage
            raise RuntimeError(f"pgvector setup failed at {stage}: status={result.returncode}; "
                               f"stderr={output['stderr']}; stdout={output['stdout']}")
        return result


def extract_profile_from_argv(argv: list[str]) -> str:
    runner_argv = argv[:argv.index("--")] if "--" in argv else argv
    for i, arg in enumerate(runner_argv):
        if arg == "--pg-test-profile" and i + 1 < len(runner_argv):
            return validate_profile_choice(runner_argv[i + 1])
        if arg.startswith("--pg-test-profile="):
            return validate_profile_choice(arg.split("=", 1)[1])
    return CORE_PROFILE


def forward_profile_into_sandbox(argv: list[str], profile: str) -> list[str]:
    validated = validate_profile_choice(profile)
    index = argv.index("--") if "--" in argv else len(argv)
    if any(arg == "--pg-test-profile" or arg.startswith("--pg-test-profile=")
           for arg in argv[:index]):
        return list(argv)
    return [*argv[:index], "--pg-test-profile", validated, *argv[index:]]


def validate_sandbox_profile_options(
    args,
    forwarded_pytest_args: Sequence[str] | None = None,
) -> None:
    if getattr(args, "pg_test_profile", CORE_PROFILE) != PGVECTOR_PROFILE:
        return
    if args.suite != "int" or not getattr(args, "no_coverage", False):
        raise RuntimeError("--pg-test-profile pgvector requires scoped int --no-coverage")
    if getattr(args, "pg_container_runtime", "docker") != "docker":
        raise RuntimeError("--pg-test-profile pgvector requires --pg-container-runtime docker")
    if forwarded_pytest_args is not None and not list(forwarded_pytest_args):
        raise RuntimeError("canonical pgvector profile requires scoped test selector(s) after '--'")


def _materialize_inner_pgvector_if_requested(
    test_argv: list[str], inner_env: dict[str, str], *,
    runner_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    archive_verifier_fn: Callable[[bytes, dict[str, object]], None] | None = None,
) -> dict[str, object] | None:
    if extract_profile_from_argv(test_argv) == CORE_PROFILE:
        return None
    index = test_argv.index("--") if "--" in test_argv else len(test_argv)
    argv = test_argv[:index]
    suites = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--suite"]
    suites += [arg.split("=", 1)[1] for arg in argv if arg.startswith("--suite=")]
    if suites != ["int"] or "--no-coverage" not in argv:
        raise RuntimeError("only canonical int scoped --no-coverage profile pgvector allowed")
    if not test_argv[index + 1:]:
        raise RuntimeError("canonical pgvector profile requires scoped test selector(s) after '--'")

    if archive_verifier_fn is not None:
        raise RuntimeError("upstream-source materialization is retired for this profile")
    setup = _SetupCommands(inner_env, runner_fn)
    try:
        from runner_pgvector_context import load_inner_plan
        role = load_inner_plan(inner_env)["role"]
        if role == "study":
            admit = admit_study_inner
        elif role == "body-study":
            from runner_body_study_sandbox import admit_body_study_inner
            admit = admit_body_study_inner
        elif role == "proof":
            from runner_pgvector_sandbox import admit_proof_inner
            admit = admit_proof_inner
        else:
            if role != "native":
                raise RuntimeError("pgvector profile requires native, proof or study role")
            admit = admit_inner
        report = admit(test_argv, inner_env, setup.execute)
        print(json.dumps(report, sort_keys=True), flush=True)
        return report
    except BaseException as error:
        setup.event(setup.failed_stage or setup.stage, "failed",
                    error=setup.safe_text(str(error))[:4096], terminal=True)
        raise
