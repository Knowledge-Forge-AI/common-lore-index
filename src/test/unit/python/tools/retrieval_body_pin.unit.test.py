"""Functional custody and registered-entrypoint contracts for body input pins."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, cast

import pytest

from repomap_test_support.retrieval_body_pin_fixtures import (
    make_body_identity,
    make_body_input_files,
    make_body_namespace_payload,
    make_body_policy,
    make_body_predecessor_files,
    materialize_body_inputs,
    materialize_body_predecessors,
)
from retrieval_body_input_metadata import validate_namespace, validate_preauthoring_gate
from retrieval_body_pin import (
    FunctionalBodyInputs,
    RegisteredBodyInputs,
    load_registered_body_inputs,
    require_registered_body_inputs,
    validate_functional_body_inputs,
)
from retrieval_body_predecessors import (
    MAX_MEMBER_BYTES,
    task_id_tokens,
    verify_predecessor_manifest,
)
from retrieval_body_values import BodyError, canonical_bytes, digest_bytes


def _functional(tmp_path: Path) -> tuple[dict[str, bytes], FunctionalBodyInputs, Path]:
    files = make_body_input_files()
    predecessor_root = tmp_path / "predecessors"
    materialize_body_predecessors(predecessor_root)
    result = validate_functional_body_inputs(
        files, identity=make_body_identity(), policy=make_body_policy(), predecessor_root=predecessor_root,
        expected_pin_sha256=digest_bytes(files["input-pin-v1.json"]),
    )
    return files, result, predecessor_root


def test_functional_full_verification_is_source_only_and_defensive(tmp_path: Path) -> None:
    files, result, _predecessor_root = _functional(tmp_path)
    assert result.pin_sha256 == digest_bytes(files["input-pin-v1.json"])
    assert result.catalog.identity == make_body_identity()
    assert result.catalog.cases[0].task.task_id == "unit-body-01"
    assert result.corpus.denied_binding_ids
    bindings = result.corpus.bindings
    bindings.clear()
    assert result.corpus.bindings
    plan = cast(dict[str, Any], result.catalog.cases[0].task.permitted_query_plan)
    plan["query"] = "changed"
    assert result.catalog.cases[0].task.permitted_query_plan["query"] != "changed"


def test_functional_objects_are_refused_by_registered_entrypoints(tmp_path: Path) -> None:
    _files, result, _predecessor_root = _functional(tmp_path)
    with pytest.raises(BodyError, match="registered_factory_required"):
        require_registered_body_inputs(result)
    with pytest.raises(BodyError, match="factory_required"):
        RegisteredBodyInputs(Path("/tmp/body"), Path("/tmp/predecessor"), b"pin")


def test_registered_loader_rejects_functional_pin_before_catalog_decode(tmp_path: Path) -> None:
    files = make_body_input_files()
    root = tmp_path / "body"
    predecessor_root = tmp_path / "predecessors"
    materialize_body_inputs(root, files)
    materialize_body_predecessors(predecessor_root)
    with pytest.raises(BodyError, match="member_digest_mismatch"):
        load_registered_body_inputs(root, predecessor_root=predecessor_root)


@pytest.mark.parametrize("field", ["phase", "reviewed_design_sha256", "clarified_design_sha256",
                                    "review_result_sha256", "parent_bridge_sha256"])
def test_gate_rejects_metadata_drift(field: str) -> None:
    from repomap_test_support.retrieval_body_pin_fixtures import make_body_gate_payload

    gate = make_body_gate_payload()
    gate[field] = "not-a-raw-sha" if field != "phase" else "wrong-phase"
    with pytest.raises(BodyError):
        validate_preauthoring_gate(canonical_bytes(gate), identity=make_body_identity())


def test_gate_rejects_boolean_coercion() -> None:
    from repomap_test_support.retrieval_body_pin_fixtures import make_body_gate_payload

    gate = make_body_gate_payload()
    gate["independent_pre_authoring_pass"] = 1
    with pytest.raises(BodyError):
        validate_preauthoring_gate(canonical_bytes(gate), identity=make_body_identity())


def test_namespace_alias_and_task_set_are_explicit(tmp_path: Path) -> None:
    files, _result, predecessor_root = _functional(tmp_path)
    predecessor = verify_predecessor_manifest(
        files["predecessor-manifest.json"], predecessor_root, identity=make_body_identity(),
    )
    namespace = make_body_namespace_payload(make_body_predecessor_files())
    validate_namespace(
        canonical_bytes(namespace), identity=make_body_identity(), predecessor=predecessor,
        task_ids=tuple(f"unit-body-{index:02d}" for index in range(1, 25)),
    )
    namespace["identity"]["supplement_id"] = "legacy-supplement"
    with pytest.raises(BodyError, match="legacy_alias"):
        validate_namespace(
            canonical_bytes(namespace), identity=make_body_identity(), predecessor=predecessor,
            task_ids=tuple(f"unit-body-{index:02d}" for index in range(1, 25)),
        )


def test_predecessor_scans_task_id_tokens_only_and_rejects_duplicates(tmp_path: Path) -> None:
    files = make_body_predecessor_files()
    first = next(iter(files))
    files[first] = b'{"tasks":[{"task_id":"cal-01"},{"task_id":"cal-01","question":"never decoded"}]}'
    manifest = {"commit": "b" * 40, "files": [
        {"path": path, "bytes": len(raw), "sha256": digest_bytes(raw)[len("sha256:"):]}
        for path, raw in sorted(files.items())
    ]}
    root = tmp_path / "predecessors"
    materialize_body_predecessors(root, files)
    with pytest.raises(BodyError, match="duplicate_task"):
        verify_predecessor_manifest(canonical_bytes(manifest), root, identity=make_body_identity())
    assert task_id_tokens(b'{"tasks":[{"task_id":"cal-\\u0030\\u0031"}]}') == ("cal-01",)


def test_predecessor_exact_hashes_limits_and_namespace(tmp_path: Path) -> None:
    files = make_body_predecessor_files()
    manifest = {"commit": "c" * 40, "files": [
        {"path": path, "bytes": len(raw), "sha256": digest_bytes(raw)[len("sha256:"):]}
        for path, raw in sorted(files.items())
    ]}
    root = tmp_path / "predecessors"
    materialize_body_predecessors(root, files)
    snapshot = verify_predecessor_manifest(canonical_bytes(manifest), root, identity=make_body_identity())
    assert snapshot.catalog_counts == {path: 3 for path in sorted(files)}
    changed = dict(files)
    key = next(iter(changed))
    changed[key] = changed[key][:-1] + b"X"
    materialize_body_predecessors(tmp_path / "changed", changed)
    with pytest.raises(BodyError, match="file_size|digest"):
        verify_predecessor_manifest(canonical_bytes(manifest), tmp_path / "changed", identity=make_body_identity())


def test_predecessor_rejects_linked_declared_member(tmp_path: Path) -> None:
    files = make_body_predecessor_files()
    manifest = {"commit": "d" * 40, "files": [
        {"path": path, "bytes": len(raw), "sha256": digest_bytes(raw)[len("sha256:"):]}
        for path, raw in sorted(files.items())
    ]}
    root = tmp_path / "linked"
    materialize_body_predecessors(root, files)
    linked = root / "src/test/fixtures/retrieval_evaluation/context-v1/task_catalog.json"
    linked.unlink()
    os.symlink(root / "src/test/fixtures/retrieval_evaluation/mixed-v1/tasks/catalog-v1.json", linked)
    with pytest.raises(BodyError, match="member_not_regular"):
        verify_predecessor_manifest(canonical_bytes(manifest), root, identity=make_body_identity())


def test_predecessor_member_and_aggregate_limits_refuse_before_read(tmp_path: Path) -> None:
    too_large = {"commit": "e" * 40, "files": [{
        "path": "context-v1/task_catalog.json", "bytes": MAX_MEMBER_BYTES + 1, "sha256": "0" * 64,
    }]}
    with pytest.raises(BodyError, match="file.bytes"):
        verify_predecessor_manifest(canonical_bytes(too_large), tmp_path, identity=make_body_identity())
    aggregate = {"commit": "f" * 40, "files": [
        {"path": f"context-v1/catalog-{index}.json", "bytes": MAX_MEMBER_BYTES, "sha256": "0" * 64}
        for index in range(5)
    ]}
    with pytest.raises(BodyError, match="aggregate_limit"):
        verify_predecessor_manifest(canonical_bytes(aggregate), tmp_path, identity=make_body_identity())


def test_current_member_pin_and_policy_drift_refuse(tmp_path: Path) -> None:
    files, _result, predecessor_root = _functional(tmp_path)
    changed = dict(files)
    changed["policy-v1.json"] = changed["policy-v1.json"].replace(b"public-dev", b"public-qat", 1)
    with pytest.raises(BodyError, match="member_digest"):
        validate_functional_body_inputs(
            changed, identity=make_body_identity(), policy=make_body_policy(), predecessor_root=predecessor_root,
            expected_pin_sha256=digest_bytes(files["input-pin-v1.json"]),
        )


def test_duplicate_and_nonfinite_metadata_json_is_rejected() -> None:
    identity = make_body_identity()
    duplicate = (
        b'{"phase":"x","phase":"y","reviewed_design_sha256":"' + b"a" * 64
        + b'","clarified_design_sha256":"' + b"b" * 64 + b'","review_result_sha256":"'
        + b"c" * 64 + b'","parent_bridge_sha256":"' + b"d" * 64
        + b'","independent_pre_authoring_pass":true,"authoring_after_gate":true}'
    )
    with pytest.raises(BodyError):
        validate_preauthoring_gate(duplicate, identity=identity)
    nonfinite = (
        b'{"phase":"x","reviewed_design_sha256":"' + b"a" * 64
        + b'","clarified_design_sha256":"' + b"b" * 64 + b'","review_result_sha256":"'
        + b"c" * 64 + b'","parent_bridge_sha256":NaN,"independent_pre_authoring_pass":true,'
        + b'"authoring_after_gate":true}'
    )
    with pytest.raises(BodyError):
        validate_preauthoring_gate(nonfinite, identity=identity)


def test_extra_input_member_is_not_part_of_functional_mapping(tmp_path: Path) -> None:
    files, _result, predecessor_root = _functional(tmp_path)
    extra = dict(files)
    extra["extra.json"] = b"{}"
    with pytest.raises(BodyError, match="input_membership"):
        validate_functional_body_inputs(
            extra, identity=make_body_identity(), policy=make_body_policy(), predecessor_root=predecessor_root,
        )


def test_factory_issued_check_is_exact_type() -> None:
    assert not RegisteredBodyInputs.is_factory_issued(object())
    assert not RegisteredBodyInputs.is_factory_issued(copy.copy(object()))
