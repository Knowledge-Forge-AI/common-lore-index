"""Closed data-only body pin records; no IO, source execution or admission."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from retrieval_body_catalog import BodyCatalog
from retrieval_body_corpus import check_body_catalog_corpus, parse_body_corpus
from retrieval_body_values import (
    INPUT_NAMES, PIN_SCHEMA, canonical_bytes, check_keys, digest_bytes,
    digest_value, integer, require, strict_json, text,
)
from retrieval_supplement_pin import policy_aliases, policy_from_bytes
from retrieval_supplement_tasks import task_digests

MAX_JSON_BYTES = 512 * 1024
PIN_FIELDS = (
    'schema', 'class_id', 'corpus_id', 'phase', 'purpose', 'input_files', 'policy_digest',
    'predecessor_manifest_sha256', 'task_digests', 'source_closure_claimed', 'execution_authority_granted',
)


def parse_body_input_pin(raw: bytes) -> dict[str, Any]:
    """Validate every pin field; the caller must still bind bytes and class."""
    pin = dict(check_keys(strict_json(raw), PIN_FIELDS, 'body.pin'))
    require(pin['schema'] == PIN_SCHEMA and pin['purpose'] == 'calibration', 'body.pin:schema_or_purpose')
    for name in ('class_id', 'corpus_id', 'phase'):
        text(pin[name], 'body.pin.' + name, 128)
    require(pin['source_closure_claimed'] is False, 'body.pin:source_closure_claimed')
    require(pin['execution_authority_granted'] is False, 'body.pin:execution_authority')
    files = check_keys(pin['input_files'], INPUT_NAMES, 'body.pin.inputs')
    for value in files.values():
        entry = check_keys(value, ('bytes', 'sha256'), 'body.pin.member')
        integer(entry['bytes'], 'body.pin.member.bytes', 0, MAX_JSON_BYTES)
        digest_value(entry['sha256'], 'body.pin.member')
    for name in ('policy_digest', 'predecessor_manifest_sha256'):
        digest_value(pin[name], 'body.pin.' + name)
    require(type(pin['task_digests']) is dict and len(pin['task_digests']) == 24, 'body.pin:task_digest_population')
    for key, value in pin['task_digests'].items():
        text(key, 'body.pin.task_id', 64)
        row = check_keys(value, ('retained_task_sha256', 'task_sha256'), 'body.pin.task_digests')
        for digest in row.values():
            digest_value(digest, 'body.pin.task_digest')
    return pin


def build_body_input_pin(
    catalog: BodyCatalog, *, input_files: Mapping[str, bytes],
    policy: BodyProjectionPolicy,
) -> dict[str, Any]:
    """Reproduce the exact data pin after actual policy, corpus and task checks."""
    require(BodyCatalog.is_factory_issued(catalog), 'body.pin:catalog_required')
    require(isinstance(input_files, Mapping) and set(input_files) == set(INPUT_NAMES), 'body.pin:input_membership')
    files = dict(input_files)
    for raw in files.values():
        require(type(raw) is bytes and len(raw) <= MAX_JSON_BYTES, 'body.pin:input_bytes')
    require(files['catalog-v1.json'] == catalog.raw, 'body.pin:catalog_bytes')
    require(type(policy) is BodyProjectionPolicy, 'body.pin:policy_required')
    policy.validate()
    actual = policy_from_bytes(files['policy-v1.json'])
    require(actual.to_dict() == policy.to_dict() and actual.max_projected_files == policy.max_projected_files, 'body.pin:policy_drift')
    require(actual.graph_id == catalog.identity.graph_id, 'body.pin:policy_graph')
    bindings = strict_json(files['bindings-v1.json'])
    require(type(bindings) is dict and bindings == policy_aliases(actual), 'body.pin:bindings_drift')
    corpus = parse_body_corpus(files['corpus-v1.json'], identity=catalog.identity, bindings=bindings)
    check_body_catalog_corpus(catalog, corpus)
    for name in ('namespace-v1.json', 'preauthoring-gate-v1.json',
                 'predecessor-manifest.json', 'static-inventory-v1.json'):
        require(type(strict_json(files[name])) is dict, 'body.pin:metadata_object')
    try:
        rationale = files['rationale.md'].decode('utf-8')
    except UnicodeDecodeError:
        require(False, 'body.pin:rationale_utf8')
        raise AssertionError('unreachable')
    text(rationale, 'body.pin.rationale', MAX_JSON_BYTES)
    identity = catalog.identity
    return {
        'schema': PIN_SCHEMA, 'class_id': identity.class_id,
        'corpus_id': identity.corpus_id, 'phase': identity.phase, 'purpose': 'calibration',
        'input_files': {name: {'bytes': len(files[name]), 'sha256': digest_bytes(files[name])}
                        for name in sorted(files)},
        'policy_digest': actual.policy_digest(),
        'predecessor_manifest_sha256': digest_bytes(files['predecessor-manifest.json']),
        'task_digests': {case.task.task_id: task_digests(case.task) for case in catalog.cases},
        'source_closure_claimed': False, 'execution_authority_granted': False,
    }


def require_reproduced_body_pin(pin_raw: bytes, rebuilt: Mapping[str, Any]) -> None:
    pin = parse_body_input_pin(pin_raw)
    require(canonical_bytes(pin) == canonical_bytes(dict(rebuilt)), 'body.pin:reproduction_drift')
