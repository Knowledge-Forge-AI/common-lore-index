"""Pure first-to-current W5 body input-pin revision checks.

This validator compares two already-read pin records.  It does not read a
catalog, corpus, predecessor tree, or source file and it is not an admission
or provenance proof.  A later loader owns those byte-level and publication
checks.
"""

from __future__ import annotations

from typing import Any

from retrieval_body_input_records import parse_body_input_pin
from retrieval_body_values import INPUT_NAMES, digest_bytes, digest_value, require


_IMMUTABLE_MEMBERS = frozenset({
    "bindings-v1.json",
    "namespace-v1.json",
    "policy-v1.json",
    "preauthoring-gate-v1.json",
})
_REVISION_MEMBERS = frozenset({
    "catalog-v1.json",
    "corpus-v1.json",
    "static-inventory-v1.json",
    "rationale.md",
    "predecessor-manifest.json",
})
_IDENTITY_FIELDS = ("class_id", "corpus_id", "phase", "purpose")
_FALSE_FLAGS = ("source_closure_claimed", "execution_authority_granted")
_TASK_COUNT = 24


def _parse_expected(value: object, label: str) -> str:
    return digest_value(value, label)


def _member_differences(
    first: dict[str, Any], current: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    first_members = first["input_files"]
    current_members = current["input_files"]
    require(set(first_members) == set(INPUT_NAMES), "body.revision:first_members")
    require(set(current_members) == set(INPUT_NAMES), "body.revision:current_members")
    require(_IMMUTABLE_MEMBERS | _REVISION_MEMBERS == set(INPUT_NAMES), "body.revision:member_contract")
    for name in sorted(_IMMUTABLE_MEMBERS):
        require(first_members[name] == current_members[name], "body.revision:immutable_member")
    changed = tuple(sorted(name for name in _REVISION_MEMBERS
                           if first_members[name] != current_members[name]))
    unchanged = tuple(sorted(set(INPUT_NAMES) - set(changed)))
    require(changed, "body.revision:no_new_source_member")
    return changed, unchanged


def verify_body_input_revision(
    first_pin_raw: bytes,
    current_pin_raw: bytes,
    *,
    expected_first_sha256: str,
    expected_current_sha256: str,
) -> dict[str, Any]:
    """Verify a bounded data-only revision from the first frozen pin.

    Equal task digests and immutable input-member records establish only that
    the two pin records claim the same task population and frozen controls.
    The caller must still verify each pin's referenced bytes and predecessor
    closure before treating the result as an accepted revision.
    """
    require(type(first_pin_raw) is bytes, "body.revision:first_pin_bytes")
    require(type(current_pin_raw) is bytes, "body.revision:current_pin_bytes")
    first_expected = _parse_expected(expected_first_sha256, "body.revision:first_expected")
    current_expected = _parse_expected(expected_current_sha256, "body.revision:current_expected")
    first_sha256 = digest_bytes(first_pin_raw)
    current_sha256 = digest_bytes(current_pin_raw)
    require(first_sha256 == first_expected, "body.revision:first_pin_digest")
    require(current_sha256 == current_expected, "body.revision:current_pin_digest")
    require(first_sha256 != current_sha256, "body.revision:pin_unchanged")

    first = parse_body_input_pin(first_pin_raw)
    current = parse_body_input_pin(current_pin_raw)
    for field in _IDENTITY_FIELDS:
        require(first[field] == current[field], "body.revision:identity_drift")
    for field in _FALSE_FLAGS:
        require(first[field] is False and current[field] is False, "body.revision:authority_flag")

    first_tasks = first["task_digests"]
    current_tasks = current["task_digests"]
    require(type(first_tasks) is dict and len(first_tasks) == _TASK_COUNT, "body.revision:first_task_count")
    require(type(current_tasks) is dict and len(current_tasks) == _TASK_COUNT, "body.revision:current_task_count")
    require(first_tasks == current_tasks, "body.revision:task_digests")
    changed, unchanged = _member_differences(first, current)
    return {
        "first_pin_sha256": first_sha256,
        "current_pin_sha256": current_sha256,
        "changed_members": changed,
        "unchanged_members": unchanged,
        "data_only": True,
        "tasks_unchanged": True,
        "new_source_pin": True,
    }


__all__ = ["verify_body_input_revision"]
