"""Exact public provider facts; no acquisition, image or database operations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any

from runner_pgvector_budget import Allocation, MAX_SELECTED, refuse

PROVIDER_SHA256 = "b366c8507b36746ea1512c4a9a3d6819214b65203645b7c9555bd450d5f797ec"
PACKAGE_SHA256 = "fa7c26fe2f076538915fa7efac6cd944777d891ccb251324bea01fda35626919"
BASE_CONFIG = "sha256:2e7ed4ea03deedbb45c26c68bf2a7090dc68dd8518a9c75928c4b9b0f59bc411"
BASE_MANIFEST = "sha256:15364b278fab4a982ae09243c788e4df96a31a5b4bed50e6dab2d83420690653"
SDK_IMAGE = "sha256:575e0004d8be9a5a8fa73e3301d1e7033280208b9020c1a3155b5d4866598c42"
ENV_OFFLINE_PLAN = "CLOE_PGVECTOR_OFFLINE_PLAN"
INNER_PLAN = "/sandbox-scratch/pgvector/plan.json"
RUNTIME_TAG = "cloe-pgvector-qualification:arm64-v2"
BASE_TAG = "cloe-pgvector-base:captured-arm64-v1"
PROVIDER_LABEL = "org.repomap.test.pgvector.package_sha256"
RECIPE_LABEL = "org.repomap.test.pgvector.recipe"
OWNER_LABEL = "org.repomap.test.pgvector.owner"
OWNER_VALUE = "CLOE-W5-PGDG1"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def strict_json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            refuse(key not in result, "duplicate JSON member")
            result[key] = value
        return result
    def constant(_value: str) -> None:
        raise RuntimeError("pgvector prebuilt refused: nonfinite JSON")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


def strict_object(raw: bytes) -> dict[str, Any]:
    value = strict_json(raw)
    refuse(isinstance(value, dict), "JSON object required")
    return value


def safe_path(name: str) -> str:
    refuse(isinstance(name, str) and 0 < len(name) < 256 and "\\" not in name
           and "\x00" not in name and not name.startswith("/")
           and ".." not in name.split("/"), "archive path")
    normalized = str(PurePosixPath(name))
    refuse(normalized == name and normalized != ".", "noncanonical archive path")
    return name


def _read_regular(path: Path, maximum: int, meter: Allocation | None = None) -> bytes:
    refuse(not any(p.is_symlink() for p in (path, *path.parents)), "input link")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        refuse(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= maximum,
               "input size/type")
        result = bytearray()
        while len(result) < before.st_size:
            count = min(65536, before.st_size - len(result))
            if meter is not None:
                count = min(count, meter.remaining("read_bytes"))
                refuse(count > 0, "read allocation exhausted")
                block = meter.read(source.read, count)
            else:
                block = source.read(count)
            refuse(bool(block), "short regular input")
            result.extend(block)
        after = os.fstat(source.fileno())
    refuse(len(result) == before.st_size and len(result) <= maximum
           and (before.st_size, before.st_mtime_ns, before.st_ino) ==
               (after.st_size, after.st_mtime_ns, after.st_ino), "input changed")
    return bytes(result)


def read_regular(path: Path, maximum: int, meter: Allocation | None = None) -> bytes:
    try:
        return _read_regular(path, maximum, meter)
    except OSError:
        raise RuntimeError("pgvector prebuilt refused: regular input unavailable") from None


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
