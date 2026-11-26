"""Real owned PostgreSQL proof over the unrelated synthetic canopy corpus."""
from __future__ import annotations

from dataclasses import replace
from functools import partial
import json
from pathlib import Path

from common_lore_index.ops.config_local import load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_service import build_graph_lexical_generation
from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from common_lore_index.retrieval.query_contracts import QueryEnvelope
from common_lore_index.storage.lexical_driver import LexicalStorageError
from common_lore_index_test_support.postgres_harness import temporary_postgres
from common_lore_index_test_support.retrieval_body_fixtures import (
    make_body_bindings, make_body_corpus_payload, make_body_identity, make_body_policy,
)
from common_lore_index_test_support.retrieval_body_publication_fixture import (
    BodyStoreFixture, publish_body_fixture, write_body_home,
)
from common_lore_index_test_support.retrieval_body_study_runtime_fixtures import functional_body_catalog
from retrieval_body_corpus import materialize_body_corpus, parse_body_corpus
from retrieval_body_values import canonical_bytes
from retrieval_body_catalog import BodyCase
from retrieval_lexical_replay import CANDIDATE, LEGACY, ReplayStore, query_without_identity
from retrieval_lexical_replay_execution import ReplayJournal, paired_replay, read_generation_census


def test_paired_query_ledger_and_failure_contract(tmp_path: Path) -> None:
    identity, bindings, policy = make_body_identity(), make_body_bindings(), make_body_policy()
    corpus = parse_body_corpus(canonical_bytes(make_body_corpus_payload()), identity=identity, bindings=bindings)
    public = {bindings["oak"], bindings["elm"]}
    expected = tuple(sorted((file.binding_id, file.source_relative_path)
                            for file in corpus.files if file.binding_id in public))
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    materialize_body_corpus(corpus, source)
    catalog = functional_body_catalog()
    by_kind = dict((case.kind, case) for case in reversed(catalog.cases))
    positive = next(case for case in catalog.cases if case.kind == "body_positive")
    private = next(case for case in catalog.cases if case.kind == "privacy")
    with temporary_postgres() as database:
        home = write_body_home(tmp_path / "home", source, identity=identity, bindings=bindings,
                              policy=policy, postgres=database)
        config_path = home / "body.cloe.toml"
        header, *blocks = config_path.read_text().split("[[graphs.source_bindings]]")
        config_path.write_text(header + "".join("[[graphs.source_bindings]]" + block for block in blocks
                                                if any(binding in block for binding in public)))
        fixture = BodyStoreFixture(publish_body_fixture(home, identity=identity, policy=policy,
                                                      psql_command=database.psql_command),
                                   psql_command=database.psql_command)
        config = load_graph_registry_config_home(home)
        assert isinstance(config, OpsConfig)
        pin = fixture.pin
        outcome = build_graph_lexical_generation(config, pin.graph_id,
                                                 authorize_body=True, selected_binding_ids=sorted(public))
        assert outcome.result == "published" and outcome.published is not None
        census = partial(read_generation_census, config, pin)
        service = partial(query_without_identity, config, pin.graph_id)
        store = ReplayStore(pin, policy, outcome.published.lexical_generation_id,
                            outcome.policy_digest, census, service)

        def replay(
            name: str, selected: ReplayStore, cases: tuple[BodyCase, ...] = (positive, private),
        ) -> dict[str, object]:
            directory = tmp_path / name
            directory.mkdir(mode=0o700)
            return paired_replay(cases, selected, expected_files=expected,
                                 treatment={"witness": "unrelated_canopy"},
                                 retain=ReplayJournal(directory).retain)

        result = replay("paired", store)
        assert result["outcome"] == "complete_descriptive", result
        assert result["query_calls_observed"] == 2
        rows = result["observations"]
        assert isinstance(rows, list) and len(rows) == 4
        assert [row["arm"] for row in rows] == [LEGACY, CANDIDATE, CANDIDATE, LEGACY]
        assert rows[0]["state"] == "available" and rows[0]["files"] == ()
        assert rows[1]["state"] == "partial" and rows[1]["omission_codes"] == ["result_page_bounded"]
        assert rows[1]["metrics"] is not None and rows[1]["files"]
        assert rows[1]["observed_identity"] == rows[0]["observed_identity"]
        assert all(row["policy_enforcement_layer"] == "evaluator_precheck" and not row["query_started"]
                   for row in rows[2:])
        frozen = json.loads((tmp_path / "paired" / "preflight.json").read_text())
        assert frozen["slots"][0]["request_sha256"] != frozen["slots"][1]["request_sha256"]
        assert all(row["request_sha256"] is None for row in frozen["slots"][2:])

        all_kinds = replay("all-kinds", store, tuple(by_kind.values()))
        assert all_kinds["outcome"] == "complete_descriptive", all_kinds
        assert all_kinds["query_calls_observed"] == 10
        controls = all_kinds["observations"]
        assert isinstance(controls, list) and {row["kind"] for row in controls} == set(by_kind)
        assert all(row["state"] in {"available", "partial"} for row in controls if row["kind"] != "privacy")
        assert all(len(row["gold_file_ranks"]) == 2 for row in controls if row["kind"] == "ambiguous")
        assert all(row["absence"] in {"correct", "incorrect", "unresolved"}
                   for row in controls if row["kind"] == "absent")
        assert all(row["fallback"] is not None for row in controls if row["kind"] == "unsupported")

        def missing_identity(request: LexicalQueryRequest) -> QueryEnvelope:
            actual = service(request)
            return replace(actual, scope=replace(actual.scope, publication_bundle_id=None))

        unobserved = replay("missing-identity", replace(store, query=missing_identity))
        assert unobserved["outcome"] == "incomplete_technical"
        assert unobserved["quality"] is None
        missing = unobserved["observations"]
        assert isinstance(missing, list) and missing[0]["reason"] == "missing_observed_generation"

        def interrupted(_request: LexicalQueryRequest) -> QueryEnvelope:
            assert (tmp_path / "interrupted" / "slot-01-started.json").is_file()
            raise KeyboardInterrupt

        interruption = replay("interrupted", replace(store, query=interrupted))
        assert interruption["outcome"] == "incomplete_technical"
        assert interruption["started_slots"] == 1 and interruption["observed_slots"] == 0
        assert not (tmp_path / "interrupted" / "slot-01-observed.json").exists()

        def timeout(_request: LexicalQueryRequest) -> QueryEnvelope:
            raise LexicalStorageError("lexical_statement_timeout", "synthetic timed-out slot")

        timed_out = replay("timeout", replace(store, query=timeout))
        assert timed_out["outcome"] == "incomplete_technical" and timed_out["query_calls_observed"] == 2
        assert timed_out["quality"] is None

        def stale(request: LexicalQueryRequest) -> QueryEnvelope:
            actual = service(request)
            return replace(actual, provenance=dict(actual.provenance, lexical_generation_id="lexgen1:" + "0" * 64))

        rejected = replay("stale", replace(store, query=stale))
        assert rejected["outcome"] == "inadmissible"
        assert rejected["reason"] == "observation_generation_drift"
        assert rejected["query_calls_observed"] == 1
