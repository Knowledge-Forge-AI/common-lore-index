"""Unrelated unit-body canopy inputs; never load the registered study data."""

from __future__ import annotations

import json
from typing import Any

from common_lore_index.graph.multi_source_records import graph_source_binding_id
from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy, ResolvedBindingPolicy
from retrieval_body_values import ARMS, BodyIdentity, digest_bytes
from retrieval_public_text_excerpt import encode_model_text

GRAPH = "unit-body-canopy"


def make_body_identity() -> BodyIdentity:
    return BodyIdentity("unit-body-catalog", "unit-body-corpus", "CLOE-W5-BODY1-UNIT", GRAPH, "unit-body-")


def make_body_bindings() -> dict[str, str]:
    return {a: graph_source_binding_id(GRAPH, a) for a in ("oak", "elm", "vault")}


def make_body_policy() -> BodyProjectionPolicy:
    values = {}
    for alias, binding in make_body_bindings().items():
        privacy = "private-memory" if alias == "vault" else "public-dev"
        values[binding] = ResolvedBindingPolicy(
            binding, alias, "source", "folder", "public-dev", "public-dev", privacy,
            privacy, "full-authorized", "full-authorized",
        )
    return BodyProjectionPolicy(graph_id=GRAPH, selected_binding_ids=frozenset(values), bindings=values).validate()


def _path(number: int) -> str:
    return f"tests/test_specimen_{number}.py" if number >= 8 else f"canopy/specimen_{number}.py"


def _symbol(number: int) -> str:
    return "test_canopy" if number >= 8 else "inspect_canopy"


def _source(number: int, alias: str) -> str:
    if number < 4:
        return (
            f"def {_symbol(number)}():\n"
            f"    # unit canopy branch specimen {alias}-{number}\n"
            f"    if 'needle' in 'needle':\n"
            f"        return '{alias}-{number}'\n"
            "    return 'fallback'\n"
        )
    if number < 8:
        return (
            f"def {_symbol(number)}():\n"
            f"    # design rationale for canopy specimen {alias}-{number}\n"
            f"    message = 'canopy policy {alias}-{number}'\n"
            "    return message\n"
        )
    return (
        f"def {_symbol(number)}():\n"
        f"    # unit canopy assertion specimen {alias}-{number}\n"
        f"    observed = '{alias}-{number}'\n"
        f"    assert observed == '{alias}-{number}'\n"
        "    return None\n"
    )


def _fact(number: int, alias: str) -> str:
    if number < 4:
        return f"return '{alias}-{number}'"
    if number < 8:
        return f"message = 'canopy policy {alias}-{number}'"
    return f"assert observed == '{alias}-{number}'"


def _witness(number: int, alias: str) -> dict[str, Any]:
    path = _path(number)
    source = _source(number, alias)
    fact = _fact(number, alias)
    symbol = _symbol(number)
    module = path.removesuffix(".py").replace("/", ".")
    key = f"python.function:{module}:{symbol}"
    model = encode_model_text(key, "python.function", symbol, path, source,
                              private_markers=("UNIT_BODY_UNRELATED_DENIED_MARKER",))
    encoded = json.dumps(fact, ensure_ascii=False, separators=(",", ":"))[1:-1]
    offset = model.index(encoded, model.index('"excerpt":'))
    fact_start_line = next(index for index, line in enumerate(source.splitlines(), start=1) if line.strip() == fact)
    return {
        "binding_id": make_body_bindings()[alias], "source_relative_path": path,
        "source_sha256": digest_bytes(source.encode()), "canonical_key": key,
        "raw_source_id": f"{path}#function:1:{symbol}", "kind": "python.function",
        "display_name": symbol, "start_line": 1, "end_line": len(source.splitlines()),
        "fact_utf8": fact, "fact_start_line": fact_start_line, "fact_end_line": fact_start_line,
        "fact_source_start_byte": source.index(fact), "fact_source_end_byte": source.index(fact) + len(fact),
        "model_text_sha256": digest_bytes(model.encode()), "fact_model_start_byte": offset,
        "fact_model_bytes": len(encoded.encode()), "model_text_bytes": len(model.encode()),
        "tokenizer_count": None, "truncation_survival": "unobserved",
    }


def _gold(number: int, alias: str, grade: int = 3) -> dict[str, Any]:
    return {"locator": {"binding_id": make_body_bindings()[alias],
        "source_relative_path": _path(number), "start_line": None,
        "end_line": None, "canonical_key": None, "evidence_key": None, "link_kind": None},
        "relevance_grade": grade, "rationale": "Unrelated canopy functional gold.", "canonical_key": None}


def _row(state: str = "ready", reason: str | None = None, surface: str = "captured_text", fallback: str = "none") -> dict[str, Any]:
    return {"preparation": state, "reason": reason, "actual_surface": surface, "fallback_policy": fallback}


def _matrix(*, body: bool = True, privacy: bool = False, literal: bool = False, enumeration: bool = False) -> dict[str, Any]:
    if privacy:
        return {arm: _row("refused", "binding_privacy_not_authorized", "none") for arm in ARMS}
    rows = [_row(fallback="record_actual"),
        _row("unavailable", "captured_text_surface_unsupported_by_metadata_graph", "none") if body else _row(surface="stored_metadata"),
        _row(fallback="required" if literal or enumeration else "none"),
        _row("unavailable", "metadata_representation_not_comparable", "none") if body else _row(surface="stored_metadata"),
        _row(), _row()]
    if literal or enumeration:
        for index in (4, 5):
            rows[index] = _row("unavailable", "unsupported_semantic_query_mode", "none")
    if enumeration:
        rows[3] = _row("unavailable", "unsupported_semantic_query_mode", "none")
    return dict(zip(ARMS, rows))


def _task(number: int, family: str, golds: list[dict[str, Any]], *, body: bool = True,
          privacy: bool = False, mode: str = "ranked_discovery", rule: str = "ranked_positive") -> dict[str, Any]:
    bindings = make_body_bindings()
    question = f"Which canopy functional observation belongs to intent {number}?"
    return {
        "task_id": f"unit-body-{number:02d}", "family": family, "split": "calibration", "question": question,
        "permitted_query_plan": {"query": question, "patterns": ["canopy", "observation"],
            "fixed_strings": True, "ignore_case": True, "limit": 10, "lexical_query_style": "ranked_terms"},
        "graph_id": GRAPH, "generation": 1,
        "selected_bindings": [bindings["vault"], bindings["oak"]] if privacy else [bindings["oak"], bindings["elm"]],
        "source_path_prefix": None, "requested_mode": mode,
        "intended_match_surface": "captured_text" if body else "stored_metadata",
        "comparability_stratum": "body" if body else "metadata", "comparator_exposure": {}, "gold_targets": golds,
        "expected_capability_state": "refused" if privacy else "available",
        "expected_refusal_code": "binding_privacy_not_authorized" if privacy else None,
        "grading_rule": rule,
    }


def make_body_catalog_payload() -> dict[str, Any]:
    """Handwritten capability tables are independent of the body implementation."""
    families = ("behavior_without_exact_symbol", "code_plus_design_or_message_context", "relevant_tests_for_behavior")
    cases: list[dict[str, Any]] = []
    for index in range(12):
        number = index + 1
        specimen = index if index < 11 else 8
        alias = "oak" if index < 11 else "elm"
        other = "elm" if alias == "oak" else "oak"
        family = families[index // 4]
        cases.append({"case_kind": "body_positive", "intent_id": f"unit-body-intent-{number}",
            "wording_stratum": "literal_clue" if index % 4 < 2 else "paraphrased_intent",
            "task": _task(number, family, [_gold(specimen, alias)]), "metadata_basis": [],
            "witnesses": [_witness(specimen, alias)],
            "distractors": [{"family": family, "witness": _witness(specimen, other),
                "metadata_fit_rationale": "Independent functional fixtures have matching symbol/path metadata and contrasting body facts."}],
            "expected_capabilities": _matrix()})
    for number in range(13, 17):
        cases.append({"case_kind": "metadata_anchor", "intent_id": f"unit-body-intent-{number}",
            "wording_stratum": "not_applicable", "task": _task(number, "exact_symbol_and_partial_name", [_gold(number % 11, "oak")], body=False),
            "metadata_basis": ["binding_alias", "symbol_name", "source_relative_path"], "witnesses": [], "distractors": [],
            "expected_capabilities": _matrix(body=False)})
    family = "absent_ambiguous_changed_deleted_stale"
    for number, kind in enumerate(("absent", "absent", "ambiguous", "ambiguous", "privacy", "privacy", "unsupported", "unsupported"), 17):
        privacy, enum = kind == "privacy", number == 24
        literal = number == 23
        golds = [] if kind in {"absent", "privacy"} else [_gold(0, "oak", 2), _gold(0, "elm", 2)]
        rule = "absent_answer" if kind == "absent" else "safety_refusal" if privacy else "ranked_positive"
        mode = "current_literal_enumeration" if enum else "literal_captured_text" if literal else "ranked_discovery"
        cases.append({"case_kind": kind, "intent_id": f"unit-body-intent-{number}", "wording_stratum": "not_applicable",
            "task": _task(number, family, golds, body=not enum, privacy=privacy, mode=mode, rule=rule),
            "metadata_basis": [], "witnesses": [], "distractors": [],
            "expected_capabilities": _matrix(body=not enum, privacy=privacy, literal=literal, enumeration=enum)})
    identity = make_body_identity()
    return {"schema": "cloe-body-calibration-catalog-v1", **identity.to_dict(), "purpose": "calibration",
        "generation": 1, "repetitions": 3, "arms": list(ARMS),
        "family_rationale": {f: "Unrelated functional canopy family." for f in families}, "cases": cases}


def make_body_corpus_payload() -> dict[str, Any]:
    files = [{"binding_alias": alias, "source_relative_path": _path(number),
        "role": "test_source" if number >= 8 else "code", "content_utf8": _source(number, alias)} for alias in ("oak", "elm") for number in range(11)]
    files.extend({"binding_alias": "vault", "source_relative_path": f"inert/marker_{n}.py", "role": "denied",
        "content_utf8": f"UNIT_CANOPY_MARKER = 'synthetic-{n}'\n"} for n in (1, 2))
    identity = make_body_identity()
    return {"schema": "cloe-supplement-corpus-v1", "corpus_id": identity.corpus_id,
        "phase": identity.phase, "purpose": "calibration", "files": files}
