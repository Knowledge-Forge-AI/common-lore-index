"""Owned declaration and retained-payload layout for one offline diagnostic."""
from __future__ import annotations

import os
from pathlib import Path
import struct
from typing import Any

from runner_pgvector_budget import Allocation, CAPS, private_file, refuse
from runner_pgvector_context import verify_checkout
from runner_pgvector_prebuilt import (
    PACKAGE_SHA256, PROVIDER_SHA256, SDK_IMAGE, digest, json_bytes, provider_facts,
    read_regular, safe_path, strict_object,
)

DECLARATION_SCHEMA = "cloe-pgvector-diagnostic-declaration-v1"
AUTHORITY_KEYS = {
    "review1_receipt_sha256", "operator_decision_sha256", "original_allocation_sha256",
    "corrective_selection_sha256", "signature_disposition_sha256",
}


def write_once(path: Path, raw: bytes) -> None:
    """No retry/overwrite; make an owned record durable before returning."""
    refuse(not any(p.is_symlink() for p in (path, *path.parents)), "record path link")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def load_declaration(path: Path, root: Path) -> dict[str, Any]:
    private_file(path)
    document = strict_object(read_regular(path, 2 * 1024**2))
    payload, sources, snapshot = (document[k] for k in ("payload", "authority", "candidate_snapshot"))
    refuse(isinstance(payload, dict) and payload.get("schema") == DECLARATION_SCHEMA
           and payload.get("phase") == "CLOE-W5-PGDG1"
           and payload.get("provider_sha256") == PROVIDER_SHA256
           and payload.get("sdk_image") == SDK_IMAGE and payload.get("caps") == CAPS,
           "declaration identity/caps")
    refuse(all(type(payload["caps"][key]) is int for key in CAPS), "declaration cap types")
    refuse(isinstance(sources, dict) and set(sources) == AUTHORITY_KEYS,
           "declaration authority inventory")
    for key in sorted(AUTHORITY_KEYS):
        row = sources[key]
        private_file(Path(row["path"]))
        raw = read_regular(Path(row["path"]), 1024**2)
        refuse(type(row.get("bytes")) is int and len(raw) == row["bytes"]
               and digest(raw) == row.get("sha256") == payload.get(key),
               "authority byte correspondence")
        if key == "review1_receipt_sha256":
            reviewed = strict_object(raw)
            refuse(reviewed.get("stage") == "work_review"
                   and reviewed.get("body", "").startswith("SOURCE-ADOPTION-GATE: PASS"),
                   "accepted independent source gate required")
    refuse(isinstance(snapshot, dict) and len(snapshot) > 100
           and digest(json_bytes(snapshot)) == payload.get("candidate_snapshot_sha256"),
           "declaration whole source identity")
    verify_checkout(root, snapshot, payload["candidate_head"])
    return document


def plan_common(document: dict[str, Any]) -> dict[str, Any]:
    payload = document["payload"]
    return {"schema": "cloe-pgvector-offline-plan-v1", "phase": "CLOE-W5-PGDG1",
        "provider_sha256": PROVIDER_SHA256, "sdk_image": SDK_IMAGE,
        "candidate_head": payload["candidate_head"],
        "candidate_snapshot": document["candidate_snapshot"],
        "review1_receipt_sha256": payload["review1_receipt_sha256"],
        "operator_decision_sha256": payload["operator_decision_sha256"],
        "diagnostic_declaration": payload,
        "diagnostic_declaration_sha256": digest(json_bytes(payload))}


def copy_bundle(index_path: Path, package_path: Path, destination: Path,
                meter: Allocation) -> None:
    """Copy only hash-bound retained regular files; never parse or install a deb."""
    facts = provider_facts()
    index = strict_object(read_regular(index_path, 2 * 1024**2, meter))
    candidates = [row for row in index["packages"] if row.get("payload_sha256") == PACKAGE_SHA256]
    refuse(len(candidates) == 1 and candidates[0].get("version") == facts["version"],
           "retained package selection")
    selected = [row for part in candidates[0]["tar"] if part["part"] == "data"
                for row in part["selected"]]
    mapping = {safe_path(row["archive_path"]): row for row in selected}
    refuse(len(mapping) == len(selected), "retained selected-file duplicates")
    package = read_regular(package_path, facts["package_bytes"], meter)
    refuse(len(package) == facts["package_bytes"] and digest(package) == PACKAGE_SHA256,
           "retained package digest")
    destination.mkdir(mode=0o700)
    write_once(destination / "pgvector.deb", package)
    for row in facts["files"]:
        retained = mapping.get(row["path"], {})
        refuse(retained.get("sha256") == row["sha256"] and retained.get("bytes") == row["bytes"],
               "retained selection correspondence")
        relative = safe_path(retained["retained"])
        # The index's documented phase root owns the retained relative paths.
        source = index_path.parent.parent / relative
        raw = read_regular(source, row["bytes"], meter)
        refuse(len(raw) == row["bytes"] and digest(raw) == row["sha256"], "retained selected digest")
        target = destination / safe_path(row["path"])
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_once(target, raw)


def read_forecast(base: Path, meter: Allocation) -> dict[str, int]:
    facts = provider_facts()
    decoded = 0
    for number, row in enumerate(facts["base_layers"]):
        path = base / f"layer-{number:02d}.tar.gz"
        refuse(not any(p.is_symlink() for p in (path, *path.parents))
               and path.stat().st_size == row["size"], "base forecast custody")
        with path.open("rb") as source:
            source.seek(-4, os.SEEK_END)
            footer = meter.read(source.read, 4)
        refuse(len(footer) == 4, "base forecast footer")
        decoded += struct.unpack("<I", footer)[0]
    refuse(decoded == 502194176, "fixed provider decoded-size forecast")
    selected = sum(row["bytes"] for row in facts["files"])
    # Two config records, manifest and conservative tar padding/control metadata.
    archive_upper = decoded + selected + 512 * (len(facts["files"]) * 2 + 100) + 256 * 1024
    upper = sum(row["size"] for row in facts["base_layers"]) + decoded + 7 * archive_upper
    upper += 2 * (facts["package_bytes"] + selected) + 16 * 1024**2
    refuse(archive_upper <= 768 * 1024**2 and upper + meter.used["read_bytes"] <= CAPS["read_bytes"],
           "forecast exceeds read/archive envelope")
    return {"decoded_base_bytes": decoded, "archive_upper_bytes": archive_upper,
            "read_upper_bytes": upper, "read_headroom_bytes": CAPS["read_bytes"] - upper}
