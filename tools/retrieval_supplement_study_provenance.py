"""Exact source/runtime/forecast provenance shared by new study and proof roles."""
from __future__ import annotations
from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any
from retrieval_supplement_study_contracts import (MAX_MODEL_WORKERS, MAX_SOURCE_CLOSURE_BYTES, RUNTIME_CONFIG_SHA256, RUNTIME_ARCHIVE_BYTES, RUNTIME_ARCHIVE_SHA256, refuse)
from retrieval_supplement_study_schedule import build_byte_forecast
from retrieval_supplement_primitives import canonical_bytes, digest_bytes
from offline_qualification_values import BASE_CONFIG
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
DERIVED_REQUIRED_FIELDS = frozenset({"base_config", "runtime_config", "runtime_config_document", "byte_forecast_binding"})
DERIVED_OPTIONAL_FIELDS = frozenset({
    "base_config_document", "base_config_raw", "base_layers", "bytes", "entrypoint",
    "native_abi_or_loading_proven", "runtime_layers", "schema", "selected_file_bytes",
    "selected_file_count", "sha256",
})
def _validate_candidate_snapshot(record: dict[str, Any]) -> None:
    head = record["candidate_head"]
    refuse(type(head) is str and re.fullmatch(r"[0-9a-f]{40}", head) is not None,
           "study_plan:candidate_head")
    snapshot = record["candidate_snapshot"]
    refuse(isinstance(snapshot, dict) and len(snapshot) > 100,
           "study_plan:whole_candidate_required")
    for name, row in snapshot.items():
        refuse(type(name) is str and bool(name) and "\\" not in name and "\x00" not in name
               and not Path(name).is_absolute()
               and ".." not in Path(name).parts
               and Path(name).as_posix() == name,
               "study_plan:candidate_member")
        refuse(isinstance(row, Mapping), "study_plan:candidate_member_record")
        if set(row) == {"sha256", "mode"}:
            refuse(type(row["sha256"]) is str and _HEX64.fullmatch(row["sha256"]) is not None,
                   "study_plan:candidate_member_hash")
            refuse(type(row["mode"]) is int and not isinstance(row["mode"], bool)
                   and 0 <= row["mode"] <= 0o777, "study_plan:candidate_member_mode")
        elif set(row) == {"symlink"}:
            target = row["symlink"]
            refuse(type(target) is str and bool(target) and not Path(target).is_absolute()
                   and "\x00" not in target
                   and "\\" not in target and Path(target).as_posix() == target,
                   "study_plan:candidate_member_link")
            depth = len(Path(name).parent.parts)
            for component in Path(target).parts:
                depth += -1 if component == ".." else 1
                refuse(depth >= 0, "study_plan:candidate_link_escape")
        else:
            refuse(False, "study_plan:candidate_member_fields")
def validate_study_candidate_snapshot(
    candidate_head: object, candidate_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the closed whole-candidate member inventory for study/proof callers."""
    _validate_candidate_snapshot({"candidate_head": candidate_head, "candidate_snapshot": candidate_snapshot})
    return dict(candidate_snapshot)
def _reject_private_absolute_paths(value: object, *, label: str = "derived") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text.endswith("_path") or key_text in {"path", "source", "root"}:
                refuse(not (type(item) is str and Path(item).is_absolute()),
                       f"study_plan:{label}_private_path")
            _reject_private_absolute_paths(item, label=label)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_private_absolute_paths(item, label=label)
def validate_study_runtime_provenance(derived: Mapping[str, Any]) -> dict[str, Any]:
    """Validate fixed runtime identity, retained layers, and nested path custody."""
    refuse(isinstance(derived, Mapping), "study_plan:derived_object")
    keys = set(derived)
    refuse({"runtime_config", "runtime_config_document"} <= keys <= DERIVED_REQUIRED_FIELDS | DERIVED_OPTIONAL_FIELDS,
           "study_plan:derived_fields")
    runtime_config = derived["runtime_config"]
    refuse(type(runtime_config) is str and runtime_config == RUNTIME_CONFIG_SHA256,
           "study_plan:derived_runtime")
    runtime_document = derived["runtime_config_document"]
    refuse(isinstance(runtime_document, Mapping), "study_plan:derived_runtime_document")
    refuse(runtime_config == digest_bytes(canonical_bytes(dict(runtime_document)) + b"\n"),
           "study_plan:derived_runtime_identity")
    if "base_config" in derived:
        refuse(derived["base_config"] == BASE_CONFIG, "study_plan:derived_base_config")
    if "schema" in derived:
        refuse(derived["schema"] == "cloe-pgvector-derived-archive-v1",
               "study_plan:derived_schema")
    if "bytes" in derived or "sha256" in derived:
        refuse(type(derived.get("bytes")) is int and derived["bytes"] == RUNTIME_ARCHIVE_BYTES
               and derived.get("sha256") == RUNTIME_ARCHIVE_SHA256.removeprefix("sha256:"),
               "study_plan:derived_archive_identity")
    if "runtime_layers" in derived:
        refuse(isinstance(derived["runtime_layers"], list), "study_plan:derived_runtime_layers")
        for layer in derived["runtime_layers"]:
            refuse(isinstance(layer, Mapping) and set(layer) == {"name", "bytes", "sha256"}
                   and type(layer["name"]) is str and type(layer["bytes"]) is int
                   and layer["bytes"] > 0 and type(layer["sha256"]) is str
                   and _HEX64.fullmatch(layer["sha256"]) is not None,
                   "study_plan:derived_runtime_layer")
        refuse(["sha256:" + row["sha256"] for row in derived["runtime_layers"]]
               == runtime_document.get("rootfs", {}).get("diff_ids"), "study_plan:runtime_layer_join")
    if "byte_forecast_binding" in derived:
        binding = derived["byte_forecast_binding"]
        refuse(isinstance(binding, Mapping)
               and set(binding) == {"source_bytes", "request_bytes", "candidate_bytes"},
               "study_plan:byte_forecast_binding")
        refuse(type(binding["source_bytes"]) is int and not isinstance(binding["source_bytes"], bool)
               and 0 < binding["source_bytes"] <= MAX_SOURCE_CLOSURE_BYTES
               and type(binding["candidate_bytes"]) is int and not isinstance(binding["candidate_bytes"], bool)
               and 0 < binding["candidate_bytes"] <= 128 * 1024**2
               and isinstance(binding["request_bytes"], (list, tuple))
               and len(binding["request_bytes"]) == MAX_MODEL_WORKERS
               and all(type(item) is int and not isinstance(item, bool)
                       and 0 < item <= 8 * 1024**2 for item in binding["request_bytes"])
               and list(binding["request_bytes"]) == [8 * 1024**2] * MAX_MODEL_WORKERS,
               "study_plan:byte_forecast_binding_values")
    _reject_private_absolute_paths(derived)
    return dict(derived)
def _validate_derived(record: dict[str, Any]) -> None:
    derived = validate_study_runtime_provenance(record["derived"])
    refuse(DERIVED_REQUIRED_FIELDS <= set(derived), "study_plan:derived_fields")
    refuse(derived["base_config"] == BASE_CONFIG,
           "study_plan:derived_base_config")
    if "byte_forecast_binding" in derived:
        binding = derived["byte_forecast_binding"]
        forecast = build_byte_forecast(
            source_bytes=binding["source_bytes"], request_bytes=tuple(binding["request_bytes"]),
            candidate_bytes=binding["candidate_bytes"],
        )
        refuse(forecast["byte_forecast_sha256"] == record["byte_forecast_sha256"],
               "study_plan:byte_forecast_hash")
