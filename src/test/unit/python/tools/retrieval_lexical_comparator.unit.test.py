"""Pure request preparation contract for the prospective lexical comparator."""

from dataclasses import replace

import pytest

from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from common_lore_index.retrieval.query_contracts import FallbackDisclosure, QueryMode
from retrieval_lexical_comparator import plan_lexical_comparator


def test_question_selects_distinct_complete_terms_in_input_order() -> None:
    original = LexicalQueryRequest(
        query='Where can we find "Copper", -heliotrope and COPPER or amber-fox in garnet?',
        selected_binding_ids=("core", "companion"),
        source_path_prefix="competence",
        path_prefixes=("competence/notes",),
        requested_mode=QueryMode.RANKED_DISCOVERY,
        limit=7,
    )
    planned = plan_lexical_comparator(original, shape="question")
    assert planned == replace(original, query="find OR copper OR heliotrope OR amber")
    assert original.query.startswith("Where")


def test_does_not_truncate_a_word_or_treat_a_number_as_a_prose_term() -> None:
    original = LexicalQueryRequest(
        query=f"{'a' * 33} 123 x0 e567 Copper {('b' * 32)}",
        selected_binding_ids=("core",),
        limit=10,
    )
    planned = plan_lexical_comparator(original, shape="question")
    assert planned.query == f"e567 OR copper OR {'b' * 32}"


def test_identifier_and_literal_keep_the_exact_request() -> None:
    identifier = LexicalQueryRequest(
        query="src/RelayQueueMarker.py", selected_binding_ids=("core",), limit=1,
    )
    literal = replace(
        identifier,
        query=" Étude.EXACT/17\n",
        query_style="literal_captured_text",
        requested_mode=QueryMode.LITERAL_CAPTURED_TEXT,
    )
    assert plan_lexical_comparator(identifier, shape="identifier_or_path") is identifier
    assert plan_lexical_comparator(literal, shape="literal") is literal


@pytest.mark.parametrize(
    ("query", "reason"),
    (
        ("", "query_required"),
        ("copper\x00", "query_required"),
        ("é" * 129, "query_too_long"),
        ("\ud800", "invalid_query_encoding"),
        ("étude copper", "question_requires_printable_ascii"),
        ("copper\tamber", "question_requires_printable_ascii"),
        ("How can we or they?", "question_has_no_terms"),
        ("123 45 x6", "question_has_no_terms"),
    ),
)
def test_query_refusals_are_fixed_codes(query: str, reason: str) -> None:
    request = LexicalQueryRequest(query=query, selected_binding_ids=("core",), limit=10)
    with pytest.raises(ValueError) as caught:
        plan_lexical_comparator(request, shape="question")
    assert str(caught.value) == f"lexical_comparator:{reason}"


@pytest.mark.parametrize(
    ("limit", "offset"),
    ((11, 0), (True, 0), (10, 1), (0, 0)),
)
def test_unacceptable_request_bounds_refuse(limit: int, offset: int) -> None:
    with pytest.raises(ValueError):
        plan_lexical_comparator(
            LexicalQueryRequest(
                query="copper", selected_binding_ids=("core",), limit=limit, offset=offset,
            ),
            shape="question",
        )


def test_style_mismatch_and_missing_bindings_refuse() -> None:
    query = LexicalQueryRequest(query="copper", selected_binding_ids=("core",), limit=10)
    with pytest.raises(ValueError, match="style_mismatch"):
        plan_lexical_comparator(query, shape="literal")
    with pytest.raises(ValueError, match="style_mismatch"):
        plan_lexical_comparator(replace(query, query_style="normalized_phrase"), shape="question")
    with pytest.raises(ValueError, match="invalid_service_request"):
        plan_lexical_comparator(replace(query, selected_binding_ids=()), shape="question")


def test_explicit_fallback_is_outside_the_comparator() -> None:
    fallback = FallbackDisclosure(
        True, QueryMode.LITERAL_CAPTURED_TEXT, QueryMode.RANKED_DISCOVERY, "caller_selected",
    )
    original = LexicalQueryRequest(
        query="copper", selected_binding_ids=("core",), limit=10, fallback=fallback,
    )
    with pytest.raises(ValueError, match="fallback_outside_contract"):
        plan_lexical_comparator(original, shape="identifier_or_path")
