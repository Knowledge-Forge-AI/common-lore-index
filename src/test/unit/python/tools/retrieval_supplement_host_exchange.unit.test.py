"""Focused host-exchange contract tests; no Docker, imports, or native runs here."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import tarfile

import pytest

import retrieval_supplement_host_exchange as exchange
import retrieval_supplement_host_transport as transport
from retrieval_semantic_bridge_transport import ReadyEvent
from retrieval_supplement_epoch import PreparationEpoch
from retrieval_supplement_model_contract import WORKER_SOURCE_FILES
from retrieval_supplement_study_contracts import (
    ARTIFACT_SOURCE_IDENTITY_SHA256, INPUT_PIN_SHA256, STUDY_PHASE,
)


CID = "a" * 64
DIGEST = "sha256:" + "1" * 64


def _epoch(ordinal: int = 1) -> PreparationEpoch:
    return PreparationEpoch(
        "study-1", ordinal, ("canonical-node-v1", "canonical-node-public-text-v2")[ordinal - 1],
        "b" * 64, f"study-1-v{ordinal}", INPUT_PIN_SHA256, DIGEST,
    )


def _ready(epoch: PreparationEpoch) -> ReadyEvent:
    return ReadyEvent("b" * 64, epoch.request_attempt_id,
                      f"{epoch.request_attempt_id}/w5-component-{'b' * 64}/request.json",
                      DIGEST, 100)


def _request(epoch: PreparationEpoch, *, legacy: bool = False) -> dict[str, object]:
    documents, queries = (66, 20) if epoch.ordinal == 1 else (40, 20)
    records = [{"record_id": f"doc-{index}", "text": "document", "mode": "document"}
               for index in range(documents)]
    records.extend({"record_id": "cal-23" if legacy and index == 0 else f"query-{index}",
                    "text": "query", "mode": "query"} for index in range(queries))
    return {"schema": "cloe-w5-native-generation-request-v1", "attempt_id": epoch.request_attempt_id,
            "input_identity": INPUT_PIN_SHA256, "representation_report_identity": DIGEST,
            "publication_identity": DIGEST, "artifact_profile_identity": ARTIFACT_SOURCE_IDENTITY_SHA256,
            "records": records}


def _raw(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def test_epoch_ack_plan_binds_fixed_container_and_epoch_directory():
    epoch = _epoch()
    argv, payload = transport.epoch_ack_plan(CID, epoch)
    assert argv[:4] == ("docker", "exec", "-i", CID)
    assert argv[-1] == transport.EPOCH_ACK_REMOTE_SOURCE
    assert payload == epoch.raw


def test_private_evidence_is_write_once_and_never_replaced(tmp_path):
    path = tmp_path / "request.json"
    transport.write_private_once(path, b"first", maximum=32, charge_bytes=lambda _count: None)
    with pytest.raises(transport.HostTransportError, match="private_exists"):
        transport.write_private_once(path, b"second", maximum=32, charge_bytes=lambda _count: None)


def test_ready_requires_epoch_attempt_and_owned_request_path():
    epoch = _epoch()
    ready = _ready(epoch)
    assert exchange._ready(
        b'CLOE_W5_COMPONENT_READY ' + _raw({
            "schema": "cloe-w5-component-ready-v1", "nonce": ready.nonce,
            "attempt_id": ready.attempt_id, "request_relative_path": ready.request_relative_path,
            "request_sha256": ready.request_sha256, "request_bytes": ready.request_bytes,
        }), epoch
    ) == ready
    with pytest.raises(exchange.HostExchangeError, match="readiness_path"):
        exchange._ready(
            b'CLOE_W5_COMPONENT_READY ' + _raw({
                "schema": "cloe-w5-component-ready-v1", "nonce": ready.nonce,
                "attempt_id": ready.attempt_id,
                "request_relative_path": "old/w5-component-" + ready.nonce + "/request.json",
                "request_sha256": ready.request_sha256, "request_bytes": ready.request_bytes,
            }), epoch,
        )


@pytest.mark.parametrize("ordinal", [1, 2])
def test_request_shape_has_exact_document_query_cohort_and_rejects_cal23(ordinal, monkeypatch):
    epoch = _epoch(ordinal)
    document = _request(epoch)
    raw = _raw(document)
    ready = replace(_ready(epoch), request_sha256="sha256:" + hashlib.sha256(raw).hexdigest())
    assert exchange._request_document(raw, ready, epoch) == document
    bad = _request(epoch, legacy=True)
    bad_raw = _raw(bad)
    bad_ready = replace(ready, request_sha256="sha256:" + hashlib.sha256(bad_raw).hexdigest())
    with pytest.raises(exchange.HostExchangeError, match="request_legacy_query"):
        exchange._request_document(bad_raw, bad_ready, epoch)


def test_transport_ack_rejects_wrong_admission_digest():
    epoch = _epoch()
    ready = _ready(epoch)
    line = "CLOE_W5_COMPONENT_ADMISSION_ACK " + json.dumps({
        "schema": transport.ACK_SCHEMA, "nonce": ready.nonce, "attempt_id": ready.attempt_id,
        "request_sha256": ready.request_sha256, "admission_sha256": DIGEST,
    }, sort_keys=True, separators=(",", ":"))
    with pytest.raises(transport.HostTransportError, match="admission_ack_identity"):
        transport.parse_admission_ack_line(line, ready=ready, admission_sha256="sha256:" + "2" * 64)


def test_source_closure_requires_exact_seventeen_names():
    with pytest.raises(exchange.HostExchangeError, match="worker_closure_shape"):
        exchange._source_digests({"foreign.py": DIGEST})
    from pathlib import Path
    from retrieval_public_text_sources import worker_closure
    closure = worker_closure(Path(exchange.__file__).resolve().parents[1]).digests
    assert exchange._source_digests(closure) == dict(sorted(closure.items()))
    with pytest.raises(exchange.HostExchangeError, match="worker_closure_identity"):
        exchange._source_digests({name: DIGEST for name in WORKER_SOURCE_FILES})


def test_admission_is_closed_and_binds_operator_gate_and_worker_closure():
    epoch = _epoch()
    document = _request(epoch)
    closure = {name: DIGEST for name in WORKER_SOURCE_FILES}
    raw = exchange._admission(document=document, request_sha=DIGEST,
                              plan={"phase": STUDY_PHASE, "operator_decision_sha256": DIGEST},
                              gate_references={"parent": DIGEST}, source_closure=closure)
    assert json.loads(raw) == {
        "schema": "cloe-w5-native-generation-admission-v1", "phase": STUDY_PHASE,
        "operator_authority_sha256": DIGEST, "gate_references": {"parent": DIGEST},
        "bounded_component_execution_admitted": True, "request_sha256": DIGEST,
        "input_identity": INPUT_PIN_SHA256, "representation_report_identity": DIGEST,
        "publication_identity": DIGEST, "artifact_profile_identity": ARTIFACT_SOURCE_IDENTITY_SHA256,
        "candidate_closure_digests": dict(sorted(closure.items())),
        "scope": STUDY_PHASE + " sibling representation worker",
    }


def test_sdk_admission_publication_failure_starts_no_model(monkeypatch, tmp_path):
    from pathlib import Path
    import time
    from unittest.mock import Mock
    from retrieval_public_text_sources import worker_closure
    epoch = _epoch()
    request_raw = _raw(_request(epoch))
    ready = replace(_ready(epoch), request_sha256="sha256:" + hashlib.sha256(request_raw).hexdigest(),
        request_bytes=len(request_raw))
    request_tar = io.BytesIO()
    with tarfile.open(fileobj=request_tar, mode="w") as archive:
        info = tarfile.TarInfo("request.json")
        info.size, info.mode = len(request_raw), 0o600
        archive.addfile(info, io.BytesIO(request_raw))
    calls = []
    class Lease:
        def require_outer(self): return CID
        def _docker(self, command, limit, **kwargs):
            calls.append("request_read")
            assert command[:3] == ["docker", "exec", CID]
            return request_tar.getvalue()
        def _docker_input(self, command, payload, limit, **kwargs):
            calls.append("admission_publish")
            assert command[:4] == ["docker", "exec", "-i", CID]
            assert payload and limit == 8192
            raise RuntimeError("synthetic admission publication failure")
    lease = Lease()
    class Reader:
        def await_frame(self, prefix, **kwargs):
            assert prefix == "CLOE_W5_COMPONENT_READY "
            return prefix.encode() + _raw({"schema": "cloe-w5-component-ready-v1",
                "nonce": ready.nonce, "attempt_id": ready.attempt_id,
                "request_relative_path": ready.request_relative_path,
                "request_sha256": ready.request_sha256, "request_bytes": ready.request_bytes})
    model = Mock(side_effect=AssertionError("no model authority without SDK ACK"))
    monkeypatch.setattr(exchange, "run_model_sibling", model)
    with pytest.raises(exchange.HostExchangeError, match="bounded_docker_failed"):
        exchange.run_exchange(epoch=epoch, epoch_started=time.monotonic(), reader=Reader(),
            require_outer=lease.require_outer, source_guard=lambda: None,
            charge_bytes=lambda count: None, charge_operation=lambda name: None,
            before_worker=lambda: calls.append("worker_reserved"),
            plan={"phase": STUDY_PHASE, "attempt_id": epoch.attempt_id,
                "evaluator_closure_sha256": epoch.evaluator_closure_sha256,
                "operator_decision_sha256": DIGEST}, artifact_root=tmp_path,
            worker_source=tmp_path, worker_entry=tmp_path / "entry", output_root=tmp_path,
            worker_digests=worker_closure(Path(exchange.__file__).resolve().parents[1]).digests,
            gate_references={"parent": DIGEST})
    assert calls == ["request_read", "admission_publish"]
    assert (tmp_path / "request.json").read_bytes() == request_raw
    assert (tmp_path / "admission.json").is_file()
    assert not (tmp_path / "response.zip").exists()
    model.assert_not_called()


def test_authority_guard_refuses_non_digest_or_exception():
    with pytest.raises(exchange.HostExchangeError, match="outer_authority_lost"):
        exchange._guard(lambda: None, lambda: (_ for _ in ()).throw(RuntimeError("private")))
    with pytest.raises(exchange.HostExchangeError, match="outer_identity"):
        exchange._guard(lambda: None, lambda: "foreign")


def test_preparation_nonce_and_component_nonce_are_distinct_but_both_bound():
    epoch = _epoch()
    ready = replace(_ready(epoch), nonce="c" * 64,
        request_relative_path=f"{epoch.request_attempt_id}/w5-component-{'c' * 64}/request.json")
    raw = b"CLOE_W5_COMPONENT_READY " + _raw({"schema": "cloe-w5-component-ready-v1",
        "nonce": ready.nonce, "attempt_id": ready.attempt_id,
        "request_relative_path": ready.request_relative_path, "request_sha256": ready.request_sha256,
        "request_bytes": ready.request_bytes})
    assert exchange._ready(raw, epoch) == ready


@pytest.mark.parametrize("receipt", [None, ARTIFACT_SOURCE_IDENTITY_SHA256, INPUT_PIN_SHA256, "bad"])
def test_artifact_metadata_or_hash_domain_does_not_supply_external_gate(receipt):
    references = {} if receipt is None else {"artifact_gate_receipt_sha256": receipt}
    with pytest.raises(exchange.HostExchangeError, match="artifact_profile_unbound"):
        exchange._artifact_verifier({"artifact_profile": {"source_identity_sha256":
            ARTIFACT_SOURCE_IDENTITY_SHA256}}, references)
