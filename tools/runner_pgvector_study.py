"""Separately closed supplement-study cohort for the canonical runner.

Only the exact study selector, the pgvector profile, a study-schema plan
and a fresh study allocation/reservation may use the 4,800-second SDK
lifetime. The existing core/native cohorts, their 1,200-second lifetime,
300-second command bound and historical plans are untouched: a study plan
can never pass through an old role, and an old plan can never gain study
authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from retrieval_supplement_study_contracts import (
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
    STUDY_CONTROLLER_SECONDS,
    STUDY_INNER_PLAN_SCHEMA,
    STUDY_PLAN_SCHEMA,
    STUDY_PROFILE,
    STUDY_SDK_SECONDS,
    STUDY_SELECTOR,
)
from retrieval_supplement_study_plan import (
    is_inner_study_plan,
    validate_inner_study_plan,
    validate_study_plan,
)
from retrieval_supplement_study_schedule import build_schedule
from offline_qualification_values import STUDY_CAPS, refuse
from offline_qualification_values import BASE_CONFIG

STUDY_SDK_LIFETIME_SECONDS = STUDY_SDK_SECONDS
STUDY_CONTROLLER_LIFETIME_SECONDS = STUDY_CONTROLLER_SECONDS
_VALUE_OPTIONS = frozenset({
    "--suite", "--jobs", "--pg-container-port", "--pg-container-runtime",
    "--pg-test-profile", "--report-dir",
})
_FLAG_OPTIONS = frozenset({"--no-coverage", "--sandbox"})


def _forwarded_study_selector(values: list[str]) -> bool:
    """Accept one exact study owner, with only pytest's canonical ``-s`` flag."""
    return values == ["-s", STUDY_SELECTOR]


def is_study_plan(plan: Mapping[str, Any]) -> bool:
    schema = plan.get("schema") if isinstance(plan, Mapping) else None
    return type(schema) is str and schema in {STUDY_PLAN_SCHEMA, STUDY_INNER_PLAN_SCHEMA}


def validate_study_cohort(argv: list[str]) -> None:
    """Exact one-node cohort, scoped int, no coverage, pgvector profile."""
    refuse(type(argv) is list and all(type(value) is str for value in argv),
           "study cohort argv")
    refuse(argv.count("--") == 1, "study cohort separator")
    separator = argv.index("--")
    refuse(_forwarded_study_selector(argv[separator + 1:]), "exact study cohort required")
    options = argv[:separator]
    values: dict[str, str | bool] = {}
    index = 0
    while index < len(options):
        raw_name = options[index]
        name = "--jobs" if raw_name.startswith("--jobs=") else raw_name
        refuse(name not in values, "duplicate study runner option")
        if name in _FLAG_OPTIONS:
            values[name] = True
            index += 1
            continue
        if raw_name.startswith("--jobs="):
            values["--jobs"] = raw_name.split("=", 1)[1]
            index += 1
            continue
        refuse(name in _VALUE_OPTIONS and index + 1 < len(options),
               "unsupported study runner option")
        values[name] = options[index + 1]
        index += 2
    refuse(values.get("--suite") == "int" and values.get("--no-coverage") is True,
           "study requires scoped int without coverage")
    refuse(values.get("--jobs", "1") == "1", "study requires one job")
    refuse(values.get("--pg-test-profile") == STUDY_PROFILE, "study profile/cohort mismatch")
    refuse(values.get("--pg-container-runtime", "docker") == "docker"
           and values.get("--pg-container-port", "55433") == "55433", "study runtime/port")
    refuse(isinstance(values.get("--report-dir"), str) and bool(values["--report-dir"]),
           "study receipt report directory required")


def validate_study_runner_plan(plan: Mapping[str, Any]) -> None:
    if is_inner_study_plan(plan):
        record = validate_inner_study_plan(plan)
    else:
        record = validate_study_plan(plan)
    refuse(record["sdk_image"] == SDK_IMAGE_SHA256, "study SDK identity mismatch")
    refuse(record["provider_sha256"] == PROVIDER_MANIFEST_SHA256.removeprefix("sha256:"),
           "study provider identity mismatch")
    refuse(record["derived"]["base_config"] == BASE_CONFIG,
           "study base runtime identity mismatch")
    archive = record["archive"]
    refuse(archive["bytes"] == RUNTIME_ARCHIVE_BYTES
           and archive["sha256"] == RUNTIME_ARCHIVE_SHA256.removeprefix("sha256:"),
           "study runtime archive identity mismatch")
    refuse(record["derived"]["runtime_config"] == RUNTIME_CONFIG_SHA256,
           "study runtime config identity mismatch")
    refuse(STUDY_CAPS.get("sdk_study") == MAX_RUNTIME_IMPORTS
           and STUDY_CAPS.get("runtime_exports") == MAX_RUNTIME_EXPORTS
           and STUDY_CAPS.get("materializations") == 0
           and STUDY_CAPS.get("read_bytes") == MAX_READ_BYTES
           and STUDY_CAPS.get("transfer_bytes") == MAX_TRANSFER_BYTES
           and int(record["archive"]["bytes"]) <= MAX_ARCHIVE_BYTES,
           "study allocation caps mismatch")
    refuse(record["schedule_sha256"] == build_schedule()["schedule_sha256"],
           "study schedule digest mismatch")


def study_sdk_lifetime(plan: Mapping[str, Any]) -> int:
    refuse(is_study_plan(plan), "study lifetime requires study plan")
    validate_study_runner_plan(plan)
    return STUDY_SDK_LIFETIME_SECONDS


__all__ = [
    "STUDY_CONTROLLER_LIFETIME_SECONDS", "STUDY_SDK_LIFETIME_SECONDS", "is_study_plan",
    "study_sdk_lifetime", "validate_study_cohort", "validate_study_runner_plan",
]
