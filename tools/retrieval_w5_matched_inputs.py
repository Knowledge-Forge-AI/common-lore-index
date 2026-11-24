"""Exact source-only custody contract for the matched W5 evaluator input."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha1, sha256
from pathlib import Path
import re
from typing import Any, cast

from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from common_lore_index.retrieval.query_contracts import QueryMode
from retrieval_lexical_comparator import ComparatorShape, plan_lexical_comparator
from retrieval_supplement_primitives import check_keys, safe_relative_path, strict_json
from retrieval_w5_matched_archive import read_matched_archive
from retrieval_w5_matched_design import Case, Slot, Stratum, Wording, plan_slots

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_GIT = re.compile(r"[0-9a-f]{40}\Z")
_ALIAS = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_SCOPE = frozenset({"public-dev", "denied"})
_REFUSAL_MODE = "evaluator_admission_refusal"
_CASE_FIELDS = ("ordinal", "stratum", "wording", "clue", "bindings", "mode")
_CORE_MEMBERS = frozenset({
    "cohort.json", "source-frame.json", "cases.json", "native-plans.json", "lexical-plans.json", "schedule.json",
})


def require_match(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("matched_input:" + code)


def digest_value(value: object) -> str:
    require_match(isinstance(value, str) and _HEX.fullmatch(value) is not None, "digest")
    return cast(str, value)


def read_json(members: Mapping[str, bytes], member: str) -> Any:
    require_match(member in members, "missing_member")
    return strict_json(members[member], maximum_bytes=256 * 1024)


def exact_rows(value: object, count: int) -> list[Any]:
    require_match(isinstance(value, list) and len(value) == count, "row_count")
    return cast(list[Any], value)


@dataclass(frozen=True, slots=True)
class InputCommitment:
    """Expected identities from the reviewed parent envelope."""

    cohort: str
    evaluation_sha256: str
    frame_sha256: str
    cases_sha256: str
    grading_sha256: str

    def __post_init__(self) -> None:
        require_match(isinstance(self.cohort, str) and _ALIAS.fullmatch(self.cohort) is not None, "cohort")
        for value in (self.evaluation_sha256, self.frame_sha256, self.cases_sha256, self.grading_sha256):
            digest_value(value)


@dataclass(frozen=True, slots=True)
class CapturedFile:
    binding: str
    path: str
    sha256: str
    content: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class TaskInput:
    case: Case
    bindings: tuple[str, ...]
    mode: str
    clue: str = field(repr=False)
    native_patterns: tuple[str, ...] = field(repr=False)
    lexical: LexicalQueryRequest | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class MatchedInput:
    commitment: InputCommitment
    public_bindings: tuple[str, ...]
    denied_bindings: tuple[str, ...]
    files: tuple[CapturedFile, ...]
    tasks: tuple[TaskInput, ...]
    slots: tuple[Slot, ...]
    _issued_identity: int | None = field(default=None, init=False, repr=False, compare=False)

    def is_factory_issued(self) -> bool:
        return self._issued_identity == id(self)


def _body(value: object, limit: int, code: str) -> str:
    require_match(
        isinstance(value, str) and 1 <= len(value) <= limit and value.isascii() and value.isprintable()
        and value == value.strip(), code,
    )
    return cast(str, value)


def _case_rows(members: Mapping[str, bytes], public: tuple[str, ...], denied: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = exact_rows(read_json(members, "cases.json"), 24)
    cases: list[dict[str, Any]] = []
    for ordinal, item in enumerate(rows, 1):
        row = check_keys(item, _CASE_FIELDS, "matched_case")
        require_match(type(row["ordinal"]) is int and row["ordinal"] == ordinal, "case_ordinal")
        clue = _body(row["clue"], 256, "case_text")
        require_match(isinstance(row["bindings"], list)
                      and all(isinstance(binding, str) for binding in row["bindings"]), "case_bindings")
        bindings = set(row["bindings"])
        require_match(len(bindings) == len(row["bindings"]), "case_bindings")
        if ordinal in (21, 22):
            require_match(bindings <= set(public + denied) and len(bindings & set(denied)) == 1
                          and len(bindings & set(public)) == (1 if ordinal == 21 else 0), "privacy_scope")
            require_match(row["mode"] == _REFUSAL_MODE, "privacy_mode")
        else:
            require_match(tuple(row["bindings"]) == public, "captured_scope")
            expected = QueryMode.LITERAL_CAPTURED_TEXT if ordinal in (23, 24) else QueryMode.RANKED_DISCOVERY
            require_match(row["mode"] == expected.value, "captured_mode")
        cases.append(row | {"clue": clue})
    privacy = set(cases[20]["bindings"] + cases[21]["bindings"])
    require_match(privacy & set(denied) == set(denied), "privacy_binding_census")
    require_match(len({" ".join(row["clue"].casefold().split()) for row in cases}) == len(cases), "case_duplicate")
    return cases


def _check_native(cases: list[dict[str, Any]], members: Mapping[str, bytes]) -> tuple[tuple[str, ...], ...]:
    plans = []
    for case, raw in zip(cases, exact_rows(read_json(members, "native-plans.json"), 24), strict=True):
        row = check_keys(raw, (
            "ordinal", "mode", "bindings", "source_path_prefix", "fixed_strings", "ignore_case", "limit", "patterns",
        ), "matched_native")
        require_match(
            type(row["ordinal"]) is int and row["ordinal"] == case["ordinal"] and row["mode"] == case["mode"]
            and row["bindings"] == case["bindings"] and row["source_path_prefix"] is None
            and row["fixed_strings"] is True and type(row["limit"]) is int and row["limit"] == 10, "native_scope",
        )
        require_match(isinstance(row["patterns"], list) and len(row["patterns"]) <= 4, "native_patterns")
        patterns = tuple(_body(item, 256, "native_pattern") for item in row["patterns"])
        mode = row["mode"]
        require_match(row["ignore_case"] is (mode == QueryMode.RANKED_DISCOVERY.value), "native_case_mode")
        if mode == _REFUSAL_MODE:
            require_match(not patterns, "native_refusal")
        elif mode == QueryMode.LITERAL_CAPTURED_TEXT.value:
            require_match(patterns == (case["clue"],), "native_literal")
        else:
            folded = tuple(s.casefold() for s in patterns)
            require_match(bool(folded) and len(set(folded)) == len(folded)
                          and all(s in case["clue"].casefold() for s in folded), "native_clue_origin")
        plans.append(patterns)
    return tuple(plans)


def _lexical_for(case: dict[str, Any]) -> tuple[LexicalQueryRequest, dict[str, Any]]:
    mode = QueryMode(case["mode"])
    style = "literal_captured_text" if mode is QueryMode.LITERAL_CAPTURED_TEXT else "ranked_terms"
    shape: ComparatorShape
    if mode is QueryMode.LITERAL_CAPTURED_TEXT:
        shape = "literal"
    elif case["stratum"] == Stratum.ANCHOR.value:
        shape = "identifier_or_path"
    else:
        shape = "question"
    prepared = plan_lexical_comparator(LexicalQueryRequest(
        query=case["clue"], query_style=style, requested_mode=mode,
        selected_binding_ids=tuple(case["bindings"]), limit=10, offset=0,
    ), shape=shape)
    return prepared, {
        "ordinal": case["ordinal"], "shape": shape, "query": prepared.query, "style": style,
        "mode": mode.value, "bindings": case["bindings"], "limit": 10, "offset": 0, "path_prefixes": [], "fallback": None,
    }


def _check_lexical(cases: list[dict[str, Any]], members: Mapping[str, bytes]) -> dict[int, LexicalQueryRequest]:
    rows = iter(exact_rows(read_json(members, "lexical-plans.json"), 22))
    result = {}
    for case in cases:
        if case["mode"] == _REFUSAL_MODE:
            continue
        prepared, expected = _lexical_for(case)
        actual = next(rows)
        require_match(type(actual) is dict and type(actual.get("ordinal")) is int
                      and type(actual.get("limit")) is int and type(actual.get("offset")) is int
                      and actual == expected, "lexical_plan")
        result[case["ordinal"]] = prepared
    return result


def _source_files(members: Mapping[str, bytes], public: tuple[str, ...]) -> tuple[CapturedFile, ...]:
    frame = check_keys(read_json(members, "source-frame.json"), ("sources", "files", "sampling_rules_sha256"), "matched_frame")
    digest_value(frame["sampling_rules_sha256"])
    sources = exact_rows(frame["sources"], len(public))
    expected = set(_CORE_MEMBERS)
    for source in sources:
        row = check_keys(source, ("binding", "repo", "commit", "tree", "license", "license_path", "license_sha256"),
                         "matched_source")
        binding = _body(row["binding"], 64, "source_binding")
        require_match(binding in public and all(isinstance(row[k], str) and _GIT.fullmatch(row[k]) is not None
                                               for k in ("commit", "tree")), "source_identity")
        _body(row["repo"], 120, "source_repo")
        _body(row["license"], 32, "source_license")
        safe_relative_path(row["license_path"])
        notice = f"notices/{binding}.txt"
        require_match(notice not in expected and notice in members
                      and sha256(members[notice]).hexdigest() == digest_value(row["license_sha256"]), "source_notice")
        expected.add(notice)
    files = []
    for item in exact_rows(frame["files"], 24):
        row = check_keys(item, ("binding", "role", "path", "blob", "bytes", "sha256", "order_key", "sample_order"),
                         "matched_file")
        binding = _body(row["binding"], 64, "file_binding")
        path = safe_relative_path(row["path"])
        member = f"population/{binding}/{path}"
        digest = digest_value(row["sha256"])
        require_match(binding in public and member not in expected and member in members, "file_scope")
        require_match(row["role"] in ("source", "docs", "tests"), "file_role")
        require_match(type(row["sample_order"]) is int and row["sample_order"] >= 1, "file_order")
        digest_value(row["order_key"])
        raw = members[member]
        require_match(type(row["bytes"]) is int and row["bytes"] == len(raw) and 1 <= len(raw) <= 4096
                      and sha256(raw).hexdigest() == digest, "file_digest")
        require_match(sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest() == row["blob"], "file_blob")
        files.append(CapturedFile(binding, path, digest, raw))
        expected.add(member)
    require_match(set(members) == expected and set(Counter(file.binding for file in files)) == set(public), "archive_inventory")
    return tuple(files)


def load_matched_input(root: Path, name: str, commitment: InputCommitment) -> MatchedInput:
    """Read a committed evaluator archive without reading grade or invoking retrieval."""
    require_match(type(commitment) is InputCommitment, "commitment")
    members = read_matched_archive(root, name, expected_sha256=commitment.evaluation_sha256)
    cohort = check_keys(read_json(members, "cohort.json"), (
        "cohort", "phase", "generation", "case_frame_sha256", "cases_sha256", "grading_commitment_sha256",
        "runtime_authorized", "binding_policy",
    ), "matched_input")
    require_match(
        cohort["cohort"] == commitment.cohort and cohort["phase"] == "CLOE-W5-HELDOUT-COHORT1"
        and type(cohort["generation"]) is int and cohort["generation"] == 1 and cohort["runtime_authorized"] is False
        and cohort["case_frame_sha256"] == commitment.frame_sha256
        and cohort["grading_commitment_sha256"] == commitment.grading_sha256
        and cohort["cases_sha256"] == commitment.cases_sha256
        and "cases.json" in members and sha256(members["cases.json"]).hexdigest() == commitment.cases_sha256,
        "cohort_binding",
    )
    policy = cohort["binding_policy"]
    require_match(type(policy) is dict and len(policy) == 4 and all(
        _ALIAS.fullmatch(key) is not None and isinstance(value, str) and value in _SCOPE
        for key, value in policy.items()
    ), "binding_policy")
    public = tuple(sorted(key for key, value in policy.items() if value == "public-dev"))
    denied = tuple(sorted(key for key, value in policy.items() if value == "denied"))
    require_match(len(public) == len(denied) == 2, "binding_census")
    files = _source_files(members, public)
    cases = _case_rows(members, public, denied)
    typed = tuple(Case(row["ordinal"], Stratum(row["stratum"]), Wording(row["wording"]) if row["wording"] is not None else None)
                  for row in cases)
    slots = plan_slots(typed)
    schedule = exact_rows(read_json(members, "schedule.json"), len(slots))
    require_match(all(type(row) is dict and type(row.get("ordinal")) is int
                      and row == {"ordinal": slot.ordinal, "arm": slot.arm.value}
                      for row, slot in zip(schedule, slots, strict=True)), "schedule_identity")
    native = _check_native(cases, members)
    lexical = _check_lexical(cases, members)
    result = MatchedInput(commitment, public, denied, files, tuple(
        TaskInput(case, tuple(row["bindings"]), row["mode"], row["clue"], patterns, lexical.get(case.ordinal))
        for case, row, patterns in zip(typed, cases, native, strict=True)
    ), slots)
    object.__setattr__(result, "_issued_identity", id(result))
    return result
