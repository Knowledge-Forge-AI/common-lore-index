"""Real preparation evidence lifecycle with functional inputs and fresh source reads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from common_lore_index_test_support.retrieval_supplement_fixtures import (
    make_test_bindings, make_test_catalog_payload, make_test_corpus_payload,
    make_test_exposures, make_test_identity, make_test_policy,
)
from common_lore_index_test_support.retrieval_supplement_prepare import check_checkpoint, prepare_checkpoint
from common_lore_index_test_support.retrieval_supplement_source import supplement_source_snapshot
from retrieval_supplement_catalog import parse_catalog
from retrieval_supplement_files import publish_member
from retrieval_supplement_freeze import build_input_pin
from retrieval_supplement_operation import PhaseRecorder
from retrieval_supplement_operation_values import FieldSample, PhaseIdentity
from retrieval_supplement_values import canonical_bytes, digest_bytes


def test_real_source_freeze_and_owned_functional_checkpoint_lifecycle(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[5]
    identity, bindings, policy = make_test_identity(), make_test_bindings(), make_test_policy()
    catalog_raw = canonical_bytes(make_test_catalog_payload())
    catalog = parse_catalog(catalog_raw, identity=identity, binding_ids=tuple(bindings.values()))
    predecessor_raw = canonical_bytes({"commit": "a" * 40, "files": [
        {"path": "pyproject.toml", "bytes": (repo / "pyproject.toml").stat().st_size,
         "sha256": digest_bytes((repo / "pyproject.toml").read_bytes())[7:]},
    ]})
    files = {
        "catalog-v1.json": catalog_raw, "corpus-v1.json": canonical_bytes(make_test_corpus_payload()),
        "policy-v1.json": canonical_bytes(policy.to_dict()), "bindings-v1.json": canonical_bytes(bindings),
        "rationale.md": b"# Functional fixture only\n", "predecessor-manifest.json": predecessor_raw,
        "namespace-v1.json": b'{"purpose":"functional_only"}', "static-inventory-v1.json": b'{"purpose":"functional_only"}',
    }
    pin = build_input_pin(catalog, input_files=files, policy=policy, predecessor_manifest_sha256=digest_bytes(predecessor_raw))
    pin_raw = json.dumps(pin, sort_keys=True, indent=2).encode() + b"\n"
    inputs = tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    for name, raw in files.items():
        publish_member(inputs, name, raw)
    publish_member(inputs, "input-pin-v1.json", pin_raw)
    output = tmp_path / "evidence"
    arguments: dict[str, Any] = dict(identity=identity, bindings=bindings, expected_pin_sha256=digest_bytes(pin_raw),
                     artifact_profile_sha256="sha256:" + "4" * 64, attempt_id="unit-functional-proposal",
                     functional_exposures=make_test_exposures())
    snapshot = supplement_source_snapshot(repo)
    recorder = PhaseRecorder()
    scope = PhaseIdentity(identity.supplement_id, digest_bytes(pin_raw), "sha256:" + "2" * 64,
                          snapshot.identity, "unit-functional-proposal", "owned-file-preparation", "input_preparation",
                          shared_owner="functional-caller")
    with recorder.scope(scope) as measured:
        manifest = prepare_checkpoint(repo, inputs, output, **arguments)
        measured.observe(FieldSample("written_files", "count", len(list(output.iterdir())), "observed", "caller"))
    assert recorder.records[0]["elapsed_ns"] >= 0
    assert recorder.records[0]["origin_or_execution_qualification"] is False
    assert manifest["execution_authorized"] is False and manifest["model_workers_started"] == 0
    assert check_checkpoint(repo, inputs, output, **arguments) == manifest
    assert supplement_source_snapshot(repo).builder_members == snapshot.builder_members
    with pytest.raises(ValueError, match="output_exists"):
        prepare_checkpoint(repo, inputs, output, **arguments)
    checkpoint = output / "checkpoint-v1.json"
    changed = json.loads(checkpoint.read_bytes())
    changed["execution_authorized"] = True
    checkpoint.write_bytes(canonical_bytes(changed))
    with pytest.raises(ValueError, match="checkpoint_manifest"):
        check_checkpoint(repo, inputs, output, **arguments)
    checkpoint.write_bytes(canonical_bytes(manifest))
    (output / "declaration-v1.json").unlink()
    with pytest.raises(ValueError, match="checkpoint_members"):
        check_checkpoint(repo, inputs, output, **arguments)
