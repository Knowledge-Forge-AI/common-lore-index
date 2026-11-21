"""Response identity follows received bytes across valid ZIP encodings."""
from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import FrozenInstanceError, replace
import hashlib
import io
import json
import operator
from pathlib import Path
from types import SimpleNamespace
import zipfile
from typing import cast

import pytest

from retrieval_body_study_exchange import (
    BodyEpochEvidence, BodyExchangeError, _ISSUED, _evidence_identity, _response_digest,
)
from retrieval_semantic_bridge_transport import encode_response_bundle
from retrieval_semantic_experiment_bridge import ComponentExchange
import retrieval_semantic_bridge_transport_io as transport_io


def _zip(files, *, compressed=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, raw in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2022, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            info.compress_type = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
            archive.writestr(info, raw)
    return output.getvalue()


def _case(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(transport_io, "SANDBOX_SCRATCH_ROOT", str(tmp_path))
    nonce = "a" * 64
    directory = tmp_path / "attempt-body-v1" / ("w5-component-" + nonce)
    directory.mkdir(mode=0o700, parents=True)
    members = {name: b"{}" for name in (
        "request.json", "admission.json", "declaration.json", "source-manifest.json", "report.json")}
    members["terminal.json"] = b'{"observation_count":0,"status":"completed"}'
    members["sources.zip"] = _zip({
        "sources/public.py": b"# public synthetic source\n",
        "inputs/request.json": members["request.json"],
        "inputs/admission.json": members["admission.json"],
    })
    raw = _zip(members)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    marker = {"nonce": nonce, "response_sha256": digest, "response_bytes": len(raw)}
    for name, content in {"response.zip": raw, "response.ready.json":
            (json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n").encode()}.items():
        path = directory / name
        path.write_bytes(content)
        path.chmod(0o600)
    return SimpleNamespace(directory=directory, nonce=nonce), members, raw, digest


def test_valid_alternative_zip_retains_original_wire_identity(tmp_path, monkeypatch):
    exchange, members, raw, expected = _case(tmp_path, monkeypatch)
    reencoded = encode_response_bundle(members)
    assert reencoded != raw
    assert "sha256:" + hashlib.sha256(reencoded).hexdigest() != expected
    assert _response_digest(exchange, members) == expected


def test_response_byte_change_after_receipt_refuses(tmp_path, monkeypatch):
    exchange, members, raw, _ = _case(tmp_path, monkeypatch)
    (exchange.directory / "response.zip").write_bytes(raw + b"x")
    with pytest.raises(BodyExchangeError, match="response_identity"):
        _response_digest(exchange, members)


def test_response_member_substitution_cannot_keep_original_identity(tmp_path, monkeypatch):
    exchange, members, _, _ = _case(tmp_path, monkeypatch)
    replacement = dict(members, **{"report.json": b'{"changed":true}'})
    with pytest.raises(BodyExchangeError, match="response_member_identity"):
        _response_digest(exchange, replacement)


def test_response_nonce_and_symlink_are_not_accepted(tmp_path, monkeypatch):
    exchange, members, _, _ = _case(tmp_path, monkeypatch)
    wrong = SimpleNamespace(directory=exchange.directory, nonce="b" * 64)
    with pytest.raises(BodyExchangeError):
        _response_digest(cast(ComponentExchange, wrong), members)
    path = exchange.directory / "response.zip"
    retained = exchange.directory / "retained-response.zip"
    path.rename(retained)
    path.symlink_to(retained)
    with pytest.raises(BodyExchangeError, match="response_bundle"):
        _response_digest(exchange, members)


def test_copying_or_rebinding_an_epoch_cannot_preserve_its_factory_seal(tmp_path):
    digest = "sha256:" + "a" * 64
    supplied = {"report.json": b"synthetic"}
    evidence = BodyEpochEvidence("synthetic-v1", "canonical-node-v1", digest, digest,
        digest, tmp_path, cast(ComponentExchange, None), supplied, SimpleNamespace(_sealed_identity=digest))
    assert BodyEpochEvidence.is_factory_issued(evidence) is False
    # Exercise the seal independently of worker execution, with unrelated data.
    object.__setattr__(evidence, "_seal", _evidence_identity(evidence))
    object.__setattr__(evidence, "_origin", _ISSUED)
    assert BodyEpochEvidence.is_factory_issued(evidence) is True
    supplied["report.json"] = b"replacement"
    assert evidence.members["report.json"] == b"synthetic"
    rebound = replace(evidence, request_sha256="sha256:" + "b" * 64)
    assert BodyEpochEvidence.is_factory_issued(rebound) is False
    with pytest.raises(FrozenInstanceError):
        setattr(evidence, "attempt_id", "rebound")
    with pytest.raises(TypeError):
        operator.setitem(cast(MutableMapping[str, bytes], evidence.members), "report.json", b"replacement")
