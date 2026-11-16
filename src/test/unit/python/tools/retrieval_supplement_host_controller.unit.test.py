"""Filesystem-only proof for the actual host controller's earliest resource gates."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
import hashlib
import os
from pathlib import Path
import sys
from typing import Any, cast

import pytest

import retrieval_supplement_host_controller as controller
from repomap_test_support.retrieval_supplement_study_fixtures import make_study_plan
from retrieval_supplement_host_meter import HostMeter
from retrieval_supplement_study_contracts import StudyRefusal
from retrieval_supplement_study_plan import create_reservation, plan_digest, read_reservation
from retrieval_supplement_values import canonical_bytes, digest_bytes
from runner_pgvector_budget import Allocation, STUDY_CAPS


def test_compatibility_exports_preserve_exact_contract_objects() -> None:
    import retrieval_supplement_primitives as primitives
    import retrieval_supplement_values as legacy
    import offline_qualification_values as values
    import offline_qualification_files as files
    import offline_qualification_checkout as checkout
    import runner_pgvector_prebuilt as prebuilt
    import runner_pgvector_budget as budget
    import runner_pgvector_context as context
    assert legacy.__all__ == primitives.__all__
    assert all(getattr(legacy, name) is getattr(primitives, name) for name in legacy.__all__)
    assert prebuilt.digest is values.digest and prebuilt.read_regular is files.read_regular
    assert budget.private_file is files.private_file and budget.STUDY_CAPS is values.STUDY_CAPS
    assert context.verify_checkout is checkout.verify_checkout


def test_large_plan_and_report_json_limits_are_explicit() -> None:
    from retrieval_supplement_primitives import strict_json, SupplementError
    raw = b"[]" + b" " * (512 * 1024)
    with pytest.raises(SupplementError, match="json_too_large"):
        strict_json(raw)
    assert strict_json(raw, maximum_bytes=2 * 1024**2) == []
    with pytest.raises(SupplementError, match="json_invalid_limit"):
        strict_json(b"[]", maximum_bytes=True)


@dataclass
class _FakeMeter:
    """A real allocation ledger with an observable, optionally failing read hook."""

    allocation: Allocation
    read_calls: list[int] = field(default_factory=list)
    read_callback: Callable[[int], None] | None = None

    def read(self, count: int) -> None:
        self.read_calls.append(count)
        with self.allocation.session() as state:
            state.charge("read_bytes", count)
        if self.read_callback is not None:
            self.read_callback(count)


@dataclass
class _Case:
    config: controller.ActualStudyConfig
    plan: dict[str, Any]
    meter: _FakeMeter
    archive: Path
    reservation: Path
    allocation: Path


def _write_allocation(
    path: Path,
    *,
    used: Mapping[str, int] | None = None,
    records: Mapping[str, Any] | None = None,
) -> Allocation:
    values = {key: 0 for key in STUDY_CAPS}
    values.update({key: int(value) for key, value in (used or {}).items()})
    payload = {
        "schema": "cloe-pgvector-allocation-v1",
        "caps": STUDY_CAPS,
        "used": values,
        "records": dict(records or {}),
    }
    path.write_bytes(canonical_bytes(payload))
    path.chmod(0o600)
    return Allocation(path, STUDY_CAPS)


def _rewrite_private(path: Path, value: Mapping[str, Any]) -> None:
    path.write_bytes(canonical_bytes(dict(value)))
    path.chmod(0o600)


def _case(
    tmp_path: Path,
    *,
    used: Mapping[str, int] | None = None,
    records: Mapping[str, Any] | None = None,
    matching_derived_archive: bool = False,
) -> _Case:
    root = tmp_path / "case"
    root.mkdir(mode=0o700)
    source_root = root / "source"
    source_root.mkdir(mode=0o700)
    attempt_root = root / "attempt"
    attempt_root.mkdir(mode=0o700)
    artifact_root = root / "artifacts"
    artifact_root.mkdir(mode=0o700)
    parent_admission = root / "parent-admission.json"
    parent_raw = b"parent-admission"
    parent_admission.write_bytes(parent_raw)
    parent_admission.chmod(0o600)

    archive = root / "runtime.tar"
    archive.write_bytes(b"x")
    archive.chmod(0o600)
    allocation = root / "allocation.json"
    reservation = root / "reservation.json"
    plan = make_study_plan(root)
    plan["archive"] = {
        "path": str(archive), "bytes": 1, "sha256": hashlib.sha256(b"x").hexdigest(),
    }
    plan["allocation_path"] = str(allocation)
    plan["reservation_path"] = str(reservation)
    allocation_records = dict(records or {})
    if matching_derived_archive:
        allocation_records["derived_archive"] = plan["archive"]
    allocation_value = _write_allocation(allocation, used=used, records=allocation_records)
    create_reservation(
        reservation, plan_sha256=plan_digest(plan), attempt_id=plan["attempt_id"],
    )
    plan_path = root / "plan.json"
    plan_path.write_bytes(canonical_bytes(plan))
    plan_path.chmod(0o600)
    config = controller.ActualStudyConfig(
        source_root=source_root, plan_path=plan_path, allocation_path=allocation,
        attempt_root=attempt_root, artifact_root=artifact_root,
        python=Path(sys.executable), parent_admission=parent_admission,
        parent_admission_sha256=digest_bytes(parent_raw), environment={},
    )
    return _Case(config, plan, _FakeMeter(allocation_value), archive, reservation, allocation)


def _host_meter(meter: _FakeMeter) -> HostMeter:
    return cast(HostMeter, meter)


def _require(case: _Case) -> None:
    controller._require_fresh_resources(case.config, _host_meter(case.meter), case.plan)


def _verify_archive(case: _Case) -> None:
    controller._verify_runtime_archive(case.plan, _host_meter(case.meter))


@pytest.mark.parametrize("matching", [False, True])
def test_fresh_resources_accept_empty_or_matching_derived_archive_record(
    tmp_path: Path, matching: bool,
) -> None:
    case = _case(tmp_path, matching_derived_archive=matching)
    _require(case)
    assert case.meter.read_calls == []


def test_foreign_plan_parent_refuses_before_resource_reuse(tmp_path: Path) -> None:
    case = _case(tmp_path)
    foreign = tmp_path / "foreign-plan"
    foreign.mkdir(mode=0o700)
    with pytest.raises(StudyRefusal, match="allocation_join"):
        _require(replace(case, config=replace(case.config, plan_path=foreign / "plan.json")))


def test_foreign_archive_parent_refuses_private_resource_join(tmp_path: Path) -> None:
    case = _case(tmp_path)
    foreign = tmp_path / "foreign-archive"
    foreign.mkdir(mode=0o700)
    archive = foreign / "runtime.tar"
    archive.write_bytes(b"x")
    archive.chmod(0o600)
    case.plan["archive"]["path"] = str(archive)
    with pytest.raises(StudyRefusal, match="private_resource_join"):
        _require(case)


@pytest.mark.parametrize("resource", ["archive", "reservation", "allocation"])
def test_world_readable_resource_refuses(tmp_path: Path, resource: str) -> None:
    case = _case(tmp_path)
    {"archive": case.archive, "reservation": case.reservation,
     "allocation": case.allocation}[resource].chmod(0o644)
    with pytest.raises(RuntimeError, match="private input custody|file_custody"):
        _require(case)


def test_spent_sdk_counter_refuses_before_any_runtime_use(tmp_path: Path) -> None:
    case = _case(tmp_path, used={"sdk_study": 1})
    with pytest.raises(StudyRefusal, match="allocation_not_fresh"):
        _require(case)


def test_foreign_allocation_record_refuses_even_when_execution_counters_are_zero(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path, records={"core_result": {"status": "done"}})
    with pytest.raises(StudyRefusal, match="allocation_not_fresh"):
        _require(case)


def test_partial_reservation_refuses_before_admission(tmp_path: Path) -> None:
    case = _case(tmp_path)
    record = read_reservation(case.reservation)
    record["runtime_imports"] = 1
    _rewrite_private(case.reservation, record)
    with pytest.raises(StudyRefusal, match="reservation_not_fresh"):
        _require(case)


def test_stale_reservation_plan_refuses_before_admission(tmp_path: Path) -> None:
    case = _case(tmp_path)
    record = read_reservation(case.reservation)
    record["plan_sha256"] = digest_bytes(b"foreign-plan")
    _rewrite_private(case.reservation, record)
    with pytest.raises(StudyRefusal, match="reservation_not_fresh"):
        _require(case)


def test_wrong_archive_hash_is_charged_before_bounded_stream(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case.plan["archive"]["sha256"] = hashlib.sha256(b"foreign-bytes").hexdigest()
    with pytest.raises(StudyRefusal, match="archive_identity"):
        _verify_archive(case)
    assert case.meter.read_calls == [2]
    with case.meter.allocation.session() as state:
        assert state.used["read_bytes"] == 2


def test_archive_size_growth_refuses_before_stream_charge(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case.archive.write_bytes(b"xx")
    with pytest.raises(StudyRefusal, match="archive_size"):
        _verify_archive(case)
    assert case.meter.read_calls == []


def test_archive_read_reservation_precedes_stream_and_spend_survives_failure(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)

    def refuse_stream(_count: int) -> None:
        raise RuntimeError("stream-not-authorized")

    case.meter.read_callback = refuse_stream
    with pytest.raises(RuntimeError, match="stream-not-authorized"):
        _verify_archive(case)
    assert case.meter.read_calls == [2]
    with case.meter.allocation.session() as state:
        assert state.used["read_bytes"] == 2


def test_archive_metadata_change_after_read_is_refused(tmp_path: Path) -> None:
    case = _case(tmp_path)

    def change_mtime(_count: int) -> None:
        info = case.archive.stat()
        os.utime(case.archive, ns=(info.st_atime_ns, info.st_mtime_ns + 1))

    case.meter.read_callback = change_mtime
    with pytest.raises(StudyRefusal, match="archive_changed"):
        _verify_archive(case)


def test_unchanged_archive_passes_with_exact_prepaid_read(tmp_path: Path) -> None:
    case = _case(tmp_path)
    _verify_archive(case)
    assert case.meter.read_calls == [2]
    with case.meter.allocation.session() as state:
        assert state.used["read_bytes"] == 2
