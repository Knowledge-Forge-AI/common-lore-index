from __future__ import annotations
import json
import os
from pathlib import Path
import pytest
from common_lore_index_test_support.retrieval_body_study_snapshot_driver import (
    export_snapshot_counter_receipt,
    export_final_snapshot_budget,
    make_pg_byte_charger,
)
from retrieval_body_study_snapshot_counter import (
    SnapshotCounterBinding,
    SnapshotCounterReceipt,
)
def _receipt(root: Path, *, valid: bool = True) -> SnapshotCounterReceipt:
    root.mkdir()
    os.chmod(root, 0o700)
    digest = "sha256:" + "b" * 64
    binding = SnapshotCounterBinding("attempt", digest, digest, digest, digest, root)
    return SnapshotCounterReceipt(
        binding, 201, 201, (("driver:inner:1", 201),), None, 0,
        True, True, True, valid,
    )
def _pending() -> dict[str, int | str]:
    return {
        "source_guard_read_bytes": 10,
        "active_guard_read_bytes": 1,
        "active_guard_read_bytes_kind": "informational_subset",
        "registered_snapshot_count": 200,
        "registered_snapshot_count_reserved": 201,
        "registered_snapshot_count_kind": "measured_interim_reserved_budget",
        "registered_snapshot_file_bytes": 1,
        "registered_snapshot_read_bytes": 201,
        "registered_snapshot_read_bytes_kind": "reserved_upper_bound",
        "source_guard_total_read_bytes": 211,
        "source_guard_total_read_bytes_kind": "source_plus_snapshot_reserved_upper_bound",
        "source_guard_byte_limit": 1000,
    }
def test_final_export_requires_valid_receipt_and_writes_both_members(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path / "counter")
    files: dict[str, bytes] = {}
    def publish(root: Path, name: str, raw: bytes) -> None:
        files[str(root / name)] = raw
    result = export_final_snapshot_budget(tmp_path / "report", _pending(), receipt, publish)
    assert result["registered_snapshot_count"] == 201
    assert result["registered_snapshot_count_kind"] == "measured_final_receipt"
    assert result["source_guard_total_read_bytes"] == 211
    receipt_path = str(tmp_path / "report" / "body-study-snapshot-counter.json")
    budget_path = str(tmp_path / "report" / "body-study-source-read-budget.json")
    assert json.loads(files[receipt_path])["valid"] is True
    budget = json.loads(files[budget_path])
    assert budget["snapshot_counter_receipt_file"] == "body-study-snapshot-counter.json"
    assert budget["registered_snapshot_count"] == 201
def test_invalid_receipt_cannot_export_source_budget(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path / "counter", valid=False)
    with pytest.raises(ValueError, match="final_receipt"):
        export_final_snapshot_budget(tmp_path / "report", _pending(), receipt, lambda *_: None)
def test_invalid_receipt_can_be_published_without_budget_claim(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path / "counter", valid=False)
    files: dict[str, bytes] = {}
    export_snapshot_counter_receipt(
        tmp_path / "report", receipt,
        lambda root, name, raw: files.__setitem__(str(root / name), raw),
    )
    stored = json.loads(files[str(tmp_path / "report" / receipt.binding.receipt_name)])
    assert stored["valid"] is False
    assert "registered_snapshot_count" not in stored
def test_pg_byte_charger_is_finite_and_reports_reserved_bytes() -> None:
    charge, state = make_pg_byte_charger(8)
    charge(4)
    assert state == {"limit": 8, "reserved": 4}
    with pytest.raises(ValueError, match="pg_budget"):
        charge(5)
