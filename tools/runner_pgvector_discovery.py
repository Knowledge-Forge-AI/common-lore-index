"""Authenticate the one offline discovery handoff without broad environment forwarding."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from runner_pgvector_budget import refuse
from runner_pgvector_context import CORE_SELECTOR, NATIVE_SELECTOR, load_inner_plan, offline_plan_requested
from runner_pgvector_prebuilt import ENV_OFFLINE_PLAN


def _profile(plan: dict[str, Any], environment: Mapping[str, str]) -> None:
    expected = "core" if plan["role"] == "core" else "pgvector"
    refuse(environment.get("REPOMAP_TEST_PG_PROFILE", "core") == expected,
           "discovery offline profile mismatch")


def project_environment(environment: Mapping[str, str], projected: dict[str, str]) -> dict[str, str]:
    if not offline_plan_requested(environment):
        return projected
    plan = load_inner_plan(environment)
    _profile(plan, environment)
    return {**projected, ENV_OFFLINE_PLAN: environment[ENV_OFFLINE_PLAN]}


def plan_for_request(request: dict[str, Any]) -> dict[str, Any] | None:
    environment = request["environment"]
    if not offline_plan_requested(environment):
        return None
    plan = load_inner_plan(environment)
    _profile(plan, environment)
    refuse(request["suite"] == "int" and request["scoped"] is True
           and request["cwd"] == "/workspace"
           and request["candidate"]["commit"] == plan["candidate_head"],
           "discovery offline request mismatch")
    arguments = request["pytest_args"]
    refuse(isinstance(arguments, list) and all(type(a) is str for a in arguments),
           "discovery argument vector")
    if len(arguments) == 5:
        base = Path(arguments[1])
        refuse(arguments[0] == "--basetemp" and arguments[2] == "-o"
               and base.is_absolute() and base.resolve().is_relative_to(Path("/sandbox-scratch"))
               and arguments[3] == "cache_dir=" + str(base / "cache"),
               "discovery canonical scratch arguments")
        arguments = arguments[4:]
    expected = CORE_SELECTOR if plan["role"] == "core" else NATIVE_SELECTOR
    refuse(arguments in ([expected], ["/workspace/" + expected]), "discovery exact offline selector")
    return plan
