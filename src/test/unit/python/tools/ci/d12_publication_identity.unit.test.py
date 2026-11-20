"""Initial publication and subsequent synchronization have distinct event evidence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ci.d12_publication_identity import publication_transition
from ci.d12_system_grant import (
    DEFAULT_GRANT_RELPATH, D12SystemGrantError, SPENT_CI146_NONCE,
    SPENT_CI146_RESERVATION_SHA256, SPENT_CI148_NONCE, SPENT_CI148_RESERVATION_SHA256, SPENT_CI149_NONCE, SPENT_CI149_RESERVATION_SHA256, SPENT_CI150_NONCE, SPENT_CI150_RESERVATION_SHA256, SPENT_CI151_NONCE, SPENT_CI151_RESERVATION_SHA256, SPENT_CI152_NONCE, SPENT_CI152_RESERVATION_SHA256, SPENT_CI153_NONCE, SPENT_CI153_RESERVATION_SHA256, SPENT_CI155_NONCE, SPENT_CI155_RESERVATION_SHA256, SPENT_CI154_NONCE, SPENT_CI154_RESERVATION_SHA256,
    build_system_admission_proof, validate_system_admission_proof, validate_system_build_grant,
)

from ci.d12_system_grant_history import SPENT_CI157_NONCE, SPENT_CI157_RESERVATION_SHA256, SPENT_CI158_NONCE, SPENT_CI158_RESERVATION_SHA256, SPENT_CI159_NONCE, SPENT_CI159_RESERVATION_SHA256, SPENT_CI160_NONCE, SPENT_CI160_RESERVATION_SHA256

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
    maximum_total = grant["maximum_total"]
    for changes in (
        {"schema": "repomap-d12-system-build-grant-v1"}, {"event_action": "opened"},
        {"nonce": SPENT_CI146_NONCE}, {"reservation_sha256": SPENT_CI146_RESERVATION_SHA256},
        {"maximum_total": maximum_total - 1}, {"maximum_total": maximum_total + 1}, {"pull_request": True},
    ):
        with pytest.raises(D12SystemGrantError):
            validate_system_build_grant(grant | changes)


def test_synchronize_refuses_the_initial_publication_baseline() -> None:
    with pytest.raises(D12SystemGrantError, match="before/base must be distinct"):
        publication_transition({"action": "synchronize", "before": BASE, "after": HEAD},
                               {**_grant(), "event_action": "synchronize"}, base_sha=BASE, head_sha=HEAD)


def test_spent_current_and_prior_identities_are_refused_in_both_records() -> None:
    root = Path(__file__).resolve().parents[6]
    grant = json.loads((root / DEFAULT_GRANT_RELPATH).read_bytes())
    proof = build_system_admission_proof(
        grant=grant, grant_sha256="1" * 64, expected_candidate_sha256="2" * 64,
        raw_event_sha256="3" * 64, after=HEAD, candidate_sha="4" * 40, candidate_tree="5" * 40,
        candidate_base_parent=grant["base"], candidate_head_parent=HEAD, run_id="123",
        native_host={"runner_os": "Linux", "runner_arch": "X64", "host_kernel": "Linux", "host_arch": "x86_64"})
    assert validate_system_admission_proof(proof) == proof
    for key, value in (("nonce", SPENT_CI160_NONCE), ("reservation_sha256", SPENT_CI160_RESERVATION_SHA256),
                       ("nonce", SPENT_CI159_NONCE), ("reservation_sha256", SPENT_CI159_RESERVATION_SHA256),
                       ("nonce", SPENT_CI158_NONCE), ("reservation_sha256", SPENT_CI158_RESERVATION_SHA256),
                       ("nonce", SPENT_CI157_NONCE), ("reservation_sha256", SPENT_CI157_RESERVATION_SHA256),
                       ("nonce", SPENT_CI155_NONCE), ("reservation_sha256", SPENT_CI155_RESERVATION_SHA256),
                       ("nonce", SPENT_CI154_NONCE), ("reservation_sha256", SPENT_CI154_RESERVATION_SHA256),
                       ("nonce", SPENT_CI153_NONCE), ("reservation_sha256", SPENT_CI153_RESERVATION_SHA256),
                       ("nonce", SPENT_CI152_NONCE), ("reservation_sha256", SPENT_CI152_RESERVATION_SHA256),
                       ("nonce", SPENT_CI151_NONCE), ("reservation_sha256", SPENT_CI151_RESERVATION_SHA256),
                       ("nonce", SPENT_CI150_NONCE), ("reservation_sha256", SPENT_CI150_RESERVATION_SHA256),
                       ("nonce", SPENT_CI149_NONCE), ("reservation_sha256", SPENT_CI149_RESERVATION_SHA256),
                       ("nonce", SPENT_CI146_NONCE), ("nonce", SPENT_CI148_NONCE),
                       ("reservation_sha256", SPENT_CI146_RESERVATION_SHA256),
                       ("reservation_sha256", SPENT_CI148_RESERVATION_SHA256)):
        with pytest.raises(D12SystemGrantError, match="reuses spent"):
            validate_system_build_grant(grant | {key: value})
        with pytest.raises(D12SystemGrantError, match="reuses spent"):
            validate_system_admission_proof(proof | {key: value})
