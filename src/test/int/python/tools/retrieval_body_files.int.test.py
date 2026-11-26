"""Body corpus file custody and accepted source-artifact correspondence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from common_lore_index_test_support.retrieval_body_fixtures import (
    make_body_bindings, make_body_catalog_payload, make_body_corpus_payload,
    make_body_identity, make_body_policy,
)
from common_lore_index_test_support.retrieval_body_pin_fixtures import (
    make_body_input_files as make_functional_input_files,
    materialize_body_inputs, materialize_body_predecessors,
)
from common_lore_index_test_support.retrieval_body_publication_fixture import BodyStoreFixture, materialize_body_home
from retrieval_body_admission import admit_body_task
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_corpus import materialize_body_corpus, parse_body_corpus
from retrieval_body_values import BodyError, canonical_bytes
from retrieval_body_pin import validate_functional_body_inputs
from retrieval_supplement_files import file_inventory, read_member


def _corpus():
    identity, bindings = make_body_identity(), make_body_bindings()
    return identity, bindings, parse_body_corpus(
        canonical_bytes(make_body_corpus_payload()), identity=identity, bindings=bindings
    )


def test_body_materialization_is_exact_exclusive_and_symlink_safe(tmp_path: Path) -> None:
    _identity, _bindings, corpus = _corpus()
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    expected = materialize_body_corpus(corpus, root)
    assert file_inventory(root) == tuple(sorted(expected))
    for member in corpus.files:
        assert read_member(root, member.member_path, max_bytes=4096, expected_sha256=member.sha256) == member.content
    with pytest.raises(BodyError):
        materialize_body_corpus(corpus, root)

    symlink_root = tmp_path / "symlink-source"
    symlink_root.mkdir(mode=0o700)
    target = tmp_path / "outside"
    target.mkdir(mode=0o700)
    (symlink_root / "oak").symlink_to(target, target_is_directory=True)
    with pytest.raises(BodyError):
        materialize_body_corpus(corpus, symlink_root)


def test_sqlite_body_publication_source_artifacts_and_functional_cross_class_refusal(tmp_path: Path) -> None:
    identity, bindings, corpus = _corpus()
    policy = make_body_policy()
    published = materialize_body_home(tmp_path / "publication", corpus=corpus, identity=identity, policy=policy)
    store = BodyStoreFixture(published)
    manifest_by_key = {
        (entry.binding_id, entry.source_relative_path): entry
        for entry in published.manifest.entries
    }
    for member in corpus.files:
        if member.role == "denied":
            continue
        entry = manifest_by_key[(member.binding_id, member.source_relative_path)]
        assert entry.reference.content_digest == member.sha256
        assert published.store.read(entry.reference) == member.content

    catalog = parse_functional_catalog(
        canonical_bytes(make_body_catalog_payload()), identity=identity, binding_ids=tuple(bindings.values())
    )
    functional_root = tmp_path / "functional-inputs"
    predecessor_root = tmp_path / "predecessors"
    files = make_functional_input_files()
    materialize_body_inputs(functional_root, files)
    materialize_body_predecessors(predecessor_root)
    functional = validate_functional_body_inputs(
        files, identity=identity, policy=policy, predecessor_root=predecessor_root,
    )
    with pytest.raises(BodyError, match="registered_factory_required"):
        admit_body_task(cast(Any, functional), catalog.cases[0].task, arm="exact_vector:canonical-node-public-text-v2",
                        policy=policy, publication_pin=store.pin, observed_backend="sqlite")
