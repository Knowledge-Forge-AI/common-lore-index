"""Pair preparation, custody checks, and observation for the W5 lexical replay."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
import time
from typing import Any

from common_lore_index.ops.config_records import OpsConfig
from common_lore_index.ops.lexical_service import query_graph_lexical_generation

from common_lore_index.retrieval.lexical_envelopes import LexicalQueryRequest
from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from common_lore_index.retrieval.query_contracts import CapabilityState, QueryEnvelope, QueryMode
from common_lore_index.storage.lexical_driver import LexicalStorageError
from offline_qualification_values import digest, json_bytes
from retrieval_body_catalog import BodyCase
from retrieval_body_study_grading import grade_snapshot_hits
from retrieval_evaluation_admission_w5 import execute_after_admission
from retrieval_lexical_comparator import plan_lexical_comparator
from retrieval_semantic_arm_validation import FileIdentityTuple
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_supplement_baselines import prepare_fts_request

LEGACY = "legacy_fts"
CANDIDATE = "public-lexical-comparator-v1"
QUESTION_CASES = frozenset({"body_positive", "absent", "ambiguous"})


class ReplayIntegrityError(RuntimeError):
    """An observation cannot be bound to the authorized scope or treatment."""


class ReplayShortfall(RuntimeError):
    """A valid pre-query population cannot support the fixed comparison."""


class ReplayTelemetryError(RuntimeError):
    """Required observed evidence is missing."""


def require_integrity(condition: bool, code: str) -> None:
    if not condition:
        raise ReplayIntegrityError(code)


def _require_observed_identity(request: LexicalQueryRequest) -> None:
    require_integrity(getattr(request, "publication_bundle_id", None) is None
                      and getattr(request, "snapshot_manifest_id", None) is None, "caller_supplied_identity")


def query_without_identity(config: OpsConfig, graph_id: str, request: LexicalQueryRequest) -> QueryEnvelope:
    """Leave both service arguments empty so only the backend can identify the generation."""
    _require_observed_identity(request)
    return query_graph_lexical_generation(
        config, graph_id, request, authorize_body=True, publication_bundle_id=None, snapshot_manifest_id=None,
    )


@dataclass(frozen=True, slots=True)
class GenerationCensus:
    publication_bundle_id: str
    snapshot_manifest_id: str
    run_id: int
    lexical_generation_id: str
    files: tuple[tuple[str, str], ...]
    lexical_policy_digest: str

    def require_pin(self, pin: VerifiedPublicationPin, generation: str, lexical_policy_digest: str) -> None:
        require_integrity(
            (self.publication_bundle_id, self.snapshot_manifest_id, self.run_id, self.lexical_generation_id)
            == (pin.publication_bundle_id, pin.snapshot_manifest_id, pin.run_id, generation),
            "census_generation_mismatch",
        )
        require_integrity(self.lexical_policy_digest == lexical_policy_digest, "generation_policy_mismatch")
        require_integrity(
            tuple(sorted(set(self.files))) == self.files
            and all(binding in pin.admitted_public_bindings for binding, _ in self.files),
            "census_file_scope",
        )


@dataclass(frozen=True, slots=True)
class Slot:
    case: BodyCase
    arm: str

    def key(self) -> dict[str, str]:
        task = self.case.task
        return {"task_id": task.task_id, "kind": self.case.kind, "family": task.family, "arm": self.arm}


@dataclass(frozen=True, slots=True)
class FrozenSlot:
    slot: Slot
    request_bytes: bytes | None
    admission: Mapping[str, Any]

    def entry(self) -> dict[str, Any]:
        return self.slot.key() | {
            "request_sha256": digest(self.request_bytes) if self.request_bytes is not None else None,
            "admission_sha256": digest(json_bytes(dict(self.admission))),
        }


@dataclass(frozen=True, slots=True)
class ReplayStore:
    pin: VerifiedPublicationPin
    policy: BodyProjectionPolicy
    generation: str
    lexical_policy_digest: str
    census: Callable[[], GenerationCensus]
    query: Callable[[LexicalQueryRequest], QueryEnvelope]


def schedule(cases: Sequence[BodyCase]) -> tuple[Slot, ...]:
    ordered = sorted(cases, key=lambda case: case.task.task_id.encode("utf-8"))
    require_integrity(
        0 < len(ordered) <= 24 and len({case.task.task_id for case in ordered}) == len(ordered),
        "case_identity_or_count",
    )
    return tuple(
        Slot(case, arm)
        for index, case in enumerate(ordered)
        for arm in ((LEGACY, CANDIDATE) if index % 2 == 0 else (CANDIDATE, LEGACY))
    )


def _prepare_request(slot: Slot, policy: BodyProjectionPolicy) -> LexicalQueryRequest:
    require_integrity(slot.case.kind != "privacy", "privacy_admitted")
    original = prepare_fts_request(slot.case.task, policy=policy).validate()
    _require_observed_identity(original)
    if slot.case.kind not in QUESTION_CASES:
        return original
    if (original.query_style != "ranked_terms" or original.fallback is not None
            or original.resolved_requested_mode() != QueryMode.RANKED_DISCOVERY
            or (original.limit, original.offset) != (10, 0)):
        raise ReplayShortfall("question_request_contract")
    try:
        candidate = plan_lexical_comparator(original, shape="question")
    except ValueError as error:
        raise ReplayShortfall("question_planner_refused") from error
    require_integrity(replace(candidate, query=original.query) == original, "request_fields_changed")
    _require_observed_identity(candidate)
    return original if slot.arm == LEGACY else candidate


def _admit(slot: Slot, store: ReplayStore, callback: Callable[..., Any]) -> tuple[Any, Any]:
    return execute_after_admission(
        slot.case.task, policy=store.policy, publication_pin=store.pin,
        observed_backend="postgresql", frozen_policy_digest=store.pin.policy_digest, execute=callback,
    )


def freeze_slots(slots: Sequence[Slot], store: ReplayStore) -> tuple[FrozenSlot, ...]:
    frozen = []
    for slot in slots:
        admission, request = _admit(slot, store, lambda _: _prepare_request(slot, store.policy))
        if slot.case.kind == "privacy":
            require_integrity(
                request is None and admission.refusal_code == slot.case.task.expected_refusal_code
                and admission.policy_enforcement_layer == "evaluator_precheck",
                "privacy_refusal_mismatch",
            )
        elif slot.case.kind in QUESTION_CASES and request is None:
            raise ReplayShortfall("required_question_not_admitted")
        raw = json_bytes(asdict(request)) if request is not None else None
        frozen.append(FrozenSlot(slot, raw, admission.to_dict()))
    return tuple(frozen)


def _validate_hits(
    observation: QueryEnvelope, prepared: LexicalQueryRequest, census: GenerationCensus, pin: VerifiedPublicationPin,
) -> tuple[FileIdentityTuple, ...]:
    scope = observation.scope
    require_integrity(scope.graph_id == pin.graph_id and scope.backend == "postgresql"
                      and scope.selected_binding_ids == prepared.selected_binding_ids
                      and scope.source_path_prefix == prepared.source_path_prefix
                      and observation.requested_mode == prepared.resolved_requested_mode(),
                      "observation_scope_drift")
    require_integrity(observation.capability_state is not CapabilityState.STALE, "stale_lexical_fence")
    if (observation.capability_state is CapabilityState.REFUSED
            and observation.refusal_code != "empty_lexical_tsquery"):
        raise ReplayIntegrityError("admitted_service_refusal")
    if observation.capability_state not in {CapabilityState.AVAILABLE, CapabilityState.PARTIAL}:
        require_integrity(not observation.hits, "hits_from_unavailable_scope")
        return ()
    observed = (scope.publication_bundle_id, scope.snapshot_manifest_id,
                observation.provenance.get("accepted_run_id"), observation.provenance.get("lexical_generation_id"))
    if any(value is None for value in observed):
        raise ReplayTelemetryError("missing_observed_generation")
    require_integrity(
        observed == (census.publication_bundle_id, census.snapshot_manifest_id,
                     census.run_id, census.lexical_generation_id),
        "observation_generation_drift",
    )
    require_integrity(observation.provenance.get("path_prefixes") == list(prepared.effective_path_prefixes()),
                      "observation_path_prefixes_drift")
    require_integrity(observation.provenance.get("policy_digest") == census.lexical_policy_digest,
                      "observation_policy_drift")
    require_integrity(observation.fallback == prepared.fallback
                      and observation.effective_mode == prepared.effective_query_mode(),
                      "observation_mode_drift")
    snapshots = pin.snapshot_by_binding()
    hits = []
    for hit in observation.hits:
        file = hit.locator.file_identity_key()
        require_integrity(file in census.files and file[0] in prepared.selected_binding_ids, "foreign_hit")
        hits.append(FileIdentityTuple(file[0], snapshots[file[0]][1], file[1]))
    require_integrity(len(hits) <= prepared.limit, "hit_bound")
    return tuple(hits)


def _graded(observation: QueryEnvelope) -> bool:
    if observation.capability_state is CapabilityState.AVAILABLE:
        return observation.scope_complete
    return (observation.capability_state is CapabilityState.PARTIAL
            and bool(observation.hits)
            and bool(observation.omissions)
            and {item.reason_code for item in observation.omissions} == {"result_page_bounded"})


def _first_hit_per_file(hits: Sequence[FileIdentityTuple]) -> tuple[FileIdentityTuple, ...]:
    first: dict[tuple[str, str], FileIdentityTuple] = {}
    for hit in hits:
        first.setdefault((hit.binding_id, hit.source_relative_path), hit)
    return tuple(first.values())


def observe_slot(frozen: FrozenSlot, store: ReplayStore, census: GenerationCensus) -> dict[str, Any]:
    slot = frozen.slot
    record: dict[str, Any] = frozen.entry()

    def observe(decision: Any) -> dict[str, Any]:
        prepared = _prepare_request(slot, store.policy)
        require_integrity(decision.to_dict() == frozen.admission
                          and json_bytes(asdict(prepared)) == frozen.request_bytes,
                          "frozen_request_or_admission_drift")
        start = time.monotonic_ns()
        try:
            envelope = store.query(prepared).validate()
        except LexicalStorageError as error:
            return {"state": "failed", "reason": error.code,
                    "elapsed_ns": time.monotonic_ns() - start, "metrics": None, "query_started": True}
        elapsed = time.monotonic_ns() - start
        try:
            hits = _validate_hits(envelope, prepared, census, store.pin)
        except (ReplayIntegrityError, ReplayTelemetryError) as error:
            state = "inadmissible" if isinstance(error, ReplayIntegrityError) else "failed"
            return {"state": state, "reason": str(error), "elapsed_ns": elapsed,
                    "metrics": None, "query_started": True}
        ranked = _first_hit_per_file(hits)
        files = tuple((hit.binding_id, hit.source_relative_path) for hit in ranked)
        comparable = _graded(envelope)
        metrics = None
        if comparable and slot.case.task.gold_targets:
            metrics = grade_snapshot_hits(
                returned_items=ranked, gold_targets=slot.case.task.gold_targets,
                verified_pin=store.pin, task=slot.case.task,
            ).metrics
        row: dict[str, Any] = {
            "state": envelope.capability_state.value, "scope_complete": envelope.scope_complete,
            "query_started": True,
            "observed_identity": {
                "publication_bundle_id": envelope.scope.publication_bundle_id,
                "snapshot_manifest_id": envelope.scope.snapshot_manifest_id,
                "run_id": envelope.provenance.get("accepted_run_id"),
                "lexical_generation_id": envelope.provenance.get("lexical_generation_id"),
            },
            "shape": prepared.query_style,
            "fallback": prepared.fallback.to_dict() if prepared.fallback is not None else None,
            "elapsed_ns": elapsed, "metrics": metrics, "files": files,
            "diversity_file_count": len(files),
            "refusal_code": envelope.refusal_code,
            "unavailable_reason": envelope.unavailable_reason,
            "omission_codes": [item.reason_code for item in envelope.omissions],
        }
        if slot.case.kind == "absent":
            row["absence"] = ("incorrect" if comparable and files else
                              "correct" if comparable and envelope.scope_complete else "unresolved")
        if slot.case.kind == "ambiguous" and comparable:
            row["gold_file_ranks"] = [
                {"file": gold.locator.file_identity_key(), "grade": gold.relevance_grade,
                 "rank": files.index(gold.locator.file_identity_key()) + 1
                 if gold.locator.file_identity_key() in files else None}
                for gold in slot.case.task.gold_targets
            ]
        return row

    decision, observed = _admit(slot, store, observe)
    require_integrity(decision.to_dict() == frozen.admission, "post_freeze_admission_drift")
    if observed is not None:
        return record | observed
    require_integrity(frozen.request_bytes is None, "admitted_request_lost")
    return record | {"state": decision.state.value, "refusal_code": decision.refusal_code,
                     "policy_enforcement_layer": decision.policy_enforcement_layer,
                     "unavailable_reason": decision.reason_category, "metrics": None,
                     "query_started": False}
