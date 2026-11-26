"""Adapter record algebra, 1-to-1 request matching, evaluator closure and F3 gate."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

import pytest

from common_lore_index_test_support.retrieval_supplement_study_fixtures import (
    FAKE_SHA,
    fake_build_request,
    fake_make_inputs,
    make_preparation,
)
from retrieval_semantic_generator_worker_contracts import ATTEMPT_ID_PATTERN
from retrieval_supplement_study_adapter import build_study_requests, parse_exchanges
from retrieval_supplement_study_closure import (
    ALL_EXPLICIT_STUDY_OWNERS,
    STUDY_RUNNER_HOOK_OWNERS,
    STUDY_SUPPORT_AND_TEST_OWNERS,
    compute_evaluator_closure,
    require_verified_artifact_profile,
    verify_study_artifact_gate,
)
from retrieval_supplement_study_contracts import (
    ARTIFACT_PIN_DOCUMENT_SHA256,
    ARTIFACT_SOURCE_IDENTITY_SHA256,
    ARTIFACT_TOTAL_BYTES,
    WORKER_CLOSURE_SHA256,
    StudyRefusal,
)

COUNTS = ((2, 2), (1, 2))


def _build(preparation):
    return build_study_requests(
        preparation, attempt_id="studytest-a1",
        artifact_profile_identity=ARTIFACT_SOURCE_IDENTITY_SHA256,
        make_inputs=fake_make_inputs, build_request=fake_build_request,
        expected_counts=COUNTS, expected_total=7, expected_slots=18,
    )


def test_adapter_builds_two_query_once_requests_with_valid_attempt_ids() -> None:
    v1, v2 = _build(make_preparation())
    assert len(v1["records"]) == 4 and len(v2["records"]) == 3
    assert ATTEMPT_ID_PATTERN.fullmatch(v1["attempt_id"]) is not None
    assert ATTEMPT_ID_PATTERN.fullmatch(v2["attempt_id"]) is not None
    assert v1["attempt_id"].endswith("-canonical-node-v1")
    assert v2["attempt_id"].endswith("-canonical-node-public-text-v2")


@pytest.mark.parametrize("mutate", [
    lambda p: p["exchanges"].reverse(),
    lambda p: p["exchanges"][0].update(document_count=99),
    lambda p: p["exchanges"][0]["documents"].append(p["exchanges"][0]["documents"][0]),
    lambda p: p["exchanges"][1]["queries"].append(p["exchanges"][0]["queries"][0]),
    lambda p: p.update(execution_authorized=True),
    lambda p: p.update(slot_count=431),
    lambda p: p["exchanges"][0]["documents"].reverse(),
    lambda p: p["exchanges"][0]["queries"].reverse(),
    lambda p: p["references"].append({
        "arm": p["references"][0]["arm"], "task_id": "task-99", "repetition": 1,
        "exchange_id": p["exchanges"][1]["exchange_id"], "query_id": "extra",
    }),
    lambda p: p["references"][0].update(query_id="wrong:query"),
])
def test_adapter_refusals(mutate) -> None:
    preparation = copy.deepcopy(make_preparation())
    mutate(preparation)
    with pytest.raises(StudyRefusal):
        parse_exchanges(preparation, expected_counts=COUNTS, expected_total=7, expected_slots=18)


def test_adapter_refuses_wrong_record_identity_mode_or_text() -> None:
    def swapped_builder(inputs, *, attempt_id, artifact_profile_identity):
        recs = list(reversed(inputs["records"]))
        return {"attempt_id": attempt_id, "records": recs}

    with pytest.raises(StudyRefusal):
        build_study_requests(
            make_preparation(), attempt_id="studytest-a1",
            artifact_profile_identity=ARTIFACT_SOURCE_IDENTITY_SHA256,
            make_inputs=fake_make_inputs, build_request=swapped_builder,
            expected_counts=COUNTS, expected_total=7, expected_slots=18,
        )


@dataclass(frozen=True)
class _File:
    sha256: str = "a" * 64
    size: int = ARTIFACT_TOTAL_BYTES // 30


@dataclass(frozen=True)
class _Profile:
    verified_files: tuple = tuple(
        _File(size=(ARTIFACT_TOTAL_BYTES // 30) + (ARTIFACT_TOTAL_BYTES % 30 if i == 0 else 0))
        for i in range(30)
    )
    wheels: tuple = tuple(range(20))
    model_files: tuple = tuple(range(10))
    source_identity_sha256: str = ARTIFACT_SOURCE_IDENTITY_SHA256
    pinned_profile_sha256: str = ARTIFACT_PIN_DOCUMENT_SHA256[7:]


@pytest.mark.parametrize("profile", [
    _Profile(verified_files=()),
    _Profile(verified_files=tuple(_File() for _ in range(29))),
    _Profile(wheels=tuple(range(19))),
    _Profile(model_files=tuple(range(9))),
    _Profile(verified_files=tuple(_File(size=1) for _ in range(30))),
    _Profile(source_identity_sha256=ARTIFACT_PIN_DOCUMENT_SHA256),
    _Profile(pinned_profile_sha256=ARTIFACT_SOURCE_IDENTITY_SHA256),
    object(),
])
def test_artifact_profile_refusals(profile, tmp_path: Path) -> None:
    with pytest.raises(StudyRefusal):
        require_verified_artifact_profile(profile)
    for bad_gate in ("", ARTIFACT_PIN_DOCUMENT_SHA256, ARTIFACT_SOURCE_IDENTITY_SHA256, WORKER_CLOSURE_SHA256):
        with pytest.raises(StudyRefusal):
            verify_study_artifact_gate(tmp_path, trusted_gate_receipt_sha256=bad_gate)
    with pytest.raises(ValueError):
        verify_study_artifact_gate(tmp_path, trusted_gate_receipt_sha256=FAKE_SHA)


def test_artifact_profile_exact30_accepted() -> None:
    assert require_verified_artifact_profile(_Profile()) == ARTIFACT_SOURCE_IDENTITY_SHA256


def test_closure_includes_all_runner_and_support_hooks_and_refuses_fixtures(tmp_path: Path) -> None:
    assert "tools/runner_pgvector_sandbox.py" in STUDY_RUNNER_HOOK_OWNERS
    assert "src/test/conftest.py" in STUDY_SUPPORT_AND_TEST_OWNERS
    root = tmp_path / "repo"
    sources = {}
    for name in ALL_EXPLICIT_STUDY_OWNERS:
        raw = f"# synthetic {name}\n".encode()
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        sources[name] = raw
    record = compute_evaluator_closure(root, builder_members=[], source_files=sources)
    assert record["member_count"] == len(ALL_EXPLICIT_STUDY_OWNERS)
    drifted = dict(sources)
    drifted[ALL_EXPLICIT_STUDY_OWNERS[0]] = b"# drifted\n"
    with pytest.raises(StudyRefusal):
        compute_evaluator_closure(root, builder_members=[], source_files=drifted)
    with pytest.raises(StudyRefusal):
        compute_evaluator_closure(
            root, builder_members=["fixtures/x.py"],
            source_files={**sources, "fixtures/x.py": b"x"},
        )
