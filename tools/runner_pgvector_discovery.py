"""Authenticate the one offline discovery handoff without broad environment forwarding."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from runner_pgvector_budget import refuse
from runner_pgvector_context import (
    CORE_SELECTOR, NATIVE_SELECTOR, PROOF_ROLE, PROOF_SELECTOR, STUDY_SELECTOR,
    load_inner_plan, offline_plan_requested,
)
from runner_pgvector_supplement_proof import forwarded_selector_matches
from runner_pgvector_prebuilt import ENV_OFFLINE_PLAN


def _profile(plan: dict[str, Any], environment: Mapping[str, str]) -> None:
    expected = "core" if plan["role"] == "core" else "pgvector"
    refuse(environment.get("REPOMAP_TEST_PG_PROFILE", "core") == expected,
           "discovery offline profile mismatch")


def _selector(plan: dict[str, Any]) -> str:
    if plan["role"] == "core":
        return CORE_SELECTOR
    if plan["role"] == "native":
        return NATIVE_SELECTOR
    if plan["role"] == "study":
        return STUDY_SELECTOR
    if plan["role"] == "body-study":
        from retrieval_body_study_contracts import BODY_SELECTOR
        return BODY_SELECTOR
    if plan["role"] == PROOF_ROLE:
        return PROOF_SELECTOR
    refuse(False, "discovery offline role")
    raise AssertionError("unreachable")


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
    if len(arguments) >= 4:
        base = Path(arguments[1])
        refuse(arguments[0] == "--basetemp" and arguments[2] == "-o"
               and base.is_absolute() and base.resolve().is_relative_to(Path("/sandbox-scratch"))
               and arguments[3] == "cache_dir=" + str(base / "cache"),
               "discovery canonical scratch arguments")
        arguments = arguments[4:]
    expected = _selector(plan)
    absolute = "/workspace/" + expected
    allow_stream = plan["role"] in {"study", "body-study", PROOF_ROLE}
    refuse((forwarded_selector_matches(arguments, expected, allow_stream=allow_stream)
            or forwarded_selector_matches(arguments, absolute, allow_stream=allow_stream)),
           "discovery exact offline selector")
    return plan
