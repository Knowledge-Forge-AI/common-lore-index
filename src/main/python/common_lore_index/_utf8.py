"""Count UTF-8 bytes without materializing large temporary byte strings."""

from __future__ import annotations

from typing import Literal

_CHUNK_CHARACTERS = 8192


def byte_length(text: str, *, errors: Literal["strict", "replace", "surrogatepass"] = "strict") -> int:
    if text.isascii():
        return len(text)
    if len(text) <= _CHUNK_CHARACTERS:
        return len(text.encode("utf-8", errors))
    byte_count = 0
    for start in range(0, len(text), _CHUNK_CHARACTERS):
        try:
            byte_count += len(text[start:start + _CHUNK_CHARACTERS].encode("utf-8", errors))
        except UnicodeEncodeError as error:
            # UTF-8 groups adjacent surrogates into one strict error, even across a chunk boundary.
            end = start + error.end
            while end < len(text) and "\ud800" <= text[end] <= "\udfff":
                end += 1
            raise UnicodeEncodeError("utf-8", text, start + error.start, end, error.reason) from None
    return byte_count
