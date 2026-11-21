"""Body model/host/epoch transport owners (written, parent executes)."""
from __future__ import annotations

from collections.abc import Mapping
import inspect
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time
from typing import cast

import pytest

from retrieval_body_study_contracts import BODY_PHASE, BODY_ROLE, BodyStudyRefusal
from retrieval_body_study_epoch import (
    EPOCH_SCHEMA, PreparationEpoch, decode_epoch_line, parse_epoch,
)
from retrieval_body_study_exchange import BodyEpochEvidence, BodyExchangeError, run_exchange_epoch
from retrieval_semantic_experiment_bridge import ComponentExchange
from retrieval_body_study_host_exchange import HostExchangeError, _artifact_verifier, run_exchange
from retrieval_body_study_host_transport import (
    ACK_PREFIX, HostTransportError, epoch_ack_plan, parse_admission_ack_line,
)
import retrieval_body_study_model_host as model_host
from retrieval_body_study_model_host import _model_command
from retrieval_body_study_model_contract import (
    ARTIFACT_PROFILE_ID, BODY_INPUT_PIN_SHA256, SDK_IMAGE_ID, WORKER_CLOSURE_ID,
    ModelHostPaths, ModelHostRefusal, ValidatedStudyRequest,
    query_manifest_identity, validate_body_model_admission,
    validate_body_model_request,
)


def _digest(value: str = "a") -> str:
    return "sha256:" + value * 64


def _request(**changes: object) -> dict[str, object]:
    manifest = [{"record_id": f"query:q{index}", "text_sha256": _digest(chr(97 + index))}
                for index in range(4)]
    value: dict[str, object] = {
        "phase": BODY_PHASE, "role": BODY_ROLE, "ordinal": 1,
        "attempt_id": "attempt-body", "request_attempt_id": "attempt-body-v1",
        "representation": "canonical-node-v1", "document_count": 66, "query_count": 4,
        "total_record_count": 70, "input_identity": _digest("b"),
        "representation_report_identity": _digest("c"), "publication_identity": _digest("d"),
        "query_task_ids": [f"q{index}" for index in range(4)], "query_manifest": manifest,
        "query_manifest_sha256": query_manifest_identity(manifest), "backend": "postgresql",
        "generation": 1, "input_pin_sha256": BODY_INPUT_PIN_SHA256,
        "execution_authority_granted": False, "model_executed": False,
    }
    value.update(changes)
    return value


def _epoch(**changes: object) -> PreparationEpoch:
    value = {
        "schema": EPOCH_SCHEMA,
        "attempt_id": "attempt-body",
        "ordinal": 1,
        "representation": "canonical-node-v1",
        "nonce": "a" * 64,
        "request_attempt_id": "attempt-body-v1",
        "input_pin_sha256": "sha256:0b62374c086e96309d66c32999a8928981942eea38cca2f976880f851c6e378b",
        "evaluator_closure_sha256": _digest("b"),
    }
    value.update(changes)
    return parse_epoch(value)


def test_body_model_request_refuses_old_phase_and_wrong_census():
    base = _request()
    assert validate_body_model_request(base)["phase"] == BODY_PHASE
    with pytest.raises(ModelHostRefusal, match="body_request_phase"):
        validate_body_model_request({**base, "phase": "CLOE-W5-SUPPLEMENT-STUDY1"})
    with pytest.raises(ModelHostRefusal, match="body_request_census"):
        validate_body_model_request({**base, "query_count": 20})
    with pytest.raises(ModelHostRefusal, match="body_request_role"):
        validate_body_model_request({**base, "role": "study"})


def test_body_descriptor_binds_query_text_and_study_attempt():
    base = _request()
    with pytest.raises(ModelHostRefusal, match="body_request_attempt_join"):
        validate_body_model_request({**base, "request_attempt_id": "other-v1"})
    with pytest.raises(ModelHostRefusal, match="body_request_query_manifest"):
        validate_body_model_request({**base, "query_manifest_sha256": _digest("e")})
    with pytest.raises(ModelHostRefusal, match="body_request_input_pin"):
        validate_body_model_request({**base, "input_pin_sha256": _digest("f")})


def test_body_model_admission_requires_body_join():
    request = {"phase": BODY_PHASE, "role": BODY_ROLE, "representation": "canonical-node-public-text-v2",
               "document_count": 44, "query_count": 20}
    assert validate_body_model_admission({"phase": BODY_PHASE, "role": BODY_ROLE}, request)["phase"] == BODY_PHASE
    with pytest.raises(ModelHostRefusal, match="body_admission_phase"):
        validate_body_model_admission({"phase": "CLOE-W5-SUPPLEMENT-STUDY1"}, request)
    with pytest.raises(ModelHostRefusal, match="body_admission_join"):
        validate_body_model_admission({"phase": BODY_PHASE, "role": "study"}, request)


def test_epoch_is_body_specific_and_canonical():
    epoch = _epoch()
    assert epoch.raw.endswith(b"\n") and epoch.to_dict()["schema"] == EPOCH_SCHEMA
    assert decode_epoch_line(b"CLOE_BODY_STUDY_PREPARATION_EPOCH " + epoch.raw[:-1]) == epoch
    with pytest.raises(BodyStudyRefusal, match="body_epoch:representation"):
        _epoch(representation="canonical-node-public-text-v2")
    with pytest.raises(BodyStudyRefusal, match="body_epoch:request_attempt"):
        _epoch(request_attempt_id="attempt-body-v2")
    with pytest.raises(BodyStudyRefusal, match="body_epoch:input_pin"):
        _epoch(input_pin_sha256=_digest("c"))


def test_epoch_ack_plan_has_no_shell_and_binds_container_and_epoch():
    command, payload = epoch_ack_plan("f" * 64, _epoch())
    assert command[:5] == ("docker", "exec", "-i", "f" * 64, "/usr/local/bin/python3")
    assert payload == _epoch().raw and "sh -c" not in command
    with pytest.raises(HostTransportError, match="container_identity"):
        epoch_ack_plan("not-a-container", _epoch())


def test_body_ack_is_distinct_and_request_bound():
    epoch = _epoch()
    ready = type("Ready", (), {"nonce": epoch.nonce, "attempt_id": epoch.request_attempt_id,
                                "request_sha256": _digest("d")})()
    raw = (ACK_PREFIX + '{"admission_sha256":"' + _digest("e") + '","attempt_id":"attempt-body-v1",'
           '"nonce":"' + epoch.nonce + '","request_sha256":"' + _digest("d") + '",'
           '"schema":"cloe-w5-body-component-admission-ack-v1"}\n').encode()
    parse_admission_ack_line(raw, ready=ready, admission_sha256=_digest("e"))
    with pytest.raises(HostTransportError, match="admission_ack_identity"):
        parse_admission_ack_line(raw.replace(b"attempt-body-v1", b"attempt-other"), ready=ready, admission_sha256=_digest("e"))


def test_outer_and_inner_exchange_apis_preserve_old_composition_signature():
    outer = set(inspect.signature(run_exchange).parameters)
    assert outer == {
        "epoch", "epoch_started", "reader", "require_outer", "source_guard", "charge_bytes",
        "charge_operation", "before_worker", "plan", "artifact_root", "worker_source", "worker_entry",
        "output_root", "worker_digests", "gate_references", "charge_docker_bytes", "clock",
    }
    inner = set(inspect.signature(run_exchange_epoch).parameters)
    assert inner == {"request_map", "request_raw", "worker_digests", "delivery", "representation",
                     "evidence_root", "evidence", "require_current"}


def test_inner_exchange_rejects_non_body_record_census_before_publish():
    request = {"phase": BODY_PHASE, "role": BODY_ROLE, "representation": "canonical-node-v1",
               "document_count": 66, "query_count": 4, "attempt_id": "attempt-body-v1"}
    with pytest.raises(BodyExchangeError, match="worker_closure_shape"):
        run_exchange_epoch(request, b"{}", worker_digests={}, delivery=None,
                           representation="canonical-node-v1", evidence_root=Path("/sandbox/body"), evidence=None)


def test_preparation_refuses_attempt_mismatch_before_emitting_epoch(monkeypatch):
    import retrieval_body_study_exchange as subject

    monkeypatch.setattr(subject, "read_inner_body_plan", lambda _path: {"attempt_id": "body-study2"})
    monkeypatch.setattr(subject, "prepare_epoch", lambda **_fields: pytest.fail("unexpected epoch"))
    with pytest.raises(BodyExchangeError, match="epoch_attempt_join"):
        subject._await_preparation_ack(
            {"attempt_id": "body-study1", "request_attempt_id": "body-study1-v1"},
            "canonical-node-v1", lambda: None,
        )


def test_preparation_refuses_io_above_its_prepaid_envelope(monkeypatch):
    import retrieval_body_study_exchange as subject

    monkeypatch.setattr(subject, "read_inner_body_plan", lambda _path: {
        "attempt_id": "body-study1", "evaluator_closure_sha256": _digest("a"),
    })

    def exceed_budget(*, charge, **_fields):
        charge(subject.INNER_EPOCH_IO_BYTES)
        charge(1)

    monkeypatch.setattr(subject, "prepare_epoch", exceed_budget)
    monkeypatch.setattr(subject, "await_acknowledgement", lambda *_args, **_kw: pytest.fail("unexpected ACK"))
    with pytest.raises(BodyExchangeError, match="epoch_byte_budget"):
        subject._await_preparation_ack(
            {"attempt_id": "body-study1", "request_attempt_id": "body-study1-v2"},
            "canonical-node-public-text-v2", lambda: None,
        )


def test_epoch_evidence_repr_and_diagnostics_do_not_expose_payload():
    result = BodyEpochEvidence("attempt-body-v1", "canonical-node-v1", _digest("a"), _digest("b"), _digest("c"),
                               Path("/sandbox/body"), cast(ComponentExchange, object()), {"report.json": b"secret"}, b"secret-response")
    assert "secret" not in repr(result)
    assert result.diagnostics["member_names"] == ("report.json",)
    member_sha256 = cast(Mapping[str, str], result.diagnostics["member_sha256"])
    assert member_sha256["report.json"] == "sha256:" + hashlib.sha256(b"secret").hexdigest()


def test_artifact_verifier_requires_distinct_external_gate_receipt():
    with pytest.raises(HostExchangeError, match="artifact_profile_unbound"):
        _artifact_verifier({})
    with pytest.raises(HostExchangeError, match="artifact_profile_unbound"):
        _artifact_verifier({"artifact_gate_receipt_sha256": ARTIFACT_PROFILE_ID})


def test_model_paths_are_explicit_and_absolute():
    paths = ModelHostPaths(*(Path("/sandbox/body") / name for name in
                             ("request.json", "admission.json", "artifacts", "worker-ro", "entry.py", "output")))
    assert all(path.is_absolute() for path in paths.__dict__.values())
    assert "representation" in ValidatedStudyRequest.__dataclass_fields__


def test_model_command_is_socket_free_and_contained():
    paths = ModelHostPaths(*(Path("/sandbox/body") / name for name in
                             ("request.json", "admission.json", "artifacts", "worker-ro", "entry.py", "output")))
    command = _model_command(paths, _digest("e"), {"repomap.phase": BODY_PHASE}, "body-model")
    assert "--network=none" in command and "--read-only" in command
    assert "--cap-drop=ALL" in command and "--security-opt=no-new-privileges" in command
    assert not any("docker.sock" in item or item == "--network=host" for item in command)


def test_model_cleanup_runs_after_base_exception_with_owned_container(monkeypatch, tmp_path):
    paths = ModelHostPaths(*(tmp_path / name for name in
                             ("request.json", "admission.json", "artifacts", "worker-ro", "entry.py", "output")))
    request = ValidatedStudyRequest(
        "attempt-body", _digest("a"), _digest("b"), _digest("c"), _digest("d"), _digest("e"),
        "canonical-node-v1", 66, 4, ARTIFACT_PROFILE_ID, WORKER_CLOSURE_ID, 70, {},
        "sha256:" + hashlib.sha256(b"entry").hexdigest())
    cid = "c" * 64
    inspect_count = 0
    calls: list[tuple[str, ...]] = []
    image = {"Id": SDK_IMAGE_ID, "Architecture": "arm64", "Config": {"Labels": {}, "Volumes": None}}

    def docker(argv, *_args, **_kwargs):
        nonlocal inspect_count
        calls.append(argv)
        if argv[1] == "image":
            return SimpleNamespace(returncode=0, stdout=json.dumps([image]).encode(), stderr=b"")
        if argv[1] == "create":
            return SimpleNamespace(returncode=0, stdout=(cid + "\n").encode(), stderr=b"")
        if argv[1] == "inspect" and argv[-1] == cid:
            inspect_count += 1
            if inspect_count == 1:
                raise KeyboardInterrupt
            return SimpleNamespace(returncode=0, stdout=json.dumps([{"State": {"Running": False}}]).encode(), stderr=b"")
        if argv[1] == "rm":
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if argv[1] == "container":
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"No such container")
        raise AssertionError(argv)

    monkeypatch.setattr(model_host, "_fixed_path", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(model_host, "_source_closure", lambda *_args: None)
    monkeypatch.setattr(model_host, "_artifact_bank", lambda *_args: None)
    monkeypatch.setattr(model_host, "_read_stable", lambda *_args: b"entry")
    monkeypatch.setattr(model_host, "_validate_request", lambda *_args: {})
    monkeypatch.setattr(model_host, "_validate_admission", lambda *_args: None)
    monkeypatch.setattr(model_host, "_model_command", lambda *_args: ("docker", "create", "body"))
    monkeypatch.setattr(model_host, "_verify_model", lambda *_args: None)
    monkeypatch.setattr(model_host, "_bounded_docker", docker)
    result = model_host.run_model_sibling(
        request, paths, nonce="a" * 64, require_outer=lambda: "e" * 64,
        charge_bytes=lambda _count: None, verify_artifacts=lambda _root: None,
        charge_operation=lambda _name: None, epoch_started=time.monotonic() - 1,
        response_deadline=time.monotonic() + 600)
    assert result.status == "failed" and result.primary_code == "model_base_exception", (
        result.status, result.code, result.primary_code, tuple(argv[:2] for argv in calls))
    assert result.model_container_id == cid and result.cleanup_proven is True
    assert any(argv[1] == "rm" for argv in calls) and any(argv[1] == "container" for argv in calls)


def test_body_epoch_ack_charges_the_bound_transport_once() -> None:
    import retrieval_body_study_host_transport as subject

    operations: list[str] = []

    class Lease:
        def require_outer(self) -> str:
            return "f" * 64

        def _docker_input(self, command, payload, maximum, **kwargs):
            assert command[:4] == ["docker", "exec", "-i", "f" * 64]
            assert isinstance(payload, bytes) and maximum == 4096
            operations.append("docker.exec")
            return b""

    lease = Lease()
    subject.publish_epoch_ack(
        _epoch(), require_outer=lease.require_outer,
        charge_bytes=lambda _count: None, charge_operation=operations.append,
        deadline=10_000_000_000.0,
    )
    assert operations == ["docker.exec"]


__all__ = []
