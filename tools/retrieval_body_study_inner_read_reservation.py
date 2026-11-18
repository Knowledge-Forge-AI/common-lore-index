"""Prepay BODY whole-candidate and closure-guard reads before SDK creation."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path
import stat
from typing import Any, Protocol, cast

from retrieval_body_study_contracts import MAX_PLAN_BYTES, refuse
from retrieval_body_study_evidence_budget import INNER_EPOCH_PREPARATION_BYTES
from retrieval_body_study_inner_guard_reads import GUARD_LIMIT, closure_guard_read_bound
from retrieval_body_study_inner_reads import READ_LIMIT
from retrieval_body_study_plan import validate_body_plan
from retrieval_body_study_schedule import BODY_INNER_REGISTERED_SNAPSHOTS


class _Session(Protocol):
    @property
    def used(self) -> Mapping[str, int]: ...

    @property
    def records(self) -> Mapping[str, Any]: ...

    def charge(self, key: str, amount: int) -> None: ...


class _Allocation(Protocol):
    def session(self) -> AbstractContextManager[_Session]: ...


def _candidate_bytes(plan: Mapping[str, Any], root: Path) -> int:
    refuse(root.is_absolute() and root.resolve() == root, "body inner candidate root")
    snapshot = plan["candidate_snapshot"]
    total = 0
    for name, row in snapshot.items():
        if isinstance(row, Mapping) and "symlink" in row:
            continue
        refuse(type(name) is str and not name.startswith("/") and "\\" not in name,
               "body inner candidate path")
        path = root.joinpath(*name.split("/"))
        for ancestor in path.parents:
            if ancestor == root:
                break
            refuse(not ancestor.is_symlink(), "body inner candidate parent alias")
        info = path.lstat()
        refuse(stat.S_ISREG(info.st_mode) and not path.is_symlink()
               and info.st_nlink == 1 and info.st_size >= 0, "body inner candidate custody")
        total += info.st_size
    refuse(0 < total <= 128 * 1024**2, "body inner candidate bytes")
    return total


def prepay_inner_reads(plan: Mapping[str, Any], allocation: _Allocation,
                       source_root: Path, *, whole_read_limit: int = READ_LIMIT) -> dict[str, int]:
    """Charge finite inner ledgers from the admitted host plan before SDK creation.

    The body sandbox calls this while it still owns the host plan.  Inner-plan
    projection happens later, after the image and reservation gates.  The
    caller's fixed/admitted context therefore remains the execution authority;
    this function only charges the already-bound read budget.
    """
    checked = validate_body_plan(plan)
    refuse(type(whole_read_limit) is int and whole_read_limit == READ_LIMIT, "body inner whole-read limit")
    bound = closure_guard_read_bound(checked, source_root)
    expected = checked.get("derived", {}).get("body_budget") if isinstance(checked.get("derived"), Mapping) else None
    refuse(isinstance(expected, Mapping), "body inner budget binding missing")
    expected_binding = cast(Mapping[str, Any], expected)
    binding = checked["derived"]["byte_forecast_binding"]
    refuse(expected_binding.get("guard_regular_file_bytes") == bound["regular_file_bytes"]
           and expected_binding.get("guard_file_visits") == bound["file_visits"]
           and expected_binding.get("guard_reserved_bytes_per_read") == bound["reserved_bytes_per_guard"]
           and expected_binding.get("inner_guard_limit") == GUARD_LIMIT
           and expected_binding.get("inner_whole_read_limit") == whole_read_limit
           and expected_binding.get("registered_inner_snapshot_reads") == BODY_INNER_REGISTERED_SNAPSHOTS
           and expected_binding.get("registered_snapshot_count") == BODY_INNER_REGISTERED_SNAPSHOTS
           and expected_binding.get("registered_snapshot_bytes") == BODY_INNER_REGISTERED_SNAPSHOTS * (int(binding["registered_input_bytes"]) + 1),
           "body inner budget binding drift")
    candidate_bytes = _candidate_bytes(checked, source_root)
    refuse(candidate_bytes == binding["candidate_bytes"], "body inner candidate binding drift")
    registered_bytes = int(binding["registered_input_bytes"])
    refuse(registered_bytes >= 12 * 1024**2, "body inner registered snapshot minimum")
    guard_bytes = GUARD_LIMIT * int(bound["reserved_bytes_per_guard"])
    whole_bytes = whole_read_limit * (candidate_bytes + MAX_PLAN_BYTES)
    registered_snapshot_bytes = BODY_INNER_REGISTERED_SNAPSHOTS * (registered_bytes + 1)
    bookkeeping = 2 * 16 * 1024 + MAX_PLAN_BYTES + INNER_EPOCH_PREPARATION_BYTES
    total = guard_bytes + whole_bytes + registered_snapshot_bytes + bookkeeping
    with allocation.session() as meter:
        refuse(meter.used.get("sdk_body_study", 0) == 0
               and "body_study_result" not in meter.records,
               "body SDK source-read reservation already spent")
        meter.charge("read_bytes", total)
    return {"candidate_bytes": candidate_bytes,
            "guard_regular_file_bytes": int(bound["regular_file_bytes"]),
            "guard_file_visits": int(bound["file_visits"]),
            "reserved_bytes_per_guard": int(bound["reserved_bytes_per_guard"]),
            "closure_guard_reads": GUARD_LIMIT,
            "whole_candidate_reads": whole_read_limit,
            "registered_inner_snapshot_reads": BODY_INNER_REGISTERED_SNAPSHOTS,
            "registered_snapshot_count": BODY_INNER_REGISTERED_SNAPSHOTS,
            "registered_snapshot_bytes": registered_snapshot_bytes,
            "bookkeeping_bytes": bookkeeping,
            "reserved_read_bytes": total}


def reserve_inner_source_reads(plan: Mapping[str, Any], allocation: _Allocation,
                               source_root: Path, *, whole_read_limit: int = READ_LIMIT) -> dict[str, int]:
    """Descriptive alias used by BODY runner adapters."""
    return prepay_inner_reads(plan, allocation, source_root, whole_read_limit=whole_read_limit)


__all__ = ["prepay_inner_reads", "reserve_inner_source_reads"]
