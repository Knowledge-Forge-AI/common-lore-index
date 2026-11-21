"""Prospective request competence using new synthetic developer witnesses."""

from dataclasses import replace
from pathlib import Path
from typing import NoReturn

from common_lore_index.ops.config_local import load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_service import (
    build_graph_lexical_generation,
    query_graph_lexical_generation,
)
from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from common_lore_index.retrieval.projection_policy import ExclusionReason
from common_lore_index.retrieval.query_contracts import CapabilityState, QueryEnvelope
from repomap_test_support.postgres_harness import temporary_postgres
from repomap_test_support.retrieval_evaluation_fixture import (
    MIXED_V1_BINDINGS,
    MIXED_V1_GRAPH_ID,
    binding_id_for_alias,
    materialize_mixed_v1_working_copy,
    write_mixed_v1_postgres_home,
)
from repomap_test_support.retrieval_evaluation_publication import publish_and_load_postgres_corpus
from retrieval_lexical_comparator import plan_lexical_comparator


def _fixture_documents(copy: Path) -> None:
    fixtures = {
        "core": {
            "sprout.txt": "heliotrope",
            "metal.txt": "copper",
            "relay.txt": "relay queue marker",
            "exact.txt": "EXACT.FRAME/17",
            "near.txt": "EXACT FRAME 17",
            "print.txt": "amber lithograph",
            "ink.txt": "amber pigment",
            **{f"batch-{index:02d}.txt": "garnet" for index in range(12)},
        },
        "companion": {"different-source.txt": "heliotrope"},
    }
    for alias, entries in fixtures.items():
        root = copy / alias / "competence"
        root.mkdir()
        for name, text in entries.items():
            (root / name).write_text(text + "\n", encoding="utf-8")


def _paths(envelope: QueryEnvelope) -> set[str]:
    return {hit.locator.source_relative_path for hit in envelope.hits}


def _should_not_connect(*_args: object, **_kwargs: object) -> NoReturn:
    raise AssertionError("privacy refusal must precede opening a connection")


def test_comparator_uses_fenced_search_and_retains_capability_states(tmp_path: Path) -> None:
    core = binding_id_for_alias("core")
    companion = binding_id_for_alias("companion")
    raw = LexicalQueryRequest(
        "Where are heliotrope and copper?",
        selected_binding_ids=(core,),
        source_path_prefix="competence",
        limit=10,
    )
    question = plan_lexical_comparator(raw, shape="question")
    with temporary_postgres() as pg:
        copy = materialize_mixed_v1_working_copy(tmp_path / "working-copy")
        _fixture_documents(copy)
        def write_home(name: str, aliases: tuple[str, ...]) -> Path:
            return write_mixed_v1_postgres_home(
                tmp_path / name, copy,
                host=pg.host, port=pg.port, database=pg.database, user=pg.user, password=pg.password,
                declared_aliases=aliases,
            )

        home = write_home("public-home", ("core", "companion"))
        config = load_graph_registry_config_home(home)
        assert isinstance(config, OpsConfig)
        private_home = write_home("private-home", MIXED_V1_BINDINGS)
        private = load_graph_registry_config_home(private_home)
        assert isinstance(private, OpsConfig)
        refusal = query_graph_lexical_generation(
            private, MIXED_V1_GRAPH_ID, question,
            authorize_body=True, connection_factory=_should_not_connect,
        )
        assert refusal.capability_state is CapabilityState.REFUSED
        assert refusal.refusal_code == ExclusionReason.GRAPH_PRIVACY_NOT_AUTHORIZED.value
        assert refusal.hits == ()

        publish_and_load_postgres_corpus(home, psql_command=pg.psql_command)
        unavailable = query_graph_lexical_generation(
            config, MIXED_V1_GRAPH_ID, question, authorize_body=True,
        )
        assert unavailable.capability_state is CapabilityState.UNAVAILABLE
        assert unavailable.unavailable_reason == "projection_absent"
        assert unavailable.hits == ()

        build = build_graph_lexical_generation(
            config, MIXED_V1_GRAPH_ID, authorize_body=True, selected_binding_ids=(core, companion),
        )
        assert build.result == "published" and build.published is not None

        def query(request: LexicalQueryRequest) -> QueryEnvelope:
            return query_graph_lexical_generation(
                config, MIXED_V1_GRAPH_ID, request, authorize_body=True,
            )

        unchanged = query(raw)
        assert unchanged.capability_state is CapabilityState.AVAILABLE
        assert unchanged.hits == ()
        result = query(question)
        assert result.capability_state is CapabilityState.AVAILABLE
        assert _paths(result) == {"competence/sprout.txt", "competence/metal.txt"}
        assert all(hit.locator.binding_id == core for hit in result.hits)
        assert result.scope.publication_bundle_id == unchanged.scope.publication_bundle_id
        assert result.scope.snapshot_manifest_id == unchanged.scope.snapshot_manifest_id
        assert result.scope.publication_bundle_id is not None

        identifier = plan_lexical_comparator(replace(raw, query="RelayQueueMarker"), shape="identifier_or_path")
        assert _paths(query(identifier)) == {"competence/relay.txt"}
        literal = plan_lexical_comparator(
            replace(raw, query="EXACT.FRAME/17", query_style="literal_captured_text"), shape="literal",
        )
        assert _paths(query(literal)) == {"competence/exact.txt"}

        empty = plan_lexical_comparator(replace(raw, query="Is there a zirconium?"), shape="question")
        absent = query(empty)
        assert absent.capability_state is CapabilityState.AVAILABLE
        assert absent.scope_complete and absent.hits == ()

        ambiguous = plan_lexical_comparator(replace(raw, query="Where is amber?"), shape="question")
        first = query(ambiguous)
        assert _paths(first) == {"competence/print.txt", "competence/ink.txt"}
        assert tuple(hit.locator for hit in first.hits) == tuple(hit.locator for hit in query(ambiguous).hits)

        bounded = plan_lexical_comparator(replace(raw, query="Where is garnet?"), shape="question")
        surplus = query(bounded)
        assert len(surplus.hits) == 10
        assert surplus.capability_state is CapabilityState.PARTIAL
        assert not surplus.scope_complete
        assert any(omission.reason_code == "result_page_bounded" for omission in surplus.omissions)
