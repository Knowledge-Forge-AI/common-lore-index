"""Fresh evaluator closure and F3 artifact-profile gate for the supplement study.

The evaluator closure is recomputed from current repository bytes on every
admission: the maintained experiment/public-text builder members plus every
explicit study source, runner hook, conftest/support hook, supplement core
module and exact study selector below, with fixture paths refused. It is a
distinct identity domain from the flat 17-file model-worker closure
(``3ef59123...``), the verified artifact source identity (``033cf41f...``), and
the artifact pin document (``f0237e38...``). Artifact admission requires all 30
freshly verified files (20 wheels + 10 model files = 182,037,753 bytes), the
verified ``source_identity_sha256``, and an external trusted gate receipt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
import re
from typing import Any
from typing import cast

from retrieval_public_text_sources import (
    PUBLIC_TEXT_EVALUATOR_PATHS,
    SourceClosureRefusal,
    evaluator_closure,
    worker_closure,
)
from retrieval_semantic_generator_artifacts import (
    ArtifactVerificationError,
    verify_artifact_directory,
)
from retrieval_semantic_generator_worker_contracts import WORKER_SOURCE_FILES
from retrieval_supplement_files import read_member
from retrieval_supplement_study_contracts import (
    ARTIFACT_FILE_COUNT,
    ARTIFACT_MODEL_FILE_COUNT,
    ARTIFACT_PIN_DOCUMENT_SHA256,
    ARTIFACT_SOURCE_IDENTITY_SHA256,
    ARTIFACT_TOTAL_BYTES,
    ARTIFACT_WHEEL_COUNT,
    INPUT_PIN_SHA256,
    WORKER_CLOSURE_FILES,
    WORKER_CLOSURE_SHA256,
    refuse,
)
from retrieval_supplement_primitives import digest_bytes, digest_json, safe_relative_path

MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 4096
_SHA = re.compile(r"\Asha256:[0-9a-f]{64}\Z")

STUDY_SOURCE_OWNERS: tuple[str, ...] = (
    "tools/offline_qualification_values.py",
    "tools/offline_qualification_files.py",
    "tools/offline_qualification_checkout.py",
    "tools/retrieval_supplement_primitives.py",
    "tools/retrieval_supplement_ownership_wire.py",
    "tools/retrieval_supplement_poll_budget.py",
    "tools/retrieval_supplement_study_adapter.py",
    "tools/retrieval_supplement_study_closure.py",
    "tools/retrieval_supplement_study_contracts.py",
    "tools/retrieval_supplement_study_live_requests.py",
    "tools/retrieval_supplement_study_plan.py",
    "tools/retrieval_supplement_study_inputs.py",
    "tools/retrieval_supplement_study_provenance.py",
    "tools/retrieval_supplement_study_reservation.py",
    "tools/retrieval_supplement_study_preflight.py",
    "tools/retrieval_supplement_study_schedule.py",
    "tools/runner_pgvector_study.py",
    "tools/retrieval_supplement_epoch.py",
    "tools/retrieval_supplement_inner_reads.py",
    "tools/retrieval_supplement_inner_read_report.py",
    "tools/retrieval_supplement_inner_read_plan.py",
    "tools/retrieval_supplement_inner_read_reservation.py",
    "tools/retrieval_supplement_model_budget.py",
    "tools/retrieval_supplement_model_contract.py",
    "tools/retrieval_supplement_model_host.py",
    "tools/retrieval_supplement_model_output.py",
    "tools/retrieval_supplement_host_admission.py",
    "tools/retrieval_supplement_host_controller.py",
    "tools/retrieval_supplement_host_inventory.py",
    "tools/retrieval_supplement_host_exchange.py",
    "tools/retrieval_supplement_host_meter.py",
    "tools/retrieval_supplement_host_report.py",
    "tools/retrieval_supplement_host_source.py",
    "tools/retrieval_supplement_host_transport.py",
    "tools/retrieval_supplement_runner_channel.py",
    "tools/retrieval_supplement_runner_lease.py",
    "tools/retrieval_supplement_runner_output.py",
    "tools/retrieval_supplement_runner_process.py",
    "tools/retrieval_supplement_runner_input.py",
    "tools/retrieval_supplement_runner_settlement.py",
    "tools/retrieval_supplement_worker_entry.py",
    "tools/retrieval_supplement_study_inner_evidence.py",
)
STUDY_RUNNER_HOOK_OWNERS: tuple[str, ...] = (
    "tools/runner_integration_execution.py",
    "tools/runner_pgvector_budget.py",
    "tools/runner_pgvector_supplement_proof.py",
    "tools/runner_pgvector_context.py",
    "tools/runner_pgvector_discovery.py",
    "tools/runner_pgvector_sandbox.py",
    "tools/runner_sandbox.py",
    "tools/test_sandbox_dispatch.py",
    "tools/test_sandbox_capacity.py",
    "tools/test_sandbox_ownership.py",
)
STUDY_SUPPORT_AND_TEST_OWNERS: tuple[str, ...] = (
    "src/test/conftest.py",
    "src/test/int/python/tools/retrieval_supplement_native.int.test.py",
    "src/test/support/python/repomap_test_support/retrieval_supplement_study.py",
    "src/test/support/python/repomap_test_support/retrieval_supplement_study_driver.py",
    "src/test/support/python/repomap_test_support/retrieval_supplement_study_pg.py",
    "src/test/support/python/repomap_test_support/retrieval_supplement_study_pg_lifecycle.py",
    "src/test/support/python/repomap_test_support/retrieval_supplement_study_setup.py",
    "src/test/support/python/repomap_test_support/retrieval_supplement_study_slots.py",
    "src/test/support/python/repomap_test_support/retrieval_supplement_runtime_fixture.py",
)
SUPPLEMENT_CORE_OWNERS: tuple[str, ...] = tuple(
    f"tools/retrieval_supplement_{name}.py"
    for name in (
        "applicability", "baselines", "capture", "catalog", "corpus",
        "declaration", "files", "freeze", "operation", "operation_values",
        "pin", "preparation", "report", "tasks", "values",
    )
)
STUDY_SELECTOR_OWNERS: tuple[str, ...] = (
    "src/test/int/python/tools/retrieval_supplement_native.int.test.py",
)
ALL_EXPLICIT_STUDY_OWNERS: tuple[str, ...] = tuple(sorted({
    *STUDY_SOURCE_OWNERS,
    *STUDY_RUNNER_HOOK_OWNERS,
    *STUDY_SUPPORT_AND_TEST_OWNERS,
    *SUPPLEMENT_CORE_OWNERS,
}))


def _members(values: Sequence[str], label: str) -> list[str]:
    refuse(not isinstance(values, (str, bytes)), f"closure:{label}_sequence_required")
    items = [safe_relative_path(value) for value in values]
    refuse(len(items) <= MAX_MEMBERS, f"closure:{label}_cap")
    unique = sorted(set(items))
    for path in unique:
        refuse("fixtures" not in PurePosixPath(path).parts, "closure:fixture_member_refused")
    return unique


def compute_evaluator_closure(
    repo_root: Path, *, builder_members: Sequence[str], source_files: Mapping[str, bytes],
) -> dict[str, Any]:
    """Re-read and hash builder + all explicit study/runner/hook owners; drift refuses."""
    refuse(not isinstance(builder_members, (str, bytes)), "closure:members_sequence_required")
    normalized_builders = [safe_relative_path(value) for value in builder_members]
    refuse(len(set(normalized_builders)) == len(normalized_builders), "closure:members_duplicate")
    members = _members([*normalized_builders, *ALL_EXPLICIT_STUDY_OWNERS], "members")
    refuse(isinstance(source_files, Mapping), "closure:source_files_required")
    refuse(set(source_files) == set(members), "closure:membership_mismatch")
    files: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for path in members:
        provided = source_files[path]
        refuse(type(provided) is bytes, "closure:source_bytes_required")
        actual = read_member(repo_root, path, max_bytes=MAX_SOURCE_BYTES)
        refuse(actual == provided, "closure:source_drift")
        files[path] = {"bytes": len(actual), "sha256": digest_bytes(actual)}
        total_bytes += len(actual)
    record: dict[str, Any] = {
        "schema": "cloe-supplement-study-evaluator-closure-v1",
        "members": members,
        "files": files,
        "member_count": len(members),
        "total_bytes": total_bytes,
        "worker_closure_sha256": WORKER_CLOSURE_SHA256,
        "worker_closure_files": WORKER_CLOSURE_FILES,
        "hash_domains": {
            "evaluator_closure": "this record digest_json",
            "worker_closure_17_file": WORKER_CLOSURE_SHA256,
            "artifact_source_identity": ARTIFACT_SOURCE_IDENTITY_SHA256,
            "artifact_pin_document": ARTIFACT_PIN_DOCUMENT_SHA256,
        },
    }
    record["evaluator_closure_sha256"] = digest_json(record)
    refuse(record["evaluator_closure_sha256"] != WORKER_CLOSURE_SHA256,
           "closure:domain_collision_refused")
    return record


def discover_study_evaluator_closure(
    repo_root: Path, *, extra_builder_members: Sequence[str] = (),
) -> dict[str, Any]:
    """Freshly read the 17-file worker closure, public-text owners, and all study owners."""
    try:
        worker = worker_closure(repo_root)
    except SourceClosureRefusal as exc:
        raise ValueError(f"closure:worker_{exc.code}") from exc
    refuse(len(worker.files) == WORKER_CLOSURE_FILES
           and set(worker.files) == set(WORKER_SOURCE_FILES)
           and worker.identity == WORKER_CLOSURE_SHA256,
           "closure:worker_closure_drift")
    base_files = {f"tools/{name}": raw for name, raw in worker.files.items()}
    try:
        public_text = evaluator_closure(repo_root, base_files)
    except SourceClosureRefusal as exc:
        raise ValueError(f"closure:evaluator_{exc.code}") from exc
    builder_set = sorted({*public_text.files, *PUBLIC_TEXT_EVALUATOR_PATHS, *extra_builder_members})
    all_paths = _members([*builder_set, *ALL_EXPLICIT_STUDY_OWNERS], "discovered")
    fresh_files = {
        path: read_member(repo_root, path, max_bytes=MAX_SOURCE_BYTES)
        for path in all_paths
    }
    return compute_evaluator_closure(
        repo_root, builder_members=builder_set, source_files=fresh_files,
    )


def verify_evaluator_closure(
    repo_root: Path, expected: Mapping[str, Any], *,
    builder_members: Sequence[str], fresh_source_files: Mapping[str, bytes],
) -> None:
    rebuilt = compute_evaluator_closure(
        repo_root, builder_members=builder_members, source_files=fresh_source_files,
    )
    refuse(dict(expected) == rebuilt, "closure:reproduction_mismatch")


def require_verified_artifact_profile(profile: Any) -> str:
    """Return verified ``source_identity_sha256`` or refuse (F3)."""
    verified = getattr(profile, "verified_files", None)
    refuse(type(verified) is tuple and len(verified) == ARTIFACT_FILE_COUNT,
           "artifact:exactly_30_verified_files_required")
    assert isinstance(verified, tuple)
    refuse(all(bool(getattr(item, "sha256", None)) for item in verified),
           "artifact:verified_file_hash")
    wheels = getattr(profile, "wheels", None)
    if wheels is not None:
        refuse(type(wheels) is tuple and len(wheels) == ARTIFACT_WHEEL_COUNT,
               "artifact:wheel_count_mismatch")
    model_files = getattr(profile, "model_files", None)
    if model_files is not None:
        refuse(type(model_files) is tuple and len(model_files) == ARTIFACT_MODEL_FILE_COUNT,
               "artifact:model_file_count_mismatch")
    sizes = [getattr(item, "size", None) for item in verified]
    if all(type(size) is int for size in sizes):
        refuse(sum(cast(int, size) for size in sizes) == ARTIFACT_TOTAL_BYTES,
               "artifact:total_bytes_mismatch")
    identity = getattr(profile, "source_identity_sha256", None)
    refuse(identity == ARTIFACT_SOURCE_IDENTITY_SHA256, "artifact:source_identity_mismatch")
    refuse(identity != ARTIFACT_PIN_DOCUMENT_SHA256, "artifact:pin_document_not_identity")
    pin_sha = getattr(profile, "pinned_profile_sha256", getattr(profile, "profile_sha256", None))
    if pin_sha is not None:
        normalized_pin = pin_sha if str(pin_sha).startswith("sha256:") else f"sha256:{pin_sha}"
        refuse(normalized_pin == ARTIFACT_PIN_DOCUMENT_SHA256, "artifact:pin_document_mismatch")
        refuse(normalized_pin != identity, "artifact:hash_domain_confusion")
    return str(identity)


def verify_study_artifact_gate(
    artifact_root: Path, *, trusted_gate_receipt_sha256: str,
) -> str:
    """Verify all 30 retained files on disk AND require the external gate receipt."""
    refuse(type(trusted_gate_receipt_sha256) is str
           and _SHA.match(trusted_gate_receipt_sha256) is not None,
           "artifact:external_gate_receipt_required")
    refuse(trusted_gate_receipt_sha256 not in {
        ARTIFACT_PIN_DOCUMENT_SHA256, ARTIFACT_SOURCE_IDENTITY_SHA256, WORKER_CLOSURE_SHA256,
        INPUT_PIN_SHA256,
    }, "artifact:gate_receipt_domain_confusion")
    try:
        profile = verify_artifact_directory(artifact_root)
    except ArtifactVerificationError as exc:
        raise ValueError(f"artifact:{exc.code}") from exc
    return require_verified_artifact_profile(profile)


__all__ = [
    "ALL_EXPLICIT_STUDY_OWNERS", "STUDY_RUNNER_HOOK_OWNERS", "STUDY_SELECTOR_OWNERS",
    "STUDY_SOURCE_OWNERS", "STUDY_SUPPORT_AND_TEST_OWNERS", "SUPPLEMENT_CORE_OWNERS",
    "compute_evaluator_closure", "discover_study_evaluator_closure",
    "require_verified_artifact_profile", "verify_evaluator_closure",
    "verify_study_artifact_gate",
]
