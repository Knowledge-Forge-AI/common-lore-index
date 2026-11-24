"""Pure class eligibility plus trusted live-publication freshness; never execute an arm."""
from __future__ import annotations

from typing import Any

from common_lore_index.retrieval.projection_policy import BodyProjectionPolicy, ResolvedBindingPolicy
from retrieval_body_catalog import BodyCase, BodyCatalog
from retrieval_body_capabilities import expected_capabilities
from retrieval_body_values import ARMS, BODY_ARMS, digest_json, require
from retrieval_evaluation_admission_w5 import AdmissionState, AdmissionVerificationError, _policy_denial, admit_w5_task
from retrieval_semantic_provenance import VerifiedPublicationPin
from retrieval_supplement_tasks import task_digests

_POLICY_FIELDS = frozenset({
    'policy_version', 'graph_id', 'selected_binding_ids', 'bindings', 'max_chunk_lines',
    'max_chunk_bytes', 'max_projected_file_bytes', 'max_input_artifact_bytes',
    'max_aggregate_input_bytes', 'max_projected_files',
})


def complete_policy_identity(policy: BodyProjectionPolicy) -> str:
    """Bind all actual fields, including the legacy digest's omitted file-count cap."""
    require(type(policy) is BodyProjectionPolicy, 'body.admission:policy_required')
    require(set(policy.__dataclass_fields__) == _POLICY_FIELDS, 'body.admission:policy_schema')
    require(all(type(value) is ResolvedBindingPolicy for value in policy.bindings.values()), 'body.admission:binding_policy_type')
    try:
        policy.validate()
        return digest_json({**policy.to_dict(), 'max_projected_files': policy.max_projected_files})
    except (TypeError, AttributeError, ValueError):
        require(False, 'body.admission:invalid_policy')
        raise AssertionError('unreachable')


def _policy_reason(case: BodyCase, policy: BodyProjectionPolicy, *, arm: str) -> str | None:
    # Whole-scope privacy denial precedes missing-scope/capability outcomes.
    for binding in case.task.selected_bindings:
        reason = _policy_denial(policy, binding, body=case.task.comparability_stratum == 'body' or arm in BODY_ARMS)
        if reason is not None:
            return reason
    for binding in case.task.selected_bindings:
        if binding not in policy.bindings:
            return 'binding_policy_missing'
        if binding not in policy.selected_binding_ids:
            return 'binding_not_selected_by_policy'
    return None


def evaluate_body_case(
    catalog: BodyCatalog, case: BodyCase, *, arm: str, policy: BodyProjectionPolicy,
    frozen_policy: BodyProjectionPolicy, publication_pin: VerifiedPublicationPin,
    observed_backend: str,
) -> dict[str, Any]:
    """Common functional/registered algorithm; a mapping is never registered admission.

    The only invoked adapter is the issued pin's existing trusted publication
    readback. No candidate, capture, model or execution callback is accepted.
    Declared privacy/unbound selections refuse without even that readback.
    """
    require(BodyCatalog.is_factory_issued(catalog), 'body.admission:catalog_required')
    require(any(value is case for value in catalog.cases), 'body.admission:case_membership')
    require(type(arm) is str and arm in ARMS, 'body.admission:arm')
    policy_identity = complete_policy_identity(policy)
    frozen_identity = complete_policy_identity(frozen_policy)
    task = case.task
    require(policy.graph_id == catalog.identity.graph_id == task.graph_id, 'body.admission:graph')
    require(type(publication_pin) is VerifiedPublicationPin and publication_pin.is_factory_issued(), 'body.admission:issued_publication_required')
    require(publication_pin.graph_id == task.graph_id, 'body.admission:publication_graph')
    require(task.split == 'calibration' and type(task.generation) is int
            and task.generation == publication_pin.generation, 'body.admission:publication_generation')
    require(type(observed_backend) is str and observed_backend in {
        'sqlite', 'postgresql', 'captured_reference', 'native_filesystem'}, 'body.admission:backend')
    capability = expected_capabilities(task, case.kind)[arm]
    row: dict[str, Any] = {
        'schema': 'cloe-body-preparation-decision-v1', 'class_id': catalog.identity.class_id,
        'catalog_sha256': catalog.digest, 'case_sha256': case.digest,
        'task_id': task.task_id, **task_digests(task), 'arm': arm,
        'observed_backend': observed_backend, 'policy_digest': policy.policy_digest(),
        'complete_policy_sha256': policy_identity, 'publication_revalidation_performed': False,
        'publication_identity': None, 'preparation': 'refused', 'reason': None,
        'actual_surface': 'none', 'fallback_policy': 'none',
        'candidate_access_performed': False, 'execution_authority_granted': False,
    }
    reason = _policy_reason(case, policy, arm=arm)
    if reason is not None:
        row['reason'] = reason
        return row
    require(policy_identity == frozen_identity, 'body.admission:frozen_policy_drift')
    try:
        decision = admit_w5_task(task, policy=policy, publication_pin=publication_pin,
                                 observed_backend=observed_backend,
                                 frozen_policy_digest=frozen_policy.policy_digest())
    except AdmissionVerificationError as error:
        require(False, 'body.admission:' + error.code)
        raise AssertionError('unreachable')
    row['publication_revalidation_performed'] = True
    row['publication_identity'] = dict(decision.publication_identity or {})
    if decision.state is AdmissionState.REFUSED:
        row['reason'] = decision.refusal_code
        return row
    if decision.state is AdmissionState.UNAVAILABLE:
        row.update(preparation='unavailable', reason=decision.reason_category)
        return row
    if capability['preparation'] != 'ready':
        row.update(capability)
        return row
    required_backend = 'sqlite' if arm == ARMS[0] else 'postgresql' if arm in ARMS[1:3] else publication_pin.backend
    if publication_pin.backend != required_backend:
        row.update(preparation='unavailable', reason='arm_backend_unavailable')
        return row
    row.update(capability)
    return row
