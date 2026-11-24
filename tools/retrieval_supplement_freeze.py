"""Data-only input pin and source-bound final freeze for the calibration supplement.

The input pin binds the eight authored input files, the actual policy (digest
recomputed, never trusted from a field), the predecessor manifest bytes and both
task digest schemes. The final freeze embeds the exact pin bytes and adds the
reviewed source closure, whose membership is the caller's *fresh* maintained builder
result plus an explicit owner list. Predecessor files are hashed as bytes only.
A freeze never authorizes executing any supplement query.
"""


from __future__ import annotations

from retrieval_supplement_pin import (
    INPUT_FILE_NAMES, PIN_FILE_NAME, _parse_pin, build_input_pin, encode_record,
    policy_aliases, policy_from_bytes, verify_input_pin,
)


from collections.abc import Mapping, Sequence


from pathlib import Path, PurePosixPath


import re


from typing import Any








from retrieval_supplement_files import publish_member, read_member




from retrieval_supplement_values import (
    FREEZE_SCHEMA,
    REGISTERED_INPUT_PIN_SHA256,
    check_keys,
    digest_bytes,
    digest_value,
    fail,
    integer,
    require,
    safe_relative_path,
    strict_json,
)


FREEZE_FILE_NAME = "freeze-v1.json"


FREEZE_FIELDS: tuple[str, ...] = (
    "schema", "supplement_id", "corpus_id", "phase", "input_pin_utf8", "input_pin_sha256",
    "predecessor_manifest_sha256", "predecessor_file_count", "source_files", "builder_members",
    "explicit_members", "source_closure_claimed", "execution_authority_granted",
)


MAX_SOURCE_BYTES = 4 * 1024 * 1024


MAX_MEMBERS = 4096


# This checks direct imports only. The maintained FTS fallback helper has an
# existing transitive support import; its reviewed closure remains bound.
_DIRECT_TEST_SUPPORT_IMPORT = re.compile(r"^\s*(?:from|import)\s+repomap_test_support\b", re.MULTILINE)


_COMMIT = re.compile(r"\A[0-9a-f]{40}\Z")


def _verify_predecessor(repo_root: Path, manifest_raw: bytes) -> int:
    """Hash every predecessor file's committed bytes; values are never decoded."""
    manifest = strict_json(manifest_raw)
    require(isinstance(manifest, dict), "predecessor:object_required")
    require({"commit", "files"} <= set(manifest), "predecessor:missing_fields")
    require(type(manifest["commit"]) is str and _COMMIT.match(manifest["commit"]), "predecessor:commit")
    entries = manifest["files"]
    require(type(entries) is list and 1 <= len(entries) <= MAX_MEMBERS, "predecessor:files")
    seen: set[str] = set()
    for item in entries:
        entry = check_keys(item, ("path", "bytes", "sha256"), "predecessor.file")
        path = safe_relative_path(entry["path"])
        require(path not in seen, "predecessor:duplicate_path")
        seen.add(path)
        size = integer(entry["bytes"], "predecessor.bytes", 0, MAX_SOURCE_BYTES)
        expected = entry["sha256"]
        # The immutable predecessor manifest uses bare hex; newer functional
        # manifests may carry the scheme prefix. Normalize only this boundary.
        if type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected):
            expected = "sha256:" + expected
        data = read_member(repo_root, path, max_bytes=size, expected_sha256=digest_value(expected, "predecessor.sha256"))
        require(len(data) == size, "predecessor:size_mismatch")
    return len(seen)


def _members(values: Sequence[str], label: str) -> list[str]:
    require(not isinstance(values, (str, bytes)), f"{label}:sequence_required")
    items = [safe_relative_path(value) for value in values]
    require(len(set(items)) == len(items) and len(items) <= MAX_MEMBERS, f"{label}:duplicate")
    return sorted(items)


def _source_allowed(path: str, data: bytes) -> None:
    pure = PurePosixPath(path)
    require("fixtures" not in pure.parts, "freeze:fixture_path_in_sources")
    excluded = {*INPUT_FILE_NAMES, PIN_FILE_NAME, FREEZE_FILE_NAME}
    require(pure.name not in excluded, "freeze:input_or_output_in_sources")
    if path.startswith("tools/retrieval_supplement_") and pure.suffix == ".py":
        try:
            source = data.decode("utf-8")
        except UnicodeDecodeError:
            fail("freeze:source_not_utf8")
        require(_DIRECT_TEST_SUPPORT_IMPORT.search(source) is None, "freeze:tool_imports_test_support")


def build_final_freeze(
    repo_root: Path,
    input_pin_raw: bytes,
    *,
    expected_input_pin_sha256: str,
    source_files: Mapping[str, bytes],
    builder_members: Sequence[str],
    explicit_members: Sequence[str],
    predecessor_manifest_raw: bytes,
) -> dict[str, Any]:
    """Final freeze: exact pin bytes + reviewed source closure re-read from ``repo_root``."""
    expected = digest_value(expected_input_pin_sha256, "freeze.expected_input_pin")
    require(digest_bytes(input_pin_raw) == expected, "freeze:input_pin_digest_mismatch")
    pin = _parse_pin(input_pin_raw)
    if pin["supplement_id"].startswith("sup1-"):
        require(expected == REGISTERED_INPUT_PIN_SHA256, "freeze:registered_pin_mismatch")
        require(pin["predecessor_manifest_sha256"] == "sha256:a9879e1e27858ffb5efb58238084fd04b43c863e76e7fd3e4ff928e8164c72d7", "freeze:registered_predecessor_mismatch")
    require(
        type(predecessor_manifest_raw) is bytes
        and digest_bytes(predecessor_manifest_raw) == pin["predecessor_manifest_sha256"],
        "freeze:predecessor_manifest_mismatch",
    )
    predecessor_count = _verify_predecessor(repo_root, predecessor_manifest_raw)
    if pin["supplement_id"].startswith("sup1-"):
        require(predecessor_count == 54, "freeze:registered_predecessor_count")
    builder = _members(builder_members, "builder_members")
    explicit = _members(explicit_members, "explicit_members")
    require(isinstance(source_files, Mapping), "source_files:mapping_required")
    sources = dict(source_files)
    require(set(sources) == set(builder) | set(explicit), "freeze:source_membership_mismatch")
    closure: dict[str, dict[str, Any]] = {}
    for path in sorted(sources):
        provided = sources[path]
        require(type(provided) is bytes, "freeze:source_bytes_required")
        _source_allowed(path, provided)
        actual = read_member(repo_root, path, max_bytes=MAX_SOURCE_BYTES)
        require(actual == provided, "freeze:source_drift")
        closure[path] = {"bytes": len(actual), "sha256": digest_bytes(actual)}
    return {
        "schema": FREEZE_SCHEMA,
        "supplement_id": pin["supplement_id"],
        "corpus_id": pin["corpus_id"],
        "phase": pin["phase"],
        "input_pin_utf8": input_pin_raw.decode("utf-8"),
        "input_pin_sha256": digest_bytes(input_pin_raw),
        "predecessor_manifest_sha256": pin["predecessor_manifest_sha256"],
        "predecessor_file_count": predecessor_count,
        "source_files": closure,
        "builder_members": builder,
        "explicit_members": explicit,
        "source_closure_claimed": True,
        "execution_authority_granted": False,
    }


def verify_final_freeze(
    repo_root: Path,
    freeze: Mapping[str, Any] | bytes,
    *,
    expected_input_pin_sha256: str,
    input_pin_raw: bytes,
    fresh_source_files: Mapping[str, bytes],
    builder_members: Sequence[str],
    explicit_members: Sequence[str],
    predecessor_manifest_raw: bytes,
) -> None:
    """Re-derive the freeze from fresh inputs; any pin/source/membership drift fails."""
    loaded = strict_json(freeze) if isinstance(freeze, bytes) else freeze
    require(isinstance(loaded, Mapping), "freeze:object_required")
    record = check_keys(dict(loaded), FREEZE_FIELDS, "freeze")
    pin_text = record["input_pin_utf8"]
    require(type(pin_text) is str and pin_text.encode("utf-8") == input_pin_raw, "freeze:input_pin_drift")
    rebuilt = build_final_freeze(
        repo_root, input_pin_raw, expected_input_pin_sha256=expected_input_pin_sha256,
        source_files=fresh_source_files, builder_members=builder_members,
        explicit_members=explicit_members, predecessor_manifest_raw=predecessor_manifest_raw,
    )
    require(encode_record(record) == encode_record(rebuilt), "freeze:reproduction_mismatch")


def publish_record(root: Path, relative: str, record: Mapping[str, Any]) -> str:
    """Exclusively publish a canonical pin/freeze record; return its digest."""
    raw = encode_record(record)
    publish_member(root, relative, raw)
    return digest_bytes(raw)


__all__ = [
    "FREEZE_FILE_NAME", "INPUT_FILE_NAMES", "PIN_FILE_NAME", "build_final_freeze",
    "build_input_pin", "policy_aliases", "policy_from_bytes", "publish_record",
    "verify_final_freeze", "verify_input_pin",
]
