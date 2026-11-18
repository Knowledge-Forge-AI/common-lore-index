"""Separately closed body-study cohort for the canonical runner.

Only the exact body selector, the pgvector profile, a body-schema plan
and a fresh body allocation/reservation may use the 4,800-second SDK
lifetime. The existing core/native cohorts, their 1,200-second lifetime,
300-second command bound and historical plans are untouched: a body plan
can never pass through an old role, and an old plan can never gain body
authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from retrieval_body_study_contracts import (
    MAX_ARCHIVE_BYTES,
    MAX_READ_BYTES,
    MAX_RUNTIME_EXPORTS,
    MAX_RUNTIME_IMPORTS,
    PROVIDER_MANIFEST_SHA256,
    RUNTIME_ARCHIVE_BYTES,
    RUNTIME_ARCHIVE_SHA256,
    RUNTIME_CONFIG_SHA256,
    SDK_IMAGE_SHA256,
    MAX_TRANSFER_BYTES,
    BODY_CONTROLLER_SECONDS,
    BODY_INNER_PLAN_SCHEMA,
    BODY_PLAN_SCHEMA,
    BODY_PROFILE,
    BODY_SDK_SECONDS,
    BODY_SELECTOR,
)
from retrieval_body_study_plan import (
    is_inner_body_plan,
    validate_inner_body_plan,
    validate_body_plan,
)
from retrieval_body_study_schedule import build_schedule
from offline_qualification_values import BODY_CAPS, refuse
from offline_qualification_values import BASE_CONFIG

BODY_SDK_LIFETIME_SECONDS = BODY_SDK_SECONDS
BODY_CONTROLLER_LIFETIME_SECONDS = BODY_CONTROLLER_SECONDS
_VALUE_OPTIONS = frozenset({
    "--suite", "--jobs", "--pg-container-port", "--pg-container-runtime",
    "--pg-test-profile", "--report-dir",
})
_FLAG_OPTIONS = frozenset({"--no-coverage", "--sandbox"})


def _forwarded_body_selector(values: list[str]) -> bool:
    """Accept one exact body owner, with only pytest's canonical ``-s`` flag."""
    return values == ["-s", BODY_SELECTOR]


def is_body_plan(plan: Mapping[str, Any]) -> bool:
    schema = plan.get("schema") if isinstance(plan, Mapping) else None
    return type(schema) is str and schema in {BODY_PLAN_SCHEMA, BODY_INNER_PLAN_SCHEMA}


def validate_body_cohort(argv: list[str]) -> None:
    """Exact one-node cohort, scoped int, no coverage, pgvector profile."""
    refuse(type(argv) is list and all(type(value) is str for value in argv),
           "body cohort argv")
    refuse(argv.count("--") == 1, "body cohort separator")
    separator = argv.index("--")
    refuse(_forwarded_body_selector(argv[separator + 1:]), "exact body cohort required")
    options = argv[:separator]
    values: dict[str, str | bool] = {}
    index = 0
    while index < len(options):
        raw_name = options[index]
        name = "--jobs" if raw_name.startswith("--jobs=") else raw_name
        refuse(name not in values, "duplicate body runner option")
        if name in _FLAG_OPTIONS:
            values[name] = True
            index += 1
            continue
        if raw_name.startswith("--jobs="):
            values["--jobs"] = raw_name.split("=", 1)[1]
            index += 1
            continue
        refuse(name in _VALUE_OPTIONS and index + 1 < len(options),
               "unsupported body runner option")
        values[name] = options[index + 1]
        index += 2
    refuse(values.get("--suite") == "int" and values.get("--no-coverage") is True,
           "body requires scoped int without coverage")
    refuse(values.get("--jobs", "1") == "1", "body requires one job")
    refuse(values.get("--pg-test-profile") == BODY_PROFILE, "body profile/cohort mismatch")
    refuse(values.get("--pg-container-runtime", "docker") == "docker"
           and values.get("--pg-container-port", "55433") == "55433", "body runtime/port")
    refuse(isinstance(values.get("--report-dir"), str) and bool(values["--report-dir"]),
           "body receipt report directory required")


def validate_body_runner_plan(plan: Mapping[str, Any]) -> None:
    if is_inner_body_plan(plan):
        record = validate_inner_body_plan(plan)
    else:
        record = validate_body_plan(plan)
    refuse(record["sdk_image"] == SDK_IMAGE_SHA256, "body SDK identity mismatch")
    refuse(record["provider_sha256"] == PROVIDER_MANIFEST_SHA256.removeprefix("sha256:"),
           "body provider identity mismatch")
    refuse(record["derived"]["base_config"] == BASE_CONFIG,
           "body base runtime identity mismatch")
    archive = record["archive"]
    refuse(archive["bytes"] == RUNTIME_ARCHIVE_BYTES
           and archive["sha256"] == RUNTIME_ARCHIVE_SHA256.removeprefix("sha256:"),
           "body runtime archive identity mismatch")
    refuse(record["derived"]["runtime_config"] == RUNTIME_CONFIG_SHA256,
           "body runtime config identity mismatch")
    refuse(BODY_CAPS.get("sdk_body_study") == MAX_RUNTIME_IMPORTS
           and BODY_CAPS.get("runtime_exports") == MAX_RUNTIME_EXPORTS
           and BODY_CAPS.get("materializations") == 0
           and BODY_CAPS.get("read_bytes") == MAX_READ_BYTES
           and BODY_CAPS.get("transfer_bytes") == MAX_TRANSFER_BYTES
           and int(record["archive"]["bytes"]) <= MAX_ARCHIVE_BYTES,
           "body allocation caps mismatch")
    refuse(record["schedule_sha256"] == build_schedule()["schedule_sha256"],
           "body schedule digest mismatch")
    from retrieval_body_study_budget import body_forecast_from_binding
    refuse(record["byte_forecast_sha256"] == body_forecast_from_binding(record)["byte_forecast_sha256"],
           "body byte forecast digest mismatch")


def body_sdk_lifetime(plan: Mapping[str, Any]) -> int:
    refuse(is_body_plan(plan), "body lifetime requires body plan")
    validate_body_runner_plan(plan)
    return BODY_SDK_LIFETIME_SECONDS


__all__ = [
    "BODY_CONTROLLER_LIFETIME_SECONDS", "BODY_SDK_LIFETIME_SECONDS", "is_body_plan",
    "body_sdk_lifetime", "validate_body_cohort", "validate_body_runner_plan",
]
