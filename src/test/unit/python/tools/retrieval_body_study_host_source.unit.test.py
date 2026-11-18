"""Owners for BODY source metadata/byte drift and no-memoization boundaries."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from retrieval_body_study_contracts import BodyStudyRefusal
from retrieval_body_study_host_source import BodySourceGuard, query_manifest, query_manifest_sha256
from retrieval_body_study_host_meter import HostMeter


def test_member_read_reserves_bytes_before_opening(tmp_path: Path, monkeypatch) -> None:
    import retrieval_body_study_host_source as source
    path = tmp_path / "public.py"
    path.write_bytes(b"public\n")
    charges = []

    class Meter:
        def read(self, count):
            charges.append(count)

    original = source.read_source_member

    def checked(root, name, *, maximum):
        assert charges == [8] and maximum == 7
        return original(root, name, maximum=maximum)

    monkeypatch.setattr(source, "read_source_member", checked)
    guard = BodySourceGuard(tmp_path, {}, cast(HostMeter, Meter()))
    assert guard.read_member("public.py") == b"public\n"


def test_source_guard_requires_freeze_before_current(tmp_path: Path) -> None:
    guard = BodySourceGuard(tmp_path, {}, cast(HostMeter, object()))
    with pytest.raises(BodyStudyRefusal):
        guard.metadata_current()


def test_source_guard_has_finite_reverification_limit() -> None:
    assert "byte_verifications" in BodySourceGuard.__dataclass_fields__
    assert "require_current" in BodySourceGuard.__dict__


def test_source_guard_rejects_frozen_member_metadata_drift(tmp_path: Path) -> None:
    member = tmp_path / "member.py"
    member.write_bytes(b"body")
    class Meter:
        def remaining(self) -> float:
            return 1.0

    guard = BodySourceGuard(tmp_path, {}, cast(HostMeter, Meter()))
    guard.fingerprints["member.py"] = guard._fingerprint("member.py")
    guard.directories[tmp_path] = (tmp_path.stat().st_dev, tmp_path.stat().st_ino,
                                   tmp_path.stat().st_mode, tmp_path.stat().st_mtime_ns,
                                   tmp_path.stat().st_ctime_ns)
    member.write_bytes(b"changed")
    with pytest.raises(BodyStudyRefusal):
        guard.metadata_current()


def test_query_manifest_binds_question_and_case_identity() -> None:
    def inputs(replacement: str) -> SimpleNamespace:
        kinds = ("metadata_anchor",) * 4 + ("body_positive",) * 12 + ("absent",) * 2 + (
            ("ambiguous",) * 2 + ("privacy",) * 2 + ("unsupported",) * 2)
        cases = [SimpleNamespace(
            task=SimpleNamespace(task_id=f"task-{index:02d}",
                                 question=replacement if index == 1 else f"q-{index}"),
            digest="sha256:" + f"{index:064x}",
            kind=kinds[index - 1]) for index in range(1, 25)]
        return SimpleNamespace(catalog=SimpleNamespace(cases=tuple(cases)))

    manifest = query_manifest(inputs("original"))
    assert len(manifest["canonical-node-v1"]) == 4
    assert len(manifest["canonical-node-public-text-v2"]) == 20
    assert query_manifest_sha256(inputs("original")) != query_manifest_sha256(inputs("replacement"))
