"""Model-free correspondence checks for the W5 body witness records.

The checker consumes only the inert catalog/corpus leaves and the maintained
Python AST extractor.  It never imports, compiles, or executes corpus source.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from common_lore_index.canonicalization.main import canonicalize_observations
from common_lore_index.extractors.documents.markdown import extract_markdown_file_observations
from common_lore_index.extractors.documents.markdown_structure import markdown_anchors_for_content
from common_lore_index.extractors.languages.python import extract_python_file_observations
from common_lore_index.extractors.languages.python_modules import PythonModuleIndex
from common_lore_index.observations.raw import RawObservation

from retrieval_body_catalog import BodyCatalog
from retrieval_body_corpus import BodyCorpus, check_body_catalog_corpus
from retrieval_body_values import BODY_FAMILIES, BODY_FLOOR, FAMILY_FLOOR, digest_bytes, require
from retrieval_body_witness_records import parse_witness
from retrieval_public_text_excerpt import map_excerpt, raw_line_count, encode_model_text
from retrieval_supplement_corpus import SupplementFile

_PUBLIC_ROLES = frozenset({"code", "test_source", "design"})
_ASSERTION_METHODS = frozenset({
    "assertEqual", "assertTrue", "assertFalse", "assertIsNone", "assertIsNotNone",
    "assertIn", "assertNotIn", "assertRaises", "assertGreater", "assertLess", "assertRegex",
})
_ESCAPED_CC = re.compile("[\\x7f-\\x9f]")

@dataclass(frozen=True)
class _MemberCapture:
    member: SupplementFile
    source: str
    tree: ast.Module | None
    observations: tuple[RawObservation, ...]

def _line_starts(raw: bytes) -> list[int]:
    starts = [0]
    starts.extend(index + 1 for index, value in enumerate(raw) if value == 0x0A)
    return starts

def _line_for(starts: list[int], offset: int) -> int:
    return bisect_right(starts, offset)


def _json_fragment(value: str) -> bytes:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)[1:-1]
    encoded = _ESCAPED_CC.sub(lambda match: f"\\u{ord(match.group(0)):04x}", encoded)
    return encoded.encode("utf-8")


def _public_captures(corpus: BodyCorpus) -> tuple[_MemberCapture, ...]:
    require(type(corpus) is BodyCorpus, "body.witness:corpus_required")
    members = tuple(member for member in corpus.files if member.role in _PUBLIC_ROLES)
    require(members, "body.witness:no_public_members")
    captures: list[_MemberCapture] = []
    by_binding: dict[str, list[SupplementFile]] = {}
    for member in members:
        by_binding.setdefault(member.binding_id, []).append(member)
    for binding_members in by_binding.values():
        sources: dict[str, str] = {}
        for member in binding_members:
            try:
                sources[member.source_relative_path] = member.content.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                require(False, "body.witness:source_utf8")
        python_paths = tuple(path for path in sources if path.endswith(".py"))
        module_index = PythonModuleIndex.from_python_paths(python_paths)
        repository_paths = frozenset(sources)
        markdown_anchors: dict[str, set[str] | frozenset[str]] = {path: markdown_anchors_for_content(source)
                            for path, source in sources.items() if path.endswith(".md")}
        for member in binding_members:
            source = sources[member.source_relative_path]
            tree: ast.Module | None = None
            observations: tuple[RawObservation, ...] = ()
            try:
                if member.source_relative_path.endswith(".py"):
                    tree = ast.parse(source, filename=member.source_relative_path)
                    observations = extract_python_file_observations(member.source_relative_path, source, module_index=module_index)
                elif member.source_relative_path.endswith(".md"):
                    observations = extract_markdown_file_observations(member.source_relative_path, source,
                                                                      repository_paths=repository_paths, markdown_anchors=markdown_anchors)
            except Exception:
                require(False, "body.witness:source_parse")
            captures.append(_MemberCapture(member, source, tree, observations))
    return tuple(captures)

def _add_inventory_item(
    groups: dict[tuple[str, str, str], dict[str, Any]],
    *, binding_id: str, canonical_key: str, display_name: str, kind: str,
    path: str | None, source_id: str | None, start_line: int | None, end_line: int | None,
) -> None:
    key = (binding_id, canonical_key, kind)
    item = groups.setdefault(key, {
        "binding_id": binding_id, "canonical_key": canonical_key, "display_name": display_name,
        "kind": kind, "paths": set(), "public": True, "spans": [],
    })
    if path is not None:
        item["paths"].add(path)
    if source_id is not None:
        require((start_line is None and end_line is None) or (
            type(start_line) is int and type(end_line) is int and 1 <= start_line <= end_line
        ), "body.witness:observation_span")
        item["spans"].append({
            "end_line": end_line, "path": path, "source_id": source_id, "start_line": start_line,
        })


def _freeze_inventory(groups: dict[tuple[str, str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for key in sorted(groups):
        item = groups[key]
        spans = sorted(item["spans"], key=lambda row: (
            row["path"], row["start_line"] is None, row["start_line"] or 0,
            row["end_line"] or 0, row["source_id"],
        ))
        result.append({
            "binding_id": item["binding_id"], "canonical_key": item["canonical_key"],
            "display_name": item["display_name"], "kind": item["kind"],
            "paths": sorted(item["paths"]), "public": True, "spans": spans,
        })
    return result

def _copy_inventory_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "binding_id": item["binding_id"], "canonical_key": item["canonical_key"],
        "display_name": item["display_name"], "kind": item["kind"],
        "paths": list(item["paths"]), "public": item["public"],
        "spans": [dict(span) for span in item["spans"]],
    }

def _canonical_graph(captures: tuple[_MemberCapture, ...], binding_id: str) -> Any:
    observations: list[RawObservation] = []
    for capture in captures:
        observations.extend(capture.observations)
    try:
        result = canonicalize_observations(observations, repository_scope=binding_id)
    except Exception:
        require(False, "body.witness:canonicalization")
    require(result.ok, "body.witness:canonicalization")
    return result.graph

def _inventory(captures: Iterable[_MemberCapture], *, denied_member_count: int = 0) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    emitted_kinds: set[str] = set()
    capture_rows = tuple(captures)
    by_binding: dict[str, list[_MemberCapture]] = {}
    for capture in capture_rows:
        by_binding.setdefault(capture.member.binding_id, []).append(capture)
        emitted_kinds.update(observation.kind for observation in capture.observations)
    for binding_id, binding_captures in by_binding.items():
        graph = _canonical_graph(tuple(binding_captures), binding_id)
        evidence_by_key = {evidence.evidence_key: evidence for evidence in graph.evidence}
        links_by_node: dict[str, list[Any]] = {}
        for link in graph.node_evidence_links:
            links_by_node.setdefault(link.canonical_key, []).append(link)
        for node in graph.nodes:
            # Canonical file nodes may be spanless and have no observed links.
            # Count them without inventing an observation or a witness path.
            _add_inventory_item(groups, binding_id=binding_id, canonical_key=node.canonical_key,
                                display_name=node.display_name, kind=node.kind, path=None,
                                source_id=None, start_line=None, end_line=None)
            seen_spans: set[tuple[str, str, int | None, int | None]] = set()
            for link in links_by_node.get(node.canonical_key, ()):
                if link.link_kind in {"inferred", "inferred_from_edge"}:
                    continue
                evidence = evidence_by_key.get(link.evidence_key)
                require(evidence is not None, "body.witness:canonical_evidence")
                assert evidence is not None
                span_key = (evidence.path, evidence.raw_source_id, evidence.start_line, evidence.end_line)
                if span_key in seen_spans:
                    continue
                seen_spans.add(span_key)
                _add_inventory_item(groups, binding_id=binding_id, canonical_key=node.canonical_key,
                                    display_name=node.display_name, kind=node.kind, path=evidence.path,
                                    source_id=evidence.raw_source_id, start_line=evidence.start_line,
                                    end_line=evidence.end_line)
    v1 = _freeze_inventory(groups)
    v2 = [_copy_inventory_item(item) for item in v1 if any(
        type(span["start_line"]) is int and type(span["end_line"]) is int
        for span in item["spans"]
    )]
    require(len(v1) <= 80, "body.witness:v1_pair_cap")
    require(len(v2) <= 80, "body.witness:v2_pair_cap")
    return {
        "schema": "cloe-body-static-inventory-v1", "inventory": v1, "spanned_inventory": v2,
        "public_v1_pairs": len(v1), "public_v2_span_pairs": len(v2),
        "public_file_count": len(capture_rows), "emitted_observation_count": sum(len(c.observations) for c in capture_rows),
        "emitted_kinds": sorted(emitted_kinds), "canonicalized_kinds": sorted({item["kind"] for item in v1}),
        "denied_member_count": denied_member_count, "source_only": True, "model_free": True,
        "queries_executed": False, "model_loaded": False, "tokenizer_observed": False,
        "native_qualified": False, "publication_observed": False,
        "accepted_publication_or_native_qualification": False,
        "tokenizer_count": None, "truncation_survival": "unobserved",
    }


def capture_body_inventory(corpus: BodyCorpus) -> dict[str, Any]:
    """Return a fresh public-only v1/v2 static inventory; denied bytes are never scanned."""
    require(type(corpus) is BodyCorpus, "body.witness:corpus_required")
    denied = sum(1 for member in corpus.files if member.role == "denied")
    return _inventory(_public_captures(corpus), denied_member_count=denied)


def _function_observation(capture: _MemberCapture, witness: dict[str, Any]) -> RawObservation:
    matches = tuple(
        observation for observation in capture.observations
        if observation.kind == "python.function" and observation.source_id == witness["raw_source_id"]
    )
    require(len(matches) == 1, "body.witness:function_observation")
    observation = matches[0]
    require(observation.target == witness["canonical_key"], "body.witness:canonical_key")
    require(observation.name == witness["display_name"], "body.witness:display_name")
    require((observation.start_line, observation.end_line) == (witness["start_line"], witness["end_line"]), "body.witness:span")
    return observation


def _function_node(tree: ast.Module | None, observation: RawObservation) -> ast.FunctionDef | ast.AsyncFunctionDef:
    require(tree is not None, "body.witness:function_ast")
    assert tree is not None
    nodes = tuple(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == observation.name and node.lineno == observation.start_line
    )
    require(len(nodes) == 1, "body.witness:function_ast")
    return nodes[0]


def _assertion_ranges(node: ast.AST) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            ranges.append((child.lineno, getattr(child, "end_lineno", child.lineno) or child.lineno))
        elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr in _ASSERTION_METHODS:
            ranges.append((child.lineno, getattr(child, "end_lineno", child.lineno) or child.lineno))
    return tuple(ranges)


def _context_present(source: str, node: ast.AST, fact_start_line: int) -> bool:
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", start) or start
    try:
        comments = tuple(token.start[0] for token in tokenize.generate_tokens(io.StringIO(source).readline)
                         if token.type == tokenize.COMMENT and start < token.start[0] <= end)
    except (IndentationError, tokenize.TokenError):
        require(False, "body.witness:context_parse")
    if comments:
        return True
    docstring: ast.Constant | None = None
    body = getattr(node, "body", ())
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        docstring = body[0].value
    return any(
        isinstance(child, ast.Constant) and isinstance(child.value, str)
        and start < (getattr(child, "lineno", 0) or 0) <= end
        and child is not docstring
        for child in ast.walk(node)
    )


def _check_model_and_fact(
    capture: _MemberCapture, observation: RawObservation, witness: dict[str, Any], family: str,
) -> None:
    raw = capture.member.content
    starts = _line_starts(raw)
    fact = witness["fact_utf8"]
    fact_bytes = fact.encode("utf-8")
    source_start, source_end = witness["fact_source_start_byte"], witness["fact_source_end_byte"]
    assert observation.start_line is not None and observation.end_line is not None
    span_start = starts[observation.start_line - 1]
    span_end = starts[observation.end_line] if observation.end_line < len(starts) else len(raw)
    body_start = starts[observation.start_line] if observation.start_line < len(starts) else len(raw)
    require(span_start < span_end and body_start <= source_start < source_end <= span_end, "body.witness:fact_bounds")
    require(raw[source_start:source_end] == fact_bytes, "body.witness:fact_source")
    require(_line_for(starts, source_start) == witness["fact_start_line"], "body.witness:fact_start_line")
    require(_line_for(starts, source_end - 1) == witness["fact_end_line"], "body.witness:fact_end_line")
    body = raw[body_start:span_end]
    require(body.count(fact_bytes) == 1 and body.find(fact_bytes) == source_start - body_start, "body.witness:fact_unique")
    span, reason = map_excerpt(
        raw, observation.start_line, observation.end_line,
        projected_raw_line_count=raw_line_count(raw), private_markers=(),
    )
    require(span is not None and reason is None, "body.witness:span_mapping")
    assert span is not None
    require(not span.span_cut, "body.witness:span_cut")
    try:
        model_text = encode_model_text(
            witness["canonical_key"], witness["kind"], witness["display_name"],
            witness["source_relative_path"], span.excerpt, private_markers=(),
        )
    except (TypeError, ValueError):
        require(False, "body.witness:model_text")
    model_bytes = model_text.encode("utf-8")
    require(digest_bytes(model_bytes) == witness["model_text_sha256"], "body.witness:model_text_sha256")
    require(len(model_bytes) == witness["model_text_bytes"], "body.witness:model_text_size")
    excerpt_marker = b'"excerpt":"'
    try:
        prefix = raw[span_start:source_start].decode("utf-8")
        fragment = _json_fragment(fact)
        expected_offset = model_bytes.index(excerpt_marker) + len(excerpt_marker) + len(_json_fragment(prefix))
    except (UnicodeDecodeError, ValueError):
        require(False, "body.witness:model_fact")
    require(model_bytes[expected_offset:expected_offset + len(fragment)] == fragment, "body.witness:model_fact")
    require(expected_offset == witness["fact_model_start_byte"], "body.witness:model_fact_offset")
    require(len(fragment) == witness["fact_model_bytes"], "body.witness:model_fact_size")
    node = _function_node(capture.tree, observation)
    if family == "relevant_tests_for_behavior":
        assertions = tuple(
            item for item in capture.observations
            if item.kind == "python.test_assertion" and item.path == observation.path
            and item.start_line == observation.start_line and item.end_line == observation.end_line
            and isinstance(item.metadata.get("assertion_count"), int) and item.metadata["assertion_count"] > 0
        )
        require(assertions and any(
            witness["fact_start_line"] >= start and witness["fact_end_line"] <= end
            for start, end in _assertion_ranges(node)
        ), "body.witness:test_assertion")
    elif family == "code_plus_design_or_message_context":
        require(_context_present(capture.source, node, witness["fact_start_line"]), "body.witness:context_evidence")


def _check_one(
    row: dict[str, Any], *, family: str, expected_role: str,
    captures: dict[tuple[str, str], _MemberCapture],
) -> None:
    witness = parse_witness(row)
    key = (witness["binding_id"], witness["source_relative_path"])
    capture = captures.get(key)
    require(capture is not None and capture.member.role == expected_role, "body.witness:membership_or_role")
    assert capture is not None
    require(digest_bytes(capture.member.content) == witness["source_sha256"], "body.witness:source_sha256")
    observation = _function_observation(capture, witness)
    _check_model_and_fact(capture, observation, witness, family)


def verify_body_witnesses(catalog: BodyCatalog, corpus: BodyCorpus) -> dict[str, Any]:
    """Recompute all public witnesses and return a fresh source-only report."""
    require(BodyCatalog.is_factory_issued(catalog), "body.witness:catalog_required")
    require(type(corpus) is BodyCorpus, "body.witness:corpus_required")
    check_body_catalog_corpus(catalog, corpus)
    captures_tuple = _public_captures(corpus)
    denied = sum(1 for member in corpus.files if member.role == "denied")
    inventory = _inventory(captures_tuple, denied_member_count=denied)
    captures = {(capture.member.binding_id, capture.member.source_relative_path): capture for capture in captures_tuple}
    body_cases = [case for case in catalog.cases if case.kind == "body_positive"]
    checked_intents: set[str] = set()
    family_counts = {family: 0 for family in BODY_FAMILIES}
    seen_witnesses: set[tuple[str, str]] = set()
    seen_distractors: set[tuple[str, str]] = set()
    for case in body_cases:
        row = case.to_dict()
        family = case.task.family
        expected_role = "test_source" if family == "relevant_tests_for_behavior" else "code"
        witnesses = tuple(parse_witness(item) for item in row["witnesses"])
        distractors = tuple(parse_witness(item["witness"]) for item in row["distractors"])
        for witness in witnesses:
            key = (witness["binding_id"], witness["source_relative_path"])
            require(key not in seen_witnesses, "body.witness:duplicate_witness")
            seen_witnesses.add(key)
            _check_one(witness, family=family, expected_role=expected_role, captures=captures)
        for distractor in distractors:
            key = (distractor["binding_id"], distractor["source_relative_path"])
            require(key not in seen_distractors, "body.witness:duplicate_distractor")
            seen_distractors.add(key)
            _check_one(distractor, family=family, expected_role=expected_role, captures=captures)
        checked_intents.add(row["intent_id"])
        family_counts[family] += 1
    require(len(checked_intents) >= BODY_FLOOR, "body.witness:body_floor")
    require(all(count >= FAMILY_FLOOR for count in family_counts.values()), "body.witness:family_floor")
    report = dict(inventory)
    report.update({
        "body_cases": len(body_cases), "witnesses_checked": len(seen_witnesses),
        "distractors_checked": len(seen_distractors), "body_file_witnesses_statically_reached": True,
        "distinct_intents": len(checked_intents), "family_counts": dict(family_counts),
        "body_floor": {"minimum": BODY_FLOOR, "observed": len(checked_intents), "met": True},
        "family_floor": {"minimum": FAMILY_FLOOR, "observed": dict(family_counts), "met": True},
        "unobserved": ["tokenizer_count", "truncation_survival", "retrieval", "model", "native", "publication"],
    })
    return report


__all__ = ["capture_body_inventory", "verify_body_witnesses"]
