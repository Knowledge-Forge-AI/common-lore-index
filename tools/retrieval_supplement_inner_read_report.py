"""Retain the bounded source-read receipt before the SDK tmpfs disappears."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from retrieval_supplement_files import read_member
from retrieval_supplement_primitives import strict_json


def validate_exported_source_read_receipt(report_root: Path, *, inner_plan: Mapping[str, Any],
        regular_file_bytes: int, charge_bytes: Callable[[int], None]) -> dict[str, Any]:
    """Require the final SDK counter before accepting a successful run."""
    from retrieval_supplement_inner_reads import validate_exported_receipt
    charge_bytes(65536)
    value = strict_json(read_member(report_root, "offline-source-read-budget.json", max_bytes=65536))
    return validate_exported_receipt(inner_plan, value, regular_file_bytes=regular_file_bytes)
