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

from runner_pgvector_budget import (
    BODY_CAPS, CAPS, INITIAL_CAPS, MAX_ARCHIVE, PROOF_CAPS, RECOVERY2_CAPS, STUDY_CAPS,
    Allocation, private_file, refuse,
)
from retrieval_supplement_study_contracts import (
    STUDY_INNER_PLAN_SCHEMA, STUDY_PHASE, STUDY_PLAN_SCHEMA, STUDY_SELECTOR,
)
from runner_pgvector_archive import runtime_config_for_diff_id
from runner_pgvector_prebuilt import (
    BASE_CONFIG, ENV_OFFLINE_PLAN, INNER_PLAN, OWNER_VALUE, PROVIDER_SHA256, SDK_IMAGE,
    digest, json_bytes, provider_facts, read_regular, recipe_digest, safe_path, strict_object,
)
from runner_pgvector_supplement_proof import (
    PROOF_INNER_SCHEMA, PROOF_ROLE, PROOF_SCHEMA, PROOF_SELECTOR,
    forwarded_selector_matches, validate_proof_cohort, validate_proof_plan,
)
from offline_qualification_checkout import MAX_PLAN, verify_checkout
from runner_body_study_context import (
    BODY_INNER_PLAN_SCHEMA, BODY_PLAN_SCHEMA, BODY_ROLE, body_host_context,
    body_inner_source_read, body_scratch_bytes, body_source_read_receipt,
    is_body_receipt_phase, select_offline_role,
)

CORE_SELECTOR = "src/test/int/python/repomap_test_support/pgvector_admission.int.test.py"
NATIVE_SELECTOR = "src/test/int/python/repomap_test_support/pgvector_prebuilt_runtime.int.test.py"
PLAN_SCHEMA = "cloe-pgvector-offline-plan-v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _selector_for_role(role: str) -> str:
    return select_offline_role(role, core=CORE_SELECTOR, native=NATIVE_SELECTOR,
                               study=STUDY_SELECTOR, proof_role=PROOF_ROLE, proof=PROOF_SELECTOR)


def require_native_host(runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
    refuse(platform.machine().lower() in {"arm64", "aarch64"}, "native ARM host required")
    command = ["docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"]
    result = (runner or subprocess.run)(command, capture_output=True, text=True,
                                       check=False, timeout=30)
    refuse(result.returncode == 0 and result.stdout.strip() in {"linux/arm64", "linux/aarch64"},
           "native ARM Docker server required")


def validate_cohort(argv: list[str], role: str) -> None:
    if role == PROOF_ROLE:
        validate_proof_cohort(argv)
        return
    if role == "study":
        from runner_pgvector_study import validate_study_cohort
        validate_study_cohort(argv)
        return
    if role == "body-study":
        from runner_body_study import validate_body_cohort
        validate_body_cohort(argv)
        return
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


def _validate_study_plan(plan: dict[str, Any]) -> None:
    from runner_pgvector_study import validate_study_runner_plan
    validate_study_runner_plan(plan)
    refuse(plan.get("provider_sha256") == PROVIDER_SHA256 and plan.get("sdk_image") == SDK_IMAGE,
           "study plan provider/SDK identity")
    for receipt_key in ("review1_receipt_sha256", "operator_decision_sha256"):
        raw_val = str(plan.get(receipt_key, ""))
        hex_val = raw_val[7:] if raw_val.startswith("sha256:") else raw_val
        refuse(_SHA.fullmatch(hex_val) is not None, "study authority receipt bindings")
    derived = plan.get("derived")
    refuse(isinstance(derived, dict) and derived.get("base_config") == BASE_CONFIG,
           "study derived base identity")
    derived = cast(dict[str, Any], derived)
    runtime = derived.get("runtime_config_document")
    refuse(isinstance(runtime, dict)
           and derived.get("runtime_config") == "sha256:" + digest(json_bytes(runtime)),
           "study derived config identity")


def validate_plan(plan: dict[str, Any], *, expected_caps: Mapping[str, int] = CAPS) -> None:
    if plan.get("schema") in {PROOF_SCHEMA, PROOF_INNER_SCHEMA}:
        validate_proof_plan(plan)
        return
    if plan.get("schema") in {STUDY_PLAN_SCHEMA, STUDY_INNER_PLAN_SCHEMA}:
        _validate_study_plan(plan)
        return
    if plan.get("schema") in {BODY_PLAN_SCHEMA, BODY_INNER_PLAN_SCHEMA}:
        from runner_body_study import validate_body_runner_plan
        validate_body_runner_plan(plan)
        return
    refuse(plan.get("role") != "study", "study role requires study schema")
    refuse(plan.get("role") != "body-study", "body-study role requires body-study schema")
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
        if self.plan.get("schema") == STUDY_PLAN_SCHEMA:
            caps = STUDY_CAPS
        elif self.plan.get("schema") in {PROOF_SCHEMA, PROOF_INNER_SCHEMA}:
            caps = PROOF_CAPS
        elif self.plan.get("schema") == BODY_PLAN_SCHEMA:
            caps = BODY_CAPS
        else:
            caps = CAPS
        return Allocation(Path(self.plan["allocation_path"]), caps)


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
    if plan.get("schema") in {BODY_PLAN_SCHEMA, BODY_INNER_PLAN_SCHEMA} or plan.get("role") == BODY_ROLE:
        return body_host_context(argv, root, path, digest(raw), plan)
    refuse(plan.get("schema") not in {STUDY_INNER_PLAN_SCHEMA, PROOF_INNER_SCHEMA},
           "inner plan cannot authorize host dispatch")
    validate_plan(plan)
    validate_cohort(argv, plan["role"])
    if plan["role"] in {"study", PROOF_ROLE}:
        count = sum((root / safe_path(name)).lstat().st_size for name, row in plan["candidate_snapshot"].items()
                    if "symlink" not in row)
        refuse(0 < count <= 128 * 1024**2, "new cohort source byte bound")
        caps = STUDY_CAPS if plan["role"] == "study" else PROOF_CAPS
        with Allocation(Path(plan["allocation_path"]), caps).session() as meter:
            meter.charge("read_bytes", count + MAX_PLAN)
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
    if plan["role"] == "study":
        from retrieval_supplement_study_plan import require_reservation_admitted_for_runner
        private_file(Path(plan["reservation_path"]))
        require_reservation_admitted_for_runner(plan, plan_path=path)
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
    if plan["role"] in {"study", PROOF_ROLE}:
        from retrieval_supplement_inner_reads import reserve_inner_source_verification
        reserve_inner_source_verification(plan, Path("/workspace"))
    elif plan["role"] == BODY_ROLE:
        body_inner_source_read(plan)
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


def sandbox_scratch_bytes(environment: Mapping[str, str]) -> int:
    """Recognize the fixed bounded mount only from a sealed inner plan.

    Capacity checkpoints do not rehash the whole source or reserve another
    source-verification slot. Entry/discovery retain that separate ownership.
    This recognizer revalidates private plan bytes and sandbox authentication
    on every call; ambient flags cannot supply a size or measurement path.
    """
    if not offline_plan_requested(environment):
        return 8 * 1024**3
    from test_sandbox import active_sandbox
    refuse(environment.get(ENV_OFFLINE_PLAN) == INNER_PLAN
           and active_sandbox(environment), "scratch profile requires inner sandbox")
    path = Path(INNER_PLAN)
    private_file(path)
    plan = strict_object(read_regular(path, MAX_PLAN))
    validate_plan(plan)
    if plan.get("role") in {"study", PROOF_ROLE}:
        refuse(plan.get("schema") in {STUDY_INNER_PLAN_SCHEMA, PROOF_INNER_SCHEMA},
               "scratch profile requires sealed inner plan")
        return 2 * 1024**3
    if plan.get("role") == BODY_ROLE:
        return body_scratch_bytes(plan)
    return 8 * 1024**3


def source_read_receipt_for_report(*, collect_only: bool = False) -> dict[str, Any] | None:
    if not collect_only and is_body_receipt_phase(os.environ):
        return body_source_read_receipt(os.environ)
    if collect_only or not os.environ.get("REPOMAP_TEST_PHASE", "").startswith(STUDY_PHASE):
        return None
    if not offline_plan_requested(os.environ):
        return None
    plan = load_inner_plan(os.environ)
    if plan["role"] not in {"study", "proof"}:
        return None
    from retrieval_supplement_inner_reads import read_receipt
    return read_receipt(plan)


def offline_session_owned_by_test(session: Any) -> bool:
    """No automatic extra cluster for either sealed, exact offline cohort."""
    if not offline_plan_requested(os.environ):
        return False
    plan = load_inner_plan(os.environ)
    expected = _selector_for_role(plan["role"])
    expected_profile = "core" if plan["role"] == "core" else "pgvector"
    refuse(os.environ.get("REPOMAP_TEST_PG_PROFILE", "core") == expected_profile,
           "pytest offline profile mismatch")
    args = session.config.args
    refuse(isinstance(args, list) and 0 < len(args) <= 128, "pytest offline cohort missing")
    if plan["role"] in {"study", PROOF_ROLE, "body-study"}:
        refuse(sum(str(argument) == "-s" for argument in args) <= 1,
               "duplicate pytest stream flag")
    for argument in args:
        if plan["role"] in {"study", PROOF_ROLE, "body-study"} and str(argument) == "-s":
            continue
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
    expected = _selector_for_role(plan["role"])
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
    selector = _selector_for_role(plan["role"])
    profile = "core" if plan["role"] == "core" else "pgvector"
    allow_stream = plan["role"] in {"study", PROOF_ROLE, "body-study"}
    refuse(args.suite == "int" and args.no_coverage is True
           and (getattr(args, "pg_test_profile", None) or "core") == profile
           and forwarded_selector_matches(forwarded, selector, allow_stream=allow_stream),
           "offline runtime ownership drift")
    refuse("REPOMAP_TEST_RUNTIME_IMAGE" not in os.environ
           and not getattr(args, "smoke_image_reference", None),
           "offline runtime image override forbidden")
    return True
