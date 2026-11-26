"""Unrelated functional body-pin inputs; no registered study bytes are used."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from common_lore_index.graph.multi_source_records import graph_source_binding_id
from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy, ResolvedBindingPolicy
from retrieval_body_catalog import parse_functional_catalog
from retrieval_body_corpus import parse_body_corpus
from retrieval_body_input_records import build_body_input_pin
from retrieval_body_values import ARMS, BodyIdentity, canonical_bytes, digest_bytes
from retrieval_body_witnesses import capture_body_inventory
from retrieval_supplement_files import publish_member


GRAPH = "unit-body-canopy"
PREDECESSOR_BASE = "src/test/fixtures/retrieval_evaluation/"
PREDECESSOR_CATALOGS = {
    PREDECESSOR_BASE + "context-v1/task_catalog.json": tuple(f"cal-{n:02d}" for n in range(1, 4)),
    PREDECESSOR_BASE + "mixed-v1/tasks/catalog-v1.json": tuple(f"mix-{n:02d}" for n in range(1, 4)),
    PREDECESSOR_BASE + "supplement-v1/catalog-v1.json": tuple(f"sup1-old-{n:02d}" for n in range(1, 4)),
}


def make_body_identity() -> BodyIdentity:
    return BodyIdentity("unit-body-catalog", "unit-body-corpus", "CLOE-W5-BODY1-UNIT", GRAPH, "unit-body-")


def make_body_bindings() -> dict[str, str]:
    return {alias: graph_source_binding_id(GRAPH, alias) for alias in ("oak", "elm", "vault")}


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
            "    if 'needle' in 'needle':\n"
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
    from retrieval_public_text_excerpt import encode_model_text

    model = encode_model_text(key, "python.function", symbol, path, source, private_markers=())
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
        "source_relative_path": _path(number), "start_line": None, "end_line": None,
        "canonical_key": None, "evidence_key": None, "link_kind": None},
        "relevance_grade": grade, "rationale": "Unrelated canopy functional gold.", "canonical_key": None}


def _row(
    state: str = "ready", reason: str | None = None, surface: str = "captured_text", fallback: str = "none",
) -> dict[str, Any]:
    return {"preparation": state, "reason": reason, "actual_surface": surface, "fallback_policy": fallback}


def _matrix(
    *, body: bool = True, privacy: bool = False, literal: bool = False, enumeration: bool = False,
) -> dict[str, Any]:
    if privacy:
        return {arm: _row("refused", "binding_privacy_not_authorized", "none") for arm in ARMS}
    rows = [_row(fallback="record_actual"),
            (_row("unavailable", "captured_text_surface_unsupported_by_metadata_graph", "none")
             if body else _row(surface="stored_metadata")),
            _row("ready", fallback="required" if literal or enumeration else "none"),
            (_row("unavailable", "metadata_representation_not_comparable", "none")
             if body else _row(surface="stored_metadata")),
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
                "metadata_fit_rationale": "Independent fixtures have matching metadata and contrasting body facts."}],
            "expected_capabilities": _matrix()})
    for number in range(13, 17):
        cases.append({"case_kind": "metadata_anchor", "intent_id": f"unit-body-intent-{number}",
            "wording_stratum": "not_applicable", "task": _task(number, "exact_symbol_and_partial_name",
            [_gold(number % 11, "oak")], body=False),
            "metadata_basis": ["binding_alias", "symbol_name", "source_relative_path"],
            "witnesses": [], "distractors": [], "expected_capabilities": _matrix(body=False)})
    family = "absent_ambiguous_changed_deleted_stale"
    kinds = ("absent", "absent", "ambiguous", "ambiguous", "privacy", "privacy", "unsupported", "unsupported")
    for number, kind in enumerate(kinds, 17):
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
        "role": "test_source" if number >= 8 else "code", "content_utf8": _source(number, alias)}
        for alias in ("oak", "elm") for number in range(11)]
    files.extend({"binding_alias": "vault", "source_relative_path": f"inert/marker_{n}.py", "role": "denied",
        "content_utf8": f"UNIT_CANOPY_MARKER = 'synthetic-{n}'\n"} for n in (1, 2))
    identity = make_body_identity()
    return {"schema": "cloe-supplement-corpus-v1", "corpus_id": identity.corpus_id,
        "phase": identity.phase, "purpose": "calibration", "files": files}


def make_body_predecessor_files() -> dict[str, bytes]:
    return {
        path: canonical_bytes({"tasks": [{"task_id": task_id} for task_id in task_ids]})
        for path, task_ids in PREDECESSOR_CATALOGS.items()
    }


def make_body_predecessor_manifest(files: dict[str, bytes] | None = None) -> dict[str, Any]:
    files = make_body_predecessor_files() if files is None else files
    return {"commit": "a" * 40, "files": [
        {"path": path, "bytes": len(raw), "sha256": digest_bytes(raw)[len("sha256:"):]}
        for path, raw in sorted(files.items())
    ]}


def make_body_gate_payload() -> dict[str, Any]:
    return {"phase": make_body_identity().phase, "reviewed_design_sha256": "1" * 64,
            "clarified_design_sha256": "2" * 64, "review_result_sha256": "3" * 64,
            "parent_bridge_sha256": "4" * 64, "independent_pre_authoring_pass": True,
            "authoring_after_gate": True}


def make_body_namespace_payload(files: dict[str, bytes] | None = None) -> dict[str, Any]:
    predecessor = make_body_predecessor_files() if files is None else files
    catalog = make_body_catalog_payload()
    identity = make_body_identity()
    task_ids = [case["task"]["task_id"] for case in catalog["cases"]]
    return {"collision_count": 0, "held_out_values_decoded": False,
        "identity": {"corpus_id": identity.corpus_id, "graph_id": identity.graph_id,
            "phase": identity.phase, "supplement_id": identity.class_id, "task_prefix": identity.task_prefix},
        "old_task_id_tokens_observed": sum(len(PREDECESSOR_CATALOGS[path]) for path in PREDECESSOR_CATALOGS),
        "original_catalog_hashes": {path: digest_bytes(predecessor[path])[len("sha256:"):]
                                     for path in sorted(predecessor)},
        "previous_ids_check": "complete ID-token-only collision verification; JSON escapes decoded for task IDs only",
        "task_ids": task_ids}


def make_body_static_inventory_payload() -> dict[str, Any]:
    identity = make_body_identity()
    bindings = make_body_bindings()
    catalog = parse_functional_catalog(canonical_bytes(make_body_catalog_payload()), identity=identity,
                                       binding_ids=tuple(bindings.values()))
    corpus = parse_body_corpus(canonical_bytes(make_body_corpus_payload()), identity=identity, bindings=bindings)
    fresh = capture_body_inventory(corpus)
    denied_binding = bindings["vault"]
    private_rows = [
        {"binding_id": denied_binding, "canonical_key": f"file:inert/marker_{number}.py",
         "display_name": f"inert/marker_{number}.py", "kind": "file", "paths": [],
         "public": False, "spans": []}
        for number in (1, 2)
    ]
    private_rows.extend(
        {"binding_id": denied_binding, "canonical_key": f"python.module:inert.marker_{number}",
         "display_name": f"inert.marker_{number}", "kind": "python.module", "paths": [],
         "public": False, "spans": []}
        for number in (1, 2)
    )
    aliases = {binding: alias for alias, binding in bindings.items()}
    gold_counts: Counter[str] = Counter()
    for case in catalog.cases:
        if case.kind == "body_positive":
            for gold in case.task.gold_targets:
                gold_counts[aliases[gold.locator.binding_id]] += 1
    return {"schema": "cloe-body-static-inventory-v1", "inventory": fresh["inventory"] + private_rows,
        "public_v1_pairs": fresh["public_v1_pairs"],
        "public_v2_span_pairs": len(fresh["spanned_inventory"]), "metadata_anchors": 4,
        "body_cases": 12, "controls": 8,
        "combined_upper_bound": 2 * (fresh["public_v1_pairs"] + len(catalog.cases)),
        "gold_region_counts": dict(sorted(gold_counts.items())),
        "all12_body_file_witnesses_statically_reached": True, "model_free": True,
        "queries_executed": False, "accepted_publication_or_native_qualification": False,
        "truncation_survival": "unobserved; later tokenizer required", "wording_by_family": {
        family: {"attrition": [], "literal_clue": 2, "paraphrased_intent": 2, "surviving": 4}
        for family in (
            "behavior_without_exact_symbol", "code_plus_design_or_message_context", "relevant_tests_for_behavior",
        )
    }}


def make_body_input_files() -> dict[str, bytes]:
    identity = make_body_identity()
    bindings = make_body_bindings()
    policy = make_body_policy()
    predecessor_files = make_body_predecessor_files()
    catalog_payload = make_body_catalog_payload()
    corpus_payload = make_body_corpus_payload()
    files: dict[str, bytes] = {
        "bindings-v1.json": canonical_bytes(bindings),
        "catalog-v1.json": canonical_bytes(catalog_payload),
        "corpus-v1.json": canonical_bytes(corpus_payload),
        "namespace-v1.json": canonical_bytes(make_body_namespace_payload(predecessor_files)),
        "policy-v1.json": canonical_bytes(policy.to_dict()),
        "preauthoring-gate-v1.json": canonical_bytes(make_body_gate_payload()),
        "predecessor-manifest.json": canonical_bytes(make_body_predecessor_manifest(predecessor_files)),
        "rationale.md": b"# Functional canopy fixture\n",
        "static-inventory-v1.json": canonical_bytes(make_body_static_inventory_payload()),
    }
    catalog = parse_functional_catalog(files["catalog-v1.json"], identity=identity,
                                       binding_ids=tuple(bindings.values()))
    files["input-pin-v1.json"] = canonical_bytes(build_body_input_pin(catalog, input_files=files, policy=policy))
    return files


def materialize_body_inputs(root: Path, files: dict[str, bytes]) -> None:
    """Test-only byte publication into a caller-owned temporary root."""
    root.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        publish_member(root, name, raw)


def materialize_body_predecessors(root: Path, files: dict[str, bytes] | None = None) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    files = make_body_predecessor_files() if files is None else files
    for path, raw in files.items():
        publish_member(root, path, raw)


__all__ = [
    "GRAPH", "make_body_bindings", "make_body_catalog_payload", "make_body_corpus_payload",
    "make_body_gate_payload", "make_body_identity", "make_body_input_files", "make_body_namespace_payload",
    "make_body_policy", "make_body_predecessor_files", "make_body_predecessor_manifest",
    "make_body_static_inventory_payload", "materialize_body_inputs", "materialize_body_predecessors",
]
