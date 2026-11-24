"""Document reference and safe-summary helpers for office extraction."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree

from common_lore_index import __version__
from common_lore_index.extractors.shared.web_references import contains_web_url_userinfo, web_url_reference
from common_lore_index.graph.keys import (
    dynamic_key,
    external_key,
    file_key,
    unknown_key,
)
from common_lore_index.observations.raw import RawObservation


EXTRACTOR_NAME = "repo-documents"
MAX_TEXT_SUMMARY_CHARS = 160
SECRET_PRONE_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "access_key",
    "refresh_token",
    "bearer",
    "auth",
    "ssn",
    "social_security",
    "tax_id",
    "account_number",
    "routing_number",
    "iban",
    "credit_card",
    "medical_record",
    "patient_id",
)
URL_PATTERN = re.compile(r"\b(?:https?://[^\s<>)]+|mailto:[^\s<>)]+)")
PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_./~-])(?:/|\.{1,2}/)?[A-Za-z0-9_${}~*?.-]+"
    r"(?:/[A-Za-z0-9_${}~*?.-]+)+"
)
ODF_NAMESPACES = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}
ODF_SAFE_PARTS = frozenset(
    ("content.xml", "meta.xml", "styles.xml", "META-INF/manifest.xml")
)
ODF_INTERNAL_REFERENCE_TARGET = unknown_key(
    "document.reference", "odf-internal-package-part"
)


_MALFORMED_DOCUMENT_URL = unknown_key("external.url", "malformed-document-reference")
_CREDENTIALED_DOCUMENT_URL = unknown_key("external.url", "credentialed-external-url")
_DOCUMENT_URL_REDACTIONS = {
    _MALFORMED_DOCUMENT_URL: "malformed-url-redacted",
    _CREDENTIALED_DOCUMENT_URL: "credentialed-url-redacted",
}


@dataclass(frozen=True)
class _Reference:
    source_key: str
    raw_value: str
    target_key: str
    reference_kind: str
    resolution_reason: str
    line_number: int
    command: str | None = None
    redacted: bool = False


def _reference_observations(
    relative_path: str, references: list[_Reference], document_format: str
) -> list[RawObservation]:
    observations: list[RawObservation] = []
    for index, reference in enumerate(references):
        metadata: dict[str, Any] = {
            "format": document_format,
            "source_key": reference.source_key,
            "reference_kind": reference.reference_kind,
            "raw_value_summary": _safe_value_summary(reference.raw_value),
            "resolution_reason": reference.resolution_reason,
            "not_fetched": True,
            "redacted": reference.redacted,
        }
        if reference.command is not None:
            metadata["command"] = reference.command
        observations.append(
            RawObservation(
                kind="document.reference",
                source_id=f"{relative_path}#document-reference:{reference.line_number}:{index}",
                path=relative_path,
                start_line=reference.line_number,
                end_line=reference.line_number,
                confidence="heuristic",
                extractor=EXTRACTOR_NAME,
                extractor_version=__version__,
                target=reference.target_key,
                metadata=metadata,
            )
        )
    return observations


def _references_from_text(
    relative_path: str,
    text: str,
    line_number: int,
    *,
    source_key: str,
    repository_paths: frozenset[str] | None,
) -> list[_Reference]:
    if _contains_secret_marker(text):
        return []
    references: list[_Reference] = []
    consumed: list[tuple[int, int]] = []
    for match in URL_PATTERN.finditer(text):
        raw_value = match.group(0).rstrip(".,;")
        references.append(
            _reference_for_value(
                relative_path,
                raw_value,
                line_number,
                source_key=source_key,
                reference_kind="url",
                resolution_reason="url-literal",
                repository_paths=repository_paths,
            )
        )
        consumed.append(match.span())
    url_index = 0
    for match in PATH_PATTERN.finditer(text):
        start = match.start()
        while url_index < len(consumed) and consumed[url_index][1] <= start:
            url_index += 1
        if url_index < len(consumed) and consumed[url_index][0] <= start:
            continue
        raw_value = match.group(0).rstrip(".,;")
        references.append(
            _reference_for_value(
                relative_path,
                raw_value,
                line_number,
                source_key=source_key,
                reference_kind="file",
                resolution_reason="path-like-string",
                repository_paths=repository_paths,
            )
        )
    return references


def _latex_reference(
    relative_path: str,
    command: str,
    argument: str,
    line_number: int,
    source_key: str,
    *,
    repository_paths: frozenset[str] | None,
) -> _Reference | None:
    if not argument or _contains_secret_marker(argument):
        return None
    if command in ("input", "include"):
        value = argument if PurePosixPath(argument).suffix else f"{argument}.tex"
        return _reference_for_value(
            relative_path,
            value,
            line_number,
            source_key=source_key,
            reference_kind="file",
            resolution_reason=f"latex-{command}",
            repository_paths=repository_paths,
            command=command,
        )
    if command in ("includegraphics", "bibliography", "addbibresource"):
        value = argument
        if command in ("bibliography", "addbibresource") and not PurePosixPath(value).suffix:
            value = f"{value}.bib"
        return _reference_for_value(
            relative_path,
            value,
            line_number,
            source_key=source_key,
            reference_kind="file",
            resolution_reason=f"latex-{command}",
            repository_paths=repository_paths,
            command=command,
        )
    if command in ("url", "href"):
        return _reference_for_value(
            relative_path,
            argument,
            line_number,
            source_key=source_key,
            reference_kind="url",
            resolution_reason=f"latex-{command}",
            repository_paths=repository_paths,
            command=command,
        )
    return None


def _reference_for_value(
    relative_path: str,
    raw_value: str,
    line_number: int,
    *,
    source_key: str,
    reference_kind: str,
    resolution_reason: str,
    repository_paths: frozenset[str] | None,
    command: str | None = None,
) -> _Reference:
    target_key = _target_key_for_reference(relative_path, raw_value, repository_paths)
    reason = _DOCUMENT_URL_REDACTIONS.get(target_key)
    return _Reference(
        source_key=source_key,
        raw_value="<redacted-url>" if reason else raw_value,
        target_key=target_key,
        reference_kind=reference_kind,
        resolution_reason=reason or resolution_reason,
        line_number=line_number,
        command=command,
        redacted=reason is not None,
    )


def _target_key_for_reference(
    relative_path: str, raw_value: str, repository_paths: frozenset[str] | None
) -> str:
    reference, has_other_scheme = web_url_reference(raw_value)
    if reference is not None:
        return _MALFORMED_DOCUMENT_URL if reference["reason"] == "malformed-url-redacted" else reference["target"]
    if has_other_scheme:
        return unknown_key("document.reference", "unsupported-scheme")
    if raw_value.startswith("/"):
        return external_key("file", "absolute-document-reference")
    if any(marker in raw_value for marker in ("$", "${", "{{", "}}", "~", "*", "?")):
        return dynamic_key("file", "dynamic-document-reference")
    base_dir = PurePosixPath(relative_path).parent
    candidate = posixpath.normpath((base_dir / raw_value).as_posix())
    if candidate.startswith("../") or candidate == "..":
        return unknown_key("file", "repo-escaping-document-reference")
    if repository_paths is not None and candidate not in repository_paths:
        # The value is still a syntactic local reference; keep a stable file target
        # so missing local artifacts are explainable without fabricating evidence.
        return file_key(candidate)
    return file_key(candidate)


def _q(prefix: str, local_name: str) -> str:
    return f"{{{ODF_NAMESPACES[prefix]}}}{local_name}"


def _odf_href_values(element: ElementTree.Element) -> list[str]:
    values: list[str] = []
    for child in element.iter():
        href = child.attrib.get(_q("xlink", "href"))
        if isinstance(href, str) and href.strip() and not _contains_secret_marker(href):
            values.append(href.strip())
    return values


def _odf_reference(
    relative_path: str,
    raw_value: str,
    *,
    source_key: str,
    repository_paths: frozenset[str] | None,
    reference_kind: str,
    resolution_reason: str,
) -> _Reference:
    return _Reference(
        source_key=source_key,
        raw_value=raw_value,
        target_key=_target_key_for_reference(relative_path, raw_value, repository_paths),
        reference_kind=reference_kind,
        resolution_reason=resolution_reason,
        line_number=1,
    )


def _odf_manifest_references(
    relative_path: str,
    manifest_root: ElementTree.Element | None,
    *,
    source_key: str,
    repository_paths: frozenset[str] | None,
) -> list[_Reference]:
    if manifest_root is None:
        return []
    references: list[_Reference] = []
    for entry in manifest_root.iter(_q("manifest", "file-entry")):
        full_path = entry.attrib.get(_q("manifest", "full-path"))
        if not isinstance(full_path, str) or not full_path or full_path in ("/", "."):
            continue
        if full_path in ODF_SAFE_PARTS or full_path.endswith("/"):
            continue
        if _contains_secret_marker(full_path):
            continue
        reference, _other_scheme = web_url_reference(full_path)
        if reference is not None and reference["reason"] == "malformed-url-redacted":
            continue
        target = ODF_INTERNAL_REFERENCE_TARGET if reference is None else reference["target"]
        redaction = _DOCUMENT_URL_REDACTIONS.get(target)
        references.append(_Reference(
            source_key=source_key,
            raw_value="<redacted-url>" if redaction else full_path,
            target_key=target,
            reference_kind="package-part",
            resolution_reason=redaction or "odf-manifest-entry",
            line_number=1,
            redacted=redaction is not None,
        ))
    return references


def _safe_summary(content: str) -> str | None:
    if _contains_secret_marker(content) or contains_web_url_userinfo(content):
        return None
    return _limited_summary(content, MAX_TEXT_SUMMARY_CHARS) or None


def _limited_summary(content: str, remaining: int) -> str:
    parts: list[str] = []
    for word in re.finditer(r"\S+", content):
        if parts:
            parts.append(" ")
            remaining -= 1
        start, end = word.span()
        part = content[start:min(end, start + remaining)]
        parts.append(part)
        remaining -= len(part)
        if remaining == 0:
            break
    return "".join(parts)


def _redacted_or_summary(value: str) -> str:
    return _safe_value_summary(value)


def _safe_value_summary(value: str) -> str:
    if _contains_secret_marker(value) or contains_web_url_userinfo(value):
        return "[redacted]"
    return _limited_summary(value, 120)


def _contains_secret_marker(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in SECRET_PRONE_MARKERS)


def _odf_element_context(root: ElementTree.Element) -> Iterator[tuple[ElementTree.Element, bool]]:
    """Visit elements in document order with only their open ancestors retained."""
    stack: list[tuple[Iterator[ElementTree.Element], bool]] = [(iter((root,)), False)]
    table_tag = _q("table", "table")
    while stack:
        siblings, inside_table = stack[-1]
        element = next(siblings, None)
        if element is None:
            stack.pop()
            continue
        yield element, inside_table
        if len(element):
            stack.append((iter(element), inside_table or element.tag == table_tag))
