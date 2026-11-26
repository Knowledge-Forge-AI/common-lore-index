"""Real SQLite/PostgreSQL captured-text correspondence for unrelated body data."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from common_lore_index_test_support.postgres_harness import temporary_postgres
from common_lore_index_test_support.retrieval_body_fixtures import (
    make_body_bindings, make_body_catalog_payload, make_body_corpus_payload,
    make_body_identity, make_body_policy,
)
from common_lore_index_test_support.retrieval_body_publication_fixture import (
    BodyStoreFixture, materialize_body_home,
)
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_corpus import parse_body_corpus
from retrieval_body_witnesses import capture_body_inventory
from retrieval_public_text_contracts import PUBLICATION_REVALIDATION_FAILED, PrivateMarkerScope, PublicTextRefusal
from retrieval_public_text_excerpt import encode_model_text, map_excerpt, model_text_input_digest, raw_line_count
from retrieval_supplement_values import canonical_bytes


def _data(root: Path, *, postgres: Any = None) -> tuple[Any, Any, Path, BodyStoreFixture]:
    identity, bindings, policy = make_body_identity(), make_body_bindings(), make_body_policy()
    catalog = parse_functional_catalog(
        canonical_bytes(make_body_catalog_payload()), identity=identity, binding_ids=tuple(bindings.values())
    )
    corpus = parse_body_corpus(
        canonical_bytes(make_body_corpus_payload()), identity=identity, bindings=bindings
    )
    published = materialize_body_home(
        root / "publication", corpus=corpus, identity=identity, policy=policy,
        postgres=postgres, psql_command="psql" if postgres is None else postgres.psql_command,
    )
    return catalog, corpus, root / "publication" / "source", BodyStoreFixture(
        published, psql_command="psql" if postgres is None else postgres.psql_command
    )


def _exercise(catalog: Any, corpus: Any, source_root: Path, store: BodyStoreFixture) -> None:
    scope = PrivateMarkerScope.create(("UNIT_BODY_PRIVATE_MARKER",))
    prepared = store.public_text(scope)
    inventory = capture_body_inventory(corpus)
    assert inventory["source_only"] and inventory["publication_observed"] is False
    assert len(prepared.documents) == inventory["public_v2_span_pairs"]
    actual_pairs = {(entry.source_binding_id, entry.canonical_key)
                    for entry in store.representations().verified_entries}
    aliases = {binding: alias for alias, binding in corpus.bindings.items()}
    public_files = {(member.binding_id, member.source_relative_path)
                    for member in corpus.files if member.role != "denied"}
    expected_pairs = set()
    for entry in inventory["inventory"]:
        binding, key = entry["binding_id"], entry["canonical_key"]
        if entry["kind"] == "file":
            # The publication adapter qualifies file keys with the binding
            # alias. Static per-binding canonicalization has no such envelope.
            # Do not invent an observed path/span or rewrite function keys.
            assert entry["paths"] == [] and entry["spans"] == []
            assert key.startswith("file:") and (binding, key[5:]) in public_files
            key = "file:" + aliases[binding] + "/" + key[5:]
        expected_pairs.add((binding, key))
    assert actual_pairs == expected_pairs
    assert len(actual_pairs) == inventory["public_v1_pairs"]
    for case in catalog.cases:
        if case.kind == "body_positive":
            witness = case.to_dict()["witnesses"][0]
            assert (witness["binding_id"], witness["canonical_key"]) in actual_pairs


    alias_by_binding = {binding: policy.alias for binding, policy in store.policy.bindings.items()}
    denied = {binding for binding, policy in store.policy.bindings.items()
              if policy.resolved_binding_privacy != "public-dev"}
    assert all(document.descriptor["source_binding_id"] not in denied for document in prepared.documents)
    for case in catalog.cases:
        if case.kind != "body_positive":
            continue
        witness = case.to_dict()["witnesses"][0]
        document = next(item for item in prepared.documents
                        if item.descriptor["source_binding_id"] == witness["binding_id"]
                        and item.descriptor["source_relative_path"] == witness["source_relative_path"]
                        and item.descriptor["canonical_key"] == witness["canonical_key"])
        descriptor = document.descriptor
        assert descriptor["kind"] == "python.function"
        assert (descriptor["start_line"], descriptor["end_line"]) == (witness["start_line"], witness["end_line"])
        assert type(descriptor["start_line"]) is int and type(descriptor["end_line"]) is int
        raw = (source_root / alias_by_binding[witness["binding_id"]] / witness["source_relative_path"]).read_bytes()
        assert "sha256:" + hashlib.sha256(raw).hexdigest() == witness["source_sha256"]
        span, reason = map_excerpt(
            raw, descriptor["start_line"], descriptor["end_line"],
            projected_raw_line_count=raw_line_count(raw), private_markers=scope.markers,
        )
        assert span is not None and reason is None and not span.span_cut
        assert descriptor["artifact_content_digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
        assert descriptor["span_start_byte"] == span.span_start_byte
        model = encode_model_text(
            descriptor["canonical_key"], descriptor["kind"], descriptor["display_name"],
            descriptor["source_relative_path"], span.excerpt, private_markers=scope.markers,
        )
        assert document.model_text == model
        assert "sha256:" + hashlib.sha256(model.encode("utf-8")).hexdigest() == witness["model_text_sha256"]
        assert descriptor["input_digest"] == model_text_input_digest(model)
        assert descriptor["start_line"] <= witness["fact_start_line"] <= descriptor["end_line"]

    target = next(member for member in corpus.files if member.role == "code")
    target_path = source_root / target.member_path
    target_path.write_bytes(target.content + b"\n# source generation advanced\n")
    store.refresh()
    with pytest.raises(PublicTextRefusal) as advanced:
        store.public_text(scope)
    assert advanced.value.code == PUBLICATION_REVALIDATION_FAILED


def test_sqlite_body_capture_witnesses_and_drift(tmp_path: Path) -> None:
    catalog, corpus, source_root, store = _data(tmp_path)
    _exercise(catalog, corpus, source_root, store)


def test_postgresql_body_capture_witnesses_and_drift(tmp_path: Path) -> None:
    with temporary_postgres() as database:
        catalog, corpus, source_root, store = _data(tmp_path, postgres=database)
        _exercise(catalog, corpus, source_root, store)
