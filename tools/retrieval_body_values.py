"""Closed values for the opt-in, inert public captured-text calibration class."""

from __future__ import annotations

from dataclasses import dataclass
import re

from retrieval_supplement_primitives import (
    ARMS as ARMS,
    SupplementError as BodyError,
    canonical_bytes as canonical_bytes,
    check_keys as check_keys,
    digest_bytes as digest_bytes,
    digest_json as digest_json,
    digest_value as digest_value,
    integer as integer,
    require as require,
    safe_id as safe_id,
    safe_relative_path as safe_relative_path,
    strict_json as strict_json,
    string_list as string_list,
    text as text,
)

PHASE = "CLOE-W5-BODY1"
CATALOG_SCHEMA = "cloe-body-calibration-catalog-v1"
PIN_SCHEMA = "cloe-body-calibration-input-pin-v1"
FIRST_INPUT_PIN_SHA256 = "sha256:c87ccd5ea3d02d2afb96dbb76ef15ad325a91ada91b6a27780f8f2c3c3cae6f4"
REGISTERED_INPUT_PIN_SHA256 = "sha256:0b62374c086e96309d66c32999a8928981942eea38cca2f976880f851c6e378b"
MAX_CASES = 24
BODY_FLOOR = 8
FAMILY_FLOOR = 2
PAIR_CAP = 80
RECORD_CAP = 500
MAX_TOKENS = 512
BODY_FAMILIES = (
    "behavior_without_exact_symbol",
    "code_plus_design_or_message_context",
    "relevant_tests_for_behavior",
)
ANCHOR_FAMILY = "exact_symbol_and_partial_name"
CONTROL_FAMILY = "absent_ambiguous_changed_deleted_stale"
CASE_KINDS = frozenset({"body_positive", "metadata_anchor", "absent", "ambiguous", "privacy", "unsupported"})
WORDINGS = frozenset({"literal_clue", "paraphrased_intent", "not_applicable"})
BODY_ARMS = (ARMS[0], ARMS[2], ARMS[4], ARMS[5])
MODES = frozenset({"ranked_discovery", "literal_captured_text", "current_literal_enumeration"})
INPUT_NAMES = (
    "bindings-v1.json", "catalog-v1.json", "corpus-v1.json", "namespace-v1.json",
    "policy-v1.json", "preauthoring-gate-v1.json", "predecessor-manifest.json",
    "rationale.md", "static-inventory-v1.json",
)


@dataclass(frozen=True)
class BodyIdentity:
    """Registered identity or a disjoint functional-test identity; no IO."""

    class_id: str
    corpus_id: str
    phase: str
    graph_id: str
    task_prefix: str

    def __post_init__(self) -> None:
        for name in ("class_id", "corpus_id", "phase", "graph_id"):
            safe_id(getattr(self, name), "body.identity." + name)
        require(self.task_prefix in {"body1-", "unit-body-"}, "body.identity:namespace")
        require(self.class_id.startswith(self.task_prefix), "body.identity:class_prefix")
        require(self.corpus_id.startswith(self.task_prefix), "body.identity:corpus_prefix")
        if self.task_prefix == "body1-":
            require((self.class_id, self.corpus_id, self.phase, self.graph_id) == (
                "body1-captured-text", "body1-service-corpus", PHASE, "body1-public-calibration",
            ), "body.identity:registered_mismatch")
        else:
            require(self.phase != PHASE, "body.identity:functional_phase")

    @property
    def reserved(self) -> bool:
        return self.task_prefix == "body1-"

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    def check_task_id(self, value: object) -> str:
        result = text(value, "body.task_id", 64)
        match = re.fullmatch(re.escape(self.task_prefix) + r"([0-9]{2})", result)
        require(match is not None, "body.task_id:namespace")
        assert match is not None
        integer(int(match.group(1)), "body.task_id.number", 1, MAX_CASES)
        return result


REGISTERED_IDENTITY = BodyIdentity(
    "body1-captured-text", "body1-service-corpus", PHASE, "body1-public-calibration", "body1-",
)

__all__ = [
    "ARMS", "BodyError", "BodyIdentity", "BODY_ARMS", "BODY_FAMILIES", "BODY_FLOOR",
    "FAMILY_FLOOR", "ANCHOR_FAMILY", "CONTROL_FAMILY", "CASE_KINDS", "WORDINGS",
    "PHASE", "CATALOG_SCHEMA", "PIN_SCHEMA", "FIRST_INPUT_PIN_SHA256", "REGISTERED_INPUT_PIN_SHA256",
    "REGISTERED_IDENTITY", "INPUT_NAMES", "MAX_CASES", "PAIR_CAP", "RECORD_CAP",
    "MAX_TOKENS", "MODES", "canonical_bytes", "check_keys", "digest_bytes",
    "digest_json", "digest_value", "integer", "require", "safe_id",
    "safe_relative_path", "strict_json", "string_list", "text",
]
