"""Finite source and Docker sub-budget proof for the two-worker host schedule.

The fixed call-site manifest bounds successful nonpoll paths plus owned cleanup.
Polling derives from the actual model timeout and shared cadence. Readiness is
one strict inspect after Docker start; malformed state refuses without a retry.
This is a controlled-operation forecast, not physical I/O or native preemption.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from retrieval_supplement_model_budget import TIMEOUTS
from retrieval_supplement_study_contracts import (
    MAX_MODEL_WORKERS, MODEL_LEASE_POLL_SECONDS, SOURCE_VERIFICATION_LIMIT, refuse,
)

# Enumerated source sites: file/directory custody, request/admission/response,
# model create/start/wait, epoch/request/ACK boundaries and bounded cleanup.
CALL_SITE_BOUNDS = {
    "nonpoll_lease_calls": 246,
    "direct_full_source_hops": 29,
    "direct_docker_operations": 38,
    "fixed_nonlease_stream_bytes": 75_620_428,
}
DOCKER_OPERATION_LIMIT = 2048
DOCKER_STREAM_LIMIT = 2 * 1024**3


def host_poll_budget(*, cadence: float = MODEL_LEASE_POLL_SECONDS,
                     full_checks_per_lease: int = 1,
                     manifest: Mapping[str, int] = CALL_SITE_BOUNDS) -> dict[str, Any]:
    """Refuse a schedule that cannot fit every declared host sub-budget."""
    refuse(type(cadence) in (int, float) and math.isfinite(cadence) and cadence > 0,
           "poll_budget:cadence")
    refuse(type(full_checks_per_lease) is int and full_checks_per_lease >= 1,
           "poll_budget:source_checks")
    refuse(dict(manifest) == CALL_SITE_BOUNDS, "poll_budget:call_site_manifest")
    # The final stop/expiry observation is conservatively included per worker.
    model_polls = math.ceil(TIMEOUTS["model"] / cadence) + 1
    readiness_polls = 1
    polls = MAX_MODEL_WORKERS * (model_polls + readiness_polls)
    lease_calls = manifest["nonpoll_lease_calls"] + 2 * polls
    source_checks = manifest["direct_full_source_hops"] + full_checks_per_lease * lease_calls
    operations = manifest["direct_docker_operations"] + polls + 2 * lease_calls
    lease_stream = 2 * (256 * 1024 + 1) + 65536 + 2 * (128 + 1) + 65536
    inspect_stream = 2 * (512 * 1024 + 1) + 65536
    streams = manifest["fixed_nonlease_stream_bytes"] + lease_calls * lease_stream + polls * inspect_stream
    refuse(source_checks <= SOURCE_VERIFICATION_LIMIT, "poll_budget:source_ceiling")
    refuse(operations <= DOCKER_OPERATION_LIMIT, "poll_budget:operation_ceiling")
    refuse(streams <= DOCKER_STREAM_LIMIT, "poll_budget:stream_ceiling")
    return {"model_seconds_per_worker": TIMEOUTS["model"], "cadence_seconds": cadence,
        "workers": MAX_MODEL_WORKERS, "model_polls_per_worker": model_polls,
        "readiness_polls_per_worker": readiness_polls, "lease_calls": lease_calls,
        "source_verifications": source_checks, "docker_operations": operations,
        "docker_stream_reservation_bytes": streams, "call_site_bounds": dict(manifest),
        "full_checks_per_lease": full_checks_per_lease,
        "limits": {"source_verifications": SOURCE_VERIFICATION_LIMIT,
            "docker_operations": DOCKER_OPERATION_LIMIT, "docker_stream_bytes": DOCKER_STREAM_LIMIT}}
