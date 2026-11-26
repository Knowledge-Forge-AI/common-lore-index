"""Static body witness correspondence and cap contracts over unrelated inputs."""

from __future__ import annotations

import ast
import copy
from typing import Any

import pytest

from common_lore_index_test_support.retrieval_body_fixtures import (
    make_body_bindings,
    make_body_catalog_payload,
    make_body_corpus_payload,
    make_body_identity,
)
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_corpus import parse_body_corpus
from retrieval_body_values import BodyError, canonical_bytes
import retrieval_body_witnesses as witness_module
from retrieval_body_witnesses import capture_body_inventory, verify_body_witnesses


def _catalog(payload: dict[str, Any] | None = None):
    value = make_body_catalog_payload() if payload is None else payload
    return parse_functional_catalog(
        canonical_bytes(value), identity=make_body_identity(),
        binding_ids=tuple(make_body_bindings().values()),
    )


def _corpus(payload: dict[str, Any] | None = None):
    value = make_body_corpus_payload() if payload is None else payload
    return parse_body_corpus(canonical_bytes(value), identity=make_body_identity(), bindings=make_body_bindings())


def test_valid_recomputation_reports_complete_public_v1_and_v2_inventory() -> None:
    report = verify_body_witnesses(_catalog(), _corpus())
    assert report["public_v1_pairs"] == 66
    assert report["public_v2_span_pairs"] == 44
    assert report["body_cases"] == 12
    assert report["witnesses_checked"] == 12
    assert report["distractors_checked"] == 12
    assert report["denied_member_count"] == 2
    assert report["source_only"] is True and report["model_loaded"] is False
    assert report["body_floor"] == {"minimum": 8, "observed": 12, "met": True}
    assert all(count >= 2 for count in report["family_counts"].values())
    assert report["queries_executed"] is False
    assert report["tokenizer_count"] is None
    assert report["truncation_survival"] == "unobserved"
    assert {"python.module", "python.function", "python.test_assertion"} <= set(report["emitted_kinds"])
    files = [item for item in report["inventory"] if item["kind"] == "file"]
    assert len(files) == 22
    assert all(item["paths"] == [] and item["spans"] == [] for item in files)


def test_inventory_and_report_return_fresh_mutable_copies() -> None:
    catalog, corpus = _catalog(), _corpus()
    first = verify_body_witnesses(catalog, corpus)
    first["inventory"].clear()
    first["family_counts"]["behavior_without_exact_symbol"] = 0
    inventory = capture_body_inventory(corpus)
    inventory["spanned_inventory"].clear()
    second = verify_body_witnesses(catalog, corpus)
    assert len(second["inventory"]) == 66
    assert len(second["spanned_inventory"]) == 44
    assert second["family_counts"]["behavior_without_exact_symbol"] == 4


@pytest.mark.parametrize("field,value", [
    ("source_sha256", "sha256:" + "0" * 64),
    ("raw_source_id", "canopy/specimen_0.py#function:99:inspect_canopy"),
    ("canonical_key", "python.function:wrong:name"),
    ("display_name", "wrong_name"),
    ("start_line", 2),
    ("fact_source_start_byte", 0),
    ("model_text_sha256", "sha256:" + "1" * 64),
    ("fact_model_start_byte", 0),
])
def test_witness_source_span_and_model_drift_refuses(field: str, value: Any) -> None:
    payload = make_body_catalog_payload()
    payload["cases"][0]["witnesses"][0][field] = value
    with pytest.raises(BodyError):
        verify_body_witnesses(_catalog(payload), _corpus())


def test_source_byte_drift_refuses_even_when_catalog_shape_is_valid() -> None:
    payload = make_body_corpus_payload()
    payload["files"][0]["content_utf8"] = payload["files"][0]["content_utf8"].replace("fallback", "changed")
    with pytest.raises(BodyError, match="source_sha256"):
        verify_body_witnesses(_catalog(), _corpus(payload))


def test_denied_members_are_not_passed_to_the_extractor(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    original = witness_module.extract_python_file_observations

    def observe(path: str, *args: Any, **kwargs: Any):
        seen.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(witness_module, "extract_python_file_observations", observe)
    verify_body_witnesses(_catalog(), _corpus())
    assert seen and all(not path.startswith("inert/") for path in seen)


def test_binding_local_indexes_do_not_resolve_foreign_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = make_body_corpus_payload()
    oak_source = next(
        row for row in payload["files"]
        if row["binding_alias"] == "oak" and row["source_relative_path"] == "canopy/specimen_0.py"
    )
    oak_source["content_utf8"] = "from foreign_only import value\n" + oak_source["content_utf8"]
    foreign = next(
        row for row in payload["files"]
        if row["binding_alias"] == "elm" and row["source_relative_path"] == "canopy/specimen_0.py"
    )
    foreign["source_relative_path"] = "foreign_only.py"
    foreign["content_utf8"] = "value = 1\n"
    scopes: dict[str, tuple[Any, ...]] = {}
    original = witness_module.canonicalize_observations

    def observe(observations: Any, *, repository_scope: str | None = None):
        assert repository_scope is not None
        scopes[repository_scope] = tuple(observations)
        return original(observations, repository_scope=repository_scope)

    monkeypatch.setattr(witness_module, "canonicalize_observations", observe)
    report = capture_body_inventory(_corpus(payload))
    bindings = make_body_bindings()
    assert set(scopes) == {bindings["oak"], bindings["elm"]}
    oak_import = next(
        observation for observation in scopes[bindings["oak"]]
        if observation.kind == "python.import" and observation.metadata.get("imported_module") == "foreign_only"
    )
    assert oak_import.metadata["resolution"] == "external"
    assert any(
        item["binding_id"] == bindings["elm"] and item["canonical_key"] == "python.module:foreign_only"
        for item in report["inventory"]
    )
    assert not any(
        item["binding_id"] == bindings["oak"] and item["canonical_key"] == "python.module:foreign_only"
        for item in report["inventory"]
    )


def test_same_line_message_is_context_but_sole_docstring_is_not() -> None:
    source = "def inspect():\n    return 'required message'\n"
    tree = ast.parse(source)
    assert witness_module._context_present(source, tree.body[0], 2)
    doc_source = 'def inspect():\n    """only documentation"""\n    return 1\n'
    doc_tree = ast.parse(doc_source)
    assert not witness_module._context_present(doc_source, doc_tree.body[0], 3)


def test_inventory_cap_is_independent_of_witness_floor() -> None:
    payload = make_body_corpus_payload()
    payload["files"][0]["content_utf8"] = "\n".join(
        f"def generated_{number}():\n    return {number}\n" for number in range(30)
    )
    with pytest.raises(BodyError, match="v1_pair_cap"):
        verify_body_witnesses(_catalog(), _corpus(payload))


def test_missing_witness_refuses_before_any_floor_claim() -> None:
    payload = make_body_catalog_payload()
    payload["cases"][0]["witnesses"].clear()
    with pytest.raises(BodyError, match="witness_required"):
        _catalog(payload)


def test_distractor_mutation_cannot_be_promoted_to_a_witness() -> None:
    payload = make_body_catalog_payload()
    payload["cases"][0]["distractors"][0]["witness"] = copy.deepcopy(payload["cases"][0]["witnesses"][0])
    with pytest.raises(BodyError, match="distractor_is_gold"):
        _catalog(payload)
