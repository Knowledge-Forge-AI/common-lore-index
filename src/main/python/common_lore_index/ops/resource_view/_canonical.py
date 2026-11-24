"""Bounded canonical JSON for resource-view payloads.

Integer text conversion is chunked so neither rendering nor parsing depends on
the interpreter's configurable decimal-conversion limit, and no process global
is changed. Work is bounded by the caller's byte budget, not by a numeric
policy ceiling: any count whose canonical text fits the payload is preserved.
"""

from __future__ import annotations

import json

_CHUNK_DIGITS = 18
_CHUNK_BASE = 10**_CHUNK_DIGITS
# floor(log10(2) * 10**5): bit_length gives a cheap lower bound on digit count.
_LOG10_2_SCALED = 30102


class CanonicalBoundError(ValueError):
    """Canonical text would exceed the caller's byte budget."""


def minimum_decimal_digits(value: int) -> int:
    """Return a lower bound on the decimal digits of a nonnegative integer."""
    bits = value.bit_length()
    return 1 if bits <= 1 else ((bits - 1) * _LOG10_2_SCALED) // 100000 + 1


def render_int(value: int, budget: int) -> str:
    """Render a nonnegative integer in at most ``budget`` characters."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("canonical integer must be nonnegative")
    if value < _CHUNK_BASE:
        text = str(value)
    else:
        if minimum_decimal_digits(value) > budget:
            raise CanonicalBoundError("canonical integer exceeds the byte budget")
        chunks: list[int] = []
        remaining = value
        while remaining:
            remaining, low = divmod(remaining, _CHUNK_BASE)
            chunks.append(low)
        text = str(chunks[-1]) + "".join(f"{chunk:018d}" for chunk in reversed(chunks[:-1]))
    if len(text) > budget:
        raise CanonicalBoundError("canonical integer exceeds the byte budget")
    return text


def parse_digits(text: str, budget: int) -> int:
    """Parse validated JSON integer text without the global conversion limit."""
    if text.startswith("-"):
        raise ValueError("canonical integer must be nonnegative")
    if not text or len(text) > budget or not text.isascii() or not text.isdigit():
        raise ValueError("canonical integer text is invalid")
    value = 0
    for start in range(0, len(text), _CHUNK_DIGITS):
        chunk = text[start:start + _CHUNK_DIGITS]
        value = value * 10 ** len(chunk) + int(chunk)
    return value


class _Writer:
    def __init__(self, budget: int) -> None:
        self.remaining = budget
        self.parts: list[bytes] = []

    def emit(self, text: str) -> None:
        encoded = text.encode("utf-8")
        self.remaining -= len(encoded)
        if self.remaining < 0:
            raise CanonicalBoundError("canonical payload exceeds the byte budget")
        self.parts.append(encoded)

    def string(self, value: str) -> None:
        if len(value) + 2 > self.remaining:
            raise CanonicalBoundError("canonical string exceeds the byte budget")
        self.emit(json.dumps(value, ensure_ascii=False))

    def value(self, item: object, depth: int) -> None:
        if depth > 8:
            raise ValueError("canonical payload is too deep")
        if item is None or isinstance(item, bool):
            self.emit("null" if item is None else "true" if item else "false")
        elif isinstance(item, int):
            self.emit(render_int(item, max(self.remaining, 0)))
        elif isinstance(item, str):
            self.string(item)
        elif isinstance(item, list):
            self.emit("[")
            for index, nested in enumerate(item):
                if index:
                    self.emit(",")
                self.value(nested, depth + 1)
            self.emit("]")
        elif isinstance(item, dict):
            keys = list(item)
            if any(not isinstance(key, str) for key in keys):
                raise ValueError("canonical object keys must be strings")
            self.emit("{")
            for index, key in enumerate(sorted(keys)):
                if index:
                    self.emit(",")
                self.string(key)
                self.emit(":")
                self.value(item[key], depth + 1)
            self.emit("}")
        else:
            raise ValueError("canonical payload holds a non-primitive value")


def canonical_bytes(payload: object, budget: int) -> bytes:
    """Return compact, sorted, minimally escaped UTF-8 JSON within ``budget`` bytes."""
    writer = _Writer(budget)
    writer.value(payload, 0)
    return b"".join(writer.parts)


__all__ = (
    "CanonicalBoundError", "canonical_bytes", "minimum_decimal_digits", "parse_digits",
    "render_int",
)
