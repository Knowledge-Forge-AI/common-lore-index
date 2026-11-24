"""Structured configuration line lookup with TOML section precedence."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Iterator
from functools import lru_cache
from typing import Any

from common_lore_index.extractors.shared.scanner import iter_text_lines
from common_lore_index.extractors.config.generic_values import (
    _safe_value_summary,
    _stable_member_key,
)
from common_lore_index.extractors.config.paths import _pointer_segments


def _line_for_toml_pointer(content: str, pointer: str) -> int | None:
    target = _pointer_segments(pointer)
    if not target:
        return None
    return next((line for candidate, line in _iter_toml_pointer_lines(content) if candidate == target), None)


def _toml_pointer_line_index(content: str) -> dict[tuple[str, ...], int]:
    index: dict[tuple[str, ...], int] = {}
    for pointer, line in _iter_toml_pointer_lines(content):
        index.setdefault(pointer, line)
    return index


def _iter_toml_pointer_lines(content: str) -> Iterator[tuple[tuple[str, ...], int]]:
    for section in _iter_toml_sections(content):
        if section.header_line is not None:
            yield section.path, section.header_line
        prefix = section.path
        if section.is_array:
            member_segment = _toml_array_member_segment(section)
            if member_segment is None:
                continue
            prefix = (*prefix, member_segment)
        for line_number, line in section.lines:
            if (key_segments := _toml_key_segments(line)) is not None:
                yield (*prefix, *key_segments), line_number


class _TomlSection:
    def __init__(
        self,
        *,
        path: tuple[str, ...],
        header_line: int | None,
        is_array: bool,
        lines: tuple[tuple[int, str], ...],
    ) -> None:
        self.path = path
        self.header_line = header_line
        self.is_array = is_array
        self.lines = lines


def _toml_sections(content: str) -> tuple[_TomlSection, ...]:
    return tuple(_iter_toml_sections(content))


def _iter_toml_sections(content: str) -> Iterator[_TomlSection]:
    path: tuple[str, ...] = ()
    header_line: int | None = None
    is_array = False
    lines: list[tuple[int, str]] = []
    for line_number, line in enumerate(iter_text_lines(content), start=1):
        header = _toml_table_header(line)
        if header is not None:
            yield _TomlSection(path=path, header_line=header_line, is_array=is_array, lines=tuple(lines))
            path, is_array = header
            header_line = line_number
            lines = []
        else:
            lines.append((line_number, line))
    yield _TomlSection(path=path, header_line=header_line, is_array=is_array, lines=tuple(lines))


def _toml_table_header(line: str) -> tuple[tuple[str, ...], bool] | None:
    stripped = line.strip()
    if stripped.startswith("[[") and stripped.endswith("]]"):
        return _toml_dotted_segments(stripped[2:-2]), True
    if stripped.startswith("[") and stripped.endswith("]"):
        return _toml_dotted_segments(stripped[1:-1]), False
    return None


def _toml_array_member_segment(section: _TomlSection) -> str | None:
    if not section.is_array:
        return None
    parsed = _parse_toml_section_lines(section.lines)
    if not isinstance(parsed, dict):
        return None
    stable_key = _stable_member_key(parsed)
    if stable_key is None:
        return None
    summary = _safe_value_summary(parsed[stable_key])
    if summary is None or isinstance(summary, bool):
        return None
    return str(summary)


def _parse_toml_section_lines(lines: tuple[tuple[int, str], ...]) -> dict[str, Any] | None:
    body = "\n".join(line for _line_number, line in lines)
    if not body.strip():
        return None
    try:
        parsed = tomllib.loads(body)
    except ValueError:
        return None
    return parsed


def _toml_key_segments(line: str) -> tuple[str, ...] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key_text = stripped.split("=", 1)[0].strip()
    if not key_text:
        return None
    return _toml_dotted_segments(key_text)


def _toml_dotted_segments(value: str) -> tuple[str, ...]:
    return tuple(
        segment.strip().strip("\"'")
        for segment in value.split(".")
        if segment.strip()
    )


def _line_for_pointer(content: str, pointer: str) -> int | None:
    if pointer == "":
        return None
    toml_line = _line_for_toml_pointer(content, pointer)
    return toml_line if toml_line is not None else _line_for_config_leaf(content, _pointer_segments(pointer)[-1])


def _config_line_locator(content: str) -> Callable[[str], int | None]:
    toml_lines = _toml_pointer_line_index(content)

    @lru_cache(maxsize=512)
    def fallback(leaf: str) -> int | None:
        return _line_for_config_leaf(content, leaf)

    def locate(pointer: str) -> int | None:
        segments = _pointer_segments(pointer)
        if not segments:
            return None
        line = toml_lines.get(segments)
        return line if line is not None else fallback(segments[-1])

    return locate


def _line_for_config_leaf(content: str, leaf: str) -> int | None:
    key = re.escape(leaf)
    json_pattern = re.compile(rf'"{key}"\s*:')
    bare_or_quoted = rf"(?:{key}|\"{key}\"|'{key}')"
    toml_pattern = re.compile(rf"^\s*{bare_or_quoted}\s*=")
    yaml_pattern = re.compile(rf"^\s*{bare_or_quoted}\s*:")
    plist_pattern = re.compile(rf"<key>\s*{key}\s*</key>")
    for line_number, line in enumerate(iter_text_lines(content), start=1):
        if leaf in line and (
            json_pattern.search(line)
            or toml_pattern.search(line)
            or yaml_pattern.search(line)
            or plist_pattern.search(line)
        ):
            return line_number
    return None
