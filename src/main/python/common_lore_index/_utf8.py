"""Count UTF-8 bytes without materializing large temporary byte strings."""

from __future__ import annotations

from typing import Literal

_CHUNK_CHARACTERS = 8192


def byte_length(text: str, *, errors: Literal["strict", "replace", "surrogatepass"] = "strict") -> int:
    if text.isascii():
        return len(text)
    if len(text) <= _CHUNK_CHARACTERS:
        return len(text.encode("utf-8", errors))
    try:
        return sum(
            len(text[start:start + _CHUNK_CHARACTERS].encode("utf-8", errors))
            for start in range(0, len(text), _CHUNK_CHARACTERS)
        )
    except UnicodeEncodeError:
        # Preserve the original text and absolute offsets on strict errors.
        return len(text.encode("utf-8", errors))
