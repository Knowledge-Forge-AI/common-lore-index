"""Shared RawObservation builders for static extractors."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import unquote, urlsplit

from common_lore_index.graph.keys import external_url_key, unknown_key
from common_lore_index.observations.raw import RawObservation


_WEB_URL_USERINFO = re.compile(
    r"(?<![a-z0-9_.+/-])(?:[a-z][a-z0-9+.-]*:)?//"
    r"(?:[^\s/?#]*@|[^:\s/?#@\[\]]+:(?![0-9]+[?#])[^\s/]*@)",
    re.IGNORECASE,
)


def contains_web_url_userinfo(value: str) -> bool:
    return _WEB_URL_USERINFO.search(value) is not None or (
        "%" in value and _WEB_URL_USERINFO.search(unquote(value)) is not None
    )


def secret_like_observation(
    *,
    relative_path: str,
    line_number: int,
    name: str,
    secret_source: str,
    reason: str,
    source_prefix: str,
    extractor: str,
    extractor_version: str,
    metadata_builder: Callable[[dict[str, Any]], Mapping[str, Any]],
    slugger: Callable[[str], str],
) -> RawObservation:
    """Build a shell.secret_like observation without storing the raw value."""
    return RawObservation(
        kind="shell.secret_like",
        source_id=(
            f"{relative_path}#{source_prefix}-secret:"
            f"{line_number}:{slugger(secret_source + '-' + name)}"
        ),
        path=relative_path,
        start_line=line_number,
        end_line=line_number,
        name=name,
        confidence="heuristic",
        extractor=extractor,
        extractor_version=extractor_version,
        metadata=metadata_builder(
            {
                "secret_source": secret_source,
                "redacted": True,
                "redaction_reason": reason,
                "raw_value_stored": False,
            }
        ),
    )


def unknown_reference(target: str, reason: str) -> dict[str, str]:
    return {"kind": "unknown", "target": target, "reason": reason}


def web_url_reference(value: str) -> tuple[dict[str, str] | None, bool]:
    """Project a literal web reference and indicate whether any other scheme was present."""
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return unknown_reference(unknown_key("external.url", "malformed-external-url"), "malformed-url-redacted"), False
    if parsed.username is not None or parsed.password is not None or contains_web_url_userinfo(value):
        return unknown_reference(unknown_key("external.url", "credentialed-external-url"), "credentialed-url-redacted"), False
    if parsed.scheme in ("http", "https", "mailto"):
        return {"kind": "external.url", "target": external_url_key(value), "reason": "url-literal", "summary": value}, False
    return None, bool(parsed.scheme)
