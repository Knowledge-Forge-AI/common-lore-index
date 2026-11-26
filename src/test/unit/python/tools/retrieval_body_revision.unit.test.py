"""Functional pin-to-pin revision checks without registered study values."""

from __future__ import annotations

from copy import deepcopy

import pytest

from common_lore_index_test_support.retrieval_body_pin_fixtures import make_body_input_files
from retrieval_body_input_records import parse_body_input_pin
from retrieval_body_revision import verify_body_input_revision
from retrieval_body_values import BodyError, canonical_bytes, digest_bytes, strict_json


def _pins() -> tuple[bytes, bytes]:
    files = make_body_input_files()
    first = files["input-pin-v1.json"]
    current = deepcopy(strict_json(first))
    current["input_files"]["catalog-v1.json"]["sha256"] = "sha256:" + "1" * 64
    current_raw = canonical_bytes(current)
    return first, current_raw


def _verify(first: bytes, current: bytes) -> dict[str, object]:
    return verify_body_input_revision(
        first,
        current,
        expected_first_sha256=digest_bytes(first),
        expected_current_sha256=digest_bytes(current),
    )


def test_functional_revision_is_data_only_and_keeps_tasks(tmp_path) -> None:
    del tmp_path
    first, current = _pins()
    result = _verify(first, current)
    assert result["data_only"] is True
    assert result["tasks_unchanged"] is True
    assert result["new_source_pin"] is True
    assert result["changed_members"] == ("catalog-v1.json",)
    unchanged_members = result["unchanged_members"]
    assert isinstance(unchanged_members, tuple)
    assert "bindings-v1.json" in unchanged_members


def test_same_pin_and_expected_digest_mismatch_refuse() -> None:
    first, _current = _pins()
    with pytest.raises(BodyError, match="pin_unchanged"):
        _verify(first, first)
    changed = canonical_bytes({**strict_json(first), "purpose": "not-calibration"})
    with pytest.raises(BodyError, match="current_pin_digest"):
        verify_body_input_revision(
            first,
            changed,
            expected_first_sha256=digest_bytes(first),
            expected_current_sha256=digest_bytes(first),
        )


@pytest.mark.parametrize("field", ["class_id", "corpus_id", "phase"])
def test_identity_drift_refuses(field: str) -> None:
    first, current = _pins()
    changed = deepcopy(strict_json(current))
    changed[field] = "unit-body-other"
    raw = canonical_bytes(changed)
    with pytest.raises(BodyError, match="identity_drift"):
        _verify(first, raw)


def test_purpose_drift_refuses() -> None:
    first, current = _pins()
    changed = deepcopy(strict_json(current))
    changed["purpose"] = "not-calibration"
    with pytest.raises(BodyError, match="schema_or_purpose"):
        _verify(first, canonical_bytes(changed))


@pytest.mark.parametrize("field", ["bindings-v1.json", "policy-v1.json", "preauthoring-gate-v1.json"])
def test_immutable_member_drift_refuses(field: str) -> None:
    first, current = _pins()
    changed = deepcopy(strict_json(current))
    changed["input_files"][field]["sha256"] = "sha256:" + "2" * 64
    with pytest.raises(BodyError, match="immutable_member"):
        _verify(first, canonical_bytes(changed))


def test_task_digest_drift_and_membership_drift_refuse() -> None:
    first, current = _pins()
    changed = deepcopy(strict_json(current))
    task_id = sorted(changed["task_digests"])[0]
    changed["task_digests"][task_id]["task_sha256"] = "sha256:" + "3" * 64
    with pytest.raises(BodyError, match="task_digests"):
        _verify(first, canonical_bytes(changed))
    changed = deepcopy(strict_json(current))
    task_id = sorted(changed["task_digests"])[0]
    changed["task_digests"]["unit-body-25"] = changed["task_digests"].pop(task_id)
    with pytest.raises(BodyError, match="task_digests"):
        _verify(first, canonical_bytes(changed))


@pytest.mark.parametrize("field", ["source_closure_claimed", "execution_authority_granted"])
def test_false_authority_flags_are_required(field: str) -> None:
    first, current = _pins()
    changed = deepcopy(strict_json(current))
    changed[field] = True
    with pytest.raises(BodyError, match="source_closure|execution_authority"):
        _verify(first, canonical_bytes(changed))


def test_unknown_pin_input_member_is_rejected() -> None:
    first, current = _pins()
    changed = deepcopy(strict_json(current))
    changed["input_files"]["unknown-v1.json"] = {"bytes": 1, "sha256": "sha256:" + "4" * 64}
    with pytest.raises(BodyError):
        parse_body_input_pin(canonical_bytes(changed))
