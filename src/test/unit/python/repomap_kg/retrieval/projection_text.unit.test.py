"""Unit tests for strict UTF-8 decoding, NUL rejection, CRLF normalization, and prefix bounding (S1)."""

from __future__ import annotations

import pytest

from common_lore_index.retrieval import projection_text
from common_lore_index.retrieval.projection_policy import ExclusionReason
from common_lore_index.retrieval.projection_text import (
    REPRESENTATION_VERSION,
    normalize_and_bound_text,
)


def test_strict_utf8_and_nul_rejection() -> None:
    """Invalid UTF-8 sequences and NUL bytes anywhere (byte 0 or after 160 chars) are rejected."""
    for nul_payload in (b"\x00leading nul\n", b"valid line\n\x00hidden after nul\n"):
        res, reason = normalize_and_bound_text(nul_payload)
        assert res is None
        assert reason == ExclusionReason.NUL_BYTE_REJECTED

    res, reason = normalize_and_bound_text(b"valid start\xff\xfeinvalid utf8\n")
    assert res is None
    assert reason == ExclusionReason.INVALID_UTF8


def test_lf_only_line_counting_ignores_vertical_tab_form_feed_and_unicode_separators() -> None:
    """Under lf-utf8-v1, VT, FF, NEL, U+2028, and U+2029 do not split lines."""
    raw = "line1\vstill_line1\fstill_line1\u0085\u2028\u2029\nline2\n".encode("utf-8")
    res, reason = normalize_and_bound_text(raw)
    assert reason is None and res is not None
    assert res.raw_line_count == 2
    assert res.normalized_line_count == 2
    assert res.projected_line_count == 2


def test_crlf_and_cr_normalization_preserves_raw_and_normalized_extents() -> None:
    """CRLF and CR normalize to LF under lf-utf8-v1 while preserving raw vs normalized extents."""
    raw = b"alpha\r\nbeta\rgamma\n"
    res, reason = normalize_and_bound_text(raw)
    assert reason is None and res is not None
    assert res.representation_version == REPRESENTATION_VERSION
    assert res.normalized_text == "alpha\nbeta\ngamma\n"
    assert res.had_crlf_or_cr is True
    assert res.raw_byte_length == len(raw)
    assert res.normalized_byte_length == len(b"alpha\nbeta\ngamma\n")
    assert res.projected_byte_length == res.normalized_byte_length
    assert res.raw_line_count == 3
    assert res.normalized_line_count == 3
    assert res.projected_line_count == 3
    assert res.truncated is False
    assert res.representation_digest.startswith("sha256:")


def test_utf8_multibyte_safe_prefix_truncation() -> None:
    """Prefix truncation never splits a multi-byte UTF-8 codepoint."""
    # 'é' is 2 bytes in UTF-8 (\xc3\xa9); '🚀' is 4 bytes (\xf0\x9f\x9a\x80)
    text = "a" * 15 + "🚀" + "b" * 20 + "\n"
    raw = text.encode("utf-8")
    # Truncate at 17 bytes (which lands in the middle of the 4-byte '🚀' at bytes 15..19)
    res, reason = normalize_and_bound_text(raw, max_projected_bytes=17)
    assert reason is None and res is not None
    assert res.truncated is True
    assert res.projected_byte_length == 15
    assert res.normalized_text == "a" * 15
    assert res.normalized_byte_length == len(raw)
    assert res.raw_byte_length == len(raw)


def test_empty_file_normalization() -> None:
    """Empty files normalize cleanly with zero byte and line extents."""
    res, reason = normalize_and_bound_text(b"")
    assert reason is None and res is not None
    assert res.normalized_text == ""
    assert res.raw_byte_length == 0
    assert res.normalized_byte_length == 0
    assert res.projected_byte_length == 0
    assert res.raw_line_count == 0
    assert res.normalized_line_count == 0
    assert res.projected_line_count == 0
    assert res.truncated is False


def test_lf_only_projection_reuses_the_validated_input_bytes() -> None:
    raw = "café\n🚀\nx".encode("utf-8")
    result, reason = normalize_and_bound_text(raw, max_projected_bytes=len(raw))
    assert result is not None and reason is None
    assert result.normalized_bytes is raw
    assert result.normalized_text == raw.decode("utf-8")
    assert result.raw_line_count == result.normalized_line_count == 3
    assert not result.had_crlf_or_cr and not result.truncated


def test_invalid_utf8_after_the_projected_prefix_still_refuses() -> None:
    raw = b"public\r\n" + bytes([0xFF])
    result, reason = normalize_and_bound_text(raw, max_projected_bytes=6)
    assert result is None and reason == ExclusionReason.INVALID_UTF8

@pytest.mark.parametrize("block_size", [1, 2, 3, 4, 5, 8, 8192])
def test_bounded_normalization_carries_crlf_and_multibyte_across_blocks(monkeypatch, block_size):
    monkeypatch.setattr(projection_text, "_NORMALIZATION_BLOCK_BYTES", block_size)
    raw = "é\r\n😀\rletter\r\nend".encode()
    sample, reason = normalize_and_bound_text(raw, max_projected_bytes=5)
    assert sample is not None and reason is None
    assert sample.normalized_bytes == "é\n".encode()
    assert sample.normalized_line_count == 4
    assert sample.normalized_byte_length == len("é\n😀\nletter\nend".encode())
    assert sample.projected_line_count == 1 and sample.truncated
    exact, reason = normalize_and_bound_text(b"alpha\r\nbeta\r\n", max_projected_bytes=11)
    assert exact is not None and reason is None
    assert exact.normalized_bytes == b"alpha\nbeta\n" and not exact.truncated


def test_invalid_tail_has_same_precedence_under_bounded_decode(monkeypatch):
    monkeypatch.setattr(projection_text, "_NORMALIZATION_BLOCK_BYTES", 4)
    assert normalize_and_bound_text(b"readable\r\n\xf0\x9f", max_projected_bytes=2) == (
        None, ExclusionReason.INVALID_UTF8,
    )
    assert normalize_and_bound_text(b"readable\r\n\xff\x00", max_projected_bytes=2) == (
        None, ExclusionReason.NUL_BYTE_REJECTED,
    )


def test_already_normalized_ascii_uses_a_prefix_without_normalizing_blocks(monkeypatch) -> None:
    def unexpected_blocks(_raw):
        raise AssertionError("ASCII without CR needs no block normalization")

    monkeypatch.setattr(projection_text, "_normalized_blocks", unexpected_blocks)
    for raw in (b"first\nsecond\nthird", b"first\nsecond\nthird\n"):
        result, reason = normalize_and_bound_text(raw, max_projected_bytes=6)
        assert reason is None and result is not None
        assert result.normalized_bytes == b"first\n"
        assert (result.normalized_byte_length, result.normalized_line_count) == (len(raw), 3)
        assert result.projected_line_count == 1 and result.truncated
