"""Pure synthetic inputs for supplement unit tests in the distinct ``unit-supplement-`` namespace.

Everything here is invented ("lantern keeper" domain) and unrelated to the registered
``sup1-`` class: no registered loader, fixture read, query, model or runtime is used.
Expectation matrices are hand-written from the accepted contract tables rather than
derived from the implementation under test.
"""

from __future__ import annotations

from typing import Any

from common_lore_index.retrieval.projection_policy import (
    BodyProjectionPolicy,
    ResolvedBindingPolicy,
)
from retrieval_supplement_applicability import CandidateExposure
from retrieval_supplement_values import ARMS, SupplementIdentity
from common_lore_index.graph.multi_source_records import graph_source_binding_id

TEST_PREFIX = "unit-supplement-"
GRAPH_ID = "unit-supplement-graph"
CODE, DOCS, VAULT = "lanterncode", "lanterndocs", "lanternvault"
_ALIASES = (CODE, DOCS, VAULT)


def make_test_identity() -> SupplementIdentity:
    return SupplementIdentity(
        supplement_id="unit-supplement-catalog",
        corpus_id="unit-supplement-corpus",
        phase="CLOE-W5-SUPPLEMENT1-UNIT",
        graph_id=GRAPH_ID,
        task_prefix=TEST_PREFIX,
    )


def make_test_bindings() -> dict[str, str]:
    """Alias -> full binding ID."""
    return {alias: graph_source_binding_id(GRAPH_ID, alias) for alias in _ALIASES}


def make_test_policy(*, vault_privacy: str = "private-memory") -> BodyProjectionPolicy:
    bindings = make_test_bindings()
    resolved = {}
    for alias, binding_id in bindings.items():
        privacy = vault_privacy if alias == VAULT else "public-dev"
        resolved[binding_id] = ResolvedBindingPolicy(
            binding_id=binding_id, alias=alias, role="source",
            source_kind="folder", original_graph_privacy="public-dev",
            resolved_graph_privacy="public-dev", original_binding_privacy=privacy,
            resolved_binding_privacy=privacy, original_evidence_retention="full-authorized",
            resolved_evidence_retention="full-authorized",
        )
    return BodyProjectionPolicy(
        graph_id=GRAPH_ID, selected_binding_ids=frozenset(bindings.values()), bindings=resolved,
    )


_CODE_FILES: dict[str, str] = {
    "lantern/registry.py": "def register_lantern(name):\n    return {'lantern': name}\n",
    "lantern/wick_gauge.py": "class WickGauge:\n    def reading(self):\n        return 0\n",
    "lantern/dimming.py": "def lower_glow_at_dusk(level):\n    return max(level - 1, 0)\n",
    "lantern/refill.py": "def plan_oil_top_up(reservoir):\n    return reservoir < 2\n",
    "lantern/beacon_rotation.py": "def rotation_period(seconds):\n    return seconds * 2\n",
    "lantern/storm_shutter.py": "def close_shutters(wind):\n    return wind > 40\n",
    "lantern/glass_cleaning.py": "def polish_lens(lens):\n    return lens.strip()\n",
    "lantern/flame_color.py": "def flame_hue(fuel):\n    return 'amber' if fuel else 'blue'\n",
    "lantern/harbor_signal.py": "def send_harbor_signal():\n    return 'flash'\n",
    "lantern/harbor_signal_legacy.py": "def send_harbor_signal_legacy():\n    return 'flash'\n",
    "lantern/keeper_roster.py": "def keeper_roster(names):\n    return sorted(names)\n",
    "lantern/keeper_shift.py": "def keeper_shift(hour):\n    return hour % 3\n",
}
_TEST_FILES: dict[str, str] = {
    "tests/test_glass_cleaning.py": "from lantern.glass_cleaning import polish_lens\n\ndef test_polish_lens_strips():\n    assert polish_lens(' glass ') == 'glass'\n",
    "tests/test_flame_color.py": "from lantern.flame_color import flame_hue\n\ndef test_flame_hue_amber():\n    assert flame_hue('oil') == 'amber'\n    assert flame_hue(None) == 'blue'\n",
}
_DOC_FILES: dict[str, str] = {
    "docs/beacon-rotation.md": "# Beacon rotation timing\n\nRotation doubles each period.\n",
    "docs/storm-shutters.md": "# Storm shutter closing\n\nShutters close in high wind.\n",
}
_VAULT_FILES: dict[str, str] = {
    "vault/inert_marker_one.py": "UNIT_SUPPLEMENT_INERT_MARKER = 'synthetic-one'\n",
    "vault/inert_marker_two.py": "UNIT_SUPPLEMENT_INERT_MARKER = 'synthetic-two'\n",
}


def corpus_members() -> list[tuple[str, str, str, str]]:
    """``(alias, path, role, content)`` for every corpus member."""
    return (
        [(CODE, p, "code", c) for p, c in _CODE_FILES.items()]
        + [(CODE, p, "test_source", c) for p, c in _TEST_FILES.items()]
        + [(DOCS, p, "design", c) for p, c in _DOC_FILES.items()]
        + [(VAULT, p, "denied", c) for p, c in _VAULT_FILES.items()]
    )


def make_test_corpus_payload() -> dict[str, Any]:
    identity = make_test_identity()
    return {
        "schema": "cloe-supplement-corpus-v1",
        "corpus_id": identity.corpus_id,
        "phase": identity.phase,
        "purpose": "calibration",
        "files": [
            {"binding_alias": a, "source_relative_path": p, "role": r, "content_utf8": c}
            for a, p, r, c in corpus_members()
        ],
    }


def _row(execution: str, reason: str | None, surface: str, fallback: str) -> dict[str, Any]:
    return {"execution": execution, "reason": reason, "actual_surface": surface,
            "fallback_policy": fallback}


Row = dict[str, Any]


def _matrix(native: Row, graph: Row, fts: Row, v1: Row, v2: Row, fusion: Row) -> dict[str, Row]:
    return dict(zip(ARMS, (native, graph, fts, v1, v2, fusion)))


def expected_ranked() -> dict[str, dict[str, Any]]:
    return _matrix(
        _row("completed", None, "captured_text", "required"),
        _row("completed", None, "stored_metadata", "none"),
        _row("completed", None, "captured_text", "none"),
        _row("completed", None, "stored_metadata", "none"),
        _row("completed", None, "captured_text", "none"),
        _row("completed", None, "captured_text", "none"),
    )


def expected_privacy() -> dict[str, dict[str, Any]]:
    row = _row("refused", "binding_privacy_not_authorized", "none", "none")
    return {arm: dict(row) for arm in ARMS}


def expected_literal_body() -> dict[str, dict[str, Any]]:
    absent = _row("unavailable", "metadata_representation_not_comparable", "none", "none")
    return _matrix(
        _row("completed", None, "captured_text", "none"),
        _row("unavailable", "captured_text_surface_unsupported_by_metadata_graph", "none", "none"),
        _row("completed", None, "captured_text", "none"),
        dict(absent), dict(absent), dict(absent),
    )


def expected_enumeration() -> dict[str, dict[str, Any]]:
    absent = _row("unavailable", "unsupported_semantic_query_mode", "none", "none")
    return _matrix(
        _row("completed", None, "captured_text", "required"),
        _row("completed", None, "stored_metadata", "none"),
        _row("completed", None, "captured_text", "required"),
        dict(absent), dict(absent), dict(absent),
    )


def gold(alias: str, path: str, grade: int = 3) -> dict[str, Any]:
    locator = {
        "binding_id": make_test_bindings()[alias], "source_relative_path": path,
        "start_line": None, "end_line": None, "canonical_key": None, "evidence_key": None,
        "link_kind": None,
    }
    return {"locator": locator, "relevance_grade": grade,
            "rationale": "synthetic unit gold", "canonical_key": None}


def make_test_task(
    number: int, family: str, question: str, aliases: list[str], golds: list[dict[str, Any]], *,
    mode: str = "ranked_discovery", surface: str = "stored_metadata", stratum: str = "metadata",
    rule: str = "ranked_positive", style: str = "ranked_terms",
) -> dict[str, Any]:
    refused = rule == "safety_refusal"
    bindings = make_test_bindings()
    return {
        "task_id": f"{TEST_PREFIX}{number:02d}", "family": family, "split": "calibration",
        "question": question,
        "permitted_query_plan": {
            "query": question, "patterns": [question.split()[-1].strip("?")],
            "fixed_strings": True, "ignore_case": True, "limit": 10, "lexical_query_style": style,
        },
        "graph_id": GRAPH_ID, "generation": 1,
        "selected_bindings": [bindings[a] for a in aliases], "source_path_prefix": None,
        "requested_mode": mode, "intended_match_surface": surface,
        "comparability_stratum": stratum, "comparator_exposure": {}, "gold_targets": golds,
        "expected_capability_state": "refused" if refused else "available",
        "expected_refusal_code": "binding_privacy_not_authorized" if refused else None,
        "grading_rule": rule,
    }


def make_test_case(
    kind: str, intent: str, task: dict[str, Any], expectations: dict[str, Row]
) -> dict[str, Any]:
    basis = ["source_relative_path", "symbol_name"] if kind == "positive" else []
    return {"case_kind": kind, "intent_id": intent, "metadata_basis": basis, "task": task,
            "expectations": expectations}


def _cases() -> list[dict[str, Any]]:
    fam = ("exact_symbol_and_partial_name", "behavior_without_exact_symbol",
           "code_plus_design_or_message_context", "relevant_tests_for_behavior")
    other = "absent_ambiguous_changed_deleted_stale"
    positives = [
        (fam[0], "Which module defines register_lantern?", [CODE], [gold(CODE, "lantern/registry.py")]),
        (fam[0], "Where is the class named WickGauge?", [CODE], [gold(CODE, "lantern/wick_gauge.py")]),
        (fam[1], "Which code lowers the glow when dusk arrives?", [CODE], [gold(CODE, "lantern/dimming.py")]),
        (fam[1], "What decides an oil top-up for a low reservoir?", [CODE], [gold(CODE, "lantern/refill.py")]),
        (fam[2], "Which code and design section cover beacon rotation?", [CODE, DOCS],
         [gold(CODE, "lantern/beacon_rotation.py"), gold(DOCS, "docs/beacon-rotation.md")]),
        (fam[2], "Which code and design section cover storm shutters?", [CODE, DOCS],
         [gold(CODE, "lantern/storm_shutter.py"), gold(DOCS, "docs/storm-shutters.md")]),
        (fam[3], "Which tests exercise lens polishing?", [CODE], [gold(CODE, "tests/test_glass_cleaning.py")]),
        (fam[3], "Which tests exercise the flame hue?", [CODE], [gold(CODE, "tests/test_flame_color.py")]),
    ]
    cases = [
        make_test_case("positive", f"unit-positive-{i + 1}", make_test_task(i + 1, f, q, a, g),
                       expected_ranked())
        for i, (f, q, a, g) in enumerate(positives)
    ]
    absent = ("Which module powers the lantern solar panel?", "Where is the fog horn volume set?")
    for offset, question in enumerate(absent):
        task = make_test_task(9 + offset, other, question, [CODE], [], rule="absent_answer")
        cases.append(make_test_case("absent", f"unit-absent-{offset + 1}", task, expected_ranked()))
    ambiguous = (
        ("Which module sends the harbor signal?", "lantern/harbor_signal.py", "lantern/harbor_signal_legacy.py"),
        ("Which module tracks keeper duty?", "lantern/keeper_roster.py", "lantern/keeper_shift.py"),
    )
    for offset, (question, first, second) in enumerate(ambiguous):
        task = make_test_task(11 + offset, other, question, [CODE], [gold(CODE, first, 2), gold(CODE, second, 2)])
        cases.append(make_test_case("ambiguous", f"unit-ambiguous-{offset + 1}", task, expected_ranked()))
    for offset, aliases in enumerate(([VAULT], [VAULT, CODE])):
        task = make_test_task(13 + offset, other, f"Which vault marker {offset + 1} exists?", aliases, [],
                              rule="safety_refusal")
        cases.append(make_test_case("privacy", f"unit-privacy-{offset + 1}", task, expected_privacy()))
    literal = make_test_task(15, other, "Find the literal text lower_glow_at_dusk", [CODE],
                             [gold(CODE, "lantern/dimming.py", 1)], mode="literal_captured_text",
                             surface="captured_text", stratum="body", style="literal_captured_text")
    cases.append(make_test_case("unsupported", "unit-unsupported-1", literal, expected_literal_body()))
    listing = make_test_task(16, other, "Enumerate files named refill", [CODE],
                             [gold(CODE, "lantern/refill.py", 1)], mode="current_literal_enumeration",
                             style="literal_captured_text")
    cases.append(make_test_case("unsupported", "unit-unsupported-2", listing, expected_enumeration()))
    return cases


def make_test_catalog_payload() -> dict[str, Any]:
    identity = make_test_identity()
    families = ("exact_symbol_and_partial_name", "behavior_without_exact_symbol",
                "code_plus_design_or_message_context", "relevant_tests_for_behavior",
                "absent_ambiguous_changed_deleted_stale")
    return {
        "schema": "cloe-calibration-supplement-catalog-v1",
        "supplement_id": identity.supplement_id, "corpus_id": identity.corpus_id,
        "phase": identity.phase, "purpose": "calibration", "repetitions": 3, "arms": list(ARMS),
        "family_rationale": {family: f"synthetic rationale for {family}" for family in families},
        "cases": _cases(),
    }


def make_test_exposures() -> tuple[CandidateExposure, ...]:
    """Every public member in every arm (doc.section spans for Markdown); vault only in graph."""
    bindings = make_test_bindings()
    out: list[CandidateExposure] = []
    for arm in ARMS:
        for alias, path, role, _content in corpus_members():
            if role == "denied" and arm != ARMS[1]:
                continue
            markdown = path.endswith(".md")
            out.append(CandidateExposure(
                arm=arm, binding_id=bindings[alias], path=path,
                canonical_key=f"unit-supplement:{alias}:{path}",
                kind="doc.section" if markdown else "function",
                start_line=1, end_line=1 if markdown else 2,
            ))
    return tuple(out)


__all__ = [
    "make_test_bindings", "make_test_catalog_payload", "make_test_corpus_payload",
    "make_test_exposures", "make_test_identity", "make_test_policy",
]
