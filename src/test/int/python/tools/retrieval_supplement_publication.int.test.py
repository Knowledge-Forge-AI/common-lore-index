"""Real SQLite/PostgreSQL span and admission boundaries on unrelated functional data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from repomap_test_support.postgres_harness import temporary_postgres
from repomap_test_support.retrieval_supplement_fixtures import (
    make_test_bindings, make_test_catalog_payload, make_test_corpus_payload,
    make_test_identity, make_test_policy,
)
from repomap_test_support.retrieval_supplement_publication_fixture import (
    SupplementStoreFixture, publish_supplement_fixture, write_supplement_home,
)
from retrieval_evaluation_admission_w5 import execute_after_admission
from retrieval_public_text_contracts import PrivateMarkerScope, PublicTextRefusal
from retrieval_supplement_applicability import CandidateExposure, reachability_report
from retrieval_supplement_catalog import parse_catalog
from retrieval_supplement_corpus import materialize_corpus, parse_corpus
from retrieval_supplement_preparation import prepare_supplement
from retrieval_supplement_values import ARMS, canonical_bytes


def _owned_data(root: Path) -> tuple[Any, Any, Path]:
    identity = make_test_identity()
    bindings = make_test_bindings()
    catalog = parse_catalog(canonical_bytes(make_test_catalog_payload()), identity=identity, binding_ids=tuple(bindings.values()))
    corpus = parse_corpus(canonical_bytes(make_test_corpus_payload()), identity=identity, bindings=bindings)
    working_copy = root / "working-copy"
    working_copy.mkdir(mode=0o700)
    materialize_corpus(corpus, working_copy)
    return catalog, corpus, working_copy


def _exercise(store: SupplementStoreFixture, catalog: Any, corpus: Any, working_copy: Path) -> None:
    representations = store.representations()
    assert representations.status == "ok" and not representations.truncated
    scope = PrivateMarkerScope.create(("UNIT_SUPPLEMENT_PRIVATE_MARKER",))
    public_text = store.public_text(scope)
    assert public_text.documents
    span_map: dict[tuple[str, str, str], tuple[int | None, int | None]] = {}
    for row in store.spans():
        meta = json.loads(row.metadata_json) if isinstance(row.metadata_json, str) else row.metadata_json
        if not isinstance(meta, dict) or row.link_kind in {"inferred", "inferred_from_edge"}:
            continue
        if type(row.start_line) is int and type(row.end_line) is int and 1 <= row.start_line <= row.end_line:
            span_map.setdefault((row.canonical_key, meta["binding_id"], meta["source_relative_path"]),
                                (row.start_line, row.end_line))
    exposures: list[CandidateExposure] = []
    for entry in representations.verified_entries:
        for path in entry.source_relative_paths:
            span = span_map.get((entry.canonical_key, entry.source_binding_id, path), (None, None))
            for arm in (ARMS[1], ARMS[3]):
                exposures.append(CandidateExposure(arm, entry.source_binding_id, path, entry.canonical_key,
                                                    entry.kind, span[0], span[1]))
    body_arms = (ARMS[0], ARMS[2], ARMS[4], ARMS[5])
    for document in public_text.documents:
        descriptor = document.descriptor
        assert descriptor["source_relative_path"].split("/")[0] not in make_test_bindings()
        for arm in body_arms:
            exposures.append(CandidateExposure(
                arm, descriptor["source_binding_id"], descriptor["source_relative_path"],
                descriptor["canonical_key"], descriptor["kind"], descriptor["start_line"], descriptor["end_line"],
            ))
    report = reachability_report(catalog, exposures, store.policy)
    assert report["floor_met"] and report["common_positive_count"] >= 8
    assert all(count >= 2 for count in report["family_counts"].values())
    assert report["gap_count"] == 0 and report["native_qualified"] is False
    headings = [document.descriptor for document in public_text.documents if document.descriptor["kind"] == "doc.section"]
    assert len(headings) >= 2
    assert all(row["start_line"] == row["end_line"] == 1 for row in headings)
    assert any(row["source_relative_path"].startswith("tests/test_") for row in [doc.descriptor for doc in public_text.documents])
    assert not any(doc.descriptor["kind"] == "doc.page" for doc in public_text.documents)
    denied = {binding for binding, policy in store.policy.bindings.items() if policy.resolved_binding_privacy != "public-dev"}
    assert not denied.intersection(document.descriptor["source_binding_id"] for document in public_text.documents)
    prepared = prepare_supplement(catalog, corpus, policy=store.policy, exposures=exposures)
    assert prepared["queries_executed"] is False and prepared["model_workers_started"] == 0
    assert prepared["execution_authorized"] is False and prepared["native_qualified"] is False
    callbacks: list[str] = []
    for case in catalog.cases:
        if case.kind == "privacy":
            for arm in ARMS:
                decision, result = execute_after_admission(
                    case.task, policy=store.policy, publication_pin=store.pin,
                    observed_backend="captured_reference", execute=lambda _decision: callbacks.append(arm),
                )
                assert decision.state.value == "refused"
                assert decision.refusal_code == "binding_privacy_not_authorized" and result is None
    assert callbacks == []
    first = next(member for member in corpus.files if member.role == "code")
    with (working_copy / first.member_path).open("ab") as stream:
        stream.write(b"\n# functional generation advance\n")
    store.refresh()
    with pytest.raises(PublicTextRefusal):
        store.public_text(scope)


def test_real_sqlite_supplement_heading_test_path_and_privacy_boundaries(tmp_path: Path) -> None:
    catalog, corpus, working_copy = _owned_data(tmp_path)
    identity, bindings, policy = make_test_identity(), make_test_bindings(), make_test_policy()
    home = write_supplement_home(tmp_path / "home", working_copy, identity=identity, bindings=bindings, policy=policy)
    published = publish_supplement_fixture(home, identity=identity, policy=policy)
    _exercise(SupplementStoreFixture(published), catalog, corpus, working_copy)


def test_real_postgres_supplement_heading_test_path_and_privacy_boundaries(tmp_path: Path) -> None:
    catalog, corpus, working_copy = _owned_data(tmp_path)
    identity, bindings, policy = make_test_identity(), make_test_bindings(), make_test_policy()
    with temporary_postgres() as database:
        home = write_supplement_home(tmp_path / "home", working_copy, identity=identity, bindings=bindings,
                                     policy=policy, postgres=database)
        published = publish_supplement_fixture(home, identity=identity, policy=policy, psql_command=database.psql_command)
        _exercise(SupplementStoreFixture(published, psql_command=database.psql_command), catalog, corpus, working_copy)
