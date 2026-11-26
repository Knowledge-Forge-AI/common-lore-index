"""Class-scoped membership, policy precedence and live-publication refusal contracts."""
from __future__ import annotations

from copy import copy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from common_lore_index_test_support.retrieval_body_admission_fixtures import make_body_publication
from common_lore_index_test_support.retrieval_body_fixtures import make_body_bindings, make_body_catalog_payload, make_body_identity, make_body_policy
from retrieval_body_admission import BodyTaskAdmission, admit_body_task
from retrieval_body_admission_rules import complete_policy_identity, evaluate_body_case
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_values import ARMS, BodyError, canonical_bytes


def _catalog():
    return parse_functional_catalog(canonical_bytes(make_body_catalog_payload()),
        identity=make_body_identity(), binding_ids=tuple(make_body_bindings().values()))


def _evaluate(*, number=0, arm=ARMS[4], policy=None, frozen=None, pin=None, backend='sqlite'):
    catalog = _catalog()
    actual = make_body_policy() if policy is None else policy
    if pin is None:
        pin, _ = make_body_publication(policy=actual)
    return evaluate_body_case(catalog, catalog.cases[number], arm=arm, policy=actual,
        frozen_policy=actual if frozen is None else frozen, publication_pin=pin, observed_backend=backend)


def test_body_v2_is_ready_but_v1_and_graph_keep_their_existing_incompatibility():
    policy = make_body_policy()
    pin, cell = make_body_publication(policy=policy)
    before = cell.reads
    row = _evaluate(policy=policy, pin=pin)
    assert row['preparation'] == 'ready' and row['actual_surface'] == 'captured_text'
    assert row['execution_authority_granted'] is False and row['candidate_access_performed'] is False
    assert row['publication_revalidation_performed'] is True and cell.reads > before
    assert _evaluate(arm=ARMS[3])['reason'] == 'metadata_representation_not_comparable'
    assert _evaluate(arm=ARMS[1])['reason'] == 'captured_text_surface_unsupported_by_metadata_graph'


@pytest.mark.parametrize('arm', ARMS)
@pytest.mark.parametrize('number', [20, 21])
def test_privacy_refuses_every_arm_before_publication_readback(number, arm):
    pin, cell = make_body_publication()
    before = cell.reads
    row = _evaluate(number=number, arm=arm, pin=pin)
    assert row['preparation'] == 'refused' and row['reason'] == 'binding_privacy_not_authorized'
    assert row['publication_revalidation_performed'] is False and cell.reads == before


@pytest.mark.parametrize('changes,reason', [
    ({'original_binding_privacy': 'private-memory'}, 'binding_privacy_not_authorized'),
    ({'resolved_graph_privacy': 'private-memory'}, 'graph_privacy_not_authorized'),
    ({'original_evidence_retention': 'redacted'}, 'retention_not_authorized'),
    ({'role': 'email'}, 'email_body_forbidden'),
    ({'source_kind': 'rss'}, 'feed_body_forbidden'),
])
def test_runtime_declared_policy_denial_precedes_incompatible_arm(changes, reason):
    base = make_body_policy()
    bindings = dict(base.bindings)
    key = make_body_bindings()['oak']
    bindings[key] = replace(bindings[key], **changes)
    policy = replace(base, bindings=bindings).validate()
    pin, cell = make_body_publication(policy=base)
    before = cell.reads
    row = _evaluate(arm=ARMS[3], policy=policy, frozen=base, pin=pin)
    assert row['preparation'] == 'refused' and row['reason'] == reason
    assert cell.reads == before


def test_declared_but_unselected_binding_refuses_without_adapter_access():
    base = make_body_policy()
    pin, cell = make_body_publication(policy=base)
    policy = replace(base, selected_binding_ids=frozenset({make_body_bindings()['oak']})).validate()
    before = cell.reads
    row = _evaluate(policy=policy, frozen=base, pin=pin)
    assert row['reason'] == 'binding_not_selected_by_policy'
    assert cell.reads == before


def test_full_policy_identity_catches_file_cap_omitted_by_legacy_digest():
    base = make_body_policy()
    changed = replace(base, max_projected_files=1).validate()
    assert base.policy_digest() == changed.policy_digest()
    assert complete_policy_identity(base) != complete_policy_identity(changed)
    pin, _ = make_body_publication(policy=base)
    with pytest.raises(BodyError, match='frozen_policy_drift'):
        _evaluate(policy=changed, frozen=base, pin=pin)


def test_metadata_anchor_still_requires_body_retention_for_public_text_arm():
    base = make_body_policy()
    bindings = dict(base.bindings)
    key = make_body_bindings()['oak']
    bindings[key] = replace(bindings[key], original_evidence_retention='redacted',
                            resolved_evidence_retention='redacted')
    policy = replace(base, bindings=bindings).validate()
    pin, _ = make_body_publication(policy=policy)
    assert _evaluate(number=12, arm=ARMS[3], policy=policy, pin=pin)['preparation'] == 'ready'
    assert _evaluate(number=12, arm=ARMS[4], policy=policy, pin=pin)['reason'] == 'retention_not_authorized'


def test_stale_and_copied_publication_pins_refuse():
    pin, cell = make_body_publication()
    with pytest.raises(BodyError, match='issued_publication_required'):
        _evaluate(pin=copy(pin))
    cell.advance()
    with pytest.raises(BodyError, match='publication_pin_revalidation_failed'):
        _evaluate(pin=pin)


def test_declared_public_absence_is_unavailable_and_not_a_privacy_denial():
    pin, _ = make_body_publication(omit=frozenset({make_body_bindings()['elm']}))
    row = _evaluate(pin=pin)
    assert row['preparation'] == 'unavailable'
    assert row['reason'] == 'declared_public_absent'


def test_anchor_metadata_capability_and_backend_are_independent():
    assert _evaluate(number=12, arm=ARMS[3])['preparation'] == 'ready'
    assert _evaluate(number=12, arm=ARMS[1])['reason'] == 'arm_backend_unavailable'
    assert _evaluate(number=12, arm=ARMS[1], backend='postgresql',
                     pin=make_body_publication(backend='postgresql')[0])['preparation'] == 'ready'


def test_literal_modes_cannot_become_semantic_query_admission():
    assert _evaluate(number=22)['reason'] == 'unsupported_semantic_query_mode'
    assert _evaluate(number=23)['reason'] == 'unsupported_semantic_query_mode'


def test_functional_objects_and_direct_constructor_cannot_issue_registered_admission():
    pin, cell = make_body_publication()
    task = _catalog().cases[0].task
    before = cell.reads
    with pytest.raises(BodyError):
        admit_body_task(cast(Any, SimpleNamespace(catalog=_catalog(), registered=True)), task, arm=ARMS[4],
                        policy=make_body_policy(), publication_pin=pin, observed_backend='sqlite')
    with pytest.raises(BodyError, match='factory_required'):
        BodyTaskAdmission(b'{}', cast(Any, object()), task, arm=ARMS[4], policy=make_body_policy(),
                          publication_pin=pin, observed_backend='sqlite')
    assert cell.reads == before and not BodyTaskAdmission.is_factory_issued(object())
