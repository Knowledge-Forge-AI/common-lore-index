"""Unit tests for deterministic chunking, long-line UTF-8 splitting, and framed digests (S1)."""

from __future__ import annotations

from typing import Any

from common_lore_index.retrieval.projection_chunks import (
    _framed_digest,
    build_file_chunks,
    frame_digest,
)


def test_empty_file_produces_explicit_complete_empty_chunk_record() -> None:
    """An empty file produces one explicit empty-file chunk record."""
    chunks = build_file_chunks(
        file_projection_id="projfile1:" + "1" * 64,
        binding_id="bind1:core",
        source_relative_path="pkg/__init__.py",
        normalized_text="",
        artifact_privacy="raw_source",
        policy_digest="sha256:" + "2" * 64,
        publication_bundle_id="bundle1:" + "3" * 64,
    )
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.is_empty_file_record is True
    assert chunk.chunk_index == 0
    assert chunk.start_line == 0 and chunk.end_line == 0
    assert chunk.normalized_byte_start == 0 and chunk.normalized_byte_end == 0
    assert chunk.byte_length == 0 and chunk.line_count == 0
    assert chunk.content_text == ""
    assert chunk.chunk_id.startswith("projchunk1:")


def test_contiguous_gapless_non_overlapping_chunk_coverage() -> None:
    """Multi-chunk files cover the exact normalized byte prefix without gaps or overlap."""
    lines = [f"line-{idx:04d}: payload value {idx}\n" for idx in range(1, 185)]
    text = "".join(lines)
    chunks = build_file_chunks(
        file_projection_id="projfile1:" + "1" * 64,
        binding_id="bind1:core",
        source_relative_path="pkg/module.py",
        normalized_text=text,
        artifact_privacy="raw_source",
        policy_digest="sha256:" + "2" * 64,
        publication_bundle_id="bundle1:" + "3" * 64,
        max_chunk_lines=80,
        max_chunk_bytes=8192,
    )
    assert len(chunks) == 3
    assert [c.line_count for c in chunks] == [80, 80, 24]
    assert chunks[0].start_line == 1 and chunks[0].end_line == 80
    assert chunks[1].start_line == 81 and chunks[1].end_line == 160
    assert chunks[2].start_line == 161 and chunks[2].end_line == 184

    cursor = 0
    for index, chunk in enumerate(chunks):
        assert chunk.chunk_index == index
        assert chunk.normalized_byte_start == cursor
        assert chunk.normalized_byte_end == cursor + chunk.byte_length
        cursor = chunk.normalized_byte_end
    assert cursor == len(text.encode("utf-8"))
    assert "".join(c.content_text for c in chunks) == text


def test_single_long_line_splits_only_at_valid_utf8_boundaries() -> None:
    """A single line exceeding max_chunk_bytes splits cleanly at UTF-8 codepoint boundaries."""
    long_line = ("café-🚀-" * 3500) + "\n"
    chunks = build_file_chunks(
        file_projection_id="projfile1:" + "a" * 64,
        binding_id="bind1:companion",
        source_relative_path="docs/unicode.md",
        normalized_text=long_line,
        artifact_privacy="raw_source",
        policy_digest="sha256:" + "b" * 64,
        publication_bundle_id="bundle1:" + "c" * 64,
        max_chunk_lines=80,
        max_chunk_bytes=512,
    )
    assert len(chunks) >= 5
    assert chunks[0].line_split_continuation is False
    assert all(c.line_split_continuation is True for c in chunks[1:])
    assert all(c.start_line == 1 and c.end_line == 1 for c in chunks)
    assert all(c.byte_length <= 512 for c in chunks)
    assert "".join(c.content_text for c in chunks) == long_line


def test_framed_digest_prevents_boundary_concatenation_collision() -> None:
    """Length-prefixed framing distinguishes ('ab', 'c') from ('a', 'bc')."""
    d1 = frame_digest("sha256", ("ab", "c"))
    d2 = frame_digest("sha256", ("a", "bc"))
    assert d1 != d2


def test_chunk_line_splitting_uses_lf_only_not_unicode_or_control_separators() -> None:
    """VT, FF, NEL, U+2028, and U+2029 remain on the same line number during chunking."""
    text = "first\vpart\fstill_first\u2028same_line\nsecond_line\n"
    chunks = build_file_chunks(
        file_projection_id="projfile1:" + "1" * 64,
        binding_id="bind1:core",
        source_relative_path="pkg/control.py",
        normalized_text=text,
        artifact_privacy="raw_source",
        policy_digest="sha256:" + "2" * 64,
        publication_bundle_id="bundle1:" + "3" * 64,
    )
    assert len(chunks) == 1
    assert chunks[0].start_line == 1 and chunks[0].end_line == 2
    assert chunks[0].line_count == 2


def test_long_utf8_lines_preserve_offsets_and_isolate_continuations() -> None:
    chunks = build_file_chunks(
        file_projection_id="projfile1:" + "a" * 64,
        binding_id="bind1:core",
        source_relative_path="docs/unicode.md",
        normalized_text="a\nééé\n\n🚀βo\nz",
        artifact_privacy="raw_source",
        policy_digest="sha256:" + "b" * 64,
        publication_bundle_id="bundle1:" + "c" * 64,
        max_chunk_lines=2,
        max_chunk_bytes=6,
    )
    assert [chunk.content_text for chunk in chunks] == ["a\n", "ééé", "\n", "\n", "🚀β", "o\n", "z"]
    assert [chunk.end_line for chunk in chunks] == [1, 2, 2, 3, 4, 4, 5]
    assert [chunk.normalized_byte_end for chunk in chunks] == [2, 8, 9, 10, 16, 18, 19]
    assert [chunk.line_split_continuation for chunk in chunks] == [False, False, True, False, False, True, False]


def test_streaming_chunks_preserve_public_tuple_contents() -> None:
    from common_lore_index.retrieval.projection_chunks import _iter_file_chunks

    options = dict(file_projection_id="file", binding_id="binding", source_relative_path="fixture.md",
                   artifact_privacy="public-dev", policy_digest="policy", publication_bundle_id="bundle")
    for text in ("", "ascii\n", "🧭" * 20 + "\nlast"):
        stream = _iter_file_chunks(normalized_text=text, max_chunk_lines=3, max_chunk_bytes=16, **options)
        assert iter(stream) is stream
        assert tuple(stream) == build_file_chunks(
            normalized_text=text, max_chunk_lines=3, max_chunk_bytes=16, **options,
        )


def test_chunk_identifier_keeps_the_existing_framing_across_byte_splits() -> None:
    fixed = ("policy", "bundle", "file", "binding", "fixture.md")
    text = "a\né🚀é\nz"
    chunks = build_file_chunks(
        file_projection_id="file", binding_id="binding", source_relative_path="fixture.md",
        normalized_text=text, artifact_privacy="public-dev", policy_digest="policy",
        publication_bundle_id="bundle", max_chunk_bytes=4,
    )
    assert [chunk.chunk_id for chunk in chunks] == [
        "projchunk1:83a0f289bab74c149be6c19ee83330d9bad368a83dcb1e4781e7c25402539e57",
        "projchunk1:0cc80fa30735ba5cc8ccc0907f510fb9a1515031c47deea0674b0cce569f6f3c",
        "projchunk1:694cc037953fcd53a0aeec0d5675827962631d5ab07506ac1ddbb3941804edb4",
        "projchunk1:2d989b725c36a81dc5d8a14545b4863e42a670d63408ced3729d37712fc75c22",
        "projchunk1:e313d8326b39ba0c02bfbe9233d37f16d4949213d8becf6848671d36a8e19b95",
    ]
    for chunk in chunks:
        data = chunk.content_text.encode("utf-8")
        assert chunk.content_digest == frame_digest("sha256", (data,))
        coordinates = (
            str(chunk.chunk_index), str(chunk.start_line), str(chunk.end_line),
            str(chunk.normalized_byte_start), str(chunk.normalized_byte_end), chunk.content_digest,
        )
        assert chunk.chunk_id == frame_digest("projchunk1", (*fixed, *coordinates))


def test_streaming_frames_keep_exact_sequence_framing() -> None:
    parts = ("", b"", "a:\x00", "β:🚀", b"\xff\n")
    assert _framed_digest("fixture", len(parts), iter(parts)) == frame_digest("fixture", parts)
    assert _framed_digest("fixture", 0, iter(())) == frame_digest("fixture", ())


def test_validated_bytes_reuse_exact_text_chunk_boundaries_and_digests() -> None:
    from common_lore_index.retrieval.projection_chunks import _iter_file_chunks
    from common_lore_index.retrieval.projection_text import normalize_and_bound_text

    words = "🚀🚀🚀\nab🚀c🚀\nλ\nα\nβ\n\n"
    normalized, reason = normalize_and_bound_text((words * 40).encode("utf-8"), max_projected_bytes=27)
    assert reason is None and normalized is not None and normalized.truncated
    assert isinstance(normalized.normalized_bytes, bytes)
    assert normalized.normalized_bytes == normalized.normalized_text.encode("utf-8")
    assert len(normalized.normalized_bytes) <= 27 < normalized.normalized_byte_length
    common: dict[str, Any] = dict(
        file_projection_id="fixture", binding_id="binding", source_relative_path="notes.md", artifact_privacy="public-dev",
        policy_digest="policy", publication_bundle_id="bundle",
    )
    for text in ("", words, normalized.normalized_text):
        for max_lines in (1, 2, 3):
            for max_bytes in range(4, 16):
                assert tuple(_iter_file_chunks(
                    normalized_text=text.encode("utf-8"), max_chunk_lines=max_lines, max_chunk_bytes=max_bytes, **common,
                )) == build_file_chunks(
                    normalized_text=text, max_chunk_lines=max_lines, max_chunk_bytes=max_bytes, **common,
                )
