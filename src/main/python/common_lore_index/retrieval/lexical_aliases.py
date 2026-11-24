"""Deterministic identifier and path alias token splitting for ranked FTS (identifier-path-split-v1)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from functools import lru_cache
import re

ALIAS_SPLIT_RULE_VERSION = "identifier-path-split-v1"
MIN_ALIAS_TOKEN_LEN = 2
MAX_ALIAS_TOKEN_LEN = 64

_RAW_CANDIDATE_RE = re.compile(r"[A-Za-z0-9_./:-]+")
_DELIMITER_SPLIT_RE = re.compile(r"[/_.:-]+")
_CAMEL_BOUNDARY_1_RE = re.compile(r"([a-z0-9])([A-Z])")
_ACRONYM_BOUNDARY_RE = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_ALNUM_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])")
_CLEAN_ALNUM_RE = re.compile(r"^[a-z0-9]{2,64}$")


def _split_camel_case(segment: str) -> list[str]:
    if not segment:
        return []
    if segment.isalnum() and (segment.islower() or segment.isdigit()):
        base = [segment]
    else:
        step1 = _ACRONYM_BOUNDARY_RE.sub(" ", segment)
        step2 = _CAMEL_BOUNDARY_1_RE.sub(r"\1 \2", step1)
        base = [part.lower() for part in step2.split(" ") if part]
    expanded: list[str] = []
    for part in base:
        expanded.append(part)
        if part.isalpha() or part.isdigit():
            continue
        for sub in _ALNUM_BOUNDARY_RE.split(part):
            if sub and sub != part:
                expanded.append(sub)
    return expanded


def _alias_candidates(raw: str) -> Iterator[str]:
    parts = [part for part in _DELIMITER_SPLIT_RE.split(raw) if part]
    for part in parts:
        yield part
        yield from _split_camel_case(part)
    if len(parts) > 1:
        yield "".join(parts)


def _append_alias_tokens(
    text_input: str, seen: set[str], ordered: list[str], seen_candidates: set[str],
    candidates_for: Callable[[str], Iterable[str]] = _alias_candidates,
) -> None:

    def _add(token: str) -> None:
        lower = token.lower()
        if (
            MIN_ALIAS_TOKEN_LEN <= len(lower) <= MAX_ALIAS_TOKEN_LEN
            and _CLEAN_ALNUM_RE.fullmatch(lower) is not None
            and lower not in seen
        ):
            seen.add(lower)
            ordered.append(lower)

    for match in _RAW_CANDIDATE_RE.finditer(text_input):
        raw = match.group(0)
        if raw in seen_candidates:
            continue
        seen_candidates.add(raw)
        for candidate in candidates_for(raw):
            _add(candidate)


def extract_alias_tokens(
    *,
    source_relative_path: str,
    chunk_text: str,
) -> tuple[str, ...]:
    """Extract deduplicated lowercase alphanumeric alias tokens under identifier-path-split-v1."""
    seen: set[str] = set()
    ordered: list[str] = []
    candidates: set[str] = set()
    _append_alias_tokens(source_relative_path, seen, ordered, candidates)
    _append_alias_tokens(chunk_text, seen, ordered, candidates)
    return tuple(ordered)


def _prepare_alias_text(source_relative_path: str) -> Callable[[str], str]:
    """Reuse the path token prefix for each independent chunk of a file."""
    ordered: list[str] = []
    path_candidates: set[str] = set()
    _append_alias_tokens(source_relative_path, set(), ordered, path_candidates)

    # Limit retained tokens to small candidates within one document.
    @lru_cache(maxsize=256)
    def cached_candidates(value: str) -> tuple[str, ...]:
        return tuple(_alias_candidates(value))

    def expand(value: str) -> Iterable[str]:
        if len(value) <= 128:
            return cached_candidates(value)
        return _alias_candidates(value)

    def format_chunk(chunk_text: str) -> str:
        tokens = list(ordered)
        _append_alias_tokens(chunk_text, set(ordered), tokens, set(path_candidates), expand)
        return " ".join(tokens)

    return format_chunk


def build_alias_tokens_text(
    *,
    source_relative_path: str,
    chunk_text: str,
) -> str:
    """Format extracted alias tokens as single-space-delimited lowercase text."""
    return " ".join(
        extract_alias_tokens(
            source_relative_path=source_relative_path,
            chunk_text=chunk_text,
        )
    )


def split_identifier_and_path_tokens(text: str) -> tuple[str, ...]:
    """Extract deduplicated lowercase alphanumeric alias tokens from a single string."""
    return extract_alias_tokens(source_relative_path="", chunk_text=text)


__all__ = [
    "ALIAS_SPLIT_RULE_VERSION",
    "MAX_ALIAS_TOKEN_LEN",
    "MIN_ALIAS_TOKEN_LEN",
    "build_alias_tokens_text",
    "extract_alias_tokens",
    "split_identifier_and_path_tokens",
]
