"""Projection matching, absence, privacy and generation behavior."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from common_lore_index.retrieval import projection_evidence, projection_query
from common_lore_index.retrieval.projection_build import build_captured_text_projection
from common_lore_index.retrieval.projection_chunks import build_file_chunks
from common_lore_index.retrieval.projection_evidence import (
    ProjectedEvidenceLink,
    first_link_for_line_span,
    index_bundle_evidence,
    links_for_line_span,
)
from common_lore_index.retrieval.projection_policy import ExclusionReason
from common_lore_index.retrieval.projection_query import CapturedTextQueryRequest, _find_chunked_matches, query_captured_text
from common_lore_index.retrieval.query_contracts import CapabilityState
from repomap_test_support.retrieval_evaluation_query_fixture import (
    build_fixture_projection as _build_fixture_projection,
)


def test_cross_chunk_literal_found_and_cross_file_boundary_never_joined(tmp_path: Path) -> None:
    """Literals spanning adjacent chunks of one file match; literals across files never match."""
    inputs, policy = _build_fixture_projection(spool_dir=tmp_path / "spool")
    projection = build_captured_text_projection(inputs, policy)
    assert len(projection.projected_files) == 2
    assert len(projection.excluded_files) == 1
    assert projection.excluded_files[0].store_opened is False
    assert projection.excluded_files[0].reason == ExclusionReason.EMAIL_BODY_FORBIDDEN

    span_query = "line 080: BOUNDARY_START_TOKEN\nline 081: BOUNDARY_END_TOKEN"
    env = query_captured_text(
        projection,
        CapturedTextQueryRequest(query=span_query),
        current_accepted_reference=inputs.accepted_reference,
    )
    assert env.capability_state == CapabilityState.AVAILABLE
    assert env.scope_complete is True
    assert len(env.hits) == 1
    hit = env.hits[0]
    assert hit.locator.source_relative_path == "pkg/alpha.py"
    assert hit.locator.start_line == 80 and hit.locator.end_line == 81
    assert hit.locator.canonical_key == "python.function:pkg.alpha:boundary_fn"
    assert hit.locator.link_kind == "defined_by"

    cross_file = "line 099: tail code\nSECOND_FILE_HEAD"
    no_hit = query_captured_text(
        projection,
        CapturedTextQueryRequest(query=cross_file),
        current_accepted_reference=inputs.accepted_reference,
    )
    assert no_hit.capability_state == CapabilityState.AVAILABLE
    assert no_hit.hits == ()
    assert no_hit.establishes_absence() is True


def test_private_scope_refusal_mixed_scope_partial_and_stale_advancement(tmp_path: Path) -> None:
    """Querying forbidden scope refuses without leaking canaries; mixed scope is partial; advanced store is stale."""
    inputs, policy = _build_fixture_projection(spool_dir=tmp_path / "spool")
    projection = build_captured_text_projection(inputs, policy)
    b_core_id = inputs.manifest.bindings[0].binding_id
    b_mail_id = inputs.manifest.bindings[1].binding_id

    refused = query_captured_text(
        projection,
        CapturedTextQueryRequest(
            query="CANARY_PRIVATE_BODY_DO_NOT_READ_991",
            selected_binding_ids=(b_mail_id,),
        ),
    )
    assert refused.capability_state == CapabilityState.REFUSED
    assert refused.hits == ()
    assert "CANARY_PRIVATE_BODY_DO_NOT_READ_991" not in str(refused.to_dict())

    mixed = query_captured_text(
        projection,
        CapturedTextQueryRequest(
            query="BOUNDARY_START_TOKEN",
            selected_binding_ids=(b_core_id, b_mail_id),
        ),
    )
    assert mixed.capability_state == CapabilityState.PARTIAL
    assert mixed.scope_complete is False
    assert len(mixed.hits) == 1
    assert any(o.binding_id == b_mail_id for o in mixed.omissions)

    advanced = replace(inputs.accepted_reference, accepted_generation=2, run_id=2)
    stale = query_captured_text(
        projection,
        CapturedTextQueryRequest(query="BOUNDARY_START_TOKEN"),
        current_accepted_reference=advanced,
    )
    assert stale.capability_state == CapabilityState.STALE
    assert stale.hits == ()


def test_direct_projection_explicit_fallback_preserves_requested_and_actual_modes(tmp_path: Path) -> None:
    from common_lore_index.retrieval.query_contracts import FallbackDisclosure, QueryMode
    inputs, policy = _build_fixture_projection(spool_dir=tmp_path / "spool")
    projection = build_captured_text_projection(inputs, policy)
    fallback = FallbackDisclosure(
        caller_selected=True, from_mode=QueryMode.RANKED_DISCOVERY,
        to_mode=QueryMode.LITERAL_CAPTURED_TEXT, reason_code="caller_selected_reference_scan",
    )
    request = CapturedTextQueryRequest(query="BOUNDARY_START_TOKEN", fallback=fallback)
    envelope = query_captured_text(projection, request, current_accepted_reference=inputs.accepted_reference)
    assert envelope.hits and envelope.requested_mode == QueryMode.RANKED_DISCOVERY
    assert envelope.effective_mode == QueryMode.LITERAL_CAPTURED_TEXT
    assert envelope.fallback == fallback
    advanced = replace(inputs.accepted_reference, run_id=2, accepted_generation=2)
    stale = query_captured_text(projection, request, current_accepted_reference=advanced)
    assert stale.capability_state == CapabilityState.STALE and not stale.hits
    assert stale.requested_mode == QueryMode.RANKED_DISCOVERY


def test_direct_projection_rejects_fallback_to_an_unimplemented_mode() -> None:
    import pytest
    from common_lore_index.retrieval.query_contracts import FallbackDisclosure, QueryMode
    request = CapturedTextQueryRequest(query="token", fallback=FallbackDisclosure(
        caller_selected=True, from_mode=QueryMode.RANKED_DISCOVERY,
        to_mode=QueryMode.SEMANTIC_DISCOVERY, reason_code="unsupported_target",
    ))
    with pytest.raises(ValueError, match="literal_captured_text"):
        request.validate()


def test_short_literal_lookahead_keeps_overlapping_starts_across_many_chunks() -> None:
    text = ("éx\néx\néx") * 18
    needle = "éx\néx\néx"
    chunks = build_file_chunks(
        file_projection_id="fixture", binding_id="binding", source_relative_path="fixture.md",
        normalized_text=text, artifact_privacy="public-dev", policy_digest="policy",
        publication_bundle_id="bundle", max_chunk_bytes=4,
    )
    expected = [index for index in range(len(text)) if text.startswith(needle, index)]
    hits, total = _find_chunked_matches(chunks, needle, max_materialize=3, skip_matches=2)
    assert total == len(expected)
    assert [hit[2] for hit in hits] == expected[2:5]
    assert [hit[0] for hit in hits] == [1 + text.count("\n", 0, index) for index in expected[2:5]]


def test_first_evidence_link_stops_at_the_first_in_order() -> None:
    selected = ProjectedEvidenceLink(
        "evidence:expected", "node:fixture", "supports", "document",
        "binding:fixture", "notes.md", 5, 7, "extracted",
    )

    def links() -> Iterator[ProjectedEvidenceLink]:
        yield replace(selected, start_line=None)
        yield replace(selected, start_line=1, end_line=2)
        yield selected
        raise AssertionError("the query read beyond its first matching evidence")

    assert first_link_for_line_span(links(), 6, 8) is selected
    single_line = replace(selected, start_line=8, end_line=None)
    candidates = (selected, single_line)
    for start, end, expected in ((4, 5, selected), (7, 8, selected), (8, 8, single_line), (9, 9, None)):
        assert first_link_for_line_span(candidates, start, end) is expected
        assert next(iter(links_for_line_span(candidates, start, end)), None) is expected


def test_query_reuses_evidence_only_within_the_same_file_and_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs, policy = _build_fixture_projection(spool_dir=tmp_path / "spool")
    projection = build_captured_text_projection(inputs, policy)
    first, second = projection.projected_files
    a = ProjectedEvidenceLink(
        "evidence:a", "node:a", "supports", "document", first.binding_id, first.source_relative_path, 1, None,
        "extracted",
    )
    b = replace(a, evidence_key="evidence:b", canonical_node_key="node:b", start_line=2)
    c = replace(b, evidence_key="evidence:c", canonical_node_key="node:c", source_relative_path=second.source_relative_path)
    files = []
    for record, body, links in ((first, "marker marker\nmarker", (a, b)), (second, "\nmarker", (c,))):
        chunk = record.chunks[0]
        chunks = build_file_chunks(
            file_projection_id=chunk.file_projection_id, binding_id=record.binding_id,
            source_relative_path=record.source_relative_path, normalized_text=body,
            artifact_privacy=chunk.artifact_privacy, policy_digest=projection.policy_digest,
            publication_bundle_id=record.publication_bundle_id, max_chunk_bytes=100,
        )
        files.append(replace(record, chunks=chunks, evidence_links=links))
    observed: list[tuple[str, int, int]] = []

    def lookup(
        links: tuple[ProjectedEvidenceLink, ...], start: int, end: int,
    ) -> ProjectedEvidenceLink | None:
        observed.append((links[0].source_relative_path, start, end))
        return first_link_for_line_span(links, start, end)

    monkeypatch.setattr(projection_query, "first_link_for_line_span", lookup)
    result = query_captured_text(
        replace(projection, projected_files=tuple(files)), CapturedTextQueryRequest(query="marker"),
    )
    assert [(hit.locator.evidence_key, hit.locator.canonical_key) for hit in result.hits] == [
        ("evidence:a", "node:a"), ("evidence:a", "node:a"), ("evidence:b", "node:b"), ("evidence:c", "node:c"),
    ]
    assert observed == [(first.source_relative_path, 1, 1), (first.source_relative_path, 2, 2),
                        (second.source_relative_path, 2, 2)]


@pytest.mark.parametrize("excluded_key", ("ev1:excluded", "ev1:alpha80"))
def test_evidence_index_limits_records_and_links_to_admitted_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, excluded_key: str,
) -> None:
    inputs, _ = _build_fixture_projection(spool_dir=tmp_path / "spool")
    evidence_row, = inputs.bundle.families["canonical_evidence"]
    link_row, = inputs.bundle.families["canonical_node_evidence"]
    binding_id = inputs.manifest.bindings[0].binding_id
    families = dict(inputs.bundle.families)
    excluded_row = {
        **evidence_row,
        "evidence_key": excluded_key,
        "metadata_json": {"binding_id": binding_id, "source_relative_path": "pkg/excluded.py"},
    }
    families["canonical_evidence"] = (evidence_row, excluded_row)
    families["canonical_node_evidence"] = (link_row, {**link_row, "evidence_key": excluded_key})
    bundle = replace(inputs.bundle, families=families)
    key = (binding_id, "pkg/alpha.py")
    expected = index_bundle_evidence(bundle)
    assert set(expected) == {key, (binding_id, "pkg/excluded.py")}
    seen: list[str] = []

    def make_link(*args: Any, **kwargs: Any) -> ProjectedEvidenceLink:
        link = ProjectedEvidenceLink(*args, **kwargs)
        seen.append(link.evidence_key)
        return link

    monkeypatch.setattr(projection_evidence, "ProjectedEvidenceLink", make_link)
    assert index_bundle_evidence(bundle, file_keys={key}) == {key: expected[key]}
    assert not index_bundle_evidence(bundle, file_keys=set())
    expected_count = 2 if excluded_key == evidence_row["evidence_key"] else 1
    assert seen == ["ev1:alpha80"] * expected_count
