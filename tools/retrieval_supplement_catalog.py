"""Strict ``cloe-calibration-supplement-catalog-v1`` parser and defensive catalog record.

The parser is generic prospective/functional validation. A *registered* (``sup1-``)
catalog additionally requires an externally supplied exact byte digest; changing
fields of a functional/test namespace can never yield a registered catalog.
Records keep canonical bytes and hand out fresh tasks/dicts on every access.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from retrieval_evaluation_schema import EvaluationTask
from retrieval_supplement_tasks import parse_task, task_from_canonical
from retrieval_supplement_values import (
    ACTUAL_SURFACES,
    ARMS,
    AUTHORED_TASK_CAP,
    CASE_KINDS,
    CATALOG_SCHEMA,
    EXECUTIONS,
    EXPECTATION_REASONS,
    FALLBACK_POLICIES,
    FAMILY_FLOOR,
    NEGATIVE_KINDS,
    OTHER_FAMILY,
    POSITIVE_FAMILIES,
    POSITIVE_FLOOR,
    PURPOSE,
    RATIONALE_FAMILIES,
    REGISTERED_INPUT_PIN_SHA256,
    REPETITIONS,
    SupplementIdentity,
    check_keys,
    digest_bytes,
    digest_value,
    frozen_json,
    integer,
    require,
    safe_id,
    strict_json,
    string_list,
    text,
    thaw_json,
)

ROOT_FIELDS: tuple[str, ...] = (
    "schema", "supplement_id", "corpus_id", "phase", "purpose", "repetitions", "arms",
    "family_rationale", "cases",
)
CASE_FIELDS: tuple[str, ...] = ("case_kind", "intent_id", "metadata_basis", "task", "expectations")
EXPECTATION_FIELDS: tuple[str, ...] = ("execution", "reason", "actual_surface", "fallback_policy")
NEGATIVE_FLOOR = 2


class SupplementCase:
    """One authored case; task and expectations are rebuilt fresh on each access."""

    __slots__ = ("_expectations", "_intent_id", "_kind", "_metadata_basis", "_task")
    _expectations: bytes
    _intent_id: str
    _kind: str
    _metadata_basis: tuple[str, ...]
    _task: bytes

    def __init__(
        self,
        kind: str,
        intent_id: str,
        metadata_basis: tuple[str, ...],
        task: EvaluationTask,
        expectations: dict[str, dict[str, Any]],
    ) -> None:
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_intent_id", intent_id)
        object.__setattr__(self, "_metadata_basis", tuple(metadata_basis))
        object.__setattr__(self, "_task", frozen_json(task.to_dict()))
        object.__setattr__(self, "_expectations", frozen_json(expectations))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SupplementCase is immutable")

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def intent_id(self) -> str:
        return self._intent_id

    @property
    def metadata_basis(self) -> tuple[str, ...]:
        return self._metadata_basis

    @property
    def task(self) -> EvaluationTask:
        return task_from_canonical(self._task)

    @property
    def expectations(self) -> dict[str, dict[str, Any]]:
        return dict(thaw_json(self._expectations))


def _expectation(value: object, arm: str) -> dict[str, Any]:
    label = f"expectations.{arm}"
    record = check_keys(value, EXPECTATION_FIELDS, label)
    execution, reason = record["execution"], record["reason"]
    surface, fallback = record["actual_surface"], record["fallback_policy"]
    require(type(execution) is str and execution in EXECUTIONS, f"{label}.execution:unsupported")
    require(reason is None or (type(reason) is str and reason in EXPECTATION_REASONS), f"{label}.reason:unsupported")
    require(type(surface) is str and surface in ACTUAL_SURFACES, f"{label}.actual_surface:unsupported")
    require(type(fallback) is str and fallback in FALLBACK_POLICIES, f"{label}.fallback_policy:unsupported")
    if execution == "completed":
        require(reason is None and surface != "none", f"{label}:completed_shape")
    else:
        require(reason is not None and surface == "none", f"{label}:noncompleted_shape")
        require(fallback == "none", f"{label}:noncompleted_fallback")
    return dict(record)


def _expectations(value: object) -> dict[str, dict[str, Any]]:
    record = check_keys(value, ARMS, "expectations")
    return {arm: _expectation(record[arm], arm) for arm in ARMS}


def _gold_files(task: EvaluationTask) -> list[tuple[tuple[str, str], int]]:
    return [(g.locator.file_identity_key(), g.relevance_grade) for g in task.gold_targets]


def _case_rules(kind: str, task: EvaluationTask) -> None:
    golds = _gold_files(task)
    rule = task.grading_rule
    if kind == "positive":
        require(task.family in POSITIVE_FAMILIES, "case.positive:family_required")
        require(golds and all(grade > 0 for _, grade in golds), "case.positive:gold_required")
        require(task.expected_capability_state == "available", "case.positive:state")
        return
    require(task.family == OTHER_FAMILY, f"case.{kind}:other_family_required")
    if kind == "absent":
        require(rule == "absent_answer" and not golds, "case.absent:shape")
    elif kind == "ambiguous":
        grades = {grade for _, grade in golds}
        require(rule == "ranked_positive" and len(golds) >= 2, "case.ambiguous:shape")
        require(len(grades) == 1 and min(grades) > 0, "case.ambiguous:equal_positive_grades")
    elif kind == "privacy":
        require(rule == "safety_refusal" and not golds, "case.privacy:shape")
    else:
        require(rule == "ranked_positive", "case.unsupported:rule")
        require(task.requested_mode != "ranked_discovery", "case.unsupported:mode")
    if kind in {"absent", "ambiguous"}:
        require(task.requested_mode == "ranked_discovery", f"case.{kind}:mode")
        require(task.expected_capability_state == "available", f"case.{kind}:state")


class SupplementCatalog:
    """Validated catalog; exposes exact raw bytes and fresh derived values."""

    __slots__ = ("_binding_ids", "_cases", "_identity", "_raw", "_registered")
    _binding_ids: tuple[str, ...]
    _cases: tuple[SupplementCase, ...]
    _identity: SupplementIdentity
    _raw: bytes
    _registered: bool

    def __init__(
        self,
        raw: bytes,
        identity: SupplementIdentity,
        binding_ids: tuple[str, ...],
        cases: tuple[SupplementCase, ...],
        registered: bool,
    ) -> None:
        object.__setattr__(self, "_raw", bytes(raw))
        object.__setattr__(self, "_identity", identity)
        object.__setattr__(self, "_binding_ids", tuple(binding_ids))
        object.__setattr__(self, "_cases", tuple(cases))
        object.__setattr__(self, "_registered", registered)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SupplementCatalog is immutable")

    @property
    def raw(self) -> bytes:
        return self._raw

    @property
    def identity(self) -> SupplementIdentity:
        return self._identity

    @property
    def binding_ids(self) -> tuple[str, ...]:
        return self._binding_ids

    @property
    def registered(self) -> bool:
        """True only for a ``sup1-`` identity whose bytes matched an external digest."""
        return self._registered

    @property
    def cases(self) -> tuple[SupplementCase, ...]:
        return tuple(self._cases)

    @property
    def tasks(self) -> tuple[EvaluationTask, ...]:
        return tuple(case.task for case in self._cases)

    @property
    def digest(self) -> str:
        return digest_bytes(self._raw)

    def to_dict(self) -> dict[str, Any]:
        return dict(strict_json(self._raw))


def _root(payload: object, identity: SupplementIdentity) -> dict[str, Any]:
    root = check_keys(payload, ROOT_FIELDS, "catalog")
    require(root["schema"] == CATALOG_SCHEMA, "catalog.schema:unsupported")
    require(root["supplement_id"] == identity.supplement_id, "catalog.supplement_id:mismatch")
    require(root["corpus_id"] == identity.corpus_id, "catalog.corpus_id:mismatch")
    require(root["phase"] == identity.phase, "catalog.phase:mismatch")
    require(root["purpose"] == PURPOSE, "catalog.purpose:unsupported")
    integer(root["repetitions"], "catalog.repetitions", REPETITIONS, REPETITIONS)
    require(type(root["arms"]) is list and tuple(root["arms"]) == ARMS, "catalog.arms:mismatch")
    rationale = check_keys(root["family_rationale"], RATIONALE_FAMILIES, "catalog.family_rationale")
    for family in RATIONALE_FAMILIES:
        text(rationale[family], "catalog.family_rationale", 2048)
    require(type(root["cases"]) is list, "catalog.cases:list_required")
    require(1 <= len(root["cases"]) <= AUTHORED_TASK_CAP, "catalog.cases:cap_exceeded")
    return root


def _check_population(cases: list[SupplementCase]) -> None:
    tasks = [case.task for case in cases]
    for label, values in (
        ("task_id", [t.task_id for t in tasks]),
        ("intent_id", [c.intent_id for c in cases]),
        ("question", [t.question for t in tasks]),
    ):
        require(len(set(values)) == len(values), f"catalog.cases:duplicate_{label}")
    kinds = [case.kind for case in cases]
    positives = [t for c, t in zip(cases, tasks) if c.kind == "positive"]
    require(len(positives) >= POSITIVE_FLOOR, "catalog.cases:positive_floor")
    for family in POSITIVE_FAMILIES:
        count = sum(1 for t in positives if t.family == family)
        require(count >= FAMILY_FLOOR, "catalog.cases:family_floor")
    for kind in NEGATIVE_KINDS:
        require(kinds.count(kind) >= NEGATIVE_FLOOR, f"catalog.cases:{kind}_template_missing")
    gold_sets = [frozenset(g.locator.file_identity_key() for g in t.gold_targets) for t in positives]
    require(len(set(gold_sets)) == len(gold_sets), "catalog.cases:positive_gold_sets_not_distinct")


def _parse(
    raw: bytes,
    identity: SupplementIdentity,
    binding_ids: Collection[str],
    expected_sha256: str | None,
) -> SupplementCatalog:
    actual_digest = digest_bytes(raw) if type(raw) is bytes else None
    if expected_sha256 is not None:
        require(actual_digest == digest_value(expected_sha256, "catalog"), "catalog:digest_mismatch")
    allowed = tuple(binding_ids)
    root = _root(strict_json(raw), identity)
    cases: list[SupplementCase] = []
    for item in root["cases"]:
        record = check_keys(item, CASE_FIELDS, "case")
        kind = record["case_kind"]
        require(kind in CASE_KINDS, "case.case_kind:unsupported")
        intent = safe_id(record["intent_id"], "case.intent_id")
        minimum = 1 if kind == "positive" else 0
        basis = string_list(
            record["metadata_basis"], "case.metadata_basis", minimum=minimum, maximum=16, item_max=256
        )
        task = parse_task(record["task"], identity, allowed)
        _case_rules(kind, task)
        cases.append(SupplementCase(kind, intent, tuple(basis), task, _expectations(record["expectations"])))
    _check_population(cases)
    return SupplementCatalog(raw, identity, allowed, tuple(cases), identity.registered)


def parse_catalog(
    raw: bytes,
    *,
    identity: SupplementIdentity,
    binding_ids: Collection[str],
    expected_sha256: str | None = None,
) -> SupplementCatalog:
    """Generic functional/prospective parse; the registered class needs the pinned loader."""
    require(isinstance(identity, SupplementIdentity), "identity:required")
    require(not identity.registered, "catalog:registered_requires_pinned_loader")
    return _parse(raw, identity, binding_ids, expected_sha256)


def load_registered_catalog(
    raw: bytes,
    *,
    identity: SupplementIdentity,
    binding_ids: Collection[str],
    pin_raw: bytes,
) -> SupplementCatalog:
    """Registered ``sup1-`` catalog bound to the externally pinned input-pin bytes."""
    require(isinstance(identity, SupplementIdentity) and identity.registered, "catalog:registered_identity_required")
    require(type(pin_raw) is bytes and digest_bytes(pin_raw) == REGISTERED_INPUT_PIN_SHA256,
            "catalog:registered_pin_mismatch")
    pin = strict_json(pin_raw)
    require(isinstance(pin, dict) and isinstance(pin.get("input_files"), dict), "catalog:registered_pin_shape")
    entry = pin["input_files"].get("catalog-v1.json")
    require(isinstance(entry, dict), "catalog:registered_pin_shape")
    return _parse(raw, identity, binding_ids, digest_value(entry.get("sha256"), "catalog"))


__all__ = ["SupplementCase", "SupplementCatalog", "load_registered_catalog", "parse_catalog"]
