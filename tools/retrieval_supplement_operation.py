"""Sequential caller-owned phase clocks; no generation-ledger subtraction."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
import json
from time import perf_counter_ns
from typing import Any

from retrieval_supplement_operation_values import FieldSample, PhaseIdentity, public_error_type
from retrieval_supplement_values import canonical_bytes, digest_json


class PhaseCaptureError(RuntimeError):
    """A sanitized secondary capture failure with no raw payload in its message."""


@dataclass(frozen=True)
class PhaseScope:
    """Collect independently observed samples; no default cost is installed."""

    identity: PhaseIdentity
    _samples: dict[str, FieldSample] = field(default_factory=dict, init=False, repr=False)

    def observe(self, sample: FieldSample) -> None:
        if type(sample) is not FieldSample:
            raise ValueError("supplement_sample_required")
        sample.validate()
        if sample.name in self._samples:
            raise ValueError("supplement_duplicate_sample")
        self._samples[sample.name] = sample

    def _capture(self, samples: Sequence[FieldSample]) -> None:
        if not isinstance(samples, (list, tuple)) or len(samples) > 64:
            raise ValueError("supplement_capture_samples")
        pending: dict[str, FieldSample] = {}
        for sample in samples:
            if type(sample) is not FieldSample:
                raise ValueError("supplement_sample_required")
            sample.validate()
            if sample.name in pending or sample.name in self._samples:
                raise ValueError("supplement_duplicate_sample")
            pending[sample.name] = sample
        self._samples.update(pending)


class PhaseRecorder:
    """Record bounded nonoverlapping caller scopes, preserving original failures.

    Elapsed time is one phase-boundary observation, inclusive of body work and
    exclusive of the later capture callback. It is not a sum with adapter,
    database, transport or worker layers. Do not nest this scope with the old
    measure_query_work on the same ledger. No adapter/kernel timing is edited,
    no query_ns snapshots are subtracted, and no generation zero is imported.
    """

    def __init__(self, *, clock: Callable[[], int] = perf_counter_ns, max_records: int = 4096) -> None:
        if not callable(clock) or type(max_records) is not int or not 1 <= max_records <= 4096:
            raise ValueError("supplement_phase_recorder_configuration")
        self._clock = clock
        self._maximum = max_records
        self._active = False
        self._identities: set[str] = set()
        self._records: list[bytes] = []

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        """Fresh snapshots prevent callers changing retained records in place."""
        return tuple(json.loads(raw) for raw in self._records)

    def _time(self) -> int:
        value = self._clock()
        if type(value) is not int or value < 0:
            raise ValueError("supplement_phase_clock_value")
        return value

    def _retain(
        self, scope: PhaseScope, *, started: int | None, ended: int | None,
        body_error: BaseException | None, clock_error: BaseException | None,
        capture_error: BaseException | None,
    ) -> None:
        elapsed = None if started is None or ended is None else ended - started
        errors = {
            "body": public_error_type(body_error), "clock": public_error_type(clock_error),
            "capture": public_error_type(capture_error),
        }
        value: dict[str, Any] = {
            "schema": "cloe-supplement-phase-observation-v1", "identity": scope.identity.to_dict(),
            "state": "failed" if any(errors.values()) else "completed",
            "elapsed_ns": elapsed, "elapsed_units": "ns",
            "elapsed_observation": "unknown" if elapsed is None else "caller_phase_boundary",
            "capture_after_clock_stop": True, "additive_with_other_layers": False,
            "samples": [sample.to_dict() for _, sample in sorted(scope._samples.items())],
            "errors": errors,
            "observation_complete": not any(errors.values()),
            "generation_ledger_defaults_imported": False,
            "origin_or_execution_qualification": False,
        }
        value["record_sha256"] = digest_json(value)
        self._records.append(canonical_bytes(value))

    @contextmanager
    def scope(
        self, identity: PhaseIdentity, *, capture: Callable[[], Sequence[FieldSample]] | None = None,
    ) -> Iterator[PhaseScope]:
        """Reraise the original body exception even when clock/capture also fails."""
        if type(identity) is not PhaseIdentity:
            raise ValueError("supplement_phase_identity_required")
        identity.validate()
        if self._active:
            raise ValueError("supplement_nested_phase_scope")
        if len(self._records) >= self._maximum:
            raise ValueError("supplement_phase_record_limit")
        key = digest_json(identity.to_dict())
        if key in self._identities:
            raise ValueError("supplement_duplicate_phase_scope")
        if capture is not None and not callable(capture):
            raise ValueError("supplement_capture_callback")
        self._identities.add(key)
        self._active = True
        scope = PhaseScope(identity)
        started = ended = None
        body_error = clock_error = capture_error = None
        try:
            try:
                started = self._time()
            except BaseException as error:
                clock_error = error
                self._retain(scope, started=None, ended=None, body_error=None,
                             clock_error=clock_error, capture_error=None)
                raise PhaseCaptureError("supplement_phase_clock_failed") from None
            try:
                yield scope
            except BaseException as error:
                body_error = error
            try:
                ended = self._time()
                if ended < started:
                    ended = None
                    raise ValueError("supplement_phase_clock_regressed")
            except BaseException as error:
                clock_error = error
                ended = None
            if capture is not None:
                try:
                    scope._capture(capture())
                except BaseException as error:
                    capture_error = error
            self._retain(scope, started=started, ended=ended, body_error=body_error,
                         clock_error=clock_error, capture_error=capture_error)
            if body_error is not None:
                raise body_error.with_traceback(body_error.__traceback__)
            if clock_error is not None or capture_error is not None:
                raise PhaseCaptureError("supplement_phase_capture_failed") from None
        finally:
            self._active = False


def _checked_record(raw: Mapping[str, Any]) -> tuple[dict[str, Any], PhaseIdentity]:
    expected = {
        "schema", "identity", "state", "elapsed_ns", "elapsed_units", "elapsed_observation",
        "capture_after_clock_stop", "additive_with_other_layers", "samples", "errors",
        "observation_complete", "generation_ledger_defaults_imported", "origin_or_execution_qualification",
        "record_sha256",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise ValueError("supplement_phase_record_fields")
    value = dict(raw)
    supplied = value.pop("record_sha256")
    if supplied != digest_json(value) or value["schema"] != "cloe-supplement-phase-observation-v1":
        raise ValueError("supplement_phase_record_digest")
    try:
        if type(value["identity"]) is not dict or set(value["identity"]) != {row.name for row in fields(PhaseIdentity)}:
            raise ValueError("supplement_phase_identity_fields")
        identity = PhaseIdentity(**value["identity"]).validate()
        samples = value["samples"]
        if type(samples) is not list or len(samples) > 64:
            raise ValueError("supplement_phase_samples")
        names: set[str] = set()
        for sample in samples:
            if type(sample) is not dict or set(sample) != {row.name for row in fields(FieldSample)}:
                raise ValueError("supplement_phase_sample_fields")
            checked = FieldSample(**sample).validate()
            if checked.name in names:
                raise ValueError("supplement_duplicate_sample")
            names.add(checked.name)
    except (TypeError, KeyError):
        raise ValueError("supplement_phase_record_types") from None
    errors = value["errors"]
    if type(errors) is not dict or set(errors) != {"body", "clock", "capture"}:
        raise ValueError("supplement_phase_errors")
    if any(error is not None and (type(error) is not str or not error.isidentifier() or len(error) > 64)
           for error in errors.values()):
        raise ValueError("supplement_phase_error_type")
    failed = any(errors.values())
    elapsed = value["elapsed_ns"]
    if elapsed is not None and (type(elapsed) is not int or not 0 <= elapsed <= 2**63 - 1):
        raise ValueError("supplement_phase_elapsed")
    if (
        value["state"] != ("failed" if failed else "completed")
        or value["observation_complete"] is not (not failed)
        or value["elapsed_units"] != "ns"
        or value["elapsed_observation"] != ("unknown" if elapsed is None else "caller_phase_boundary")
        or value["capture_after_clock_stop"] is not True
        or value["additive_with_other_layers"] is not False
        or value["generation_ledger_defaults_imported"] is not False
        or value["origin_or_execution_qualification"] is not False
        or (elapsed is None and not errors["clock"])
    ):
        raise ValueError("supplement_phase_record_claim")
    return value, identity


def summarize_phase_costs(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Keep each layer and shared owner separate; deliberately compute no sum."""
    if not isinstance(records, (list, tuple)) or len(records) > 4096:
        raise ValueError("supplement_phase_records")
    identities: set[str] = set()
    groups: dict[str, list[dict[str, Any]]] = {}
    binding: tuple[str, ...] | None = None
    for raw in records:
        _value, identity = _checked_record(raw)
        current = (identity.supplement_id, identity.input_pin_sha256, identity.freeze_sha256,
                   identity.source_closure_sha256, identity.attempt_id)
        if binding is not None and binding != current:
            raise ValueError("supplement_phase_foreign_binding")
        binding = current
        key = digest_json(identity.to_dict())
        if key in identities:
            raise ValueError("supplement_duplicate_phase_scope")
        identities.add(key)
        owner = "shared:" + identity.shared_owner if identity.shared_owner else str(identity.arm)
        groups.setdefault(owner, []).append(json.loads(canonical_bytes(raw)))
    return {
        "schema": "cloe-supplement-phase-cost-summary-v1", "record_count": len(records),
        "groups": dict(sorted(groups.items())), "cross_layer_sum": None,
        "shared_cost_allocation": "retained_once_not_divided",
        "unknown_defaults": "remain_unknown", "native_or_scientific_acceptance": False,
    }
