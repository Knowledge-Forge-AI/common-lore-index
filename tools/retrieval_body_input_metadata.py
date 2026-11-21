"""Frozen metadata and source-only witness checks for body input custody."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
import re
from typing import Any

from retrieval_body_catalog import BodyCatalog
from retrieval_body_corpus import BodyCorpus
from retrieval_body_predecessors import PredecessorSnapshot
from retrieval_body_values import (
    BODY_FAMILIES,
    BodyIdentity,
    check_keys,
    canonical_bytes,
    integer,
    require,
    safe_relative_path,
    strict_json,
    string_list,
    text,
)
from retrieval_body_witnesses import verify_body_witnesses


GATE_FIELDS = (
    "phase", "reviewed_design_sha256", "clarified_design_sha256", "review_result_sha256",
    "parent_bridge_sha256", "independent_pre_authoring_pass", "authoring_after_gate",
)
NAMESPACE_FIELDS = (
    "collision_count", "held_out_values_decoded", "identity", "old_task_id_tokens_observed",
    "original_catalog_hashes", "previous_ids_check", "task_ids",
)
NAMESPACE_IDENTITY_FIELDS = ("corpus_id", "graph_id", "phase", "supplement_id", "task_prefix")
STATIC_REQUIRED_FIELDS = (
    "schema", "inventory", "public_v1_pairs", "public_v2_span_pairs", "metadata_anchors",
    "body_cases", "controls", "combined_upper_bound", "gold_region_counts", "wording_by_family",
    "all12_body_file_witnesses_statically_reached", "model_free", "queries_executed",
    "accepted_publication_or_native_qualification", "truncation_survival",
)
STATIC_FIELDS = STATIC_REQUIRED_FIELDS
INVENTORY_FIELDS = ("binding_id", "canonical_key", "display_name", "kind", "paths", "public", "spans")
SPAN_FIELDS = ("end_line", "path", "source_id", "start_line")
_RAW_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_NAMESPACE_CHECK = "complete ID-token-only collision verification; JSON escapes decoded for task IDs only"


def _raw_sha(value: object, label: str) -> str:
    require(type(value) is str and _RAW_SHA256.fullmatch(value) is not None, f"{label}:raw_sha256")
    return str(value)


def validate_preauthoring_gate(raw: bytes, *, identity: BodyIdentity) -> dict[str, Any]:
    """Validate the exact independent design gate; booleans are not coerced."""
    require(type(identity) is BodyIdentity, "body.gate:identity")
    gate = check_keys(strict_json(raw), GATE_FIELDS, "body.gate")
    require(gate["phase"] == identity.phase, "body.gate:phase")
    for field in GATE_FIELDS[1:5]:
        _raw_sha(gate[field], "body.gate." + field)
    require(gate["independent_pre_authoring_pass"] is True, "body.gate:independent_pass")
    require(gate["authoring_after_gate"] is True, "body.gate:authoring_order")
    return dict(gate)


def validate_namespace(
    raw: bytes,
    *,
    identity: BodyIdentity,
    predecessor: PredecessorSnapshot,
    task_ids: Collection[str],
) -> dict[str, Any]:
    """Validate the frozen namespace alias and ID-only predecessor census."""
    require(type(identity) is BodyIdentity, "body.namespace:identity")
    require(type(predecessor) is PredecessorSnapshot, "body.namespace:predecessor")
    root = check_keys(strict_json(raw), NAMESPACE_FIELDS, "body.namespace")
    integer(root["collision_count"], "body.namespace.collision_count", 0, 0)
    require(root["held_out_values_decoded"] is False, "body.namespace:held_out_decoded")
    old_count = integer(root["old_task_id_tokens_observed"], "body.namespace.old_count", 0, 4096)
    require(old_count == predecessor.task_id_token_count, "body.namespace:old_count_drift")
    require(root["previous_ids_check"] == _NAMESPACE_CHECK, "body.namespace:check_description")
    metadata_identity = check_keys(root["identity"], NAMESPACE_IDENTITY_FIELDS, "body.namespace.identity")
    require(metadata_identity["supplement_id"] == identity.class_id, "body.namespace:legacy_alias")
    require(metadata_identity["corpus_id"] == identity.corpus_id, "body.namespace:corpus_id")
    require(metadata_identity["phase"] == identity.phase, "body.namespace:phase")
    require(metadata_identity["graph_id"] == identity.graph_id, "body.namespace:graph_id")
    require(metadata_identity["task_prefix"] == identity.task_prefix, "body.namespace:task_prefix")
    for field in NAMESPACE_IDENTITY_FIELDS:
        text(metadata_identity[field], "body.namespace.identity." + field, 256)
    hashes = root["original_catalog_hashes"]
    require(isinstance(hashes, dict), "body.namespace:catalog_hashes")
    require(dict(hashes) == predecessor.catalog_hashes, "body.namespace:catalog_hash_drift")
    for path, value in hashes.items():
        text(path, "body.namespace.catalog_path", 512)
        _raw_sha(value, "body.namespace.catalog_hash")
    current = string_list(root["task_ids"], "body.namespace.task_ids", minimum=1, maximum=24, item_max=64)
    expected = tuple(sorted(task_ids))
    require(tuple(sorted(current)) == expected and len(current) == len(expected), "body.namespace:task_set")
    for value in current:
        identity.check_task_id(value)
    require(not set(current) & set(predecessor.task_ids), "body.namespace:task_collision")
    return dict(root)


def _span(row: object) -> dict[str, Any]:
    span = check_keys(row, SPAN_FIELDS, "body.static.span")
    require(type(span["path"]) is str or span["path"] is None, "body.static.span.path")
    require(type(span["source_id"]) is str or span["source_id"] is None, "body.static.span.source")
    if span["path"] is not None:
        safe_relative_path(span["path"])
    if span["start_line"] is None or span["end_line"] is None:
        require(span["start_line"] is None and span["end_line"] is None, "body.static.span.lines")
    else:
        integer(span["start_line"], "body.static.span.start", 1, 10_000_000)
        integer(span["end_line"], "body.static.span.end", span["start_line"], 10_000_000)
    return dict(span)


def _inventory_row(row: object, *, denied: frozenset[str]) -> dict[str, Any]:
    item = check_keys(row, INVENTORY_FIELDS, "body.static.inventory")
    for field in ("binding_id", "canonical_key", "display_name", "kind"):
        text(item[field], "body.static.inventory." + field, 1024)
    paths = string_list(item["paths"], "body.static.inventory.paths", minimum=0, maximum=24, item_max=512)
    for path in paths:
        safe_relative_path(path)
    require(type(item["public"]) is bool, "body.static.inventory.public")
    if item["public"]:
        require(item["binding_id"] not in denied, "body.static.inventory:private_row")
    else:
        require(item["binding_id"] in denied, "body.static.inventory:denied_row")
    spans = item["spans"]
    require(type(spans) is list and len(spans) <= 24, "body.static.inventory.spans")
    checked = [_span(value) for value in spans]
    return {"binding_id": item["binding_id"], "canonical_key": item["canonical_key"],
            "display_name": item["display_name"], "kind": item["kind"], "paths": paths,
            "public": item["public"], "spans": checked}


def _sorted_rows(value: object, *, denied: frozenset[str]) -> list[dict[str, Any]]:
    require(type(value) is list and len(value) <= 80, "body.static.inventory.count")
    assert isinstance(value, list)
    rows = [_inventory_row(item, denied=denied) for item in value]
    return sorted(rows, key=lambda row: (
        row["binding_id"], row["canonical_key"], row["kind"], tuple(row["paths"]),
        canonical_bytes(row["spans"]),
    ))


def _expected_wording(catalog: BodyCatalog) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for family in BODY_FAMILIES:
        counts = Counter(
            case.to_dict()["wording_stratum"]
            for case in catalog.cases
            if case.kind == "body_positive" and case.task.family == family
        )
        literal = counts.get("literal_clue", 0)
        paraphrased = counts.get("paraphrased_intent", 0)
        result[family] = {
            "attrition": [], "literal_clue": literal, "paraphrased_intent": paraphrased,
            "surviving": literal + paraphrased,
        }
    return result


def validate_static_inventory(raw: bytes, *, catalog: BodyCatalog, corpus: BodyCorpus) -> dict[str, Any]:
    """Recompute public AST inventory and compare independent frozen counts/rows."""
    require(BodyCatalog.is_factory_issued(catalog), "body.static:catalog")
    require(type(corpus) is BodyCorpus, "body.static:corpus")
    root = check_keys(strict_json(raw), STATIC_FIELDS, "body.static")
    require(root["schema"] == "cloe-body-static-inventory-v1", "body.static:schema")
    fresh = verify_body_witnesses(catalog, corpus)
    denied = corpus.denied_binding_ids
    frozen_v1 = _sorted_rows(root["inventory"], denied=denied)
    fresh_v1 = _sorted_rows(fresh["inventory"], denied=denied)
    frozen_public = [row for row in frozen_v1 if row["public"]]
    require(frozen_public == fresh_v1, "body.static:inventory_drift")
    fresh_v2 = [row for row in fresh_v1 if any(
        type(span["start_line"]) is int and type(span["end_line"]) is int for span in row["spans"]
    )]
    require(integer(root["public_v1_pairs"], "body.static.v1_count", 0, 80) == len(fresh_v1),
            "body.static:v1_count")
    require(integer(root["public_v2_span_pairs"], "body.static.v2_count", 0, 80) == len(fresh_v2),
            "body.static:v2_count")
    body_cases = [case for case in catalog.cases if case.kind == "body_positive"]
    controls = [case for case in catalog.cases if case.kind in {"absent", "ambiguous", "privacy", "unsupported"}]
    require(integer(root["metadata_anchors"], "body.static:anchors", 0, 24) ==
            sum(case.kind == "metadata_anchor" for case in catalog.cases), "body.static:anchors")
    require(integer(root["body_cases"], "body.static:body_cases", 0, 24) == len(body_cases),
            "body.static:body_cases")
    require(integer(root["controls"], "body.static:controls", 0, 24) == len(controls),
            "body.static:controls")
    require(integer(root["combined_upper_bound"], "body.static:combined", 0, 208) ==
            2 * (len(fresh_v1) + len(catalog.cases)), "body.static:combined")
    gold_counts: Counter[str] = Counter()
    aliases = {binding: alias for alias, binding in corpus.bindings.items()}
    for case in body_cases:
        for gold in case.task.gold_targets:
            alias = aliases.get(gold.locator.binding_id)
            require(alias is not None, "body.static:gold_binding")
            assert alias is not None
            gold_counts[alias] += 1
    require(root["gold_region_counts"] == dict(sorted(gold_counts.items())), "body.static:gold_regions")
    require(root["all12_body_file_witnesses_statically_reached"] is True, "body.static:witness_reachability")
    require(root["model_free"] is True and fresh["model_free"] is True, "body.static:model_free")
    require(root["queries_executed"] is False, "body.static:queries_executed")
    require(root["accepted_publication_or_native_qualification"] is False,
            "body.static:accepted_qualification")
    require(type(root["truncation_survival"]) is str and root["truncation_survival"].startswith("unobserved"),
            "body.static:truncation_claim")
    wording = root["wording_by_family"]
    require(isinstance(wording, dict) and set(wording) == set(BODY_FAMILIES), "body.static:wording")
    require(canonical_bytes(wording) == canonical_bytes(_expected_wording(catalog)), "body.static:wording")
    return dict(root)


__all__ = [
    "GATE_FIELDS", "NAMESPACE_FIELDS", "STATIC_FIELDS", "validate_namespace",
    "validate_preauthoring_gate", "validate_static_inventory",
]
