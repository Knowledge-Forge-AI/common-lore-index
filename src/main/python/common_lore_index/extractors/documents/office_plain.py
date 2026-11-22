"""Plain text, delimited table, and LaTeX document extraction."""

from __future__ import annotations

import csv
import io
import re
from pathlib import PurePosixPath
from typing import Any

from common_lore_index import __version__
from common_lore_index.extractors.documents.office_common import (
    PARSER_NAME,
    _column_type_summary,
    _latex_lines,
    _strip_latex_comment,
    _text_lines,
    _parse_error,
    _pointer_for_slug,
    _scalar_type,
    _slugify,
)
from common_lore_index.extractors.documents.office_references import (
    EXTRACTOR_NAME,
    _contains_secret_marker,
    _latex_reference,
    _redacted_or_summary,
    _Reference,
    _reference_observations,
    _references_from_text,
    _safe_summary,
)
from common_lore_index.graph.keys import (
    document_column_key,
    document_file_key,
    document_latex_command_key,
    document_section_key,
    document_table_key,
)
from common_lore_index.observations.raw import RawObservation


MAX_ROWS = 2000
TXT_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LATEX_COMMAND_PATTERN = re.compile(r"\\([A-Za-z]+)\s*(?:\[[^\]]*\]\s*)?(?:\{([^{}]*)\})?")
LATEX_SECTION_LEVELS = {
    "part": 1,
    "chapter": 2,
    "section": 3,
    "subsection": 4,
    "subsubsection": 5,
    "paragraph": 6,
}
LATEX_COMMANDS = frozenset(
    (
        "label",
        "ref",
        "pageref",
        "autoref",
        "cite",
        "citep",
        "citet",
        "input",
        "include",
        "includegraphics",
        "bibliography",
        "addbibresource",
        "usepackage",
        "url",
        "href",
    )
)


def extract_document_file_observations(
    relative_path: str,
    content: str,
    *,
    repository_paths: frozenset[str] | None = None,
) -> tuple[RawObservation, ...]:
    suffix = PurePosixPath(relative_path).suffix.lower()
    if suffix == ".txt":
        return _extract_text(relative_path, content, repository_paths=repository_paths)
    if suffix == ".csv":
        return _extract_table(
            relative_path,
            content,
            delimiter=",",
            document_format="csv",
            repository_paths=repository_paths,
        )
    if suffix == ".tsv":
        return _extract_table(
            relative_path,
            content,
            delimiter="\t",
            document_format="tsv",
            repository_paths=repository_paths,
        )
    if suffix in (".tex", ".latex"):
        return _extract_latex(relative_path, content, repository_paths=repository_paths)
    return ()

def _extract_text(
    relative_path: str,
    content: str,
    *,
    repository_paths: frozenset[str] | None,
) -> tuple[RawObservation, ...]:
    line_count = 0
    sections: list[tuple[str, str, int, int]] = []
    current_section_key = document_file_key(relative_path)
    references: list[_Reference] = []
    for line_count, line in enumerate(_text_lines(content), start=1):
        match = TXT_HEADING_PATTERN.match(line)
        if match:
            pointer = _pointer_for_slug("sections", _slugify(match.group(2)))
            current_section_key = document_section_key(relative_path, pointer)
            sections.append((pointer, match.group(2), len(match.group(1)), line_count))
            continue
        references.extend(
            _references_from_text(
                relative_path,
                line,
                line_count,
                source_key=current_section_key,
                repository_paths=repository_paths,
            )
        )

    stripped = content.strip()
    paragraph_count = 1 + sum(1 for _ in re.finditer(r"\n\s*\n", stripped)) if stripped else 0
    metadata: dict[str, Any] = {
        "format": "txt",
        "parser": PARSER_NAME,
        "byte_count": len(content.encode("utf-8")),
        "line_count": line_count,
        "paragraph_count": paragraph_count,
        "section_count": len(sections),
        "reference_count": len(references),
        "summary_redacted": _contains_secret_marker(content),
    }
    summary = _safe_summary(content)
    if summary is not None:
        metadata["text_summary"] = summary

    observations = [
        RawObservation(
            kind="document.text_document",
            source_id=f"{relative_path}#document-text",
            path=relative_path,
            confidence="extracted",
            extractor=EXTRACTOR_NAME,
            extractor_version=__version__,
            target=document_file_key(relative_path),
            metadata=metadata,
        )
    ]
    for pointer, heading, level, line_number in sections:
        observations.append(
            RawObservation(
                kind="document.text_section",
                source_id=f"{relative_path}#document-section:{pointer}",
                path=relative_path,
                start_line=line_number,
                end_line=line_number,
                name=pointer,
                confidence="heuristic",
                extractor=EXTRACTOR_NAME,
                extractor_version=__version__,
                target=document_section_key(relative_path, pointer),
                metadata={
                    "format": "txt",
                    "pointer": pointer,
                    "heading_level": level,
                    "heading_summary": _redacted_or_summary(heading),
                    "document_key": document_file_key(relative_path),
                    "redacted": _contains_secret_marker(heading),
                },
            )
        )
    observations.extend(_reference_observations(relative_path, references, "txt"))
    return tuple(observations)


def _extract_table(
    relative_path: str,
    content: str,
    *,
    delimiter: str,
    document_format: str,
    repository_paths: frozenset[str] | None,
) -> tuple[RawObservation, ...]:
    non_empty_rows = []
    try:
        # Parse the entire input for diagnostics, retaining only eligible prefix rows.
        with io.StringIO(content) as source:
            for index, row in enumerate(csv.reader(source, delimiter=delimiter)):
                if index < MAX_ROWS and any(cell.strip() for cell in row):
                    non_empty_rows.append(row)
    except csv.Error as error:
        return (_parse_error(relative_path, document_format, "csv-parser-error", str(error), line_number=None),)
    if not non_empty_rows:
        return (
            RawObservation(
                kind="document.table_document",
                source_id=f"{relative_path}#document-table",
                path=relative_path,
                confidence="extracted",
                extractor=EXTRACTOR_NAME,
                extractor_version=__version__,
                target=document_table_key(relative_path, "/table"),
                metadata={
                    "format": document_format,
                    "parser": "stdlib-csv",
                    "document_key": document_file_key(relative_path),
                    "pointer": "/table",
                    "row_count": 0,
                    "column_count": 0,
                    "header_present": False,
                    "delimiter": "\\t" if delimiter == "\t" else delimiter,
                },
            ),
        )

    expected_width = max(len(row) for row in non_empty_rows)
    if any(len(row) not in (0, expected_width) for row in non_empty_rows):
        return (
            _parse_error(
                relative_path,
                document_format,
                "ragged-row",
                "rows have inconsistent column counts",
                line_number=None,
            ),
        )

    header_present = _looks_like_header(non_empty_rows[0], non_empty_rows[1:])
    headers = non_empty_rows[0] if header_present else []
    data_rows = non_empty_rows[1:] if header_present else non_empty_rows
    table_key = document_table_key(relative_path, "/table")
    references: list[_Reference] = []
    observations: list[RawObservation] = [
        RawObservation(
            kind="document.table_document",
            source_id=f"{relative_path}#document-table",
            path=relative_path,
            confidence="extracted",
            extractor=EXTRACTOR_NAME,
            extractor_version=__version__,
            target=table_key,
            metadata={
                "format": document_format,
                "parser": "stdlib-csv",
                "document_key": document_file_key(relative_path),
                "pointer": "/table",
                "row_count": len(data_rows),
                "column_count": expected_width,
                "header_present": header_present,
                "delimiter": "\\t" if delimiter == "\t" else delimiter,
            },
        )
    ]
    for column_index in range(expected_width):
        column_name = headers[column_index] if column_index < len(headers) else f"column-{column_index + 1}"
        values = [row[column_index] for row in data_rows if column_index < len(row)]
        redacted = _contains_secret_marker(column_name)
        pointer = _pointer_for_slug("table/columns", _slugify(column_name or f"column-{column_index + 1}"))
        type_summary = "redacted" if redacted else _column_type_summary(values)
        metadata: dict[str, Any] = {
            "format": document_format,
            "pointer": pointer,
            "table_key": table_key,
            "column_index": column_index,
            "type_summary": type_summary,
            "non_empty_count": sum(1 for value in values if value.strip()),
            "redacted": redacted,
        }
        if redacted:
            metadata["redaction_reason"] = "secret-prone-column-name"
            metadata["column_name_summary"] = "[redacted]"
        else:
            metadata["column_name_summary"] = column_name
            for row_index, value in enumerate(values, start=2 if header_present else 1):
                references.extend(
                    _references_from_text(
                        relative_path,
                        value,
                        row_index,
                        source_key=table_key,
                        repository_paths=repository_paths,
                    )
                )
        observations.append(
            RawObservation(
                kind="document.table_column",
                source_id=f"{relative_path}#document-column:{pointer}",
                path=relative_path,
                name=pointer,
                confidence="extracted",
                extractor=EXTRACTOR_NAME,
                extractor_version=__version__,
                target=document_column_key(relative_path, pointer),
                metadata=metadata,
            )
        )
    observations.extend(_reference_observations(relative_path, references, document_format))
    return tuple(observations)


def _extract_latex(
    relative_path: str,
    content: str,
    *,
    repository_paths: frozenset[str] | None,
) -> tuple[RawObservation, ...]:
    line_count = 0
    observations: list[RawObservation] = []
    references: list[_Reference] = []
    section_count = 0
    command_count = 0
    for line_count, line in enumerate(_latex_lines(content), start=1):
        for match in LATEX_COMMAND_PATTERN.finditer(line):
            command = match.group(1)
            argument = (match.group(2) or "").strip()
            if command in LATEX_SECTION_LEVELS and argument:
                section_count += 1
                pointer = _pointer_for_slug("sections", f"{section_count}-{_slugify(argument)}")
                observations.append(
                    RawObservation(
                        kind="document.latex_section",
                        source_id=f"{relative_path}#latex-section:{pointer}",
                        path=relative_path,
                        start_line=line_count,
                        end_line=line_count,
                        name=pointer,
                        confidence="heuristic",
                        extractor=EXTRACTOR_NAME,
                        extractor_version=__version__,
                        target=document_section_key(relative_path, pointer),
                        metadata={
                            "format": "latex",
                            "pointer": pointer,
                            "command": command,
                            "heading_level": LATEX_SECTION_LEVELS[command],
                            "heading_summary": _redacted_or_summary(argument),
                            "document_key": document_file_key(relative_path),
                            "redacted": _contains_secret_marker(argument),
                        },
                    )
                )
                continue
            if command not in LATEX_COMMANDS:
                continue
            command_count += 1
            pointer = f"/commands/{command}:{command_count}"
            command_key = document_latex_command_key(relative_path, pointer)
            observations.append(
                RawObservation(
                    kind="document.latex_command",
                    source_id=f"{relative_path}#latex-command:{pointer}",
                    path=relative_path,
                    start_line=line_count,
                    end_line=line_count,
                    name=pointer,
                    confidence="heuristic",
                    extractor=EXTRACTOR_NAME,
                    extractor_version=__version__,
                    target=command_key,
                    metadata={
                        "format": "latex",
                        "pointer": pointer,
                        "command": command,
                        "argument_summary": _redacted_or_summary(argument),
                        "document_key": document_file_key(relative_path),
                        "redacted": _contains_secret_marker(argument),
                    },
                )
            )
            reference = _latex_reference(
                relative_path,
                command,
                argument,
                line_count,
                command_key,
                repository_paths=repository_paths,
            )
            if reference is not None:
                references.append(reference)

    observations.insert(
        0,
        RawObservation(
            kind="document.latex_document",
            source_id=f"{relative_path}#latex-document",
            path=relative_path,
            confidence="extracted",
            extractor=EXTRACTOR_NAME,
            extractor_version=__version__,
            target=document_file_key(relative_path),
            metadata={
                "format": "latex",
                "parser": PARSER_NAME,
                "line_count": line_count,
                "section_count": section_count,
                "command_count": command_count,
                "reference_count": len(references),
                "compiled": False,
            },
        ),
    )
    observations.extend(_reference_observations(relative_path, references, "latex"))
    return tuple(observations)

def _looks_like_header(header: list[str], data_rows: list[list[str]]) -> bool:
    if not header or any(not cell.strip() for cell in header):
        return False
    normalized = [cell.strip().lower() for cell in header]
    if len(set(normalized)) != len(normalized):
        return False
    if not data_rows:
        return True
    header_textish = sum(1 for cell in header if _scalar_type(cell) == "text")
    first_data = data_rows[0]
    data_non_text = sum(1 for cell in first_data if _scalar_type(cell) != "text")
    return header_textish >= max(1, len(header) // 2) and data_non_text > 0


def _strip_latex_comments(content: str) -> str:
    return "\n".join(_strip_latex_comment(line) for line in _text_lines(content))
