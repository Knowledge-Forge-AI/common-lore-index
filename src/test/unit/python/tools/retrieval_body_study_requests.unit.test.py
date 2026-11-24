"""Unit ownership for exact body request descriptors and generic wire joins."""
from __future__ import annotations

from retrieval_body_study_requests import canonical_body_wire_bytes
import pytest

from retrieval_body_study_requests import BodyStudyRequest


def test_request_descriptor_rejects_non_body_phase_before_wire_use() -> None:
    request = BodyStudyRequest(
        1, "canonical-node-v1",
        {"phase": "CLOE-W5-BODY-STUDY0", "representation": "canonical-node-v1",
         "document_count": 66, "query_count": 4, "attempt_id": "attempt-1"},
        {}, b"", (), 66, 4, (), "sha256:" + "0" * 64, "sha256:" + "1" * 64,
    )
    with pytest.raises(ValueError):
        request.require_current()


def test_request_wire_identity_is_not_a_caller_supplied_record_count() -> None:
    request = BodyStudyRequest(
        2, "canonical-node-public-text-v2",
        {"phase": "CLOE-W5-BODY-STUDY1", "representation": "canonical-node-public-text-v2",
         "document_count": 44, "query_count": 20, "attempt_id": "attempt-1"},
        {"schema": "wrong"}, b"", (), 44, 20, (), "sha256:" + "0" * 64, "sha256:" + "1" * 64,
    )
    with pytest.raises(ValueError):
        request.require_current()


def test_request_bytes_match_compact_sorted_transport_contract() -> None:
    wire = {"records": [], "attempt_id": "body-v1", "schema": "body"}
    assert canonical_body_wire_bytes(wire) == b'{"attempt_id":"body-v1","records":[],"schema":"body"}'
