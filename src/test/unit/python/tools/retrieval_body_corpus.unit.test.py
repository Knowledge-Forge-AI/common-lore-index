"""Unrelated corpus byte custody, role membership and refusal behavior."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from common_lore_index_test_support.retrieval_body_fixtures import (
    make_body_bindings, make_body_catalog_payload, make_body_corpus_payload, make_body_identity,
)
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_corpus import check_body_catalog_corpus, materialize_body_corpus, parse_body_corpus
from retrieval_body_values import BodyError, canonical_bytes


def _parse(payload: dict[str, Any]):
    return parse_body_corpus(canonical_bytes(payload), identity=make_body_identity(), bindings=make_body_bindings())


def _catalog():
    return parse_functional_catalog(canonical_bytes(make_body_catalog_payload()), identity=make_body_identity(),
                                    binding_ids=tuple(make_body_bindings().values()))


def test_body_corpus_preserves_exact_bytes_and_defensive_bindings():
    payload = make_body_corpus_payload()
    corpus = _parse(payload)
    check_body_catalog_corpus(_catalog(), corpus)
    assert corpus.raw == canonical_bytes(payload) and len(corpus.files) == 24
    corpus.bindings.clear()
    corpus.to_dict()["files"].clear()
    assert corpus.bindings == make_body_bindings() and len(corpus.files) == 24
    assert corpus.denied_binding_ids == {make_body_bindings()["vault"]}
    with pytest.raises(AttributeError):
        corpus._raw = b"{}"


@pytest.mark.parametrize("field,value", [
    ("binding_alias", "unknown"), ("binding_alias", True), ("role", "execute"),
    ("source_relative_path", "../escape.py"), ("source_relative_path", "oak/prefixed.py"),
    ("source_relative_path", "tests/test_mislabelled.py"), ("source_relative_path", "module.md"),
    ("content_utf8", "x" * 4097), ("content_utf8", "\x00"),
    ("content_utf8", "\ud800"), ("content_utf8", False),
])
def test_corpus_member_refuses_unsafe_shapes(field: str, value: Any):
    payload = make_body_corpus_payload()
    payload["files"][0][field] = value
    with pytest.raises(BodyError):
        _parse(payload)


def test_corpus_refuses_duplicate_members_cap_and_mixed_denied_binding():
    payload = make_body_corpus_payload()
    payload["files"][1] = dict(payload["files"][0])
    with pytest.raises(BodyError, match="duplicate_member"):
        _parse(payload)
    payload = make_body_corpus_payload()
    payload["files"].append(dict(payload["files"][0]))
    with pytest.raises(BodyError, match="file_cap"):
        _parse(payload)
    payload = make_body_corpus_payload()
    payload["files"][0]["role"] = "denied"
    with pytest.raises(BodyError, match="mixed_denied_binding"):
        _parse(payload)


def test_corpus_catalog_requires_real_gold_and_family_roles():
    payload = make_body_corpus_payload()
    payload["files"].pop(0)
    with pytest.raises(BodyError, match="gold_missing"):
        check_body_catalog_corpus(_catalog(), _parse(payload))
    payload = make_body_corpus_payload()
    payload["files"][0]["role"] = "test_source"
    with pytest.raises(BodyError, match="test_role_path"):
        _parse(payload)


def test_materialization_is_data_only_and_never_overwrites(tmp_path: Path):
    corpus = _parse(make_body_corpus_payload())
    root = tmp_path / "inert-corpus"
    root.mkdir(mode=0o700)
    result = materialize_body_corpus(corpus, root)
    assert len(result) == 24
    for member in corpus.files:
        assert (root / member.member_path).read_bytes() == member.content
    with pytest.raises(BodyError, match="root_not_empty"):
        materialize_body_corpus(corpus, root)


def test_materialization_refuses_linked_root_without_writing(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(BodyError, match="unsafe_directory_component"):
        materialize_body_corpus(_parse(make_body_corpus_payload()), link)
    assert not list(target.iterdir())
