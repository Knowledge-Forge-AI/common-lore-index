"""Query execution over the in-memory captured-input text projection reference (S1)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType

from common_lore_index.retrieval.projection_build import (
    CapturedTextProjection,
    ProjectedFileRecord,
)
from common_lore_index.retrieval.projection_chunks import ProjectedChunk, frame_digest
from common_lore_index.retrieval.projection_evidence import ProjectedEvidenceLink, first_link_for_line_span
from common_lore_index.retrieval.publication_binding import AcceptedPublicationReference
from common_lore_index.retrieval.query_contracts import (
    CapabilityState,
    DEFAULT_ORDERING_POLICY_VERSION,
    EvaluationLocator,
    FallbackDisclosure,
    MatchSurface,
    OmissionRecord,
    QUERY_CONTRACT_VERSION,
    QueryEnvelope,
    QueryHit,
    QueryMode,
    QueryScope,
    UnavailableReason,
    refused_envelope,
    stale_envelope,
    unavailable_envelope,
)


@dataclass(frozen=True)
class CapturedTextQueryRequest:
    """Bounded literal captured-text query request."""

    query: str
    selected_binding_ids: tuple[str, ...] = ()
    source_path_prefix: str | None = None
    limit: int = 50
    offset: int = 0
    fallback: FallbackDisclosure | None = None

    def validate(self) -> CapturedTextQueryRequest:
        if not isinstance(self.query, str) or not self.query or "\x00" in self.query:
            raise ValueError("query must be a non-empty string without NUL")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1 or self.limit > 500:
            raise ValueError("limit must be an integer between 1 and 500")
        if isinstance(self.offset, bool) or not isinstance(self.offset, int) or self.offset < 0 or self.offset > 10000:
            raise ValueError("offset must be an integer between 0 and 10000")
        if self.fallback is not None:
            self.fallback.validate()
            if self.fallback.to_mode != QueryMode.LITERAL_CAPTURED_TEXT:
                raise ValueError("projection fallback target must be literal_captured_text")
        return self


def _resolve_current_reference(
    current: AcceptedPublicationReference | Callable[[], AcceptedPublicationReference | None] | None,
) -> tuple[bool, AcceptedPublicationReference | None]:
    if current is None:
        return False, None
    if callable(current):
        return True, current()
    return True, current


def _find_file_matches(
    file_rec: ProjectedFileRecord,
    needle: str,
    *,
    max_materialize: int | None = None,
    skip_matches: int = 0,
) -> tuple[list[tuple[int, int, int, str, str]], int]:
    """Find literal occurrences across contiguous chunks of one file without crossing files."""
    chunks = file_rec.chunks
    if not chunks or chunks[0].is_empty_file_record:
        return [], 0
    if len(needle) <= _CHUNK_LOOKAHEAD_LIMIT:
        return _find_chunked_matches(chunks, needle, max_materialize, skip_matches)
    # For wide needles, joining once avoids repeatedly copying their overlap.
    full_text = "".join(chunk.content_text for chunk in chunks)
    snippet_digest: str | None = None

    needle_len = len(needle)
    line_span = 0
    line = 1
    line_cursor = 0
    chunk_idx = 0
    chunk_end = len(chunks[0].content_text)

    matches: list[tuple[int, int, int, str, str]] = []
    total_count = 0
    start_pos = 0
    while True:
        page_full = max_materialize is not None and len(matches) >= max_materialize
        if page_full and needle_len == 1:
            # Single-character occurrences cannot overlap.
            total_count += full_text.count(needle, start_pos)
            break
        found = full_text.find(needle, start_pos)
        if found == -1:
            break
        total_count += 1
        if total_count > skip_matches and not page_full:
            line += full_text.count("\n", line_cursor, found)
            line_cursor = found
            while found >= chunk_end and chunk_idx + 1 < len(chunks):
                chunk_idx += 1
                chunk_end += len(chunks[chunk_idx].content_text)
            if snippet_digest is None:
                line_span = _matched_line_span(needle)
                snippet_digest = frame_digest("sha256", (needle,))
            matches.append((line, line + line_span, found, chunks[chunk_idx].chunk_id, snippet_digest))
        start_pos = found + 1
    return matches, total_count


_CHUNK_LOOKAHEAD_LIMIT = 128


def _matched_line_span(needle: str) -> int:
    # Preserve the reference locator convention, including trailing LF runs.
    last_character = len(needle) - 1
    end_line_offset = last_character - int(last_character > 0 and needle.endswith("\n"))
    return needle.count("\n", 0, end_line_offset)


def _match_window(
    chunks: tuple[ProjectedChunk, ...], index: int, start: int, overlap: int,
) -> str:
    parts = [chunks[index].content_text[start:]]
    for following_index in range(index + 1, len(chunks)):
        tail = chunks[following_index].content_text[:overlap]
        parts.append(tail)
        overlap -= len(tail)
        if overlap == 0:
            break
    return "".join(parts)


def _find_chunked_matches(
    chunks: tuple[ProjectedChunk, ...],
    needle: str,
    max_materialize: int | None,
    skip_matches: int,
) -> tuple[list[tuple[int, int, int, str, str]], int]:
    """Count overlapping literal starts in each chunk with a bounded lookahead."""
    matches: list[tuple[int, int, int, str, str]] = []
    total_count = 0
    char_base = 0
    start_line = 1
    digest: str | None = None
    overlap = len(needle) - 1
    line_span = _matched_line_span(needle)
    for index, chunk in enumerate(chunks):
        text = chunk.content_text
        boundary: str | None = None
        suffix_start = max(0, len(text) - overlap)
        pos = 0
        line = start_line
        line_cursor = 0
        while pos < len(text):
            page_full = max_materialize is not None and len(matches) >= max_materialize
            if page_full and overlap == 0:
                total_count += text.count(needle, pos)
                break
            found = text.find(needle, pos) if boundary is None else -1
            if found < 0 and overlap and index + 1 < len(chunks):
                if boundary is None:
                    boundary = _match_window(chunks, index, suffix_start, overlap)
                relative = boundary.find(needle, max(0, pos - suffix_start))
                if relative >= 0:
                    found = suffix_start + relative
            # A start in the lookahead belongs to its own chunk.
            if found < 0 or found >= len(text):
                break
            total_count += 1
            if total_count > skip_matches and not page_full:
                line += text.count("\n", line_cursor, found)
                line_cursor = found
                if digest is None:
                    digest = frame_digest("sha256", (needle,))
                matches.append((line, line + line_span, char_base + found, chunk.chunk_id, digest))
            pos = found + 1
        start_line += text.count("\n")
        char_base += len(text)
    return matches, total_count


def query_captured_text(
    projection: CapturedTextProjection,
    request: CapturedTextQueryRequest,
    *,
    current_accepted_reference: (
        AcceptedPublicationReference
        | Callable[[], AcceptedPublicationReference | None]
        | None
    ) = None,
) -> QueryEnvelope:
    """Execute a literal_captured_text query over a verified in-memory projection."""
    request.validate()
    requested_mode = request.fallback.from_mode if request.fallback is not None else QueryMode.LITERAL_CAPTURED_TEXT
    requested_bindings = (
        request.selected_binding_ids
        if request.selected_binding_ids
        else tuple(sorted(projection.policy.selected_binding_ids))
    )
    scope = QueryScope(
        graph_id=projection.accepted_reference.graph_id,
        backend="captured_reference",
        publication_bundle_id=projection.publication_bundle_id,
        snapshot_manifest_id=projection.snapshot_manifest_id,
        selected_binding_ids=requested_bindings,
        source_path_prefix=request.source_path_prefix,
    ).validate()

    checked_current, actual_current = _resolve_current_reference(current_accepted_reference)
    if checked_current and not projection.accepted_reference.matches_current(actual_current):
        return stale_envelope(
            requested_mode=requested_mode,
            scope=scope,
            detail="accepted publication advanced or disappeared before query completion",
        )
    if not requested_bindings:
        return unavailable_envelope(
            requested_mode=requested_mode,
            scope=scope,
            reason=UnavailableReason.PROJECTION_ABSENT.value,
            provenance={"projection_digest": projection.projection_digest},
        )

    req_binding_set = frozenset(requested_bindings)
    excluded_binding_map = {eb.binding_id: eb for eb in projection.excluded_bindings}
    omissions: list[OmissionRecord] = []

    unauthorized_requested: list[str] = []
    for b_id in requested_bindings:
        if b_id in excluded_binding_map:
            eb = excluded_binding_map[b_id]
            unauthorized_requested.append(eb.reason.value)
            omissions.append(
                OmissionRecord(
                    binding_id=b_id,
                    source_relative_path=None,
                    reason_code=eb.reason.value,
                    detail=f"binding {b_id} excluded from body projection ({eb.reason.value})",
                )
            )
        elif b_id not in projection.policy.bindings:
            unauthorized_requested.append("unresolved_inherit_policy")
            omissions.append(
                OmissionRecord(
                    binding_id=b_id,
                    source_relative_path=None,
                    reason_code="unresolved_inherit_policy",
                    detail=f"binding {b_id} has no resolved projection policy",
                )
            )

    if requested_bindings and len(unauthorized_requested) == len(requested_bindings):
        return refused_envelope(
            requested_mode=requested_mode,
            scope=scope,
            refusal_code=unauthorized_requested[0],
            omissions=tuple(omissions),
            provenance={"projection_digest": projection.projection_digest},
        )

    prefix = request.source_path_prefix
    for ex_file in projection.excluded_files:
        if ex_file.binding_id not in req_binding_set:
            continue
        if ex_file.binding_id in excluded_binding_map:
            continue
        if prefix is not None and not ex_file.source_relative_path.startswith(prefix):
            continue
        omissions.append(
            OmissionRecord(
                binding_id=ex_file.binding_id,
                source_relative_path=ex_file.source_relative_path,
                reason_code=ex_file.reason.value,
                detail=f"file excluded from body projection ({ex_file.reason.value})",
            )
        )

    raw_candidates: list[tuple[ProjectedFileRecord, int, int, int, str, str]] = []
    total_matches = 0
    ordered_files = sorted(
        (file for file in projection.projected_files if file.binding_id in req_binding_set
         and (prefix is None or file.source_relative_path.startswith(prefix))),
        key=lambda file: (file.binding_id, file.source_relative_path),
    )
    for file_rec in ordered_files:
        if file_rec.truncated:
            omissions.append(
                OmissionRecord(
                    binding_id=file_rec.binding_id,
                    source_relative_path=file_rec.source_relative_path,
                    reason_code="projected_prefix_truncated",
                    detail=(
                        f"projected prefix truncated at {file_rec.projected_byte_length} "
                        f"of {file_rec.normalized_byte_length} normalized bytes"
                    ),
                )
            )
        remaining = max(0, request.limit - len(raw_candidates))
        file_matches, file_total = _find_file_matches(
            file_rec,
            request.query,
            max_materialize=remaining,
            skip_matches=max(0, request.offset - total_matches),
        )
        total_matches += file_total
        for start_line, end_line, char_pos, chunk_id, snippet_digest in file_matches:
            raw_candidates.append(
                (file_rec, start_line, end_line, char_pos, chunk_id, snippet_digest)
            )

    paged = raw_candidates
    has_more = (request.offset + request.limit) < total_matches
    if has_more or request.offset > 0:
        omissions.append(
            OmissionRecord(
                binding_id=requested_bindings[0] if requested_bindings else "scope",
                source_relative_path=None,
                reason_code="result_page_bounded",
                detail=f"returned {len(paged)} of {total_matches} matches (offset={request.offset}, limit={request.limit})",
            )
        )

    hits: list[QueryHit] = []
    previous_file: ProjectedFileRecord | None = None
    previous_span: tuple[int, int] | None = None
    primary_link: ProjectedEvidenceLink | None = None
    for rank, (file_rec, start_line, end_line, _pos, chunk_id, snippet_digest) in enumerate(
        paged,
        start=1,
    ):
        span = (start_line, end_line)
        if file_rec is not previous_file or span != previous_span:
            primary_link = first_link_for_line_span(file_rec.evidence_links, start_line, end_line)
            previous_file = file_rec
            previous_span = span
        locator = EvaluationLocator(
            binding_id=file_rec.binding_id,
            source_relative_path=file_rec.source_relative_path,
            start_line=start_line,
            end_line=end_line,
            canonical_key=primary_link.canonical_node_key if primary_link is not None else None,
            evidence_key=primary_link.evidence_key if primary_link is not None else None,
            link_kind=primary_link.link_kind if primary_link is not None else None,
        ).validate()
        hits.append(
            QueryHit(
                locator=locator,
                match_surface=MatchSurface.CAPTURED_TEXT,
                rank=rank,
                score=1.0,
                snippet_digest=snippet_digest,
                chunk_id=chunk_id,
                representation_version=file_rec.representation_version,
                artifact_privacy=file_rec.artifact_privacy,
            ).validate()
        )

    is_partial = len(omissions) > 0
    cap_state = CapabilityState.PARTIAL if is_partial else CapabilityState.AVAILABLE
    return QueryEnvelope(
        contract_version=QUERY_CONTRACT_VERSION,
        requested_mode=requested_mode,
        effective_mode=QueryMode.LITERAL_CAPTURED_TEXT,
        match_surface=MatchSurface.CAPTURED_TEXT,
        scope=scope,
        ordering_policy_version=DEFAULT_ORDERING_POLICY_VERSION,
        capability_state=cap_state,
        hits=tuple(hits),
        omissions=tuple(omissions),
        scope_complete=not is_partial,
        fallback=request.fallback,
        provenance=MappingProxyType({
            "projection_digest": projection.projection_digest,
            "policy_digest": projection.policy_digest,
            "accepted_backend": projection.accepted_reference.backend,
            "accepted_run_id": projection.accepted_reference.run_id,
            "total_matches_before_paging": total_matches,
        }),
    ).validate()
