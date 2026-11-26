"""Real PostgreSQL lexical and published captured-text pagination proof."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from common_lore_index.ops.config_local import load_graph_registry_config_home
from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_service import (
    build_graph_lexical_generation,
    query_graph_lexical_generation,
)
from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from common_lore_index.retrieval import projection_query
from common_lore_index.retrieval.projection_build import build_captured_text_projection
from common_lore_index.retrieval.projection_chunks import build_file_chunks
from common_lore_index.retrieval.projection_evidence import index_bundle_evidence
from common_lore_index.retrieval.projection_query import CapturedTextQueryRequest, query_captured_text
from common_lore_index.retrieval.query_contracts import CapabilityState
from common_lore_index_test_support.postgres_harness import temporary_postgres
from common_lore_index_test_support.retrieval_evaluation_fixture import (
    MIXED_V1_GRAPH_ID,
    binding_id_for_alias,
    materialize_mixed_v1_working_copy,
    write_mixed_v1_postgres_home,
    write_mixed_v1_sqlite_home,
)
from common_lore_index_test_support.retrieval_evaluation_publication import (
    publish_and_load_postgres_corpus,
    publish_and_load_sqlite_corpus,
)


def _public_core_fixture(tmp_path: Path, pg_db: Any) -> tuple[OpsConfig, str]:
    """Publish the maintained all-public fixture and build a real core generation."""
    working_copy = materialize_mixed_v1_working_copy(tmp_path / "working-copy")
    home = write_mixed_v1_postgres_home(
        tmp_path / "postgres-home",
        working_copy,
        host=pg_db.host,
        port=pg_db.port,
        database=pg_db.database,
        user=pg_db.user,
        password=pg_db.password,
        declared_aliases=("core", "companion"),
    )
    publish_and_load_postgres_corpus(home, psql_command=pg_db.psql_command)
    config = load_graph_registry_config_home(home)
    assert isinstance(config, OpsConfig)
    core_id = binding_id_for_alias("core")
    built = build_graph_lexical_generation(
        config,
        MIXED_V1_GRAPH_ID,
        selected_binding_ids=(core_id,),
        authorize_body=True,
    )
    assert built.result == "published"
    assert built.published is not None
    return config, core_id


def test_real_literal_pagination_order_and_occurrence_cap(tmp_path: Path) -> None:
    """Real literal pages preserve order and disclose per-file occurrence caps."""
    with temporary_postgres() as pg_db:
        config, core_id = _public_core_fixture(tmp_path, pg_db)
        full = query_graph_lexical_generation(
            config,
            MIXED_V1_GRAPH_ID,
            LexicalQueryRequest(
                query="def",
                query_style="literal_captured_text",
                selected_binding_ids=(core_id,),
                limit=100,
            ),
            authorize_body=True,
        )
        assert full.capability_state == CapabilityState.AVAILABLE
        assert full.scope_complete is True
        assert len(full.hits) >= 2
        assert not any(omission.reason_code == "result_page_bounded" for omission in full.omissions)
        full_locators = [hit.locator.identity_key() for hit in full.hits]

        exact_cap = max(Counter(hit.locator.source_relative_path for hit in full.hits).values())
        exact = query_graph_lexical_generation(
            config,
            MIXED_V1_GRAPH_ID,
            LexicalQueryRequest(
                query="def", query_style="literal_captured_text", selected_binding_ids=(core_id,), limit=100,
            ),
            authorize_body=True,
            max_occurrences_per_file=exact_cap,
        )
        assert exact.capability_state == CapabilityState.AVAILABLE
        assert [hit.locator.identity_key() for hit in exact.hits] == full_locators
        assert not any(item.reason_code == "literal_occurrence_cap_exceeded" for item in exact.omissions)

        page = query_graph_lexical_generation(
            config,
            MIXED_V1_GRAPH_ID,
            LexicalQueryRequest(
                query="def",
                query_style="literal_captured_text",
                selected_binding_ids=(core_id,),
                limit=1,
                offset=1,
            ),
            authorize_body=True,
        )
        assert page.capability_state == CapabilityState.PARTIAL
        assert page.scope_complete is False
        assert len(page.hits) == 1
        assert page.hits[0].rank == 1
        assert full.hits[0].rank == 1 and full.hits[1].rank == 2
        assert page.hits[0].locator.identity_key() == full_locators[1]
        assert any(omission.reason_code == "result_page_bounded" for omission in page.omissions)

        capped = query_graph_lexical_generation(
            config,
            MIXED_V1_GRAPH_ID,
            LexicalQueryRequest(
                query="def",
                query_style="literal_captured_text",
                selected_binding_ids=(core_id,),
                limit=100,
            ),
            authorize_body=True,
            max_occurrences_per_file=1,
        )
        assert capped.capability_state == CapabilityState.PARTIAL
        assert capped.scope_complete is False
        cap_omissions = [
            omission
            for omission in capped.omissions
            if omission.reason_code == "literal_occurrence_cap_exceeded"
        ]
        assert cap_omissions
        assert any(omission.source_relative_path == "pkg/pipeline.py" for omission in cap_omissions)
        assert capped.hits
        per_file_counts = Counter(hit.locator.source_relative_path for hit in capped.hits)
        assert all(count <= 1 for count in per_file_counts.values())


def test_published_literal_page_retains_cross_chunk_unicode_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    working_copy = materialize_mixed_v1_working_copy(tmp_path / "working-copy")
    state = publish_and_load_sqlite_corpus(write_mixed_v1_sqlite_home(tmp_path / "home", working_copy))
    inputs = state.projection_inputs()
    projection = build_captured_text_projection(inputs, state.policy)
    evidence = index_bundle_evidence(inputs.bundle)
    projected_evidence = {
        (record.binding_id, record.source_relative_path): record.evidence_links
        for record in projection.projected_files
        if record.evidence_links
    }
    assert projected_evidence
    admitted = {(record.binding_id, record.source_relative_path) for record in projection.projected_files}
    assert projected_evidence == {key: links for key, links in evidence.items() if key in admitted}
    ascii_file, = (record for record in projection.projected_files
                   if record.source_relative_path == "generated/truncatable_prose.md")
    source = (working_copy / "companion" / ascii_file.source_relative_path).read_bytes()
    assert source.isascii() and b"\r" not in source
    assert ascii_file.truncated
    assert (ascii_file.normalized_byte_length, ascii_file.normalized_line_count) == (len(source), source.count(b"\n"))
    assert b"".join(chunk.content_text.encode("utf-8") for chunk in ascii_file.chunks) == source[:ascii_file.projected_byte_length]
    policy = replace(state.policy, max_projected_file_bytes=38)
    bounded = build_captured_text_projection(inputs, policy)
    wide_path = "generated/long_line_notes.md"
    short_note, = (record for record in bounded.projected_files if record.source_relative_path == wide_path)
    source_bytes = (working_copy / "companion" / wide_path).read_bytes()
    assert short_note.truncated and source_bytes.decode("utf-8")
    expected_chunks = build_file_chunks(
        file_projection_id=short_note.file_projection_id, binding_id=short_note.binding_id, source_relative_path=wide_path,
        normalized_text=source_bytes[:38].decode("utf-8", errors="ignore"), artifact_privacy=short_note.artifact_privacy,
        policy_digest=bounded.policy_digest, publication_bundle_id=bounded.publication_bundle_id,
        max_chunk_lines=policy.max_chunk_lines, max_chunk_bytes=policy.max_chunk_bytes,
    )
    assert short_note.chunks == expected_chunks
    note, = (item for item in projection.projected_files
             if item.source_relative_path == "generated/long_line_notes.md")
    left, right = note.chunks[1:3]
    needle = left.content_text[-8:] + right.content_text[:8]
    assert not needle.isascii()
    text = "".join(chunk.content_text for chunk in note.chunks)
    start = len(note.chunks[0].content_text) + len(left.content_text) - 8
    starts = [index for index in range(len(text)) if text.startswith(needle, index)]
    envelope = query_captured_text(projection, CapturedTextQueryRequest(
        query=needle, source_path_prefix=note.source_relative_path, offset=starts.index(start), limit=1,
    ))
    hit, = envelope.hits
    assert hit.chunk_id == left.chunk_id
    assert hit.locator.start_line == left.start_line == 2
    assert envelope.provenance["total_matches_before_paging"] == len(starts)

    lookups = 0
    first_link = projection_query.first_link_for_line_span
    request = CapturedTextQueryRequest(query=needle, source_path_prefix=note.source_relative_path, limit=8)
    expected = query_captured_text(projection, request)

    def observe(*args: Any) -> Any:
        nonlocal lookups
        lookups += 1
        return first_link(*args)

    monkeypatch.setattr(projection_query, "first_link_for_line_span", observe)
    repeated = query_captured_text(projection, request)
    assert repeated.hits == expected.hits
    assert repeated.provenance["total_matches_before_paging"] == len(starts)
    assert [item.locator.start_line for item in repeated.hits] == [2] * 8
    assert len(repeated.hits) == 8
    assert lookups == 1


def test_real_literal_document_budget_refuses_before_returning_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from common_lore_index.storage import lexical_query_sql

    with temporary_postgres() as pg_db:
        config, core_id = _public_core_fixture(tmp_path, pg_db)
        sql = lexical_query_sql.LITERAL_CAPTURED_TEXT_QUERY_SQL
        assert sql.count("LIMIT 4097") == sql.count("count(*) > 4096") == 1
        fixture_sql = sql.replace("LIMIT 4097", "LIMIT 2").replace("count(*) > 4096", "count(*) > 1")
        monkeypatch.setattr(lexical_query_sql, "LITERAL_CAPTURED_TEXT_QUERY_SQL", fixture_sql)
        request = LexicalQueryRequest(
            query="def", query_style="literal_captured_text", selected_binding_ids=(core_id,),
        )
        result = query_graph_lexical_generation(config, MIXED_V1_GRAPH_ID, request, authorize_body=True)
        assert result.capability_state == CapabilityState.PARTIAL and not result.hits
        assert result.provenance["total_matches_before_paging"] == 0
        assert any(item.reason_code == "literal_scan_budget_exceeded" for item in result.omissions)
