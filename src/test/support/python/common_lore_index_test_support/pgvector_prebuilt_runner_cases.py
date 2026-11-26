"""Doubles-only actual pre-pytest cache gate; no image or toolchain execution."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

import runner_pgvector_context as contexts
from runner_pgvector_context import CORE_SELECTOR, NATIVE_SELECTOR, validate_cohort
from runner_pgvector_prebuilt import ENV_OFFLINE_PLAN
from runner_unit_execution import run_selected_suites
import common_lore_index_test_support.resource_test_images as images


def runtime_cache_case(monkeypatch: Any, role: str, mutation: str) -> None:
    calls: list[str] = []
    selected_role = "native" if role == "native" else "core"
    selector = NATIVE_SELECTOR if selected_role == "native" else CORE_SELECTOR
    profile = "pgvector" if selected_role == "native" else "core"
    args = SimpleNamespace(suite="int", no_coverage=True, pg_test_profile=profile,
                           smoke_image_reference=None)
    forwarded = [selector]
    argv = ["--suite", "int", "--no-coverage", "--pg-test-profile", profile,
            "--report-dir", "/public/owned-report", "--", selector]
    monkeypatch.delenv(ENV_OFFLINE_PLAN, raising=False)
    monkeypatch.setenv("REPOMAP_TEST_RUNTIME_IMAGE", "public-reset-registration")
    monkeypatch.delenv("REPOMAP_TEST_RUNTIME_IMAGE", raising=False)
    if role != "ordinary":
        monkeypatch.setenv(ENV_OFFLINE_PLAN, "/public/unused-unit-plan")
    if mutation == "argv":
        argv[-1] = "src/test/int/python/unrelated.int.test.py"
    elif mutation == "profile":
        args.pg_test_profile = "wrong-profile"
    elif mutation == "forwarded":
        forwarded.append("src/test/int/python/unrelated.int.test.py")
    elif mutation == "ambient":
        monkeypatch.setenv("REPOMAP_TEST_RUNTIME_IMAGE", "sha256:" + "0" * 64)
    monkeypatch.setattr(sys, "argv", ["tools/run_tests.py", *argv])
    def inner(actual: list[str], environment: Any) -> dict[str, Any]:
        calls.append("auth")
        assert environment[ENV_OFFLINE_PLAN] == "/public/unused-unit-plan"
        if mutation == "auth":
            raise RuntimeError("synthetic authentication refusal")
        validate_cohort(actual, selected_role)
        return {"role": selected_role}
    monkeypatch.setattr(contexts, "inner_plan", inner)
    def ensure(**_kwargs: Any) -> str:
        calls.append("cache")
        return "sha256:" + "1" * 64
    monkeypatch.setattr(images, "ensure_canonical_runtime_image", ensure)
    def terminal() -> dict[str, str]:
        calls.append("terminal")
        return {}
    def execute(actual: Any, suites: Any, selected: list[str]) -> int:
        assert actual is args and suites == ("int",) and selected == forwarded
        calls.append("pytest")
        return 0
    def run() -> int:
        return run_selected_suites(args, forwarded, object(),
            SimpleNamespace(client=object(), verify_terminal=terminal),
            require_integration_sandbox_fn=lambda *_a: None,
            validate_runner_options_fn=lambda *_a: None, run_system_suite_fn=None,
            run_smoke_suite_fn=lambda *_a: 0,
            prepare_go_test_environment_fn=lambda *_a: calls.append("go"),
            run_pytest_suites_fn=execute, report_docker_projection_fn=lambda *_a, **_k: None)
    if role != "ordinary" and mutation != "none":
        with pytest.raises(RuntimeError):
            run()
        assert "cache" not in calls and "pytest" not in calls and "go" not in calls
    else:
        assert run() == 0
        expected = (["cache"] if mutation != "ambient" else []) if role == "ordinary" else ["auth"]
        assert calls == [*expected, "go", "pytest", "terminal"]
