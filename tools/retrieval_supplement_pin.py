"""Data-only pin validation for the calibration supplement.

The input pin binds the eight authored input files, the actual policy (digest
recomputed, never trusted from a field), the predecessor manifest bytes and both
task digest schemes. The final freeze embeds the exact pin bytes and adds the
reviewed source closure, whose membership is the caller's *fresh* maintained builder
result plus an explicit owner list. Predecessor files are hashed as bytes only.
A freeze never authorizes executing any supplement query.
"""


from __future__ import annotations


from collections.abc import Mapping


from pathlib import Path




from typing import Any


from common_lore_index.retrieval.projection_policy import (
    BodyProjectionPolicy,
    ResolvedBindingPolicy,
)


from retrieval_supplement_catalog import (
    SupplementCatalog,
    load_registered_catalog,
    parse_catalog,
)


from retrieval_supplement_corpus import check_catalog_corpus, parse_corpus


from retrieval_supplement_files import file_inventory, read_member


from retrieval_supplement_tasks import task_digests


from retrieval_supplement_values import (
    INPUT_PIN_SCHEMA,
    MAX_JSON_BYTES,
    PURPOSE,
    REGISTERED_INPUT_PIN_SHA256,
    SupplementError,
    SupplementIdentity,
    canonical_bytes,
    check_keys,
    digest_bytes,
    digest_value,
    fail,
    integer,
    mapping_copy,
    require,
    strict_json,
    string_list,
    text,
)


INPUT_FILE_NAMES: tuple[str, ...] = (
    "catalog-v1.json", "corpus-v1.json", "rationale.md", "bindings-v1.json", "policy-v1.json",
    "predecessor-manifest.json", "namespace-v1.json", "static-inventory-v1.json",
)


PIN_FILE_NAME = "input-pin-v1.json"


PIN_FIELDS: tuple[str, ...] = (
    "schema", "supplement_id", "corpus_id", "phase", "purpose", "input_files", "policy_digest",
    "predecessor_manifest_sha256", "task_digests", "source_closure_claimed",
    "execution_authority_granted",
)


POLICY_FIELDS: tuple[str, ...] = (
    "policy_version", "graph_id", "selected_binding_ids", "bindings", "max_chunk_lines",
    "max_chunk_bytes", "max_projected_file_bytes", "max_input_artifact_bytes",
    "max_aggregate_input_bytes", "policy_digest",
)


DIGEST_FIELDS: tuple[str, ...] = ("retained_task_sha256", "task_sha256")


MAX_MEMBERS = 4096


def policy_from_bytes(raw: bytes) -> BodyProjectionPolicy:
    """Rebuild the policy from JSON; any field or ``policy_digest`` drift is refused."""
    data = check_keys(strict_json(raw), POLICY_FIELDS, "policy")
    bindings_raw = data["bindings"]
    require(isinstance(bindings_raw, dict), "policy.bindings:object_required")
    limits = {name: integer(data[name], f"policy.{name}", 1, 2**40) for name in POLICY_FIELDS[4:9]}
    selected = string_list(
        data["selected_binding_ids"], "policy.selected_binding_ids",
        minimum=0, maximum=MAX_MEMBERS, item_max=256,
    )
    try:
        bindings = {key: ResolvedBindingPolicy.from_mapping(value) for key, value in bindings_raw.items()}
        policy = BodyProjectionPolicy(
            policy_version=text(data["policy_version"], "policy.policy_version", 128),
            graph_id=text(data["graph_id"], "policy.graph_id", 256),
            selected_binding_ids=frozenset(selected),
            bindings=bindings,
            max_chunk_lines=limits["max_chunk_lines"],
            max_chunk_bytes=limits["max_chunk_bytes"],
            max_projected_file_bytes=limits["max_projected_file_bytes"],
            max_input_artifact_bytes=limits["max_input_artifact_bytes"],
            max_aggregate_input_bytes=limits["max_aggregate_input_bytes"],
        ).validate()
    except SupplementError:
        raise
    except (TypeError, AttributeError, ValueError):
        fail("policy:invalid")
    require(canonical_bytes(policy.to_dict()) == canonical_bytes(data), "policy:field_or_digest_drift")
    return policy


def policy_aliases(policy: BodyProjectionPolicy) -> dict[str, str]:
    """Unique alias -> full binding ID mapping derived from the actual policy."""
    aliases = {resolved.alias: binding_id for binding_id, resolved in policy.bindings.items()}
    require(len(aliases) == len(policy.bindings), "policy:duplicate_alias")
    return aliases


def _input_files(input_files: Mapping[str, bytes]) -> dict[str, bytes]:
    require(isinstance(input_files, Mapping), "input_files:mapping_required")
    files = dict(input_files)
    require(set(files) == set(INPUT_FILE_NAMES), "input_files:membership_mismatch")
    for name, data in files.items():
        require(type(data) is bytes and len(data) <= MAX_JSON_BYTES, f"input_files.{name}:invalid")
    return files


def build_input_pin(
    catalog: SupplementCatalog,
    *,
    input_files: Mapping[str, bytes],
    policy: BodyProjectionPolicy,
    predecessor_manifest_sha256: str,
) -> dict[str, Any]:
    """Build the data-only input pin after re-checking every bound input."""
    require(isinstance(catalog, SupplementCatalog), "catalog:required")
    files = _input_files(input_files)
    identity = catalog.identity
    require(files["catalog-v1.json"] == catalog.raw, "input_pin:catalog_bytes_mismatch")
    policy.validate()
    require(policy.graph_id == identity.graph_id, "input_pin:policy_graph_mismatch")
    actual_policy = policy_from_bytes(files["policy-v1.json"])
    require(actual_policy.policy_digest() == policy.policy_digest(), "input_pin:policy_mismatch")
    require(set(catalog.binding_ids) <= set(policy.bindings), "input_pin:catalog_binding_unknown")
    corpus = parse_corpus(files["corpus-v1.json"], identity=identity, bindings=policy_aliases(policy))
    check_catalog_corpus(catalog, corpus)
    for name in ("bindings-v1.json", "namespace-v1.json", "static-inventory-v1.json",
                 "predecessor-manifest.json"):
        strict_json(files[name])
    try:
        require(files["rationale.md"].decode("utf-8").strip() != "", "input_files.rationale:empty")
    except UnicodeDecodeError:
        fail("input_files.rationale:invalid_utf8")
    predecessor = digest_value(predecessor_manifest_sha256, "predecessor_manifest")
    require(digest_bytes(files["predecessor-manifest.json"]) == predecessor, "input_pin:predecessor_drift")
    return {
        "schema": INPUT_PIN_SCHEMA,
        "supplement_id": identity.supplement_id,
        "corpus_id": identity.corpus_id,
        "phase": identity.phase,
        "purpose": PURPOSE,
        "input_files": {
            name: {"bytes": len(files[name]), "sha256": digest_bytes(files[name])}
            for name in sorted(files)
        },
        "policy_digest": policy.policy_digest(),
        "predecessor_manifest_sha256": predecessor,
        "task_digests": {task.task_id: task_digests(task) for task in catalog.tasks},
        "source_closure_claimed": False,
        "execution_authority_granted": False,
    }


def encode_record(record: Mapping[str, Any]) -> bytes:
    """Canonical UTF-8 bytes for a pin or freeze record."""
    return canonical_bytes(dict(record))


def _parse_pin(pin_raw: bytes) -> dict[str, Any]:
    pin = check_keys(strict_json(pin_raw), PIN_FIELDS, "input_pin")
    # The earlier pin's exact bytes are authoritative, including formatting.
    # Canonicalization is only for semantic reproduction, never a replacement.
    for field in ("supplement_id", "corpus_id", "phase"):
        text(pin[field], f"input_pin.{field}", 128)
    require(pin["schema"] == INPUT_PIN_SCHEMA, "input_pin.schema:unsupported")
    require(pin["purpose"] == PURPOSE, "input_pin.purpose:unsupported")
    require(pin["source_closure_claimed"] is False, "input_pin:source_closure_claimed")
    require(pin["execution_authority_granted"] is False, "input_pin:execution_authority")
    entries = check_keys(pin["input_files"], INPUT_FILE_NAMES, "input_pin.input_files")
    for name in INPUT_FILE_NAMES:
        entry = check_keys(entries[name], ("bytes", "sha256"), "input_pin.input_files.entry")
        integer(entry["bytes"], "input_pin.input_files.bytes", 0, MAX_JSON_BYTES)
        digest_value(entry["sha256"], "input_pin.input_files")
    digest_value(pin["policy_digest"], "input_pin.policy_digest")
    digest_value(pin["predecessor_manifest_sha256"], "input_pin.predecessor_manifest_sha256")
    require(isinstance(pin["task_digests"], dict), "input_pin.task_digests:object_required")
    for value in pin["task_digests"].values():
        record = check_keys(value, DIGEST_FIELDS, "input_pin.task_digests")
        for field in DIGEST_FIELDS:
            digest_value(record[field], "input_pin.task_digests")
    return pin


def verify_input_pin(
    root: Path,
    pin_raw: bytes,
    *,
    expected_pin_sha256: str,
    identity: SupplementIdentity,
    bindings: Mapping[str, str],
) -> SupplementCatalog:
    """Verify the exact pinned tree and return its catalog for the explicit identity."""
    require(isinstance(identity, SupplementIdentity), "identity:required")
    expected = digest_value(expected_pin_sha256, "input_pin")
    if identity.registered:
        require(expected == REGISTERED_INPUT_PIN_SHA256, "input_pin:registered_digest_mismatch")
    else:
        require(expected != REGISTERED_INPUT_PIN_SHA256, "input_pin:registered_identity_required")
    require(type(pin_raw) is bytes and digest_bytes(pin_raw) == expected, "input_pin:digest_mismatch")
    pin = _parse_pin(pin_raw)
    for field in ("supplement_id", "corpus_id", "phase"):
        require(pin[field] == getattr(identity, field), f"input_pin.{field}:identity_mismatch")
    members = tuple(sorted((*INPUT_FILE_NAMES, PIN_FILE_NAME)))
    require(file_inventory(root) == members, "input_pin:tree_membership_mismatch")
    stored = read_member(root, PIN_FILE_NAME, max_bytes=MAX_JSON_BYTES, expected_sha256=expected)
    require(stored == pin_raw, "input_pin:stored_bytes_mismatch")
    files: dict[str, bytes] = {}
    for name in INPUT_FILE_NAMES:
        entry = pin["input_files"][name]
        data = read_member(root, name, max_bytes=entry["bytes"], expected_sha256=entry["sha256"])
        require(len(data) == entry["bytes"], "input_pin:member_size_mismatch")
        files[name] = data
    policy = policy_from_bytes(files["policy-v1.json"])
    require(policy.policy_digest() == pin["policy_digest"], "input_pin:policy_digest_mismatch")
    explicit = mapping_copy(bindings, "bindings")
    derived = policy_aliases(policy)
    require(explicit == derived, "input_pin:bindings_mismatch")
    binding_ids = tuple(explicit.values())
    if identity.registered:
        catalog = load_registered_catalog(
            files["catalog-v1.json"], identity=identity, binding_ids=binding_ids, pin_raw=pin_raw
        )
    else:
        catalog = parse_catalog(
            files["catalog-v1.json"], identity=identity, binding_ids=binding_ids,
            expected_sha256=pin["input_files"]["catalog-v1.json"]["sha256"],
        )
    parse_corpus(files["corpus-v1.json"], identity=identity, bindings=explicit)
    rebuilt = build_input_pin(
        catalog, input_files=files, policy=policy,
        predecessor_manifest_sha256=pin["predecessor_manifest_sha256"],
    )
    require(encode_record(rebuilt) == canonical_bytes(pin), "input_pin:reproduction_mismatch")
    return catalog
