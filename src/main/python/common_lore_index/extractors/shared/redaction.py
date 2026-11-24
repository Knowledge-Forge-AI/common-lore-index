"""Shared redaction predicates and safe reference projection for static extractors."""

from __future__ import annotations

import re
from collections.abc import Container
from urllib.parse import unquote, urlsplit

from common_lore_index.extractors.shared.scanner import identifier_parts
from common_lore_index.graph.keys import external_url_key, unknown_key


def secret_name_redaction_reason(
    name: str,
    secret_parts: Container[str],
    *,
    exact_names: Container[str] = (),
    exact_reason: str = "secret-like-name",
) -> str:
    """Return the redaction reason for a secret-like identifier, or ``""``."""
    if name in exact_names:
        return exact_reason
    if any(part in secret_parts for part in identifier_parts(name)):
        return "secret-like-name"
    return ""


def secret_value_redaction_reason(
    value: str,
    secret_parts: Container[str],
) -> str:
    """Return the redaction reason for a secret-like short value, or ``""``."""
    if not value:
        return ""
    if "FAKE_" in value:
        return "secret-like-value"
    if any(part in secret_parts for part in identifier_parts(value)):
        return "secret-like-value"
    return ""


_WEB_URL_USERINFO = re.compile(
    r"(?<![a-z0-9_.+/-])(?:[a-z][a-z0-9+.-]*:)?//"
    r"(?:[^\s/?#]*@|[^:\s/?#@\[\]]+:(?![0-9]+[?#])[^\s/]*@)",
    re.IGNORECASE,
)


def contains_web_url_userinfo(value: str) -> bool:
    return _WEB_URL_USERINFO.search(value) is not None or (
        "%" in value and _WEB_URL_USERINFO.search(unquote(value)) is not None
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
