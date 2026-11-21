"""BODY bookkeeping must agree with the maintained fresh worker helper."""
from __future__ import annotations

from pathlib import Path

import pytest

from retrieval_body_study_closure import worker_closure_sha256
from retrieval_body_study_closure_paths import BODY_WORKER_SOURCE_FILES
from retrieval_body_study_contracts import BodyStudyRefusal
from retrieval_body_study_worker_closure import fresh_worker_closure, require_worker_closure
from retrieval_semantic_generator_worker_contracts import WORKER_SOURCE_FILES
import retrieval_body_study_worker_closure as subject


def _functional_sources(root: Path) -> None:
    tools = root / "tools"
    tools.mkdir(mode=0o700)
    for name in WORKER_SOURCE_FILES:
        path = tools / name
        path.write_bytes(b"unrelated functional fixture; never executed\n")
        path.chmod(0o600)


def test_body_bookkeeping_names_equal_the_maintained_worker_population() -> None:
    assert BODY_WORKER_SOURCE_FILES == WORKER_SOURCE_FILES
    assert len(BODY_WORKER_SOURCE_FILES) == len(set(BODY_WORKER_SOURCE_FILES)) == 17


def test_fresh_worker_validation_uses_the_official_identity_and_rereads_bytes(tmp_path: Path) -> None:
    _functional_sources(tmp_path)
    first = fresh_worker_closure(tmp_path)
    assert set(first["files"]) == set(WORKER_SOURCE_FILES)
    assert first["member_count"] == 17
    assert first["identity"] == worker_closure_sha256(first["digests"])
    changed = tmp_path / "tools" / WORKER_SOURCE_FILES[0]
    changed.write_bytes(b"different unrelated fixture bytes\n")
    second = fresh_worker_closure(tmp_path)
    assert second["identity"] != first["identity"]
    with pytest.raises(BodyStudyRefusal, match="body worker closure drift"):
        require_worker_closure(tmp_path, first["identity"])


def test_bookkeeping_cannot_silently_shrink_the_actual_worker_population(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _functional_sources(tmp_path)
    monkeypatch.setattr(subject, "BODY_WORKER_SOURCE_FILES", BODY_WORKER_SOURCE_FILES[:-1])
    with pytest.raises(BodyStudyRefusal, match="body worker closure names"):
        fresh_worker_closure(tmp_path)


def test_missing_worker_source_is_refused_by_the_maintained_reader(tmp_path: Path) -> None:
    _functional_sources(tmp_path)
    (tmp_path / "tools" / WORKER_SOURCE_FILES[0]).unlink()
    with pytest.raises(ValueError, match="body worker closure unavailable"):
        fresh_worker_closure(tmp_path)
