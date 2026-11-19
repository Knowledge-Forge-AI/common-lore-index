"""Explicit one-use W5 lexical replay entrypoint, outside test discovery."""
from __future__ import annotations

from collections import Counter
from functools import partial
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from offline_qualification_values import BASE_CONFIG, SDK_IMAGE, digest, json_bytes
from runner_lexical_replay import (
    INPUT_PIN, LEXICAL_REPLAY_ARG, LexicalReplaySandbox, check_source, gate, host_context, load_inner,
)

ROOT = Path(__file__).resolve().parents[1]
SCOPE_COUNTS = {"body_positive": 12, "metadata_anchor": 4, "absent": 2,
                "ambiguous": 2, "privacy": 2, "unsupported": 2}
OUTCOME_PRECEDENCE = {
    "complete_descriptive": 0, "inconclusive_preflight": 1, "incomplete_technical": 2, "inadmissible": 3,
}


def _inner() -> int:
    from repomap_test_support.retrieval_body_study_pg import disposable_socket_postgres
    from retrieval_body_study_inputs import load_body_study_inputs
    from retrieval_lexical_replay import schedule
    from retrieval_lexical_replay_execution import ReplayJournal
    from test_sandbox_capacity import read_binding, validate_scratch_capacity
    from test_sandbox_contract import INNER_REPORT_ROOT

    plan = load_inner(os.environ.get("CLOE_LEXICAL_REPLAY_SEAL", ""), root=ROOT)
    read_binding()
    validate_scratch_capacity()
    gate("CLOE_PGVECTOR_OFFLINE_PLAN" not in os.environ and
         "REPOMAP_TEST_PG_IMAGE" not in os.environ, "foreign runtime control")
    INNER_REPORT_ROOT.mkdir(mode=0o700, parents=True)
    journal = ReplayJournal(INNER_REPORT_ROOT)
    treatment = {
        "attempt": plan["attempt"], "source": plan["candidate_head"],
        "source_sha256": digest(json_bytes(plan["candidate_snapshot"])),
        "protocol_sha256": plan["protocol_sha256"], "input_pin_sha256": INPUT_PIN,
        "sdk_image": SDK_IMAGE, "core_image": BASE_CONFIG, "exposure": "already_exposed_secondary",
    }
    journal.retain("attempt.json", {"treatment": treatment, "schedule": plan["schedule"]})
    result: dict[str, Any] | None = None
    lifecycle: dict[str, Any] | None = None
    stage = "registered_input"
    faults: list[dict[str, str]] = []
    try:
        base = ROOT / "src/test/fixtures/retrieval_evaluation"
        # require_current performs the complete factory/pin validation once.
        frame = load_body_study_inputs(base / "body-v2", predecessor_root=base, validate=False)
        snapshot = frame.require_current()
        cases = snapshot.catalog.cases
        gate(snapshot.pin_sha256 == INPUT_PIN and Counter(case.kind for case in cases) == SCOPE_COUNTS,
             "registered cohort census")
        gate(sorted(Counter(case.task.family for case in cases if case.kind == "body_positive").values())
             == [4, 4, 4], "registered family census")
        journal.retain("declared-slots.json", {"slots": [slot.key() for slot in schedule(cases)]})
        stage = "postgres_start"
        with disposable_socket_postgres(BASE_CONFIG, install_schema=_core_only) as (postgres, lifecycle):
            stage = "publication_and_replay"
            # Settle the primary outcome before crossing the cleanup boundary.
            try:
                result = _run_publication_replay(frame, snapshot, postgres, treatment, journal)
            except (Exception, KeyboardInterrupt) as error:
                result = _record_fault({"treatment": treatment}, stage, error, faults)
            stage = "postgres_cleanup"
    except (Exception, KeyboardInterrupt) as error:
        result = _record_fault(result or {"treatment": treatment}, stage, error, faults)
    cleanup = lifecycle is not None and lifecycle.get("container_absent") is True and lifecycle.get("socket_absent") is True
    assert result is not None
    if not cleanup:
        faults.append({"stage": "postgres_cleanup", "error_type": "missing_cleanup_evidence"})
        if result["outcome"] != "incomplete_technical":
            result = _terminal_disposition(result, "incomplete_technical", "inner_cleanup_unproven")
    try:
        check_source(ROOT, plan)
    except (OSError, RuntimeError):
        faults.append({"stage": "source_readback", "error_type": "source_drift"})
        result = _terminal_disposition(result, "inadmissible", "source_drift")
    journal.retain("terminal.json", result | {
        "inner_cleanup_proven": cleanup,
        "technical_faults": faults,
        "lifecycle_counts": {key: lifecycle.get(key) for key in
                             ("command_count", "command_reserved_bytes", "readiness_probe_count",
                              "container_id_digest", "container_absent", "socket_absent")}
                             if lifecycle is not None else None,
    })
    print(f"Lexical replay terminal outcome {result['outcome']}", flush=True)
    return 0 if cleanup else 2


def _run_publication_replay(
    frame: Any, snapshot: Any, postgres: Any, treatment: dict[str, str], journal: Any,
) -> dict[str, Any]:
    from repomap_test_support.retrieval_body_study_pg_publication import publish_registered_lexical_store
    from retrieval_lexical_replay import ReplayIntegrityError, ReplayShortfall, ReplayStore, query_without_identity
    from retrieval_lexical_replay_execution import paired_replay, read_generation_census

    registered = publish_registered_lexical_store(
        Path("/sandbox-scratch/lexical-workspace"), inputs=frame.inputs, policy=snapshot.policy,
        postgres=postgres.database, psql_command=postgres.psql_command,
    )
    pin = registered.store.pin
    files = tuple(sorted(
        (file.binding_id, file.source_relative_path) for file in snapshot.corpus.files
        if file.binding_id in pin.admitted_public_bindings
    ))
    if len(pin.admitted_public_bindings) != 2:
        raise ReplayIntegrityError("registered_public_binding_scope")
    if len(files) != 22:
        raise ReplayShortfall("registered_public_file_shortfall")
    store = ReplayStore(
        pin, snapshot.policy, registered.generation_id, registered.lexical_policy_digest,
        partial(read_generation_census, registered.ops_config, pin),
        partial(query_without_identity, registered.ops_config, pin.graph_id),
    )
    return paired_replay(snapshot.catalog.cases, store, expected_files=files,
                         treatment=treatment, retain=journal.retain)


def _record_fault(
    result: dict[str, Any], stage: str, error: BaseException, faults: list[dict[str, str]],
) -> dict[str, Any]:
    from retrieval_lexical_replay import ReplayIntegrityError, ReplayShortfall

    faults.append({"stage": stage, "error_type": type(error).__name__})
    if isinstance(error, ReplayIntegrityError):
        return _terminal_disposition(result, "inadmissible", str(error))
    if isinstance(error, ReplayShortfall):
        return _terminal_disposition(result, "inconclusive_preflight", str(error))
    return _terminal_disposition(result, "incomplete_technical", type(error).__name__)


def _terminal_disposition(result: dict[str, Any], outcome: str, reason: str) -> dict[str, Any]:
    if OUTCOME_PRECEDENCE.get(result.get("outcome", ""), -1) >= OUTCOME_PRECEDENCE[outcome]:
        return result
    return result | {"outcome": outcome, "reason": reason, "quality": None}


def _core_only(_handle: object) -> None:
    """Use the existing core migrations without an extension setup callback."""


def main(argv: list[str]) -> int:
    if argv == ["--inner"]:
        return _inner()
    if len(argv) != 2 or argv[0] != "--plan":
        raise SystemExit("usage: run_lexical_replay.py --plan <absolute private plan.json>")
    from runner_pgvector_context import require_native_host
    from test_sandbox import _captured, active_sandbox, cached_sandbox_image, run_in_sandbox
    from test_sandbox_dispatch import DOCKERFILE

    gate(not active_sandbox(), "inner authority cannot launch an outer sandbox")
    require_native_host()
    image = cached_sandbox_image(dockerfile=DOCKERFILE, expected_image_id=SDK_IMAGE)
    context = host_context(Path(argv[1]), ROOT)
    offline = LexicalReplaySandbox(context, subprocess.run, _captured)
    return run_in_sandbox([LEXICAL_REPLAY_ARG], repo_root=ROOT, image_id=image,
                          offline_override=offline, ownership_reporter=offline.record_owner)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"Lexical replay precondition or execution failed ({type(exc).__name__})") from None
