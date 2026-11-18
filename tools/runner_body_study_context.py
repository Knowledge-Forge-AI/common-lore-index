"""Body-only additions to offline dispatch; old cohorts keep their validators."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from offline_qualification_checkout import MAX_PLAN
from offline_qualification_values import BODY_CAPS, refuse, safe_path
from retrieval_body_study_contracts import (
    BODY_INNER_PLAN_SCHEMA, BODY_PHASE, BODY_PLAN_SCHEMA, BODY_ROLE, BODY_RUNTIME_ARCHIVE_BYTES,
    BODY_RUNTIME_ARCHIVE_SHA256, BODY_SELECTOR,
)


def select_offline_role(role: str, *, core: str, native: str, study: str,
                        proof_role: str, proof: str) -> str:
    selectors = {"core": core, "native": native, "study": study,
                 proof_role: proof, BODY_ROLE: BODY_SELECTOR}
    refuse(role in selectors, "unknown offline cohort role")
    return selectors[role]


def body_host_context(argv: list[str], root: Path, path: Path,
                      raw_sha256: str, plan: dict[str, Any]) -> Any:
    """Validate a host plan and its consumed, live runner admission."""
    from offline_qualification_checkout import verify_checkout
    from offline_qualification_files import private_file
    from runner_body_study import validate_body_cohort, validate_body_runner_plan
    from runner_pgvector_budget import Allocation, MAX_ARCHIVE
    from runner_pgvector_context import OfflineContext
    from retrieval_body_study_plan import require_reservation_admitted_for_runner

    refuse(plan.get("schema") == BODY_PLAN_SCHEMA and plan.get("role") == BODY_ROLE,
           "inner body plan cannot authorize host dispatch")
    validate_body_runner_plan(plan)
    validate_body_cohort(argv)
    count = sum((root / safe_path(name)).lstat().st_size
                for name, row in plan["candidate_snapshot"].items() if "symlink" not in row)
    refuse(0 < count <= 128 * 1024**2, "body source byte bound")
    with Allocation(Path(plan["allocation_path"]), BODY_CAPS).session() as meter:
        meter.charge("read_bytes", count + MAX_PLAN)
    verify_checkout(root, plan["candidate_snapshot"], plan["candidate_head"])
    archive = plan["archive"]
    refuse(type(archive["bytes"]) is int and 0 < archive["bytes"] <= MAX_ARCHIVE,
           "body archive descriptor")
    archive_path = Path(archive["path"])
    refuse(archive_path.is_absolute(), "relative body archive")
    private_file(archive_path)
    refuse(archive_path.stat().st_size == archive["bytes"] == BODY_RUNTIME_ARCHIVE_BYTES
           and archive.get("sha256") == BODY_RUNTIME_ARCHIVE_SHA256.removeprefix("sha256:"),
           "body archive descriptor")
    for name in ("allocation_path", "reservation_path"):
        resource = Path(plan[name])
        private_file(resource)
        refuse(resource.parent == path.parent, "nonlocal body offline resources")
    require_reservation_admitted_for_runner(plan, plan_path=path)
    return OfflineContext(path, plan, raw_sha256, root)


def body_inner_source_read(plan: Mapping[str, Any]) -> None:
    from retrieval_body_study_inner_reads import reserve_inner_source_verification
    refuse(plan.get("schema") == BODY_INNER_PLAN_SCHEMA and plan.get("role") == BODY_ROLE,
           "body source read requires sealed inner plan")
    reserve_inner_source_verification(plan, Path("/workspace"))


def body_source_read_receipt(environment: Mapping[str, str]) -> dict[str, Any] | None:
    from runner_pgvector_context import load_inner_plan, offline_plan_requested
    from retrieval_body_study_inner_read_report_finalizer import refresh_body_source_read_report
    if not offline_plan_requested(environment):
        return None
    plan = load_inner_plan(environment)
    refuse(plan["role"] == BODY_ROLE, "body receipt requires sealed body cohort")
    # The outer prepay reserves MAX_PLAN_BYTES plus two 16-KiB records for
    # final metadata. This suballocation cannot extend that reservation.
    from retrieval_body_study_contracts import MAX_PLAN_BYTES
    remaining = MAX_PLAN_BYTES + 2 * 16 * 1024

    def reserve_metadata(count: int) -> None:
        nonlocal remaining
        refuse(type(count) is int and 0 <= count <= remaining,
               "body final report metadata budget")
        remaining -= count

    result = refresh_body_source_read_report(
        plan, source_root=Path("/workspace"), expected_registered_snapshot_count=201,
        charge_bytes=reserve_metadata,
    )
    return dict(result["source_read_receipt"])


def is_body_receipt_phase(environment: Mapping[str, str]) -> bool:
    return environment.get("REPOMAP_TEST_PHASE", "").startswith(BODY_PHASE)


def body_scratch_bytes(plan: Mapping[str, Any]) -> int:
    refuse(plan.get("schema") == BODY_INNER_PLAN_SCHEMA and plan.get("role") == BODY_ROLE,
           "body scratch requires sealed inner plan")
    return 2 * 1024**3
