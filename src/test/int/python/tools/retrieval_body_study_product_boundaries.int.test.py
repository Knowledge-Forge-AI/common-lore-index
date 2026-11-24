"""Real retrieval ambiguity, absence and published lexical-policy boundaries."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from common_lore_index.ops.config import OpsConfig
from common_lore_index.ops.config_local import load_graph_registry_config_home
from common_lore_index.ops.lexical_service import (
    build_graph_lexical_generation,
    query_graph_lexical_generation,
)
from common_lore_index.retrieval.context_assembly import build_compact_context
from common_lore_index.retrieval.context_contracts import ContextRequest
from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from common_lore_index.retrieval.projection_build import build_captured_text_projection
from common_lore_index.retrieval.projection_policy import ExclusionReason
from common_lore_index.retrieval.projection_query import (
    CapturedTextQueryRequest,
    query_captured_text,
)
from common_lore_index.retrieval.query_contracts import CapabilityState, QueryEnvelope
from repomap_test_support.context_assertions import (
    assert_valid_context_packet,
    build_postgres_context_fixture,
)
from repomap_test_support.context_fixture import (
    BINDING_CORE,
    CTX_GRAPH_ID,
    build_sqlite_context_fixture,
)
from repomap_test_support.postgres_harness import temporary_postgres
from repomap_test_support.retrieval_evaluation_fixture import (
    MIXED_V1_GRAPH_ID,
    binding_id_for_alias,
    materialize_mixed_v1_working_copy,
    write_mixed_v1_postgres_home,
    write_mixed_v1_sqlite_home,
)
from repomap_test_support.retrieval_evaluation_publication import (
    publish_and_load_postgres_corpus,
    publish_and_load_sqlite_corpus,
)


_ABSENT_STORED_QUERY = "W5_ABSENT_STORED_METADATA_7C0E"
_ABSENT_CAPTURED_QUERY = "W5_ABSENT_CAPTURED_TEXT_9F42"


def _ranked_context_request(query: str, *, max_items: int) -> ContextRequest:
    return ContextRequest(
        graph_id=CTX_GRAPH_ID,
        mode="ranked_discovery",
        query=query,
        intent="definition",
        bindings=(BINDING_CORE,),
        kinds=("python.class",),
        budget_bytes=16384,
        max_items=max_items,
    )


def test_context_real_backends_disclose_ambiguous_alpha_handler_budget(
    tmp_path: Path,
) -> None:
    """A real SQLite/PG ranked tie exposes its omitted top alternative."""
    _db, _cfg, _graph, _binding, sqlite_adapter = build_sqlite_context_fixture(
        tmp_path / "sqlite"
    )
    with temporary_postgres() as postgres:
        _home, postgres_adapter = build_postgres_context_fixture(
            tmp_path / "postgres", postgres
        )
        request = _ranked_context_request("AlphaHandler", max_items=1)
        with sqlite_adapter:
            sqlite_packet = build_compact_context(request, sqlite_adapter)
        with postgres_adapter:
            postgres_packet = build_compact_context(request, postgres_adapter)

    for packet in (sqlite_packet, postgres_packet):
        assert_valid_context_packet(packet)
        assert packet["status"] == "partial"
        assert packet["scope"]["scope_complete"] is False
        assert packet["ordering"]["ambiguity"] == {
            "status": "ambiguous",
            "tied_top_count": 2,
        }
        assert len(packet["items"]) == 1
        assert packet["items"][0]["canonical_key"] in {
            "py:pkg.ambig.AlphaHandler",
            "py:pkg.ambig2.AlphaHandler",
        }
        assert packet["omission_counts"]["item_limit"] == 1
        assert packet["omission_counts"]["ambiguous_alternative_omitted_by_budget"] == 1

    assert [item["canonical_key"] for item in sqlite_packet["items"]] == [
        item["canonical_key"] for item in postgres_packet["items"]
    ]


def test_context_real_backends_establish_complete_stored_metadata_absence(
    tmp_path: Path,
) -> None:
    """A complete ranked scan distinguishes stored-metadata absence from partial scope."""
    _db, _cfg, _graph, _binding, sqlite_adapter = build_sqlite_context_fixture(
        tmp_path / "sqlite"
    )
    with temporary_postgres() as postgres:
        _home, postgres_adapter = build_postgres_context_fixture(
            tmp_path / "postgres", postgres
        )
        request = _ranked_context_request(_ABSENT_STORED_QUERY, max_items=3)
        with sqlite_adapter:
            sqlite_packet = build_compact_context(request, sqlite_adapter)
        with postgres_adapter:
            postgres_packet = build_compact_context(request, postgres_adapter)

    for packet in (sqlite_packet, postgres_packet):
        assert_valid_context_packet(packet)
        assert packet["status"] == "complete"
        assert packet["scope"]["scope_complete"] is True
        assert packet["absence_basis"] == (
            "stored_direct_locatable_metadata_fields_in_selected_scope"
        )
        assert packet["items"] == []
        assert packet["neighbors"] == []
        assert packet["relations"] == []
        assert packet["omissions"] == []
        assert all(value == 0 for value in packet["omission_counts"].values())


def _assert_complete_captured_text_absence(envelope: QueryEnvelope) -> None:
    assert envelope.capability_state == CapabilityState.AVAILABLE
    assert envelope.hits == ()
    assert envelope.omissions == ()
    assert envelope.scope_complete is True
    assert envelope.establishes_absence() is True


def test_captured_text_real_sqlite_and_postgres_establish_complete_absence(
    tmp_path: Path,
) -> None:
    """A full retained-text projection qualifies an empty result as true absence."""
    working_sqlite = materialize_mixed_v1_working_copy(tmp_path / "sqlite-working")
    sqlite_state = publish_and_load_sqlite_corpus(
        write_mixed_v1_sqlite_home(tmp_path / "sqlite-home", working_sqlite)
    )
    sqlite_projection = build_captured_text_projection(
        sqlite_state.projection_inputs(), sqlite_state.policy
    )
    sqlite_envelope = query_captured_text(
        sqlite_projection,
        CapturedTextQueryRequest(
            query=_ABSENT_CAPTURED_QUERY,
            selected_binding_ids=(binding_id_for_alias("core"),),
            limit=20,
        ),
        current_accepted_reference=sqlite_state.accepted_reference,
    )
    _assert_complete_captured_text_absence(sqlite_envelope)

    with temporary_postgres() as postgres:
        working_postgres = materialize_mixed_v1_working_copy(
            tmp_path / "postgres-working"
        )
        postgres_home = write_mixed_v1_postgres_home(
            tmp_path / "postgres-home",
            working_postgres,
            host=postgres.host,
            port=postgres.port,
            database=postgres.database,
            user=postgres.user,
            password=postgres.password,
        )
        postgres_state = publish_and_load_postgres_corpus(
            postgres_home, psql_command=postgres.psql_command
        )
        postgres_projection = build_captured_text_projection(
            postgres_state.projection_inputs(), postgres_state.policy
        )
        postgres_envelope = query_captured_text(
            postgres_projection,
            CapturedTextQueryRequest(
                query=_ABSENT_CAPTURED_QUERY,
                selected_binding_ids=(binding_id_for_alias("core"),),
                limit=20,
            ),
            current_accepted_reference=postgres_state.accepted_reference,
        )
        _assert_complete_captured_text_absence(postgres_envelope)


def _public_postgres_fixture(
    tmp_path: Path, postgres: Any
) -> tuple[Path, OpsConfig, str]:
    working_copy = materialize_mixed_v1_working_copy(tmp_path / "working-copy")
    home = write_mixed_v1_postgres_home(
        tmp_path / "postgres-home",
        working_copy,
        host=postgres.host,
        port=postgres.port,
        database=postgres.database,
        user=postgres.user,
        password=postgres.password,
        declared_aliases=("core", "companion"),
    )
    publish_and_load_postgres_corpus(home, psql_command=postgres.psql_command)
    config = load_graph_registry_config_home(home)
    assert isinstance(config, OpsConfig)
    return home, config, binding_id_for_alias("core")


def _rewrite_binding_block(
    text: str,
    alias: str,
    *,
    revision: int | None = None,
    enabled: bool | None = None,
) -> str:
    header, *blocks = text.split("[[graphs.source_bindings]]")
    for index, block in enumerate(blocks):
        if f'alias = "{alias}"' not in block:
            continue
        if revision is not None:
            block, count = re.subn(
                r"(?m)^revision = \d+$", f"revision = {revision}", block, count=1
            )
            assert count == 1
        if enabled is not None:
            value = "true" if enabled else "false"
            block, count = re.subn(
                r"(?m)^enabled = (?:true|false)$", f"enabled = {value}", block, count=1
            )
            assert count == 1
        blocks[index] = block
        return "[[graphs.source_bindings]]".join((header, *blocks))
    raise AssertionError(f"binding block not found: {alias}")


def test_real_pg_lexical_revision_and_enabled_drift_are_explicitly_refused(
    tmp_path: Path,
) -> None:
    """A published PG lexical generation detects revision drift and disabled bindings."""
    with temporary_postgres() as postgres:
        home, config, core_id = _public_postgres_fixture(tmp_path, postgres)
        built = build_graph_lexical_generation(
            config,
            MIXED_V1_GRAPH_ID,
            selected_binding_ids=(core_id,),
            authorize_body=True,
        )
        assert built.result == "published"
        assert built.published is not None

        request = LexicalQueryRequest(
            query="RetrievalExecutionPipeline",
            selected_binding_ids=(core_id,),
        )
        initial = query_graph_lexical_generation(
            config, MIXED_V1_GRAPH_ID, request, authorize_body=True
        )
        assert initial.capability_state == CapabilityState.AVAILABLE
        assert any(
            hit.locator.source_relative_path == "pkg/pipeline.py" for hit in initial.hits
        )

        config_file = home / "repomap.rpl.toml"
        original = config_file.read_text(encoding="utf-8")
        try:
            config_file.write_text(
                _rewrite_binding_block(original, "core", revision=2),
                encoding="utf-8",
            )
            revision_drift = query_graph_lexical_generation(
                config, MIXED_V1_GRAPH_ID, request, authorize_body=True
            )
            assert revision_drift.capability_state == CapabilityState.STALE
            assert revision_drift.provenance["stale_detail"] == "stale_policy_digest_mismatch"
        finally:
            config_file.write_text(original, encoding="utf-8")

        try:
            config_file.write_text(
                _rewrite_binding_block(original, "core", enabled=False),
                encoding="utf-8",
            )
            disabled = query_graph_lexical_generation(
                config, MIXED_V1_GRAPH_ID, request, authorize_body=True
            )
            assert disabled.capability_state == CapabilityState.REFUSED
            assert disabled.refusal_code == ExclusionReason.BINDING_NOT_SELECTED.value
        finally:
            config_file.write_text(original, encoding="utf-8")
