"""Shared constants, identity and strict JSON/text helpers for the W5 calibration supplement.

This module is a pure leaf: it performs no filesystem, network, model or query work
and never imports test support. Error messages carry public-safe codes and field
labels only (no paths or raw payload text).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from typing import Any, NoReturn

ARMS: tuple[str, ...] = (
    "native_tools_baseline",
    "current_graph_baseline:postgresql",
    "postgres_native_fts:postgresql",
    "exact_vector:canonical-node-v1",
    "exact_vector:canonical-node-public-text-v2",
    "hybrid_rrf:canonical-node-public-text-v2",
)
NATIVE_ARM, GRAPH_ARM, FTS_ARM, V1_ARM, V2_ARM, FUSION_ARM = ARMS
POSITIVE_FAMILIES: tuple[str, ...] = (
    "exact_symbol_and_partial_name",
    "behavior_without_exact_symbol",
    "code_plus_design_or_message_context",
    "relevant_tests_for_behavior",
)
OTHER_FAMILY = "absent_ambiguous_changed_deleted_stale"
RATIONALE_FAMILIES: tuple[str, ...] = (*POSITIVE_FAMILIES, OTHER_FAMILY)

CATALOG_SCHEMA = "cloe-calibration-supplement-catalog-v1"
CORPUS_SCHEMA = "cloe-supplement-corpus-v1"
INPUT_PIN_SCHEMA = "cloe-calibration-supplement-input-pin-v1"
FREEZE_SCHEMA = "cloe-calibration-supplement-freeze-v1"
PURPOSE = "calibration"
PHASE = "CLOE-W5-SUPPLEMENT1"
REGISTERED_PREFIX = "sup1-"
REGISTERED_INPUT_PIN_SHA256 = (
    "sha256:c401ff63624c3ed3c0a3b28987cc3b99cacd82907f8a576d51fe56278db98b62"
)
REPETITIONS = 3
AUTHORED_TASK_CAP = 32
PREPARATION_TASK_CAP = 24
CANDIDATE_PAIR_CAP = 80
COMBINED_RECORD_CAP = 500
POSITIVE_FLOOR = 8
FAMILY_FLOOR = 2
MAX_TOKENS = 512
MAX_EXCERPT_BYTES = 4096
MAX_JSON_BYTES = 512 * 1024
MAX_JSON_DEPTH = 32
MAX_PATH_BYTES = 512

CASE_KINDS: tuple[str, ...] = ("positive", "absent", "ambiguous", "privacy", "unsupported")
NEGATIVE_KINDS: tuple[str, ...] = CASE_KINDS[1:]
REQUESTED_MODES = frozenset({"ranked_discovery", "literal_captured_text", "current_literal_enumeration"})
INTENDED_SURFACES = frozenset({"stored_metadata", "captured_text"})
STRATA = frozenset({"metadata", "body"})
GRADING_RULES = frozenset({"ranked_positive", "absent_answer", "safety_refusal"})
LEXICAL_STYLES = frozenset({"ranked_terms", "literal_captured_text"})
EXECUTIONS = frozenset({"completed", "refused", "unavailable"})
ACTUAL_SURFACES = frozenset({"stored_metadata", "captured_text", "none"})
FALLBACK_POLICIES = frozenset({"record_actual", "required", "none"})
PRIVACY_REASON = "binding_privacy_not_authorized"
GRAPH_BODY_REASON = "captured_text_surface_unsupported_by_metadata_graph"
NOT_COMPARABLE_REASON = "metadata_representation_not_comparable"
UNSUPPORTED_MODE_REASON = "unsupported_semantic_query_mode"
EXPECTATION_REASONS = frozenset(
    {PRIVACY_REASON, GRAPH_BODY_REASON, NOT_COMPARABLE_REASON, UNSUPPORTED_MODE_REASON}
)

_SAFE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PREFIX = re.compile(r"\A[a-z0-9][a-z0-9-]{0,30}-\Z")
_PHASE_ID = re.compile(r"\A[A-Z0-9][A-Z0-9-]{0,63}\Z")
_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


class SupplementError(ValueError):
    """Public-safe validation failure carrying a stable code."""


def fail(code: str) -> NoReturn:
    """Raise a :class:`SupplementError` with ``code``."""
    raise SupplementError(code)


def require(condition: object, code: str) -> None:
    """Raise ``SupplementError(code)`` unless ``condition`` is truthy."""
    if not condition:
        raise SupplementError(code)


def safe_id(value: object, label: str) -> str:
    """Return a bounded ASCII identifier or raise."""
    require(type(value) is str and _SAFE_ID.match(value) is not None, f"{label}:invalid_id")
    return str(value)


def is_digest(value: object) -> bool:
    """True when ``value`` is a ``sha256:<64 lowercase hex>`` scheme string."""
    return type(value) is str and _DIGEST.match(value) is not None


def digest_value(value: object, label: str) -> str:
    """Return a validated digest string or raise."""
    require(is_digest(value), f"{label}:invalid_sha256")
    return str(value)


@dataclass(frozen=True)
class SupplementIdentity:
    """Class identity: registered ``sup1-`` or a distinct functional/test namespace."""

    supplement_id: str
    corpus_id: str
    phase: str
    graph_id: str
    task_prefix: str

    def __post_init__(self) -> None:
        safe_id(self.supplement_id, "supplement_id")
        safe_id(self.corpus_id, "corpus_id")
        safe_id(self.graph_id, "graph_id")
        require(type(self.phase) is str and _PHASE_ID.match(self.phase), "phase:invalid_id")
        prefix = self.task_prefix
        require(type(prefix) is str and _PREFIX.match(prefix), "task_prefix:invalid")
        require(self.supplement_id.startswith(prefix), "supplement_id:prefix_mismatch")
        require(self.corpus_id.startswith(prefix), "corpus_id:prefix_mismatch")
        if prefix == REGISTERED_PREFIX:
            require(self.phase == PHASE, "phase:registered_mismatch")
        else:
            require(not prefix.startswith(REGISTERED_PREFIX), "task_prefix:reserved")

    @property
    def registered(self) -> bool:
        """Whether this identity names the reserved registered class."""
        return self.task_prefix == REGISTERED_PREFIX

    def to_dict(self) -> dict[str, str]:
        return {
            "supplement_id": self.supplement_id,
            "corpus_id": self.corpus_id,
            "phase": self.phase,
            "graph_id": self.graph_id,
            "task_prefix": self.task_prefix,
        }


def _json_depth(text: str) -> int:
    depth = peak = 0
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            peak = max(peak, depth)
        elif char in "]}":
            depth -= 1
    return peak


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in out, "json_duplicate_key")
        out[key] = value
    return out


def _reject_constant(_token: str) -> NoReturn:
    fail("json_nonfinite_number")


def _finite_float(token: str) -> float:
    value = float(token)
    require(math.isfinite(value), "json_nonfinite_number")
    return value


def strict_json(raw: bytes) -> Any:
    """Decode bounded strict UTF-8 JSON bytes (no duplicates, NaN/Infinity, or excess depth)."""
    require(type(raw) is bytes, "json_bytes_required")
    require(len(raw) <= MAX_JSON_BYTES, "json_too_large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        fail("json_invalid_utf8")
    require(not text.startswith("﻿"), "json_bom_forbidden")
    require(_json_depth(text) <= MAX_JSON_DEPTH, "json_too_deep")
    try:
        return json.loads(
            text,
            object_pairs_hook=_no_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except SupplementError:
        raise
    except (ValueError, RecursionError):
        fail("json_malformed")


def canonical_bytes(value: Any, ascii_only: bool = False) -> bytes:
    """Canonical JSON (sorted keys, compact separators); UTF-8 unless ``ascii_only``."""
    try:
        text = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=ascii_only, allow_nan=False
        )
        return text.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        fail("json_not_canonicalizable")


def digest_bytes(raw: bytes) -> str:
    """SHA-256 scheme string over exact bytes."""
    require(type(raw) is bytes, "digest_bytes_required")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def digest_json(value: Any, ascii_only: bool = False) -> str:
    """SHA-256 scheme string over the canonical JSON encoding of ``value``."""
    return digest_bytes(canonical_bytes(value, ascii_only=ascii_only))


def safe_relative_path(value: object) -> str:
    """Return a canonical POSIX relative path or raise for unsafe spellings."""
    require(type(value) is str and value, "path:invalid")
    path = str(value)
    require(len(path.encode("utf-8", "surrogatepass")) <= MAX_PATH_BYTES, "path:too_long")
    require(not any(ord(char) < 0x20 or char == "\x7f" for char in path), "path:control_char")
    require("\\" not in path and ":" not in path, "path:forbidden_char")
    require(not path.startswith("/"), "path:absolute")
    parts = path.split("/")
    require(all(part not in {"", ".", ".."} for part in parts), "path:traversal")
    require(all(len(part.encode("utf-8", "surrogatepass")) <= 255 for part in parts), "path:part_too_long")
    canonical = PurePosixPath(path).as_posix()
    require(canonical == path, "path:noncanonical")
    return canonical


def check_keys(mapping: object, expected: Iterable[str], label: str = "record") -> dict[str, Any]:
    """Require a JSON object whose key set exactly equals ``expected``."""
    require(isinstance(mapping, dict), f"{label}:object_required")
    assert isinstance(mapping, dict)
    wanted = set(expected)
    actual = set(mapping)
    require(not (actual - wanted), f"{label}:unknown_fields")
    require(not (wanted - actual), f"{label}:missing_fields")
    return mapping


def text(value: object, label: str, maximum: int = 4096) -> str:
    """Require a non-blank, NUL-free string of at most ``maximum`` UTF-8 bytes."""
    require(type(value) is str, f"{label}:string_required")
    string = str(value)
    require(string.strip() != "", f"{label}:empty")
    require("\x00" not in string, f"{label}:nul")
    try:
        size = len(string.encode("utf-8"))
    except UnicodeEncodeError:
        fail(f"{label}:invalid_unicode")
    require(size <= maximum, f"{label}:too_long")
    return string


def integer(value: object, label: str, minimum: int, maximum: int) -> int:
    """Require an exact ``int`` (never ``bool``) within an inclusive range."""
    require(type(value) is int, f"{label}:integer_required")
    assert isinstance(value, int)
    require(minimum <= value <= maximum, f"{label}:out_of_range")
    return value


def string_list(value: object, label: str, *, minimum: int, maximum: int, item_max: int) -> list[str]:
    """Require a JSON list of unique bounded non-blank strings."""
    require(type(value) is list, f"{label}:list_required")
    assert isinstance(value, list)
    require(minimum <= len(value) <= maximum, f"{label}:bad_length")
    items = [text(item, label, item_max) for item in value]
    require(len(set(items)) == len(items), f"{label}:duplicate")
    return items


def frozen_json(value: Any) -> bytes:
    """Defensive storage form for nested JSON values (fresh copies via :func:`thaw_json`)."""
    return canonical_bytes(value, ascii_only=False)


def thaw_json(raw: bytes) -> Any:
    """Return a fresh mutable copy of a value stored with :func:`frozen_json`."""
    return json.loads(raw.decode("utf-8"))


def mapping_copy(value: Mapping[str, str], label: str) -> dict[str, str]:
    """Copy a str->str mapping after exact type checks."""
    require(isinstance(value, Mapping), f"{label}:mapping_required")
    out: dict[str, str] = {}
    for key, item in value.items():
        require(type(key) is str and type(item) is str, f"{label}:string_entries_required")
        out[key] = item
    return out


__all__ = [
    "ARMS", "AUTHORED_TASK_CAP", "CANDIDATE_PAIR_CAP", "CASE_KINDS", "CATALOG_SCHEMA",
    "COMBINED_RECORD_CAP", "CORPUS_SCHEMA", "FREEZE_SCHEMA", "INPUT_PIN_SCHEMA", "OTHER_FAMILY",
    "PHASE", "POSITIVE_FAMILIES", "PREPARATION_TASK_CAP", "REGISTERED_INPUT_PIN_SHA256",
    "REGISTERED_PREFIX", "REPETITIONS", "SupplementError", "SupplementIdentity", "canonical_bytes",
    "check_keys", "digest_bytes", "digest_json", "integer", "require", "safe_relative_path",
    "strict_json", "text",
]
