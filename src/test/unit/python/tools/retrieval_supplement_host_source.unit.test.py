"""Real filesystem custody and durable metering without a model or Docker."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from retrieval_supplement_host_meter import HostMeter
from retrieval_supplement_host_source import StudySourceGuard, private_directory, put_private
from runner_pgvector_budget import Allocation, STUDY_CAPS


def meter(root: Path):
    path = root / "allocation.json"
    path.write_text(json.dumps({"schema": "cloe-pgvector-allocation-v1", "caps": STUDY_CAPS,
        "used": dict.fromkeys(STUDY_CAPS, 0), "records": {}}), encoding="utf-8")
    path.chmod(0o600)
    return HostMeter(Allocation(path, STUDY_CAPS))


@pytest.fixture
def source(tmp_path: Path, monkeypatch):
    import retrieval_supplement_host_source as owner
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    names = ("entry.py", "policy.json")
    for name, raw in zip(names, (b"value = 1\n", b'{"public":true}\n')):
        (root / name).write_bytes(raw)
    digest = "sha256:" + "a" * 64
    file_rows: dict[str, dict[str, int | str]] = {
        name: {"bytes": (root / name).stat().st_size,
            "sha256": "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()}
        for name in names
    }
    closure: dict[str, object] = {
        "evaluator_closure_sha256": digest, "members": list(names), "member_count": 2,
        "files": file_rows,
        "total_bytes": sum(int(row["bytes"]) for row in file_rows.values()),
    }
    calls: list[str] = []
    monkeypatch.setattr(owner, "verify_checkout", lambda *args: calls.append("whole-candidate"))
    monkeypatch.setattr(owner, "discover_study_evaluator_closure", lambda *args: closure)
    plan = {"candidate_snapshot": {name: {} for name in names}, "candidate_head": "b" * 40,
        "evaluator_closure_sha256": digest}
    value = StudySourceGuard(root, plan, meter(tmp_path))
    value.freeze()
    return value, root, calls


def test_fresh_bytes_and_whole_tree_metadata_have_distinct_evidence(source):
    value, _, calls = source
    value.metadata_current()
    assert value.byte_verifications == 0
    value.require_current()
    assert value.byte_verifications == 1 and calls == ["whole-candidate"]
    with value.meter.allocation.session() as used:
        assert used.used["read_bytes"] > value.closure["total_bytes"]
    value.verify_whole_candidate()
    assert calls == ["whole-candidate", "whole-candidate"]


@pytest.mark.parametrize("mutation", ["content", "mode", "replace", "symlink", "new_file", "hardlink"])
def test_changed_source_or_directory_fails_before_next_authority_hop(source, mutation):
    value, root, _ = source
    path = root / "entry.py"
    if mutation == "content":
        before = path.stat()
        path.write_bytes(b"value = 2\n")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif mutation == "mode": path.chmod(0o444)
    elif mutation == "replace":
        other = root / "replacement"
        other.write_bytes(path.read_bytes())
        other.replace(path)
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to(root / "policy.json")
    elif mutation == "hardlink": os.link(path, root / "alias")
    else: (root / "new.py").write_bytes(b"new = True\n")
    with pytest.raises(ValueError, match="metadata_changed|directory_changed"):
        value.require_current()
    assert value.byte_verifications == 0


def test_content_hash_refuses_even_when_metadata_observer_is_unavailable(source, monkeypatch):
    value, root, _ = source
    (root / "entry.py").write_bytes(b"value = 2\n")
    monkeypatch.setattr(value, "metadata_current", lambda: None)
    with pytest.raises(ValueError, match="source_changed"):
        value.require_current()


def test_verification_ceiling_does_not_become_infinite_polling(source):
    value, _, _ = source
    value.byte_verifications = 1024
    with pytest.raises(ValueError, match="verification_ceiling"):
        value.require_current()


def test_private_evidence_is_write_once_and_its_reservation_survives_failure(tmp_path):
    value = meter(tmp_path)
    destination = tmp_path / "out"
    destination.mkdir(mode=0o700)
    path = destination / "result.json"
    put_private(path, b'{"complete":false}', meter=value)
    first = path.read_bytes()
    with value.allocation.session() as used:
        initial = used.used["transfer_bytes"]
    with pytest.raises(FileExistsError):
        put_private(path, b"replacement", meter=value)
    assert path.read_bytes() == first and path.stat().st_mode & 0o777 == 0o600
    with value.allocation.session() as used:
        assert used.used["transfer_bytes"] == initial + len(b"replacement")


def test_private_directory_alias_or_public_mode_refuses(tmp_path):
    destination = tmp_path / "out"
    destination.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="directory_custody"):
        private_directory(destination)
    destination.chmod(0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match="directory_path"):
        private_directory(alias)


def test_host_worker_reservations_and_operation_classes_are_finite(tmp_path):
    value = meter(tmp_path)
    value.worker()
    value.worker()
    with pytest.raises(RuntimeError, match="exhausted study_workers"):
        value.worker()
    with value.allocation.session() as used:
        assert used.used["study_workers"] == 2
    value.max_operations = 1
    value.operation("docker.inspect")
    with pytest.raises(ValueError, match="operation_ceiling"):
        value.operation("docker.inspect")
    with pytest.raises(ValueError, match="operation_class"):
        value.operation("docker.build")
