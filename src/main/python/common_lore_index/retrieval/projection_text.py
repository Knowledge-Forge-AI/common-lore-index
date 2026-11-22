"""Strict UTF-8 decoding, NUL byte rejection, CRLF/CR normalization, and prefix bounding (S1)."""

from __future__ import annotations

import codecs
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import stat

from common_lore_index.retrieval.projection_chunks import frame_digest
from common_lore_index.retrieval.projection_policy import ExclusionReason

REPRESENTATION_VERSION = "lf-utf8-v1"
DEFAULT_MAX_PROJECTED_FILE_BYTES = 262144


def _count_lf_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") if text.endswith("\n") else text.count("\n") + 1


def _utf8_safe_prefix(data: bytes, max_bytes: int) -> bytes:
    """Cut already validated UTF-8 before any incomplete code point."""
    cut = max_bytes
    while cut > 0 and (data[cut] & 0xC0) == 0x80:
        cut -= 1
    return data[:cut]


_NORMALIZATION_BLOCK_BYTES = 64 * 1024


def _normalized_blocks(raw: bytes) -> Iterator[bytes]:
    start = 0
    while start < len(raw):
        end = min(start + _NORMALIZATION_BLOCK_BYTES, len(raw))
        if end < len(raw) and raw[end - 1] == 13 and raw[end] == 10:
            end += 1
        yield raw[start:end].replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        start = end


def _bounded_normalized_prefix(raw: bytes, limit: int) -> tuple[bytes, int, int]:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    prefix = bytearray()
    size = breaks = 0
    ends_in_lf = False
    for block in _normalized_blocks(raw):
        decoder.decode(block)
        size += len(block)
        breaks += block.count(b"\n")
        ends_in_lf = block.endswith(b"\n")
        if len(prefix) <= limit:
            prefix.extend(block[:limit + 1 - len(prefix)])
    decoder.decode(b"", final=True)
    if size <= limit:
        return bytes(prefix), size, breaks + int(size > 0 and not ends_in_lf)
    return _utf8_safe_prefix(bytes(prefix), limit), size, breaks + int(not ends_in_lf)


@dataclass(frozen=True)
class NormalizedTextResult:
    """Normalized UTF-8 text and extent accounting under lf-utf8-v1."""

    representation_version: str
    normalized_text: str
    normalized_bytes: bytes
    raw_byte_length: int
    raw_line_count: int
    normalized_byte_length: int
    normalized_line_count: int
    projected_byte_length: int
    projected_line_count: int
    had_crlf_or_cr: bool
    truncated: bool
    representation_digest: str


def normalize_and_bound_text(
    raw_bytes: bytes,
    *,
    max_projected_bytes: int = DEFAULT_MAX_PROJECTED_FILE_BYTES,
) -> tuple[NormalizedTextResult | None, ExclusionReason | None]:
    """Validate strict UTF-8 without NUL bytes, normalize line endings, and bound prefix."""
    if max_projected_bytes < 1:
        raise ValueError("max_projected_bytes must be positive")
    if b"\x00" in raw_bytes:
        return None, ExclusionReason.NUL_BYTE_REJECTED
    had_crlf_or_cr = b"\r" in raw_bytes
    raw = bytes(raw_bytes)
    try:
        if len(raw) > max_projected_bytes:
            projected_bytes, normalized_byte_length, normalized_line_count = _bounded_normalized_prefix(
                raw, max_projected_bytes,
            )
            projected_text = projected_bytes.decode("utf-8", errors="strict")
        else:
            projected_bytes = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n") if had_crlf_or_cr else raw
            projected_text = projected_bytes.decode("utf-8", errors="strict")
            normalized_byte_length = len(projected_bytes)
            normalized_line_count = _count_lf_lines(projected_text)
    except UnicodeDecodeError:
        return None, ExclusionReason.INVALID_UTF8

    truncated = normalized_byte_length > max_projected_bytes
    projected_byte_length = len(projected_bytes)
    projected_line_count = _count_lf_lines(projected_text)
    rep_digest = frame_digest(
        "sha256",
        (REPRESENTATION_VERSION, projected_bytes),
    )
    return (
        NormalizedTextResult(
            representation_version=REPRESENTATION_VERSION,
            normalized_text=projected_text,
            normalized_bytes=projected_bytes,
            raw_byte_length=len(raw_bytes),
            raw_line_count=normalized_line_count,
            normalized_byte_length=normalized_byte_length,
            normalized_line_count=normalized_line_count,
            projected_byte_length=projected_byte_length,
            projected_line_count=projected_line_count,
            had_crlf_or_cr=had_crlf_or_cr,
            truncated=truncated,
            representation_digest=rep_digest,
        ),
        None,
    )


def _overlaps_any_private_root(target: Path, private_roots: Sequence[Path]) -> bool:
    if not private_roots:
        return False
    try:
        resolved = target.resolve(strict=False)
        return any(resolved.is_relative_to(root.resolve(strict=False)) for root in private_roots)
    except OSError:
        return True


def _read_bounded_regular_file(
    path: Path, *, max_input_bytes: int, private_roots: Sequence[Path],
) -> tuple[bytes | None, ExclusionReason | None]:
    if _overlaps_any_private_root(path, private_roots):
        return None, ExclusionReason.ARTIFACT_PRIVACY_FORBIDDEN
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None, ExclusionReason.UNSUPPORTED_SOURCE_FORMAT
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            return None, ExclusionReason.UNSUPPORTED_SOURCE_FORMAT
        if metadata.st_size > max_input_bytes:
            return None, ExclusionReason.ARTIFACT_OVERSIZED
        chunks: list[bytes] = []
        remaining = max_input_bytes
        # A file can grow after fstat. Consume at most one byte to detect it.
        while chunk := os.read(fd, min(65536, remaining + 1)):
            remaining -= len(chunk)
            if remaining < 0:
                return None, ExclusionReason.ARTIFACT_OVERSIZED
            chunks.append(chunk)
        return b"".join(chunks), None
    except OSError:
        return None, ExclusionReason.ARTIFACT_CORRUPT
    finally:
        os.close(fd)
