"""Closed cohort and source custody for the separately authorized offline proof."""
from __future__ import annotations

from dataclasses import dataclass
import os
import platform
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, cast, Callable, Mapping

from runner_pgvector_budget import INITIAL_CAPS, RECOVERY2_CAPS, Allocation, CAPS, MAX_ARCHIVE, private_file, refuse
from runner_pgvector_archive import runtime_config_for_diff_id
from runner_pgvector_prebuilt import (
    BASE_CONFIG, ENV_OFFLINE_PLAN, INNER_PLAN, OWNER_VALUE, PROVIDER_SHA256, SDK_IMAGE,
    digest, json_bytes, provider_facts, read_regular, recipe_digest, safe_path, strict_object,
)

CORE_SELECTOR = "src/test/int/python/repomap_test_support/pgvector_admission.int.test.py"
NATIVE_SELECTOR = "src/test/int/python/repomap_test_support/pgvector_prebuilt_runtime.int.test.py"
PLAN_SCHEMA = "cloe-pgvector-offline-plan-v1"
MAX_PLAN = 2 * 1024**2
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def require_native_host(runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
    refuse(platform.machine().lower() in {"arm64", "aarch64"}, "native ARM host required")
    command = ["docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"]
    result = (runner or subprocess.run)(command, capture_output=True, text=True,
                                       check=False, timeout=30)
    refuse(result.returncode == 0 and result.stdout.strip() in {"linux/arm64", "linux/aarch64"},
           "native ARM Docker server required")


def validate_cohort(argv: list[str], role: str) -> None:
    refuse(role in {"core", "native"} and argv.count("--") == 1, "cohort role/separator")
    separator = argv.index("--")
    expected = CORE_SELECTOR if role == "core" else NATIVE_SELECTOR
    refuse(argv[separator + 1:] == [expected], "exact offline cohort required")
    options = argv[:separator]
    values: dict[str, str | bool] = {}
    i = 0
    while i < len(options):
        name = options[i]
        refuse(name not in values, "duplicate runner option")
        if name in {"--no-coverage", "--sandbox"}:
            values[name] = True
            i += 1
        else:
            refuse(name in {"--suite", "--pg-container-port", "--pg-container-runtime",
                            "--pg-test-profile", "--report-dir"} and i + 1 < len(options),
                   "unsupported offline runner option")
            values[name] = options[i + 1]
            i += 2
    refuse(values.get("--suite") == "int" and values.get("--no-coverage") is True,
           "offline requires scoped int without coverage")
    refuse(values.get("--pg-container-runtime", "docker") == "docker"
           and values.get("--pg-container-port", "55433") == "55433", "offline runtime/port")
    refuse(values.get("--pg-test-profile", "core") == ("core" if role == "core" else "pgvector"),
           "offline profile/cohort mismatch")
    if role == "native":
        refuse(isinstance(values.get("--report-dir"), str) and bool(values["--report-dir"]),
               "native receipt report directory required")


def verify_checkout(root: Path, expected: dict[str, Any], head: str) -> None:
    def git(*args: str) -> bytes:
        result = subprocess.run(["git", "-c", "safe.directory=" + str(root.resolve()), *args], cwd=root, capture_output=True, check=False,
                                timeout=30, env=os.environ | {"GIT_OPTIONAL_LOCKS": "0"})
        refuse(result.returncode == 0 and len(result.stdout) <= MAX_PLAN, "source inventory command")
        return result.stdout
    refuse(git("rev-parse", "HEAD").decode().strip() == head, "source HEAD drift")
    names = {name.decode() for name in git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
             .split(b"\0") if name}
    refuse(names == set(expected), "source inventory drift")
    for name, row in expected.items():
        path = root / safe_path(name)
        if "symlink" in row:
            refuse(set(row) == {"symlink"} and path.is_symlink()
                   and os.readlink(path) == row["symlink"], "source link drift")
        else:
            refuse(set(row) == {"sha256", "mode"} and not path.is_symlink()
                   and path.stat().st_mode & 0o777 == row["mode"]
                   and digest(read_regular(path, 8 * 1024**2)) == row["sha256"], "source content drift")


def validate_plan(plan: dict[str, Any], *, expected_caps: Mapping[str, int] = CAPS) -> None:
    refuse(expected_caps in (CAPS, RECOVERY2_CAPS, INITIAL_CAPS), "unknown plan caps version")
    attempt = plan.get("core_attempt", 1)
    allowed = (1, 3) if expected_caps == CAPS else ((1, 2) if expected_caps == RECOVERY2_CAPS else (1,))
    refuse(type(attempt) is int and attempt in allowed, "core attempt ordinal")
    if attempt > 1:
        refuse(_SHA.fullmatch(str(plan.get("recovery_parent_sha256", ""))) is not None,
               "recovery parent binding")
    refuse(plan.get("schema") == PLAN_SCHEMA and plan.get("phase") == OWNER_VALUE
           and plan.get("role") in {"core", "native"}, "plan identity")
    refuse(plan.get("provider_sha256") == PROVIDER_SHA256 and plan.get("sdk_image") == SDK_IMAGE,
           "plan provider/SDK identity")
    refuse(_SHA.fullmatch(str(plan.get("review1_receipt_sha256", ""))) is not None
           and _SHA.fullmatch(str(plan.get("operator_decision_sha256", ""))) is not None,
           "required authority receipt bindings")
    refuse(isinstance(plan.get("candidate_snapshot"), dict) and len(plan["candidate_snapshot"]) > 100,
           "whole candidate required")
    declaration = plan.get("diagnostic_declaration")
    refuse(isinstance(declaration, dict)
           and declaration.get("schema") == "cloe-pgvector-diagnostic-declaration-v1"
           and declaration.get("phase") == plan["phase"] and declaration.get("caps") == expected_caps
           and all(type(declaration["caps"][key]) is int for key in CAPS)
           and declaration.get("provider_sha256") == PROVIDER_SHA256
           and declaration.get("sdk_image") == SDK_IMAGE
           and declaration.get("candidate_head") == plan.get("candidate_head")
           and declaration.get("candidate_snapshot_sha256") == digest(json_bytes(plan["candidate_snapshot"]))
           and declaration.get("review1_receipt_sha256") == plan["review1_receipt_sha256"]
           and declaration.get("operator_decision_sha256") == plan["operator_decision_sha256"]
           and plan.get("diagnostic_declaration_sha256") == digest(json_bytes(declaration)),
           "diagnostic declaration correspondence")
    declaration = cast(dict[str, Any], declaration)
    for key in ("review1_receipt_sha256", "operator_decision_sha256", "original_allocation_sha256",
                "corrective_selection_sha256", "signature_disposition_sha256"):
        value = declaration.get(key)
        refuse(isinstance(value, str) and _SHA.fullmatch(value) is not None and value != "0" * 64,
               "diagnostic declaration authority hash")
    expected = plan.get("derived")
    refuse(isinstance(expected, dict) and expected.get("base_config") == BASE_CONFIG,
           "derived base identity")
    expected = cast(dict[str, Any], expected)
    runtime = expected.get("runtime_config_document")
    refuse(isinstance(runtime, dict)
           and expected.get("runtime_config") == "sha256:" + digest(json_bytes(runtime)),
           "derived config identity")
    runtime = cast(dict[str, Any], runtime)
    base_raw = expected.get("base_config_raw")
    refuse(isinstance(base_raw, str) and "sha256:" + digest(base_raw.encode()) == BASE_CONFIG,
           "derived raw base config")
    base_raw = cast(str, base_raw)
    base = strict_object(base_raw.encode())
    refuse(expected.get("base_config_document") == base, "derived base config correspondence")
    facts = provider_facts()
    refuse(runtime.get("os") == "linux" and runtime.get("architecture") == "arm64"
           and runtime.get("rootfs", {}).get("diff_ids", [])[:-1] ==
               [row["diff_id"] for row in facts["base_layers"]], "derived ordered base layers")
    refuse(len(expected.get("runtime_layers", [])) == len(facts["base_layers"]) + 1,
           "derived layer inventory")
    refuse(runtime["rootfs"]["diff_ids"] ==
           ["sha256:" + row["sha256"] for row in expected["runtime_layers"]],
           "derived layer correspondence")
    refuse(runtime == runtime_config_for_diff_id(base, runtime["rootfs"]["diff_ids"][-1]),
           "unexpected runtime configuration change")
    labels = runtime.get("config", {}).get("Labels", {})
    refuse(labels.get("org.repomap.test.pgvector.recipe") == recipe_digest()
           and labels.get("org.repomap.test.pgvector.owner") == OWNER_VALUE
           and runtime["config"].get("Cmd") == ["postgres"], "derived recipe/owner")


@dataclass(frozen=True)
class OfflineContext:
    plan_path: Path
    plan: dict[str, Any]
    raw_sha256: str
    repo_root: Path

    def fixed(self) -> None:
        private_file(self.plan_path)
        refuse(digest(read_regular(self.plan_path, MAX_PLAN)) == self.raw_sha256, "plan changed")

    def allocation(self) -> Allocation:
        return Allocation(Path(self.plan["allocation_path"]))


def _host_context(argv: list[str], root: Path,
                 environment: Mapping[str, str] | None = None) -> OfflineContext | None:
    text = (os.environ if environment is None else environment).get(ENV_OFFLINE_PLAN)
    if text is None:
        return None
    path = Path(text)
    refuse(path.parent.stat().st_uid == os.getuid() and path.parent.stat().st_mode & 0o777 == 0o700,
           "plan directory custody")
    private_file(path)
    raw = read_regular(path, MAX_PLAN)
    plan = strict_object(raw)
    validate_plan(plan)
    validate_cohort(argv, plan["role"])
    verify_checkout(root, plan["candidate_snapshot"], plan["candidate_head"])
    archive = plan.get("archive")
    refuse(isinstance(archive, dict) and type(archive.get("bytes")) is int
           and 0 < archive["bytes"] <= MAX_ARCHIVE
           and _SHA.fullmatch(str(archive.get("sha256", ""))) is not None, "archive descriptor")
    archive = cast(dict[str, Any], archive)
    private_file(Path(archive["path"]))
    private_file(Path(plan["allocation_path"]))
    refuse(Path(archive["path"]).parent == path.parent
           and Path(plan["allocation_path"]).parent == path.parent, "nonlocal offline resources")
    if plan["role"] == "core":
        refuse(Path(plan["export_path"]).parent == path.parent, "nonlocal runtime export")
    return OfflineContext(path, plan, digest(raw), root)


def host_context(argv: list[str], root: Path,
                 environment: Mapping[str, str] | None = None) -> OfflineContext | None:
    try:
        return _host_context(argv, root, environment)
    except (OSError, ValueError):
        raise RuntimeError("pgvector prebuilt refused: offline plan unavailable or malformed") from None


def load_inner_plan(environment: Mapping[str, str]) -> dict[str, Any]:
    refuse(environment.get(ENV_OFFLINE_PLAN) == INNER_PLAN, "inner offline plan missing")
    path = Path(INNER_PLAN)
    private_file(path)
    plan = strict_object(read_regular(path, MAX_PLAN))
    validate_plan(plan)
    from test_sandbox import active_sandbox
    refuse(active_sandbox(environment), "unauthenticated offline inner sandbox")
    verify_checkout(Path("/workspace"), plan["candidate_snapshot"], plan["candidate_head"])
    return plan


def inner_plan(argv: list[str], environment: Mapping[str, str]) -> dict[str, Any]:
    plan = load_inner_plan(environment)
    validate_cohort(argv, plan["role"])
    return plan


def offline_plan_requested(environment: Mapping[str, str]) -> bool:
    """A leftover sealed artifact only refuses lost authority; it never admits."""
    if ENV_OFFLINE_PLAN in environment:
        return True
    try:
        Path(INNER_PLAN).lstat()
    except FileNotFoundError:
        return False
    except OSError:
        raise RuntimeError("pgvector prebuilt refused: offline marker unavailable") from None
    raise RuntimeError("pgvector prebuilt refused: offline authority lost")


def offline_session_owned_by_test(session: Any) -> bool:
    """No automatic extra cluster for either sealed, exact offline cohort."""
    if not offline_plan_requested(os.environ):
        return False
    plan = load_inner_plan(os.environ)
    expected = CORE_SELECTOR if plan["role"] == "core" else NATIVE_SELECTOR
    expected_profile = "core" if plan["role"] == "core" else "pgvector"
    refuse(os.environ.get("REPOMAP_TEST_PG_PROFILE", "core") == expected_profile,
           "pytest offline profile mismatch")
    args = session.config.args
    refuse(isinstance(args, list) and 0 < len(args) <= 128, "pytest offline cohort missing")
    for argument in args:
        path = Path(str(argument).split("::", 1)[0])
        if path.is_absolute():
            refuse(path.is_relative_to(Path("/workspace")), "pytest offline scope escape")
            path = path.relative_to(Path("/workspace"))
        refuse(path.as_posix() == expected, "pytest offline cohort drift")
    return True


def verify_offline_collected_items(session: Any) -> None:
    """Validate actual collected node owners, including discovery, before tests."""
    if not offline_session_owned_by_test(session):
        return
    plan = load_inner_plan(os.environ)
    expected = CORE_SELECTOR if plan["role"] == "core" else NATIVE_SELECTOR
    refuse(bool(session.items), "offline collected cohort empty")
    for item in session.items:
        owner = str(item.nodeid).split("::", 1)[0]
        refuse(owner == expected, "offline collected owner drift")


def offline_pytest_owns_runtime(args: Any, forwarded: list[str]) -> bool:
    """Only authenticated exact offline cohorts omit the unused Python image."""
    if not offline_plan_requested(os.environ):
        return False
    # Validate the real entrypoint argv rather than manufacture substitute args.
    plan = inner_plan(sys.argv[1:], os.environ)
    selector = CORE_SELECTOR if plan["role"] == "core" else NATIVE_SELECTOR
    profile = "core" if plan["role"] == "core" else "pgvector"
    refuse(args.suite == "int" and args.no_coverage is True
           and (getattr(args, "pg_test_profile", None) or "core") == profile
           and forwarded == [selector], "offline runtime ownership drift")
    refuse("REPOMAP_TEST_RUNTIME_IMAGE" not in os.environ
           and not getattr(args, "smoke_image_reference", None),
           "offline runtime image override forbidden")
    return True
