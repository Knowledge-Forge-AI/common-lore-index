"""Closed offline delivery contract for the synthetic W5 supplement seam.

This role proves runner, discovery and owned PostgreSQL setup only.  It is not
an actual supplement study: the schema deliberately has no study reservation,
task/model count, or registered-task authority and cannot enter the study
allocation or admission state machine.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import re
from typing import Any, cast

from offline_qualification_values import PROOF_CAPS, refuse
from offline_qualification_values import PROVIDER_SHA256, SDK_IMAGE, digest, json_bytes
from retrieval_supplement_study_provenance import (
    validate_study_candidate_snapshot, validate_study_runtime_provenance,
)


PROOF_PHASE = "CLOE-W5-SUPPLEMENT-STUDY1"
PROOF_ROLE = "proof"
PROOF_PROFILE = "pgvector"
PROOF_SELECTOR = "src/test/int/python/tools/retrieval_supplement_study_seam.int.test.py"
PROOF_SELECTORS = frozenset({PROOF_SELECTOR})
PROOF_SELECTOR_REGISTRY = (PROOF_SELECTOR,)
PROOF_SCHEMA = "cloe-pgvector-supplement-proof-v1"
PROOF_INNER_SCHEMA = "cloe-pgvector-supplement-proof-inner-v1"
PROOF_DECLARATION_SCHEMA = "cloe-pgvector-supplement-proof-declaration-v1"
PROOF_SDK_SECONDS = 1200
PROOF_COMMAND_SECONDS = 300
PROOF_RUNTIME_CONFIG = "sha256:4b8c5fb11ee4bd545a4cc7db662932d74c8213e0016bbb92d3d6b5809476a1f8"
PROOF_RUNTIME_ARCHIVE_BYTES = 502_569_984
PROOF_RUNTIME_ARCHIVE_SHA256 = "efa131b8123911f523845d4b36411936a2e63a66c15457cdc5ac86cd4c7a3a66"

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_STUDY_FIELDS = frozenset({
    "reservation_path", "study_admission", "study_result", "study_workers",
    "model_workers", "registered_tasks", "task_count", "query_count",
})
_COMMON_FIELDS = frozenset({
    "schema", "phase", "role", "profile", "selector", "candidate_head",
    "candidate_snapshot", "candidate_snapshot_sha256", "source_freeze_sha256",
    "provider_sha256", "sdk_image", "archive", "derived", "declaration",
})
_PLAN_FIELDS = _COMMON_FIELDS | {"allocation_path"}
_INNER_FIELDS = _COMMON_FIELDS | {"host_plan_sha256"}


def proof_declaration() -> dict[str, Any]:
    """Return the immutable zero-study finite proof declaration."""
    return {
        "schema": PROOF_DECLARATION_SCHEMA,
        "phase": PROOF_PHASE,
        "role": PROOF_ROLE,
        "profile": PROOF_PROFILE,
        "selector": PROOF_SELECTOR,
        "sdk_seconds": PROOF_SDK_SECONDS,
        "command_seconds": PROOF_COMMAND_SECONDS,
        "runtime_imports": 1,
        "runtime_exports": 0,
        "materializations": 0,
        "builds": 0,
        "pulls": 0,
        "caps": dict(PROOF_CAPS),
    }


def _snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    return digest(json_bytes(dict(snapshot)))


def _hex(value: Any, pattern: re.Pattern[str], code: str) -> str:
    text = str(value)
    refuse(pattern.fullmatch(text) is not None, code)
    return text


def forwarded_selector_matches(
    arguments: Sequence[str], selector: str, *, allow_stream: bool = False,
) -> bool:
    """Check one exact owner while allowing the canonical pytest ``-s`` flag."""
    values = [str(argument) for argument in arguments]
    stream_count = values.count("-s")
    if not allow_stream and stream_count:
        return False
    return stream_count <= 1 and [value for value in values if value != "-s"] == [selector]


def validate_proof_cohort(argv: list[str]) -> None:
    """Admit only the scoped pgvector proof selector and fixed runner options."""
    refuse(argv.count("--") == 1, "proof cohort separator")
    separator = argv.index("--")
    refuse(forwarded_selector_matches(argv[separator + 1:], PROOF_SELECTOR, allow_stream=True),
           "proof exact selector")
    options = argv[:separator]
    values: dict[str, str | bool] = {}
    index = 0
    while index < len(options):
        raw_name = options[index]
        name = raw_name.split("=", 1)[0] if raw_name.startswith(
            ("--jobs=", "--smoke-image-reference=")
        ) else raw_name
        refuse(name not in values, "duplicate proof runner option")
        if name in {"--no-coverage", "--sandbox"}:
            values[name] = True
            index += 1
            continue
        if raw_name.startswith(("--jobs=", "--smoke-image-reference=")):
            values[name] = raw_name.split("=", 1)[1]
            index += 1
            continue
        refuse(name in {"--suite", "--jobs", "--pg-container-port", "--pg-container-runtime",
                        "--pg-test-profile", "--report-dir", "--smoke-image-reference"}
               and index + 1 < len(options),
               "unsupported proof runner option")
        refuse(index + 1 < len(options), "unsupported proof runner option")
        values[name] = options[index + 1]
        index += 2
    refuse(values.get("--suite") == "int" and values.get("--no-coverage") is True,
           "proof requires scoped int without coverage")
    refuse(values.get("--jobs", "1") == "1", "proof requires one job")
    refuse(values.get("--pg-test-profile") == PROOF_PROFILE, "proof profile mismatch")
    refuse(values.get("--pg-container-runtime", "docker") == "docker"
           and values.get("--pg-container-port", "55433") == "55433", "proof runtime/port")
    refuse(values.get("--smoke-image-reference", PROOF_RUNTIME_CONFIG) == PROOF_RUNTIME_CONFIG,
           "proof runtime identity option")
    refuse(isinstance(values.get("--report-dir"), str) and bool(values["--report-dir"]),
           "proof report directory required")


def _validate_declaration(value: Any) -> None:
    refuse(isinstance(value, Mapping) and json_bytes(dict(value)) == json_bytes(proof_declaration()),
           "proof declaration")


def validate_proof_plan(plan: Mapping[str, Any], *, inner: bool | None = None) -> dict[str, Any]:
    """Validate the host or projected inner proof plan with closed fields."""
    refuse(isinstance(plan, Mapping), "proof plan object")
    record = dict(plan)
    inferred_inner = record.get("schema") == PROOF_INNER_SCHEMA
    if inner is None:
        inner = inferred_inner
    refuse(bool(inner) == inferred_inner, "proof plan schema")
    refuse(set(record) == (_INNER_FIELDS if inner else _PLAN_FIELDS), "proof plan fields")
    refuse(not (_FORBIDDEN_STUDY_FIELDS & set(record)), "proof study authority field")
    refuse(record["phase"] == PROOF_PHASE and record["role"] == PROOF_ROLE
           and record["profile"] == PROOF_PROFILE and record["selector"] in PROOF_SELECTORS,
           "proof identity")
    _hex(record["candidate_head"], _HEX40, "proof candidate head")
    snapshot = record["candidate_snapshot"]
    refuse(isinstance(snapshot, Mapping) and len(snapshot) > 100, "proof whole candidate")
    snapshot = cast(Mapping[str, Any], snapshot)
    validate_study_candidate_snapshot(record["candidate_head"], snapshot)
    source_sha = _snapshot_sha256(snapshot)
    refuse(record["candidate_snapshot_sha256"] == source_sha
           and record["source_freeze_sha256"] == source_sha, "proof source freeze")
    refuse(record["provider_sha256"] == PROVIDER_SHA256 and record["sdk_image"] == SDK_IMAGE,
           "proof provider/SDK identity")
    archive = record["archive"]
    expected_archive_fields = {"bytes", "sha256"} if inner else {"path", "bytes", "sha256"}
    refuse(isinstance(archive, Mapping) and set(archive) == expected_archive_fields,
           "proof archive fields")
    archive = cast(Mapping[str, Any], archive)
    if not inner:
        refuse(type(archive["path"]) is str and Path(str(archive["path"])).is_absolute(),
               "proof archive path")
    refuse(archive["bytes"] == PROOF_RUNTIME_ARCHIVE_BYTES
           and archive["sha256"] == PROOF_RUNTIME_ARCHIVE_SHA256, "proof archive identity")
    derived = record["derived"]
    refuse(isinstance(derived, Mapping), "proof runtime provenance")
    derived = cast(Mapping[str, Any], derived)
    validate_study_runtime_provenance(derived)
    runtime_document = derived.get("runtime_config_document")
    refuse(derived.get("runtime_config") == PROOF_RUNTIME_CONFIG
           and isinstance(runtime_document, Mapping)
           and derived["runtime_config"] == "sha256:" + digest(json_bytes(runtime_document)),
           "proof runtime identity")
    refuse(isinstance(derived.get("runtime_layers"), list), "proof runtime layers")
    _validate_declaration(record["declaration"])
    if inner:
        _hex(record["host_plan_sha256"], _HEX64, "proof host plan binding")
    else:
        allocation = record["allocation_path"]
        refuse(type(allocation) is str and Path(allocation).is_absolute(), "proof allocation path")
    return record


def project_inner_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Strip host paths before the verified proof plan enters the inner SDK."""
    record = validate_proof_plan(plan, inner=False)
    inner: dict[str, Any] = {key: record[key] for key in _COMMON_FIELDS}
    inner["schema"] = PROOF_INNER_SCHEMA
    inner["archive"] = {key: record["archive"][key] for key in ("bytes", "sha256")}
    inner["host_plan_sha256"] = digest(json_bytes(record))
    return validate_proof_plan(inner, inner=True)


def proof_runtime_identity(plan: Mapping[str, Any]) -> str:
    """Return the exact retained PG runtime identity after plan validation."""
    record = validate_proof_plan(plan)
    return str(record["derived"]["runtime_config"])


__all__ = [
    "PROOF_CAPS", "PROOF_COMMAND_SECONDS", "PROOF_DECLARATION_SCHEMA", "PROOF_INNER_SCHEMA",
    "PROOF_PHASE", "PROOF_PROFILE", "PROOF_ROLE", "PROOF_RUNTIME_ARCHIVE_BYTES",
    "PROOF_RUNTIME_ARCHIVE_SHA256", "PROOF_RUNTIME_CONFIG", "PROOF_SCHEMA", "PROOF_SDK_SECONDS",
    "PROOF_SELECTORS", "PROOF_SELECTOR", "PROOF_SELECTOR_REGISTRY", "forwarded_selector_matches",
    "proof_declaration", "proof_runtime_identity", "project_inner_plan", "validate_proof_cohort",
    "validate_proof_plan",
]
