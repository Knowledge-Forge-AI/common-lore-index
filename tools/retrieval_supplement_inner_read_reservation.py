"""Prepay finite inner reads on the host before the new SDK is created."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol

from retrieval_supplement_inner_reads import READ_LIMIT
from offline_qualification_values import refuse, safe_path


class _Session(Protocol):
    @property
    def used(self) -> Mapping[str, int]: ...
    @property
    def records(self) -> Mapping[str, Any]: ...
    def charge(self, key: str, amount: int) -> None: ...


class _Allocation(Protocol):
    def session(self) -> AbstractContextManager[_Session]: ...


def prepay_inner_reads(plan: Mapping[str, Any], allocation: _Allocation, source_root: Path) -> None:
    refuse(plan["role"] in {"study", "proof"}, "new source-read cohort required")
    count = sum((source_root / safe_path(name)).lstat().st_size
                for name, row in plan["candidate_snapshot"].items() if "symlink" not in row)
    refuse(0 < count <= 128 * 1024**2, "new cohort source byte bound")
    reservation = READ_LIMIT * (count + 2 * 1024**2)
    if plan["role"] == "study":
        reservation += 1024**3 + 128 * int(plan["derived"]["byte_forecast_binding"]["source_bytes"])
    with allocation.session() as meter:
        refuse(meter.used.get("sdk_" + plan["role"]) == 0
               and plan["role"] + "_result" not in meter.records, "new SDK source-read reservation already spent")
        meter.charge("read_bytes", reservation)
