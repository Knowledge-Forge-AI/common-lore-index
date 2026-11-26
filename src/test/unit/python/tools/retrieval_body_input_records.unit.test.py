"""Pure data-pin reproduction on unrelated canopy inputs, without registration."""
from __future__ import annotations

from dataclasses import replace

import pytest

from common_lore_index_test_support.retrieval_body_fixtures import (
    make_body_bindings, make_body_catalog_payload, make_body_corpus_payload,
    make_body_identity, make_body_policy,
)
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_input_records import build_body_input_pin, parse_body_input_pin, require_reproduced_body_pin
from retrieval_body_values import BodyError, INPUT_NAMES, canonical_bytes


def _inputs():
    policy = make_body_policy()
    raw = canonical_bytes(make_body_catalog_payload())
    catalog = parse_functional_catalog(raw, identity=make_body_identity(),
                                     binding_ids=tuple(make_body_bindings().values()))
    files = {name: b'{}' for name in INPUT_NAMES}
    files.update({
        'catalog-v1.json': raw, 'corpus-v1.json': canonical_bytes(make_body_corpus_payload()),
        'policy-v1.json': canonical_bytes(policy.to_dict()),
        'bindings-v1.json': canonical_bytes(make_body_bindings()),
        'rationale.md': b'Unrelated functional canopy data-pin rationale.\n',
    })
    return catalog, policy, files


def test_pure_pin_reproduces_both_task_digests_and_all_input_bytes():
    catalog, policy, files = _inputs()
    pin = build_body_input_pin(catalog, input_files=files, policy=policy)
    assert len(pin['task_digests']) == 24 and set(pin['input_files']) == set(INPUT_NAMES)
    assert pin['source_closure_claimed'] is False and pin['execution_authority_granted'] is False
    raw = canonical_bytes(pin)
    assert parse_body_input_pin(raw) == pin
    require_reproduced_body_pin(raw, build_body_input_pin(catalog, input_files=files, policy=policy))
    files['rationale.md'] += b' Additional source-independent rationale.\n'
    with pytest.raises(BodyError, match='reproduction_drift'):
        require_reproduced_body_pin(raw, build_body_input_pin(catalog, input_files=files, policy=policy))


def test_policy_cap_omitted_by_legacy_encoding_cannot_be_supplied_as_an_override():
    catalog, policy, files = _inputs()
    changed = replace(policy, max_projected_files=1).validate()
    assert changed.policy_digest() == policy.policy_digest()
    with pytest.raises(BodyError, match='policy_drift'):
        build_body_input_pin(catalog, input_files=files, policy=changed)


@pytest.mark.parametrize('field,value', [
    ('source_closure_claimed', True), ('execution_authority_granted', True),
    ('purpose', 'held-out'), ('schema', 'cloe-supplement-input-pin-v1'),
    ('task_digests', {}), ('policy_digest', 'bad'),
])
def test_pin_refuses_false_claims_and_wrong_shapes(field, value):
    catalog, policy, files = _inputs()
    pin = build_body_input_pin(catalog, input_files=files, policy=policy)
    pin[field] = value
    with pytest.raises(BodyError):
        parse_body_input_pin(canonical_bytes(pin))


def test_reproduction_requires_exact_input_tree_and_actual_catalog_bytes():
    catalog, policy, files = _inputs()
    files['unexpected.json'] = b'{}'
    with pytest.raises(BodyError, match='input_membership'):
        build_body_input_pin(catalog, input_files=files, policy=policy)
    del files['unexpected.json']
    files['catalog-v1.json'] += b' '
    with pytest.raises(BodyError, match='catalog_bytes'):
        build_body_input_pin(catalog, input_files=files, policy=policy)
