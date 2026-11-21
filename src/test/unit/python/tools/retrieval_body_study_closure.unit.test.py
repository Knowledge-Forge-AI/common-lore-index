"""Owners for BODY closure domains and the maintained 17-file worker map."""
from __future__ import annotations

import hashlib
import json

import pytest

from retrieval_body_study_closure import (
    BODY_SOURCE_MEMBERS, BODY_TREATMENT_MEMBERS, source_closure_sha256, treatment_sha256,
    worker_closure_sha256,
)
from retrieval_body_study_contracts import BodyStudyRefusal


def _files(count: int) -> dict[str, str]:
    return {f"worker-{index:02d}.py": "sha256:" + f"{index + 1:064x}" for index in range(count)}


def test_worker_identity_is_official_json_closure_domain() -> None:
    files = _files(17)
    expected_raw = json.dumps({"closure": "worker", "files": dict(sorted(files.items()))},
                              ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    assert worker_closure_sha256(files) == "sha256:" + hashlib.sha256(expected_raw).hexdigest()


def test_source_and_treatment_domains_cannot_alias() -> None:
    files = {"tools/body.py": "sha256:" + "a" * 64}
    assert source_closure_sha256(files) != treatment_sha256(files)


def test_worker_count_is_exact() -> None:
    with pytest.raises(BodyStudyRefusal):
        worker_closure_sha256(_files(16))


def test_body_source_closure_owns_all_runner_mechanics() -> None:
    assert all(name in BODY_SOURCE_MEMBERS for name in (
        "tools/retrieval_body_study_host_runner_process.py",
        "tools/retrieval_body_study_host_runner_output.py",
        "tools/retrieval_body_study_host_runner_lease.py",
        "tools/retrieval_body_study_host_runner_settlement.py",
        "tools/retrieval_body_study_inner_guard_reads.py"))
    assert "tools/retrieval_body_study_registered_guard.py" in BODY_TREATMENT_MEMBERS
