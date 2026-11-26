"""Body-class admission against real accepted SQLite/PostgreSQL publications."""

from __future__ import annotations

from copy import copy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from common_lore_index_test_support.postgres_harness import temporary_postgres
from common_lore_index_test_support.retrieval_body_fixtures import (
    make_body_bindings, make_body_catalog_payload, make_body_corpus_payload, make_body_identity,
    make_body_policy,
)
from common_lore_index_test_support.retrieval_body_pin_fixtures import (
    make_body_input_files as make_functional_input_files,
    materialize_body_inputs, materialize_body_predecessors,
)
from common_lore_index_test_support.retrieval_body_publication_fixture import BodyStoreFixture, materialize_body_home
from retrieval_body_admission import admit_body_task
from retrieval_body_admission_rules import evaluate_body_case
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_corpus import parse_body_corpus
from retrieval_body_values import ARMS, BodyError, canonical_bytes
from retrieval_body_pin import validate_functional_body_inputs
from retrieval_body_records import _build_query_records
from retrieval_semantic_generator_kernel_contracts import SemanticMode


def _setup(root: Path, *, postgres: Any = None) -> tuple[Any, Any, BodyStoreFixture]:
    identity, bindings, policy = make_body_identity(), make_body_bindings(), make_body_policy()
    catalog = parse_functional_catalog(
        canonical_bytes(make_body_catalog_payload()), identity=identity, binding_ids=tuple(bindings.values())
    )
    corpus = parse_body_corpus(
        canonical_bytes(make_body_corpus_payload()),
        identity=identity, bindings=bindings,
    )
    published = materialize_body_home(
        root / "publication", corpus=corpus, identity=identity, policy=policy,
        postgres=postgres, psql_command="psql" if postgres is None else postgres.psql_command,
    )
    return catalog, policy, BodyStoreFixture(
        published, psql_command="psql" if postgres is None else postgres.psql_command
    )


def _exercise(root: Path, *, postgres: Any = None) -> None:
    catalog, policy, store = _setup(root, postgres=postgres)
    backend = "sqlite" if postgres is None else "postgresql"
    case = next(item for item in catalog.cases if item.kind == "body_positive")
    rows = {
        arm: evaluate_body_case(catalog, case, arm=arm, policy=policy, frozen_policy=policy,
                                publication_pin=store.pin, observed_backend=backend)
        for arm in ARMS
    }
    assert rows[ARMS[0]]["execution_authority_granted"] is False
    assert rows[ARMS[4]]["preparation"] == "ready"
    assert rows[ARMS[1]]["reason"] == "captured_text_surface_unsupported_by_metadata_graph"
    assert rows[ARMS[3]]["reason"] == "metadata_representation_not_comparable"
    if backend == "sqlite":
        assert rows[ARMS[0]]["preparation"] == "ready"
        assert rows[ARMS[2]]["reason"] == "arm_backend_unavailable"
    else:
        assert rows[ARMS[0]]["reason"] == "arm_backend_unavailable"
        assert rows[ARMS[2]]["preparation"] == "ready"

    privacy = next(item for item in catalog.cases if item.kind == "privacy")
    for arm in ARMS:
        row = evaluate_body_case(catalog, privacy, arm=arm, policy=policy, frozen_policy=policy,
                                 publication_pin=store.pin, observed_backend=backend)
        assert row["preparation"] == "refused"
        assert row["reason"] == "binding_privacy_not_authorized"
        assert row["publication_revalidation_performed"] is False

    selected = case.task.selected_bindings[0]
    wrong = replace(policy, selected_binding_ids=frozenset({selected})).validate()
    wrong_row = evaluate_body_case(catalog, case, arm=ARMS[4], policy=wrong, frozen_policy=policy,
                                   publication_pin=store.pin, observed_backend=backend)
    assert wrong_row["reason"] == "binding_not_selected_by_policy"
    changed = replace(policy, max_projected_files=1).validate()
    with pytest.raises(BodyError, match="frozen_policy_drift"):
        evaluate_body_case(catalog, case, arm=ARMS[4], policy=changed, frozen_policy=policy,
                           publication_pin=store.pin, observed_backend=backend)
    with pytest.raises(BodyError, match="issued_publication_required"):
        evaluate_body_case(catalog, case, arm=ARMS[4], policy=policy, frozen_policy=policy,
                           publication_pin=copy(store.pin), observed_backend=backend)

    files = make_functional_input_files()
    functional_root = root / "functional-inputs"
    predecessor_root = root / "predecessors"
    materialize_body_inputs(functional_root, files)
    materialize_body_predecessors(predecessor_root)
    functional = validate_functional_body_inputs(
        files, identity=catalog.identity, policy=policy, predecessor_root=predecessor_root,
    )
    functional_catalog = functional.catalog
    entries = []
    for selected_case in (functional_catalog.cases[0], functional_catalog.cases[12]):
        for arm in (ARMS[4], ARMS[5]):
            row = evaluate_body_case(functional_catalog, selected_case, arm=arm, policy=policy,
                frozen_policy=functional.policy, publication_pin=store.pin, observed_backend=backend)
            row["input_pin_sha256"] = functional.pin_sha256
            entries.append((selected_case.task, row))
    ledger = _build_query_records(entries)
    assert len(ledger.records) == 2  # Fusion reuses exact-v2 records.
    tasks = {item.task.task_id: item.task for item in functional_catalog.cases}
    for record in ledger.records:
        assert record.semantic_text.text.encode("utf-8") == tasks[record.task_id].question.encode("utf-8")
        assert record.semantic_text.mode is SemanticMode.QUERY
        assert record.semantic_text.record_id == "query:" + record.task_id
        assert record.to_dict()["input_policy"] == "cloe-w5-public-generation-input-v1"
    assert ledger.to_dict()["execution_state"] == "not_executed"
    assert ledger.to_dict()["origin_proven"] is False
    with pytest.raises(BodyError, match="registered_factory_required"):
        admit_body_task(cast(Any, functional), case.task, arm=ARMS[4], policy=policy,
                        publication_pin=store.pin, observed_backend=backend)


def test_sqlite_body_publication_admission_boundaries(tmp_path: Path) -> None:
    _exercise(tmp_path)


def test_postgresql_body_publication_admission_boundaries(tmp_path: Path) -> None:
    with temporary_postgres() as database:
        _exercise(tmp_path, postgres=database)
