"""Shared helpers for conservative office-document extraction."""

from __future__ import annotations

import re
import sys
import zipfile
from collections.abc import Iterable, Iterator
from importlib import import_module

from common_lore_index import __version__
from common_lore_index.extractors.documents.office_references import EXTRACTOR_NAME, URL_PATTERN
from common_lore_index.extractors.shared.scanner import iter_text_lines as _text_lines
from common_lore_index.observations.raw import RawObservation


PARSER_NAME = "stdlib-document-conservative"


def _parse_error(
    relative_path: str,
    document_format: str,
    error_kind: str,
    message: str,
    *,
    line_number: int | None,
) -> RawObservation:
    return RawObservation(
        kind="document.parse_error",
        source_id=f"{relative_path}#document-parse-error:{error_kind}",
        path=relative_path,
        start_line=line_number,
        end_line=line_number,
        confidence="unknown",
        extractor=EXTRACTOR_NAME,
        extractor_version=__version__,
        metadata={
            "format": document_format,
            "parser": PARSER_NAME,
            "error_kind": error_kind,
            "message_summary": message[:120],
            "recovered": False,
        },
    )

def _column_type_summary(values: Iterable[str]) -> str:
    observed = "empty"
    for value in values:
        scalar = _scalar_type(value)
        if scalar == "empty":
            continue
        if observed != "empty" and scalar != observed:
            return "mixed"
        observed = scalar
    return observed


def _scalar_type(value: str) -> str:
    text = value.strip()
    if not text:
        return "empty"
    lowered = text.lower()
    if lowered in ("true", "false", "yes", "no"):
        return "boolean"
    if re.fullmatch(r"[+-]?\d+", text):
        return "integer"
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+)", text):
        return "decimal"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.-]+Z?)?", text):
        return "date-like"
    if URL_PATTERN.fullmatch(text):
        return "url-like"
    return "text"

def _pointer_for_slug(prefix: str, slug: str) -> str:
    return "/" + "/".join(part for part in (prefix, slug or "untitled") if part)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower()).strip("-")
    return slug[:64] or "untitled"


def _latex_lines(content: str) -> Iterator[str]:
    """Strip comments per line, dropping the last line if it becomes empty."""
    pending = None
    for line in _text_lines(content):
        if pending is not None:
            yield pending
        pending = _strip_latex_comment(line)
    if pending:
        yield pending


def _strip_latex_comment(line: str) -> str:
    index = 0
    comment_at: int | None = None
    while True:
        index = line.find("%", index)
        if index == -1:
            break
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and line[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            comment_at = index
            break
        index += 1
    return line[:comment_at] if comment_at is not None else line


def _odf_zip_read_errors() -> tuple[type[Exception], ...]:
    errors: list[type[Exception]] = [EOFError, OSError, RuntimeError, zipfile.BadZipFile]
    codecs = [("zlib", "error"), ("lzma", "LZMAError")]
    if sys.version_info >= (3, 14):
        codecs.append(("compression.zstd", "ZstdError"))
    for module_name, error_name in codecs:
        try:
            codec = import_module(module_name)
        except ImportError:
            continue
        errors.append(getattr(codec, error_name))
    return tuple(errors)


ODF_ZIP_READ_ERRORS = _odf_zip_read_errors()
