"""Exact public provider facts; no acquisition, image or database operations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from runner_pgvector_budget import Allocation, MAX_SELECTED, refuse
from offline_qualification_files import _read_regular as _read_regular, read_regular as read_regular
from offline_qualification_values import (
    BASE_CONFIG as BASE_CONFIG, BASE_MANIFEST as BASE_MANIFEST, BASE_TAG as BASE_TAG,
    ENV_OFFLINE_PLAN as ENV_OFFLINE_PLAN, INNER_PLAN as INNER_PLAN, OWNER_LABEL as OWNER_LABEL,
    OWNER_VALUE as OWNER_VALUE, PACKAGE_SHA256 as PACKAGE_SHA256, PROVIDER_LABEL as PROVIDER_LABEL,
    PROVIDER_SHA256 as PROVIDER_SHA256, RECIPE_LABEL as RECIPE_LABEL, RUNTIME_TAG as RUNTIME_TAG,
    SDK_IMAGE as SDK_IMAGE, digest as digest, json_bytes as json_bytes, safe_path as safe_path,
    strict_json as strict_json, strict_object as strict_object,
)


def provider_facts() -> dict[str, Any]:
    raw = read_regular(Path(__file__).with_name("pgvector_test_prebuilt.json"), 65536)
    refuse(digest(raw) == PROVIDER_SHA256, "provider manifest drift")
    value = strict_object(raw)
    refuse(value["package_sha256"] == PACKAGE_SHA256 and value["base_config"] == BASE_CONFIG
           and value["base_manifest"] == BASE_MANIFEST and value["sdk_image"] == SDK_IMAGE,
           "provider identity")
    refuse("sha256:" + digest("".join(value["base_config_utf8_chunks"]).encode()) == BASE_CONFIG,
           "provider raw base config")
    files = value["files"]
    refuse(len(files) == 45 and len({row["path"] for row in files}) == 45
           and sum(row["bytes"] for row in files) <= MAX_SELECTED, "provider file inventory")
    for row in files:
        safe_path(row["path"])
    return value


def recipe_bytes() -> bytes:
    return json_bytes({"schema": "cloe-pgvector-offline-recipe-v1",
        "provider_sha256": PROVIDER_SHA256, "base_config": BASE_CONFIG,
        "base_manifest": BASE_MANIFEST, "files": provider_facts()["files"],
        "uid": 0, "gid": 0, "mode": 0o644, "mtime": 0,
        "package_install": False, "compile": False, "jit_qualification": False})


def recipe_digest() -> str:
    return digest(recipe_bytes())


def selected_payloads(root: Path, meter: Allocation) -> dict[str, bytes]:
    facts = provider_facts()
    package = read_regular(root / "pgvector.deb", facts["package_bytes"], meter)
    refuse(len(package) == facts["package_bytes"] and digest(package) == PACKAGE_SHA256,
           "package identity")
    result: dict[str, bytes] = {}
    for row in facts["files"]:
        raw = read_regular(root / row["path"], row["bytes"], meter)
        refuse(len(raw) == row["bytes"] and digest(raw) == row["sha256"], "selected-file drift")
        result[row["path"]] = raw
    return result
