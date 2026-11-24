"""Inert request planner for the public-lexical-comparator-v1 contract."""

from dataclasses import replace
import re
from typing import Literal

from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest

ComparatorShape = Literal["question", "identifier_or_path", "literal"]

_REQUIRED_STYLE = {
    "question": "ranked_terms",
    "identifier_or_path": "ranked_terms",
    "literal": "literal_captured_text",
}
_QUESTION_TOKEN = re.compile(r"[a-z0-9]+")
_FUNCTION_WORDS = frozenset(
    """
    a about am an and are as at be been being but by can could did do does for
    from had has have how i if in into is it its may might must no nor not of on
    or our shall should so than that the their them then there these they this
    those through to under up upon us was we were what when where which who whom
    why will with would you your
    """.split()
)
_MAX_QUERY_BYTES = 256
_MAX_QUESTION_TERMS = 4
_MAX_RESULT_HITS = 10


def _require_query_text(query: str) -> None:
    if not isinstance(query, str) or not query or "\x00" in query:
        raise ValueError("lexical_comparator:query_required")
    if len(query) > _MAX_QUERY_BYTES:
        raise ValueError("lexical_comparator:query_too_long")
    try:
        size = len(query.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("lexical_comparator:invalid_query_encoding") from None
    if size > _MAX_QUERY_BYTES:
        raise ValueError("lexical_comparator:query_too_long")


def _render_question(question: str) -> str:
    if not question.isascii() or not question.isprintable():
        raise ValueError("lexical_comparator:question_requires_printable_ascii")
    words = _QUESTION_TOKEN.findall(question.lower())
    distinct_terms = dict.fromkeys(
        word for word in words
        if 3 <= len(word) <= 32 and word[0].isalpha() and word not in _FUNCTION_WORDS
    )
    terms = list(distinct_terms)[:_MAX_QUESTION_TERMS]
    if not terms:
        raise ValueError("lexical_comparator:question_has_no_terms")
    return " OR ".join(terms)


def plan_lexical_comparator(
    request: LexicalQueryRequest,
    *,
    shape: ComparatorShape,
) -> LexicalQueryRequest:
    """Plan a bounded request without reading source, ranking, or opening a store."""
    required_style = _REQUIRED_STYLE.get(shape)
    if required_style is None:
        raise ValueError("lexical_comparator:unknown_shape")
    if request.query_style != required_style:
        raise ValueError("lexical_comparator:style_mismatch")
    if request.fallback is not None:
        raise ValueError("lexical_comparator:fallback_outside_contract")
    _require_query_text(request.query)
    try:
        request.validate()
    except ValueError:
        raise ValueError("lexical_comparator:invalid_service_request") from None
    if request.limit > _MAX_RESULT_HITS or request.offset != 0:
        raise ValueError("lexical_comparator:bounds_outside_contract")
    if shape == "question":
        return replace(request, query=_render_question(request.query))
    return request
