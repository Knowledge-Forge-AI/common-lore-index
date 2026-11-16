"""Unit proofs for the source-read ledger; no checkout verification is run."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from retrieval_supplement_inner_reads import (
    InnerReadRefusal,
    MAX_REGULAR_FILE_BYTES,
    READ_LIMIT,
    counter_path_for_plan,
    read_receipt,
    reserve_inner_source_verification,
    validate_exported_receipt,
)

_HASH = "a" * 64
_HEAD = "b" * 40


def _private_dir(path: Path) -> Path:
    path.mkdir()
    path.chmod(0o700)
    return path


def _fixture(tmp_path: Path, *, role: str = "study") -> tuple[Path, Path, dict[str, Any]]:
    repo = _private_dir(tmp_path / "repo")
    ledger = _private_dir(tmp_path / "ledger")
    source = repo / "source.py"
    source.write_bytes(b"source\n")
    source.chmod(0o644)
    schema = {
        "study": "cloe-supplement-study-inner-plan-v1",
        "proof": "cloe-pgvector-supplement-proof-inner-v1",
    }[role]
    plan: dict[str, Any] = {
        "schema": schema,
        "role": role,
        "candidate_head": _HEAD,
        "candidate_snapshot": {"source.py": {"sha256": _HASH, "mode": 0o644}},
    }
    return repo, ledger, plan


def _reserve(plan: dict[str, Any], repo: Path, ledger: Path) -> dict[str, Any]:
    return reserve_inner_source_verification(plan, repo, ledger, lock_timeout=5.0)


def test_exact_limit_persists_and_thirty_third_read_refuses(tmp_path: Path) -> None:
    repo, ledger, plan = _fixture(tmp_path)
    receipts = [_reserve(plan, repo, ledger) for _ in range(READ_LIMIT)]
    assert [item["consumed_reads"] for item in receipts] == list(range(1, READ_LIMIT + 1))
    current = read_receipt(plan, ledger_root=ledger, receipt=receipts[0])
    assert current["consumed_reads"] == READ_LIMIT and current["remaining_reads"] == 0
    assert current["bookkeeping_only"] is True and current["execution_authorized"] is False
    with pytest.raises(InnerReadRefusal, match="read_budget_exhausted"):
        _reserve(plan, repo, ledger)


def test_concurrent_reservations_are_serialized_to_exact_counts(tmp_path: Path) -> None:
    repo, ledger, plan = _fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_reserve, plan, repo, ledger) for _ in range(READ_LIMIT)]
        receipts = [future.result() for future in futures]
    assert sorted(item["consumed_reads"] for item in receipts) == list(range(1, READ_LIMIT + 1))
    assert read_receipt(plan, ledger_root=ledger)["consumed_reads"] == READ_LIMIT


def test_study_and_proof_inner_schemas_are_distinct_plan_bindings(tmp_path: Path) -> None:
    repo, ledger, study = _fixture(tmp_path, role="study")
    first = _reserve(study, repo, ledger)
    proof = dict(study, role="proof", schema="cloe-pgvector-supplement-proof-inner-plan-v1")
    with pytest.raises(InnerReadRefusal):
        read_receipt(proof, ledger_root=ledger, receipt=first)


def test_stale_plan_receipt_and_aliases_refuse(tmp_path: Path) -> None:
    repo, ledger, plan = _fixture(tmp_path)
    receipt = _reserve(plan, repo, ledger)
    stale = dict(plan, candidate_head="c" * 40)
    with pytest.raises(InnerReadRefusal):
        read_receipt(stale, ledger_root=ledger, receipt=receipt)
    forged = dict(receipt, plan_sha256="sha256:" + "c" * 64)
    with pytest.raises(InnerReadRefusal, match="receipt_plan_mismatch"):
        read_receipt(plan, ledger_root=ledger, receipt=forged)
    aliases = dict(plan)
    aliases.pop("candidate_head")
    aliases["current_head"] = _HEAD
    aliases["source_snapshot"] = aliases.pop("candidate_snapshot")
    with pytest.raises(InnerReadRefusal, match="counter_plan_mismatch"):
        _reserve(aliases, repo, ledger)


@pytest.mark.parametrize("bad_name", ["/absolute.py", "../escape.py", "dir/../x.py", "bad\\name.py"])
def test_bad_source_paths_refuse_before_a_counter_is_created(tmp_path: Path, bad_name: str) -> None:
    repo, ledger, plan = _fixture(tmp_path)
    plan["candidate_snapshot"] = {bad_name: {"sha256": _HASH, "mode": 0o644}}
    with pytest.raises(InnerReadRefusal):
        _reserve(plan, repo, ledger)
    assert not list(ledger.glob("*.json"))


def test_symlink_rows_are_lstat_checked_without_content_reads(tmp_path: Path) -> None:
    repo, ledger, plan = _fixture(tmp_path)
    (repo / "target.py").write_bytes(b"target\n")
    (repo / "link.py").symlink_to("target.py")
    plan["candidate_snapshot"] = {"link.py": {"symlink": "target.py"}}
    assert _reserve(plan, repo, ledger)["regular_file_bytes"] == 0
    mismatch = dict(plan, candidate_snapshot={"link.py": {"symlink": "other.py"}})
    with pytest.raises(InnerReadRefusal, match="source_symlink_drift"):
        _reserve(mismatch, repo, ledger)
    regular = dict(plan, candidate_snapshot={"link.py": {"sha256": _HASH, "mode": 0o644}})
    with pytest.raises(InnerReadRefusal, match="source_regular_required"):
        _reserve(regular, repo, ledger)


def test_root_lock_counter_and_hardlink_custody_are_private(tmp_path: Path) -> None:
    repo, ledger, plan = _fixture(tmp_path)
    ledger.chmod(0o755)
    with pytest.raises(InnerReadRefusal, match="ledger_root_custody"):
        _reserve(plan, repo, ledger)
    ledger.chmod(0o700)
    receipt = _reserve(plan, repo, ledger)
    counter = counter_path_for_plan(plan, ledger)
    counter.chmod(0o644)
    with pytest.raises(InnerReadRefusal, match="private_file_custody"):
        read_receipt(plan, ledger_root=ledger, receipt=receipt)
    counter.chmod(0o600)
    alias = counter.with_name("counter-alias.json")
    os.link(counter, alias)
    alias.chmod(0o600)
    with pytest.raises(InnerReadRefusal, match="private_file_custody"):
        read_receipt(plan, ledger_root=ledger)


def test_pending_journal_is_fail_closed_and_never_consumes(tmp_path: Path) -> None:
    repo, ledger, plan = _fixture(tmp_path)
    counter = counter_path_for_plan(plan, ledger)
    pending = counter.with_name(counter.name + ".next")
    pending.write_bytes(b"pending\n")
    pending.chmod(0o600)
    with pytest.raises(InnerReadRefusal, match="pending_journal"):
        _reserve(plan, repo, ledger)
    pending.unlink()
    receipt = _reserve(plan, repo, ledger)
    pending.write_bytes(b"pending\n")
    pending.chmod(0o600)
    with pytest.raises(InnerReadRefusal, match="pending_journal"):
        read_receipt(plan, ledger_root=ledger, receipt=receipt)


def test_source_growth_is_bounded_and_does_not_refund_a_spent_read(tmp_path: Path) -> None:
    repo, ledger, plan = _fixture(tmp_path)
    source = repo / "source.py"
    source.write_bytes(b"x")
    receipt = _reserve(plan, repo, ledger)
    source.write_bytes(b"xx")
    with pytest.raises(InnerReadRefusal, match="source_size_changed"):
        _reserve(plan, repo, ledger)
    assert read_receipt(plan, ledger_root=ledger, receipt=receipt)["consumed_reads"] == 1

    source.write_bytes(b"x")
    with source.open("r+b") as stream:
        stream.truncate(MAX_REGULAR_FILE_BYTES + 1)
    with pytest.raises(InnerReadRefusal, match="source_bytes_cap"):
        _reserve(dict(plan), repo, ledger)


def test_proof_snapshot_commitment_accepts_current_newline_digest(tmp_path: Path) -> None:
    repo, ledger, plan = _fixture(tmp_path, role="proof")
    snapshot = plan["candidate_snapshot"]
    raw = json.dumps(snapshot, ensure_ascii=True, sort_keys=True,
                     separators=(",", ":")).encode() + b"\n"
    digest = hashlib.sha256(raw).hexdigest()
    plan["candidate_snapshot_sha256"] = digest
    plan["source_freeze_sha256"] = digest
    assert _reserve(plan, repo, ledger)["role"] == "proof"


def test_non_path_object_is_not_accepted_as_a_repo_root(tmp_path: Path) -> None:
    repo, ledger, plan = _fixture(tmp_path)
    with pytest.raises(InnerReadRefusal, match="repo_root_path"):
        reserve_inner_source_verification(plan, cast(Path, str(repo)), ledger)


def test_exported_counter_requires_exact_projected_plan_and_final_counts(tmp_path: Path) -> None:
    repo, ledger, plan = _fixture(tmp_path)
    receipt = _reserve(plan, repo, ledger)
    assert validate_exported_receipt(plan, receipt, regular_file_bytes=7) == receipt
    for key, value in (("plan_sha256", "sha256:" + "c" * 64), ("consumed_reads", 33),
                       ("remaining_reads", 0), ("execution_authorized", True)):
        with pytest.raises(InnerReadRefusal):
            validate_exported_receipt(plan, dict(receipt, **{key: value}), regular_file_bytes=7)
    with pytest.raises(InnerReadRefusal, match="source_size_changed"):
        validate_exported_receipt(plan, receipt, regular_file_bytes=8)
