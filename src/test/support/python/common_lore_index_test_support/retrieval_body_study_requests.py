"""Native support import boundary for body request construction."""
from __future__ import annotations

from typing import Any

from retrieval_body_study_requests import (
    BodyStudyRequest, BodyStudyRequests, build_body_study_requests,
    canonical_body_wire_bytes, seal_body_v2_worker_response,
)


def build_actual_body_requests(
    preparation: Any, *, postgres_store: Any, scope: Any,
    attempt_id: str, artifact_profile_identity: str,
) -> BodyStudyRequests:
    return build_body_study_requests(
        preparation, postgres_store=postgres_store, scope=scope,
        attempt_id=attempt_id, artifact_profile_identity=artifact_profile_identity,
    )


__all__ = [
    "BodyStudyRequest", "BodyStudyRequests", "build_actual_body_requests",
    "build_body_study_requests", "canonical_body_wire_bytes",
    "seal_body_v2_worker_response",
]
