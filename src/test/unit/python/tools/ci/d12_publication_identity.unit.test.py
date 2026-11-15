"""Initial publication and subsequent synchronization have distinct event evidence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ci.d12_publication_identity import publication_transition
from ci.d12_system_grant import (
    DEFAULT_GRANT_RELPATH, D12SystemGrantError, SPENT_CI146_NONCE,
    SPENT_CI146_RESERVATION_SHA256, validate_system_build_grant,
)

BASE, HEAD, BEFORE = "1" * 40, "2" * 40, "3" * 40


def _grant(action: str = "opened") -> dict[str, str]:
    return {"base": BASE, "before": BASE if action == "opened" else BEFORE, "event_action": action}


def test_opened_uses_declared_baseline_and_observed_head() -> None:
    assert publication_transition({"action": "opened"}, _grant(), base_sha=BASE, head_sha=HEAD) == (BASE, HEAD)


@pytest.mark.parametrize("field", ["before", "after"])
@pytest.mark.parametrize("value", [None, "", BASE, HEAD, False])
def test_opened_refuses_even_null_transition_fields(field: str, value: object) -> None:
    with pytest.raises(D12SystemGrantError, match="must be absent"):
        publication_transition({"action": "opened", field: value}, _grant(), base_sha=BASE, head_sha=HEAD)


@pytest.mark.parametrize("base,head,grant,action", [
    (None, HEAD, _grant(), "opened"),
    (BASE, "A" * 40, _grant(), "opened"),
    (HEAD, BEFORE, _grant(), "opened"),
    (BASE, HEAD, {**_grant(), "before": BEFORE}, "opened"),
    (BASE, BASE, _grant(), "opened"),
    (BASE, HEAD, _grant(), "synchronize"),
    (BASE, HEAD, _grant("reopened"), "reopened"),
])
def test_initial_identity_refusals(base: object, head: object, grant: dict[str, str], action: str) -> None:
    with pytest.raises(D12SystemGrantError):
        publication_transition({"action": action}, grant, base_sha=base, head_sha=head)


def test_synchronize_keeps_observed_transition_contract() -> None:
    event = {"action": "synchronize", "before": BEFORE, "after": HEAD}
    assert publication_transition(event, _grant("synchronize"), base_sha=BASE, head_sha=HEAD) == (BEFORE, HEAD)
    for bad in ({"before": None}, {"after": None}, {"before": BASE}, {"after": BASE}):
        with pytest.raises(D12SystemGrantError):
            publication_transition(event | bad, _grant("synchronize"), base_sha=BASE, head_sha=HEAD)


def test_current_grant_refuses_previous_schema_and_ci146_reuse() -> None:
    root = Path(__file__).resolve().parents[6]
    grant = json.loads((root / DEFAULT_GRANT_RELPATH).read_bytes())
    assert validate_system_build_grant(grant) == grant
    for changes in (
        {"schema": "repomap-d12-system-build-grant-v1"}, {"event_action": "synchronize"},
        {"nonce": SPENT_CI146_NONCE}, {"reservation_sha256": SPENT_CI146_RESERVATION_SHA256},
        {"maximum_total": 54}, {"maximum_total": 56}, {"pull_request": True},
    ):
        with pytest.raises(D12SystemGrantError):
            validate_system_build_grant(grant | changes)
