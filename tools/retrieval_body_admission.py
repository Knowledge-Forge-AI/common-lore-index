"""Registered body-class preparation decisions with current input/publication joins."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy
from retrieval_body_admission_rules import complete_policy_identity, evaluate_body_case
from retrieval_body_values import canonical_bytes, digest_bytes, require, strict_json
from retrieval_evaluation_schema import EvaluationTask
from retrieval_semantic_provenance import VerifiedPublicationPin

if TYPE_CHECKING:
    from retrieval_body_pin import RegisteredBodyInputs

_TOKEN = object()


class BodyTaskAdmission:
    """Inert factory-issued decision; it grants no model or candidate execution."""
    __slots__ = ('_raw', '_inputs', '_task_raw', '_arm', '_policy', '_pin', '_backend', '_seal')
    _raw: bytes
    _inputs: RegisteredBodyInputs
    _task_raw: bytes
    _arm: str
    _policy: BodyProjectionPolicy
    _pin: VerifiedPublicationPin
    _backend: str
    _seal: tuple[Any, ...]

    def __init__(self, raw: bytes, inputs: RegisteredBodyInputs, task: EvaluationTask, *,
                 arm: str, policy: BodyProjectionPolicy, publication_pin: VerifiedPublicationPin,
                 observed_backend: str, _token: object = None) -> None:
        require(_token is _TOKEN, 'body.admission:factory_required')
        for name, value in (('_raw', raw), ('_inputs', inputs), ('_task_raw', canonical_bytes(task.to_dict())),
                            ('_arm', arm), ('_policy', policy), ('_pin', publication_pin), ('_backend', observed_backend)):
            object.__setattr__(self, name, value)
        object.__setattr__(self, '_seal', self._current_seal())

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError('BodyTaskAdmission is immutable')

    def _current_seal(self) -> tuple[Any, ...]:
        return (id(self), digest_bytes(self._raw), digest_bytes(self._task_raw), id(self._inputs),
                self._arm, id(self._pin), self._backend, complete_policy_identity(self._policy))

    @staticmethod
    def is_factory_issued(value: object) -> bool:
        try:
            return type(value) is BodyTaskAdmission and value._seal == value._current_seal()
        except (AttributeError, TypeError, ValueError):
            return False

    @property
    def task(self) -> EvaluationTask:
        return EvaluationTask.from_mapping(strict_json(self._task_raw))

    @property
    def arm(self) -> str:
        return self._arm

    @property
    def identity(self) -> str:
        return digest_bytes(self._raw)

    def to_dict(self) -> dict[str, Any]:
        return dict(strict_json(self._raw))

    def require_current(self) -> None:
        require(self.is_factory_issued(self), 'body.admission:issued_decision_required')
        fresh = admit_body_task(self._inputs, self.task, arm=self._arm, policy=self._policy,
                               publication_pin=self._pin, observed_backend=self._backend)
        require(fresh._raw == self._raw, 'body.admission:current_decision_drift')


def admit_body_task(
    inputs: RegisteredBodyInputs, task: EvaluationTask, *, arm: str,
    policy: BodyProjectionPolicy, publication_pin: VerifiedPublicationPin,
    observed_backend: str,
) -> BodyTaskAdmission:
    """Issue inert preparation after exact class membership, policy and freshness checks."""
    from retrieval_body_pin import require_registered_body_inputs
    current = require_registered_body_inputs(inputs)
    require(type(task) is EvaluationTask, 'body.admission:task_required')
    catalog = current.catalog
    matches = [case for case in catalog.cases if case.task.task_id == task.task_id]
    require(len(matches) == 1, 'body.admission:task_membership')
    case = matches[0]
    require(canonical_bytes(task.to_dict()) == canonical_bytes(case.task.to_dict()), 'body.admission:task_digest')
    row = evaluate_body_case(catalog, case, arm=arm, policy=policy,
                             frozen_policy=current.policy, publication_pin=publication_pin,
                             observed_backend=observed_backend)
    row['input_pin_sha256'] = current.pin_sha256
    return BodyTaskAdmission(canonical_bytes(row), current, task, arm=arm, policy=policy,
                             publication_pin=publication_pin, observed_backend=observed_backend, _token=_TOKEN)
