"""Closed body catalog parsing and immutable factory-issued catalog records."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from typing import Any

from retrieval_evaluation_schema import EvaluationTask
from retrieval_body_capabilities import expected_capabilities
from retrieval_body_tasks import parse_body_task
from retrieval_body_values import (
    ARMS, BODY_FAMILIES, CASE_KINDS, CATALOG_SCHEMA, MAX_CASES, REGISTERED_IDENTITY,
    REGISTERED_INPUT_PIN_SHA256, WORDINGS, BodyIdentity, canonical_bytes, check_keys,
    digest_bytes, integer, require, safe_id, strict_json, string_list, text,
)
from retrieval_body_witness_records import parse_distractor, parse_witness

ROOT_FIELDS = (
    "schema", "class_id", "corpus_id", "phase", "purpose", "graph_id", "generation",
    "task_prefix", "repetitions", "arms", "family_rationale", "cases",
)
CASE_FIELDS = (
    "case_kind", "intent_id", "wording_stratum", "task", "metadata_basis",
    "witnesses", "distractors", "expected_capabilities",
)
_TOKEN = object()


class BodyCase:
    """Own canonical bytes; every caller receives fresh mutable leaf values."""

    __slots__ = ("_raw",)
    _raw: bytes

    def __init__(self, value: dict[str, Any], *, _token: object = None) -> None:
        require(_token is _TOKEN, "body.case:factory_required")
        object.__setattr__(self, "_raw", canonical_bytes(value))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("BodyCase is immutable")

    def to_dict(self) -> dict[str, Any]:
        return dict(strict_json(self._raw))

    @property
    def task(self) -> EvaluationTask:
        return EvaluationTask.from_mapping(self.to_dict()["task"])

    @property
    def kind(self) -> str:
        return str(self.to_dict()["case_kind"])

    @property
    def digest(self) -> str:
        return digest_bytes(self._raw)


class BodyCatalog:
    """Parsing is not registered admission, model permission or execution proof."""

    __slots__ = ("_raw", "_identity", "_cases", "_pin", "_issued")
    _raw: bytes
    _identity: BodyIdentity
    _cases: tuple[BodyCase, ...]
    _pin: str | None
    _issued: object

    def __init__(
        self, raw: bytes, identity: BodyIdentity, cases: tuple[BodyCase, ...],
        *, pin: str | None = None, _token: object = None,
    ) -> None:
        require(_token is _TOKEN, "body.catalog:factory_required")
        for name, value in (("_raw", raw), ("_identity", identity), ("_cases", cases), ("_pin", pin), ("_issued", _TOKEN)):
            object.__setattr__(self, name, value)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("BodyCatalog is immutable")

    @staticmethod
    def is_factory_issued(value: object) -> bool:
        return type(value) is BodyCatalog and getattr(value, "_issued", None) is _TOKEN

    @property
    def registered(self) -> bool:
        return self.is_factory_issued(self) and self._identity == REGISTERED_IDENTITY and self._pin == REGISTERED_INPUT_PIN_SHA256

    @property
    def identity(self) -> BodyIdentity:
        return self._identity

    @property
    def raw(self) -> bytes:
        return self._raw

    @property
    def digest(self) -> str:
        return digest_bytes(self._raw)

    @property
    def cases(self) -> tuple[BodyCase, ...]:
        return self._cases

    def to_dict(self) -> dict[str, Any]:
        return dict(strict_json(self._raw))


def _body_shape(row: dict[str, Any], task: EvaluationTask) -> None:
    witnesses, distractors = row["witnesses"], row["distractors"]
    require(type(witnesses) is list and type(distractors) is list, "body.case:witness_lists")
    if row["case_kind"] != "body_positive":
        require(not witnesses and not distractors, "body.case:unexpected_witness")
        return
    require(1 <= len(witnesses) <= 16 and 1 <= len(distractors) <= 16, "body.case:witness_required")
    golds = {gold.locator.file_identity_key() for gold in task.gold_targets}
    checked = [parse_witness(item) for item in witnesses]
    require({(w["binding_id"], w["source_relative_path"]) for w in checked} == golds, "body.case:gold_witness_coverage")
    require(all(w["binding_id"] in task.selected_bindings for w in checked), "body.case:witness_selection")
    for item in distractors:
        contrast = parse_distractor(item, family=task.family)["witness"]
        require(contrast["binding_id"] in task.selected_bindings, "body.case:distractor_selection")
        require((contrast["binding_id"], contrast["source_relative_path"]) not in golds, "body.case:distractor_is_gold")
        metadata = (contrast["source_relative_path"], contrast["kind"], contrast["display_name"])
        require(any(metadata == (w["source_relative_path"], w["kind"], w["display_name"]) and contrast["fact_utf8"] != w["fact_utf8"] for w in checked), "body.case:distractor_metadata_or_fact")


def _case(value: object, identity: BodyIdentity, binding_ids: Collection[str]) -> BodyCase:
    row = dict(check_keys(value, CASE_FIELDS, "body.case"))
    kind, wording = row["case_kind"], row["wording_stratum"]
    require(type(kind) is str and kind in CASE_KINDS, "body.case:kind")
    require(type(wording) is str and wording in WORDINGS, "body.case:wording")
    require((wording == "not_applicable") == (kind != "body_positive"), "body.case:wording_role")
    safe_id(row["intent_id"], "body.case.intent")
    task = parse_body_task(row["task"], kind=kind, identity=identity, binding_ids=binding_ids)
    basis = string_list(row["metadata_basis"], "body.case.metadata_basis", minimum=0, maximum=8, item_max=128)
    if kind == "metadata_anchor":
        require(set(basis) == {"binding_alias", "symbol_name", "source_relative_path"}, "body.case:anchor_basis")
    else:
        require(not basis, "body.case:body_not_metadata")
    _body_shape(row, task)
    expected = expected_capabilities(task, kind)
    # Compare exact closed mappings, including fallback flags; authored labels
    # cannot promote a missing/refused arm into a capable one.
    require(canonical_bytes(row["expected_capabilities"]) == canonical_bytes(expected), "body.case:capability_matrix")
    return BodyCase(row, _token=_TOKEN)


def _population(cases: tuple[BodyCase, ...]) -> None:
    require(len(cases) == MAX_CASES, "body.catalog:24_case_matrix")
    require(Counter(c.kind for c in cases) == {"body_positive": 12, "metadata_anchor": 4, "absent": 2, "ambiguous": 2, "privacy": 2, "unsupported": 2}, "body.catalog:role_matrix")
    for field in ("intent_id", "task_id", "question"):
        values = [case.to_dict()[field] if field == "intent_id" else getattr(case.task, field) for case in cases]
        require(len(set(values)) == len(cases), "body.catalog:duplicate_" + field)
    body = [case for case in cases if case.kind == "body_positive"]
    for family in BODY_FAMILIES:
        counts = Counter(case.to_dict()["wording_stratum"] for case in body if case.task.family == family)
        require(counts == {"literal_clue": 2, "paraphrased_intent": 2}, "body.catalog:family_wording_matrix")
    gold_sets = [tuple(sorted(g.locator.file_identity_key() for g in case.task.gold_targets)) for case in body]
    require(len(set(gold_sets)) == len(body), "body.catalog:duplicate_body_gold_set")


def _parse_catalog(
    raw: bytes, *, identity: BodyIdentity, binding_ids: Collection[str], pin: str | None,
) -> BodyCatalog:
    require(type(identity) is BodyIdentity, "body.catalog:identity")
    require(not isinstance(binding_ids, (str, bytes)) and bool(binding_ids), "body.catalog:bindings")
    root = check_keys(strict_json(raw), ROOT_FIELDS, "body.catalog")
    require(root["schema"] == CATALOG_SCHEMA and root["purpose"] == "calibration", "body.catalog:schema_or_purpose")
    for name, expected in identity.to_dict().items():
        require(root[name] == expected, "body.catalog:identity_" + name)
    integer(root["generation"], "body.catalog.generation", 1, 1)
    integer(root["repetitions"], "body.catalog.repetitions", 3, 3)
    require(type(root["arms"]) is list and root["arms"] == list(ARMS), "body.catalog:arms")
    rationale = check_keys(root["family_rationale"], BODY_FAMILIES, "body.catalog.rationale")
    for value in rationale.values():
        text(value, "body.catalog.rationale.text", 2048)
    values = root["cases"]
    require(type(values) is list and len(values) <= MAX_CASES, "body.catalog:case_cap")
    cases = tuple(_case(value, identity, binding_ids) for value in values)
    _population(cases)
    return BodyCatalog(raw, identity, cases, pin=pin, _token=_TOKEN)


def parse_functional_catalog(
    raw: bytes, *, identity: BodyIdentity, binding_ids: Collection[str],
) -> BodyCatalog:
    """Functional parsing cannot reach the reserved class or issue admission."""
    require(type(identity) is BodyIdentity and not identity.reserved, "body.catalog:registered_loader_required")
    return _parse_catalog(raw, identity=identity, binding_ids=binding_ids, pin=None)


def _parse_registered_catalog(raw: bytes, *, pin_raw: bytes, binding_ids: Collection[str]) -> BodyCatalog:
    """Private pin-loader seam: exact registered pin and catalog member are required."""
    require(digest_bytes(pin_raw) == REGISTERED_INPUT_PIN_SHA256, "body.catalog:registered_pin")
    pin = strict_json(pin_raw)
    expected = pin["input_files"]["catalog-v1.json"]
    require(len(raw) == expected["bytes"] and digest_bytes(raw) == expected["sha256"], "body.catalog:registered_catalog_bytes")
    return _parse_catalog(raw, identity=REGISTERED_IDENTITY, binding_ids=binding_ids, pin=REGISTERED_INPUT_PIN_SHA256)
