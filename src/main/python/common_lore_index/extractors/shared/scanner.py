"""Small shared scanner helpers for static extractors."""

from __future__ import annotations

import re
from collections.abc import Iterator


def identifier_parts(value: str) -> tuple[str, ...]:
    """Split a source identifier or short value into lower-case word parts."""
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return tuple(
        part for part in re.split(r"[^0-9A-Za-z]+", expanded.lower()) if part
    )


_LINE_BREAK = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def iter_text_lines(content: str) -> Iterator[str]:
    """Match splitlines() without retaining the list or a final empty line."""
    start = 0
    for boundary in _LINE_BREAK.finditer(content):
        yield content[start:boundary.start()]
        start = boundary.end()
    if start < len(content):
        yield content[start:]
