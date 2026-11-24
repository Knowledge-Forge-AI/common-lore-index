"""BODY-only bridge from a sealed plan to the finite byte forecast."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import stat
from typing import Any, cast

from retrieval_body_study_contracts import (INNER_SOURCE_READ_LIMIT, MAX_SOURCE_CLOSURE_BYTES,
                                            REGISTERED_SNAPSHOT_READ_BYTES, refuse)
from retrieval_body_study_inner_guard_reads import closure_guard_read_bound
from retrieval_body_study_schedule import BODY_GIT_STATE_SNAPSHOTS, build_byte_forecast

_BODY_BUDGET_FIELDS = frozenset({
    "candidate_bytes", "guard_regular_file_bytes", "guard_file_visits",
    "guard_reserved_bytes_per_read", "inner_guard_limit", "inner_whole_read_limit",
    "registered_input_bytes", "registered_inner_snapshot_reads", "registered_snapshot_count",
    "registered_snapshot_bytes", "git_custody_bytes", "git_state_snapshots",
    "wrapper_bytes", "python_bytes", "plan_overhead_bytes",
})
_FIXED_GUARD_LIMIT = 512
_FIXED_WHOLE_READ_LIMIT = INNER_SOURCE_READ_LIMIT
_FIXED_REGISTERED_SNAPSHOTS = 201


def _binding(plan: Mapping[str, Any]) -> Mapping[str, Any]:
    derived = plan.get("derived")
    value = derived.get("byte_forecast_binding") if isinstance(derived, Mapping) else None
    refuse(isinstance(value, Mapping) and set(value) == {
        "source_bytes", "treatment_bytes", "worker_bytes", "registered_input_bytes",
        "request_bytes", "candidate_bytes",
    }, "body budget six-field binding")
    value = cast(Mapping[str, Any], value)
    for key in ("source_bytes", "treatment_bytes", "worker_bytes", "registered_input_bytes", "candidate_bytes"):
        refuse(type(value[key]) is int and value[key] >= 0, "body budget binding integer")
    refuse(type(value["registered_input_bytes"]) is int
           and value["registered_input_bytes"] >= REGISTERED_SNAPSHOT_READ_BYTES,
           "body budget registered minimum")
    refuse(isinstance(value["request_bytes"], list) and len(value["request_bytes"]) == 2
           and all(type(item) is int and item >= 0 for item in value["request_bytes"]),
           "body budget request binding")
    return value


def _candidate_bytes(plan: Mapping[str, Any], source_root: Path) -> int:
    snapshot = plan.get("candidate_snapshot")
    refuse(isinstance(snapshot, Mapping), "body budget candidate snapshot")
    snapshot = cast(Mapping[str, Any], snapshot)
    total = 0
    for name, row in snapshot.items():
        refuse(type(name) is str and name and "\\" not in name and not name.startswith("/"),
               "body budget candidate path")
        if isinstance(row, Mapping) and "symlink" in row:
            continue
        refuse(isinstance(row, Mapping) and set(row) == {"sha256", "mode"},
               "body budget candidate row")
        path = source_root.joinpath(*name.split("/"))
        for ancestor in path.parents:
            if ancestor == source_root:
                break
            refuse(not ancestor.is_symlink(), "body budget candidate parent alias")
        info = path.lstat()
        refuse(stat.S_ISREG(info.st_mode) and not path.is_symlink() and info.st_nlink == 1,
               "body budget candidate custody")
        total += info.st_size
    refuse(0 < total <= 128 * 1024**2, "body budget candidate bytes")
    return total


def _body_budget(plan: Mapping[str, Any]) -> Mapping[str, Any]:
    derived = plan.get("derived")
    refuse(isinstance(derived, Mapping), "body budget derived")
    derived = cast(Mapping[str, Any], derived)
    value = derived.get("body_budget")
    refuse(isinstance(value, Mapping) and set(value) == _BODY_BUDGET_FIELDS,
           "body budget fields")
    value = cast(Mapping[str, Any], value)
    for key in _BODY_BUDGET_FIELDS:
        refuse(type(value[key]) is int and value[key] >= 0, "body budget integer")
    refuse(value["inner_guard_limit"] == _FIXED_GUARD_LIMIT
           and value["inner_whole_read_limit"] == _FIXED_WHOLE_READ_LIMIT
           and value["registered_inner_snapshot_reads"] == _FIXED_REGISTERED_SNAPSHOTS
           and value["registered_snapshot_count"] == _FIXED_REGISTERED_SNAPSHOTS
           and value["git_state_snapshots"] == BODY_GIT_STATE_SNAPSHOTS,
           "body budget fixed reads")
    return value


def body_forecast_from_binding(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild and verify the exact forecast sealed in a prepared BODY plan."""
    refuse(plan.get("role") == "body-study", "body budget role")
    binding = _binding(plan)
    budget = _body_budget(plan)
    forecast = build_byte_forecast(
        source_bytes=int(binding["source_bytes"]),
        treatment_bytes=int(binding["treatment_bytes"]),
        worker_bytes=int(binding["worker_bytes"]),
        registered_input_bytes=int(binding["registered_input_bytes"]),
        request_bytes=list(binding["request_bytes"]),
        candidate_bytes=int(binding["candidate_bytes"]),
        guard_regular_file_bytes=int(budget["guard_regular_file_bytes"]),
        guard_file_visits=int(budget["guard_file_visits"]),
        guard_limit=_FIXED_GUARD_LIMIT,
        whole_read_limit=_FIXED_WHOLE_READ_LIMIT,
        git_custody_bytes=int(budget["git_custody_bytes"]),
        git_state_snapshots=int(budget["git_state_snapshots"]),
        wrapper_bytes=int(budget["wrapper_bytes"]),
        python_bytes=int(budget["python_bytes"]),
        plan_overhead_bytes=int(budget["plan_overhead_bytes"]),
    )
    for key in _BODY_BUDGET_FIELDS:
        refuse(forecast.get(key) == budget[key], "body budget forecast drift")
    return forecast


def body_budget_inputs(plan: Mapping[str, Any], source_root: Path) -> dict[str, int | list[int]]:
    """Re-stat the exact current plan population before prepay or forecast."""
    refuse(plan.get("role") == "body-study", "body budget role")
    value = _binding(plan)
    root = source_root
    refuse(isinstance(root, Path) and root.is_absolute() and not root.is_symlink(),
           "body budget source root")
    bound = closure_guard_read_bound(plan, root)
    refuse(bound["regular_file_bytes"] <= 32 * 1024**2, "body budget guard bytes")
    refuse(value["source_bytes"] + value["treatment_bytes"] <= MAX_SOURCE_CLOSURE_BYTES,
           "body budget closure bytes")
    return {
        "source_bytes": int(value["source_bytes"]),
        "treatment_bytes": int(value["treatment_bytes"]),
        "worker_bytes": int(value["worker_bytes"]),
        "registered_input_bytes": int(value["registered_input_bytes"]),
        "request_bytes": list(value["request_bytes"]),
        "candidate_bytes": _candidate_bytes(plan, root),
        "guard_regular_file_bytes": int(bound["regular_file_bytes"]),
        "guard_file_visits": int(bound["file_visits"]),
    }


def build_body_byte_forecast(plan: Mapping[str, Any], source_root: Path) -> dict[str, Any]:
    """Recheck fresh custody measurements, then return the sealed forecast."""
    values = body_budget_inputs(plan, source_root)
    binding = _binding(plan)
    budget = _body_budget(plan)
    refuse(values["candidate_bytes"] == binding["candidate_bytes"]
           and values["candidate_bytes"] == budget["candidate_bytes"],
           "body budget candidate drift")
    refuse(values["guard_regular_file_bytes"] == budget["guard_regular_file_bytes"]
           and values["guard_file_visits"] == budget["guard_file_visits"],
           "body budget guard drift")
    return body_forecast_from_binding(plan)


__all__ = ["body_budget_inputs", "body_forecast_from_binding", "build_body_byte_forecast"]
