"""Source-bound freeze for the inert W5 captured-text preparation class."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from retrieval_body_pin import RegisteredBodyInputs, require_registered_body_inputs
from common_lore_index_test_support.retrieval_body_source import BodySourceSnapshot, body_source_snapshot
from retrieval_body_input_records import parse_body_input_pin
from retrieval_body_values import (
    PIN_SCHEMA, canonical_bytes, digest_bytes, digest_json, digest_value,
    require, safe_relative_path, strict_json,
)
from retrieval_supplement_files import publish_member, read_member

FREEZE_SCHEMA = "cloe-body-source-freeze-v1"
FREEZE_FIELDS = (
    "schema", "class_id", "corpus_id", "phase", "input_pin_utf8", "input_pin_sha256",
    "predecessor_manifest_sha256", "predecessor_file_count", "predecessor_commit",
    "predecessor_catalog_hashes", "source_closure", "source_closure_sha256",
    "source_closure_claimed", "execution_authority_granted",
)


def _snapshot_record(snapshot: BodySourceSnapshot) -> dict[str, Any]:
    return snapshot.to_dict()


def _record(inputs: RegisteredBodyInputs, source: BodySourceSnapshot) -> dict[str, Any]:
    pin_raw = inputs.pin_raw
    predecessor = inputs.predecessor
    require(digest_bytes(pin_raw) == inputs.pin_sha256, "body.freeze:pin_digest")
    require(strict_json(pin_raw).get("schema") == PIN_SCHEMA, "body.freeze:pin_schema")
    closure = _snapshot_record(source)
    return {
        "schema": FREEZE_SCHEMA,
        "class_id": inputs.catalog.identity.class_id,
        "corpus_id": inputs.catalog.identity.corpus_id,
        "phase": inputs.catalog.identity.phase,
        "input_pin_utf8": pin_raw.decode("utf-8"),
        "input_pin_sha256": inputs.pin_sha256,
        "predecessor_manifest_sha256": digest_bytes(
            read_member(inputs.root, "predecessor-manifest.json", max_bytes=512 * 1024)
        ),
        "predecessor_file_count": len(predecessor.files),
        "predecessor_commit": predecessor.commit,
        "predecessor_catalog_hashes": predecessor.catalog_hashes,
        "source_closure": closure,
        "source_closure_sha256": source.identity,
        "source_closure_claimed": True,
        "execution_authority_granted": False,
    }


def build_body_source_freeze(repo_root: Path, inputs: RegisteredBodyInputs) -> dict[str, Any]:
    """Revalidate registered inputs and bind them to a fresh source closure."""
    current = require_registered_body_inputs(inputs)
    source = body_source_snapshot(repo_root)
    return _record(current, source)


def parse_body_source_freeze(raw: bytes | Mapping[str, Any]) -> dict[str, Any]:
    value = strict_json(raw) if isinstance(raw, bytes) else dict(raw)
    require(isinstance(value, dict), "body.freeze:object")
    require(set(value) == set(FREEZE_FIELDS), "body.freeze:fields")
    require(value["schema"] == FREEZE_SCHEMA, "body.freeze:schema")
    require(type(value["input_pin_utf8"]) is str, "body.freeze:pin_text")
    pin_raw = value["input_pin_utf8"].encode("utf-8")
    require(digest_bytes(pin_raw) == digest_value(value["input_pin_sha256"], "body.freeze:pin_digest"),
            "body.freeze:pin_digest")
    pin = parse_body_input_pin(pin_raw)
    for field in ("class_id", "corpus_id", "phase", "predecessor_manifest_sha256"):
        require(value[field] == pin[field], "body.freeze:pin_identity")
    for field in ("class_id", "corpus_id", "phase", "predecessor_commit"):
        require(type(value[field]) is str and value[field], f"body.freeze:{field}")
    require(digest_value(value["predecessor_manifest_sha256"], "body.freeze:predecessor_digest"),
            "body.freeze:predecessor_digest")
    require(isinstance(value["predecessor_catalog_hashes"], dict), "body.freeze:predecessor_hashes")
    for path, digest in value["predecessor_catalog_hashes"].items():
        safe_relative_path(path)
        digest_value("sha256:" + digest if isinstance(digest, str) and len(digest) == 64 else digest,
                     "body.freeze:catalog_hash")
    require(value["source_closure_claimed"] is True, "body.freeze:closure_claim")
    require(value["execution_authority_granted"] is False, "body.freeze:execution_authority")
    closure = value["source_closure"]
    require(isinstance(closure, dict), "body.freeze:closure")
    closure_fields = {"schema", "builder_members", "explicit_members", "files", "member_count", "total_bytes", "identity"}
    require(set(closure) == closure_fields, "body.freeze:closure_fields")
    require(closure["schema"] == "cloe-body-source-closure-v1", "body.freeze:closure_schema")
    for label in ("builder_members", "explicit_members"):
        values = closure[label]
        require(type(values) is list, f"body.freeze:{label}")
        normalized = [safe_relative_path(item) for item in values]
        require(normalized == sorted(set(normalized)), f"body.freeze:{label}_order")
        require(all("fixtures" not in Path(item).parts for item in normalized),
                f"body.freeze:{label}_fixtures")
    require(not set(closure["builder_members"]) & set(closure["explicit_members"]),
            "body.freeze:closure_overlap")
    members = set(closure["builder_members"]) | set(closure["explicit_members"])
    files = closure["files"]
    require(isinstance(files, dict) and set(files) == members, "body.freeze:closure_membership")
    total = 0
    for name, entry in files.items():
        safe_relative_path(name)
        require(isinstance(entry, dict) and set(entry) == {"bytes", "sha256"},
                "body.freeze:closure_entry")
        require(type(entry["bytes"]) is int and 0 <= entry["bytes"] <= 4 * 1024 * 1024,
                "body.freeze:closure_size")
        digest_value(entry["sha256"], "body.freeze:closure_file_digest")
        total += entry["bytes"]
    require(1 <= len(members) <= 4096 and total <= 32 * 1024 * 1024, "body.freeze:closure_capacity")
    require(type(closure["member_count"]) is int and type(closure["total_bytes"]) is int
            and closure["member_count"] == len(members) and closure["total_bytes"] == total,
            "body.freeze:closure_counts")
    identity_record = {
        "schema": closure["schema"], "builder_members": closure["builder_members"],
        "explicit_members": closure["explicit_members"], "files": files,
    }
    require(closure["identity"] == digest_json(identity_record), "body.freeze:closure_identity")
    require(closure["identity"] == value["source_closure_sha256"], "body.freeze:closure_digest")
    require(type(value["predecessor_file_count"]) is int and 1 <= value["predecessor_file_count"] <= 4096,
            "body.freeze:predecessor_count")
    return value


def verify_body_source_freeze(
    repo_root: Path, freeze: bytes | Mapping[str, Any], inputs: RegisteredBodyInputs,
) -> None:
    """Recompute both input custody and source closure; any drift refuses."""
    expected = parse_body_source_freeze(freeze if isinstance(freeze, bytes) else canonical_bytes(dict(freeze)))
    current = require_registered_body_inputs(inputs)
    rebuilt = build_body_source_freeze(repo_root, current)
    require(canonical_bytes(expected) == canonical_bytes(rebuilt), "body.freeze:reproduction_drift")


def publish_body_source_freeze(
    inputs: RegisteredBodyInputs, root: Path, relative: str, freeze: Mapping[str, Any], *, repo_root: Path,
) -> str:
    """Publish a new freeze record exclusively outside the registered input tree."""
    current = require_registered_body_inputs(inputs)
    require(isinstance(root, Path) and root.is_absolute() and not root.is_symlink(),
            "body.freeze:output_root")
    input_root = current.root.resolve()
    output_root = root.resolve()
    require(output_root != input_root and not output_root.is_relative_to(input_root),
            "body.freeze:output_inside_input_tree")
    safe_relative_path(relative)
    record = parse_body_source_freeze(canonical_bytes(dict(freeze)))
    verify_body_source_freeze(repo_root, record, current)
    raw = canonical_bytes(record)
    publish_member(root, relative, raw)
    return digest_bytes(raw)


__all__ = [
    "FREEZE_FIELDS", "FREEZE_SCHEMA", "build_body_source_freeze",
    "parse_body_source_freeze", "publish_body_source_freeze", "verify_body_source_freeze",
]
