"""Bounded versioned cumulative lineage through the allocated third core."""
from __future__ import annotations

import re
from typing import Any, cast

from runner_pgvector_budget import Allocation, CAPS, INITIAL_CAPS, RECOVERY2_CAPS, refuse
from runner_pgvector_prebuilt import digest, json_bytes

PARENT_SCHEMA = "cloe-pgdg-core-recovery-parent-v1"
_SHA = re.compile(r"[0-9a-f]{64}")
_PARENT_KEYS = {"schema", "phase", "previous_allocation_sha256", "previous_plan_sha256",
                "previous_result_sha256", "original_caps", "original_used", "original_records"}


def previous_used(value: Any, *, attempt: int = 1) -> dict[str, int]:
    refuse(type(attempt) is int and attempt in (1, 2), "previous attempt ordinal")
    caps = INITIAL_CAPS if attempt == 1 else RECOVERY2_CAPS
    refuse(isinstance(value, dict) and set(value) == set(caps), "previous counter keys")
    used = cast(dict[str, int], value)
    refuse(all(type(used[k]) is int and 0 <= used[k] <= cap
               for k, cap in caps.items()), "previous counter values")
    refuse(used["materializations"] == 1
           and all(used[k] == attempt for k in ("sdk_core", "base_loads", "runtime_exports"))
           and all(used[k] == 0 for k in ("sdk_native", "native_lifecycles", "native_runtime_loads"))
           and used["read_bytes"] > 0 and used["transfer_bytes"] > 0,
           "previous attempt allocation")
    return used


def attempt_number(plan: dict[str, Any]) -> int:
    number = plan.get("core_attempt", 1)
    refuse(type(number) is int and number in (1, 2, 3), "core attempt ordinal")
    return cast(int, number)


def _parent(parent: Any, *, attempt: int, derived: Any, head: str) -> dict[str, int]:
    refuse(isinstance(parent, dict) and set(parent) == _PARENT_KEYS, "recovery parent shape")
    parent = cast(dict[str, Any], parent)
    expected_caps = INITIAL_CAPS if attempt == 1 else RECOVERY2_CAPS
    refuse(parent["schema"] == PARENT_SCHEMA and parent["phase"] == "CLOE-W5-PGDG1"
           and parent["original_caps"] == expected_caps
           and all(type(parent["original_caps"][k]) is int for k in expected_caps),
           "recovery parent identity")
    used = previous_used(parent["original_used"], attempt=attempt)
    for key in ("previous_allocation_sha256", "previous_plan_sha256", "previous_result_sha256"):
        refuse(_SHA.fullmatch(str(parent[key])) is not None, "previous receipt identity")
    records = parent["original_records"]
    expected = {"derived_archive", "core_export", "core_result"}
    if attempt == 2:
        expected.add("recovery_parent")
    refuse(isinstance(records, dict) and set(records) == expected, "previous records missing")
    records = cast(dict[str, Any], records)
    failed = records["core_result"]
    refuse(isinstance(failed, dict) and set(failed) == {
        "exit", "cleanup_proven", "candidate_head", "candidate_snapshot_sha256"}
        and type(failed["exit"]) is int and failed["exit"] == 1
        and failed["cleanup_proven"] is False and failed["candidate_head"] == head
        and _SHA.fullmatch(str(failed["candidate_snapshot_sha256"])) is not None,
           "previous failure lost")
    exported = records["core_export"]
    refuse(records["derived_archive"] == derived and isinstance(exported, dict)
           and exported.get("validated_ordered_layers") is True
           and exported.get("runtime_config") == derived["runtime_config"],
           "materialization identity changed")
    if attempt == 2:
        first_used = _parent(records["recovery_parent"], attempt=1, derived=derived, head=head)
        refuse(all(used[k] >= first_used[k] for k in used), "nested counters decreased")
    return used


def validate_history(plan: dict[str, Any], used: dict[str, int], records: dict[str, Any]) -> int:
    number = attempt_number(plan)
    if number == 1:
        refuse("recovery_parent" not in records and "recovery_parent_sha256" not in plan,
               "unexpected recovery lineage")
        return number
    parent = records.get("recovery_parent")
    refuse(isinstance(parent, dict)
           and digest(json_bytes(parent)) == plan.get("recovery_parent_sha256"),
           "recovery parent identity")
    previous = _parent(parent, attempt=number - 1, derived=plan["derived"], head=plan["candidate_head"])
    refuse(set(used) == set(CAPS)
           and all(type(used[k]) is int and used[k] >= previous[k] for k in CAPS),
           "recovery counters decreased")
    refuse(records.get("derived_archive") == plan["derived"], "materialization identity changed")
    return number


def require_attempt(meter: Allocation, plan: dict[str, Any]) -> int:
    return validate_history(plan, meter.used, meter.records)
