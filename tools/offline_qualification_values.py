"""Pure values and validation helpers for the offline qualification path."""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any


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

# Separately closed supplement-study cohort (ADR 0203): fresh per-attempt
# allocation file only; old core/native ledgers keep CAPS exactly.
STUDY_CAPS = {
    "read_bytes": 12 * 1024**3, "transfer_bytes": 6 * 1024**3,
    "materializations": 0, "sdk_study": 1, "native_lifecycles": 1,
    "base_loads": 0, "runtime_exports": 0, "native_runtime_loads": 1,
    "study_workers": 2,
}
# Synthetic proof delivery is a different role from the actual study.  It has
# one bounded SDK invocation and one retained-runtime import, and cannot mint
# a study reservation, model worker, task, materialization, build, pull, or
# export.  Keep this dictionary separate so old ledgers remain byte-for-byte
# governed by CAPS/RECOVERY2_CAPS/INITIAL_CAPS.
# Body-study cohort (ADR0206): distinct ledger; STUDY_CAPS unchanged.
BODY_CAPS = {
    "read_bytes": 12 * 1024**3, "transfer_bytes": 6 * 1024**3,
    "materializations": 0, "sdk_body_study": 1, "native_lifecycles": 1,
    "base_loads": 0, "runtime_exports": 0, "native_runtime_loads": 1,
    "body_study_workers": 2,
    "builds": 0, "pulls": 0,
}
LEXICAL_REPLAY_CAPS = {
    "read_bytes": 12 * 1024**3,
    "transfer_bytes": 6 * 1024**3,
    "sdk_lexical_replay": 1,
    "core_imports": 1,
    "postgres_lifecycles": 1,
}
PROOF_CAPS = {
    "read_bytes": 6 * 1024**3,
    "transfer_bytes": 2 * 1024**3,
    "materializations": 0,
    "sdk_proof": 1,
    "proof_runtime_imports": 1,
    "proof_lifecycles": 1,
    "runtime_exports": 0,
    "builds": 0,
    "pulls": 0,
}


def refuse(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError("pgvector prebuilt refused: " + code)


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
