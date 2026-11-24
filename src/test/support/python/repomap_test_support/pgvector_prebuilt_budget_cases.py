"""Focused durable-allocation fixtures for the offline pgvector owner.

These helpers use only temporary files and a child process they create.  They
never start Docker, PostgreSQL, an SDK daemon, or a project test runner.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any
from unittest.mock import patch

from runner_pgvector_budget import Allocation, CAPS
from runner_pgvector_prebuilt import json_bytes


def allocation(tmp_path: Path) -> Allocation:
    """Create a private, empty allocation ledger for one focused case."""
    path = tmp_path / "allocation.json"
    path.write_bytes(json_bytes({"schema": "cloe-pgvector-allocation-v1",
                                 "caps": CAPS, "used": dict.fromkeys(CAPS, 0)}))
    path.chmod(0o600)
    return Allocation(path)


def _child_module_dir() -> Path:
    import runner_pgvector_budget as module
    return Path(module.__file__).resolve().parent


def killed_child_reservation(tmp_path: Path, key: str = "materializations") -> dict[str, Any]:
    """Kill a child after its persisted charge and return the surviving state."""
    budget = allocation(tmp_path)
    marker = tmp_path / "charge-written"
    code = (
        "import sys,time\nfrom pathlib import Path\n"
        "sys.path.insert(0, sys.argv[3])\n"
        "from runner_pgvector_budget import Allocation\n"
        "path=Path(sys.argv[1]); marker=Path(sys.argv[2])\n"
        "with Allocation(path).session() as meter:\n"
        " meter.charge(sys.argv[4], 1)\n marker.write_text('persisted')\n"
        " marker.chmod(0o600)\n time.sleep(60)\n"
    )
    child = subprocess.Popen([sys.executable, "-B", "-c", code, str(budget.path),
                              str(marker), str(_child_module_dir()), key],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    deadline = time.monotonic() + 10
    try:
        while not marker.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError("allocation child exited before its charge marker")
            time.sleep(0.01)
        if not marker.exists():
            raise AssertionError("allocation child did not publish its charge marker")
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        if child.stdout is not None:
            child.stdout.close()
        if child.stderr is not None:
            child.stderr.close()
    state = json.loads(budget.path.read_bytes())
    if state["used"][key] != 1:
        raise AssertionError("a killed child reopened a spent allocation")
    return state


def stale_pending_state(tmp_path: Path) -> None:
    """A leftover journal is a hard stop and is never deleted by a session."""
    budget = allocation(tmp_path)
    pending = budget.path.with_suffix(".next")
    pending.write_bytes(b'{"ambiguous":true}\n')
    pending.chmod(0o600)
    try:
        with budget.session():
            raise AssertionError("stale allocation journal was accepted")
    except RuntimeError as error:
        if "ambiguous pending" not in str(error):
            raise
    if not pending.exists():
        raise AssertionError("ambiguous allocation journal was removed")


def duplicate_state(tmp_path: Path) -> None:
    """Duplicate JSON members cannot be used to widen or rewrite the ledger."""
    budget = allocation(tmp_path)
    budget.path.write_bytes(b'{"schema":"cloe-pgvector-allocation-v1",'
                            b'"schema":"cloe-pgvector-allocation-v1"}\n')
    try:
        with budget.session():
            raise AssertionError("duplicate allocation state was accepted")
    except RuntimeError as error:
        if "duplicate allocation state member" not in str(error):
            raise


def persistence_failure(tmp_path: Path, failure: str = "replace") -> None:
    """Leave a journal for fsync/rename failures and reject the next owner."""
    import runner_pgvector_budget as module

    budget = allocation(tmp_path)
    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("synthetic allocation persistence failure")

    with patch.object(module.os, "replace" if failure == "replace" else "fsync", side_effect=fail):
        try:
            with budget.session() as meter:
                meter.charge("sdk_core", 1)
        except RuntimeError:
            pass
    pending = budget.path.with_suffix(".next")
    if not pending.exists():
        raise AssertionError("persistence failure discarded its ambiguous journal")
    try:
        with budget.session():
            raise AssertionError("next session ignored a failed persistence journal")
    except RuntimeError as error:
        if "ambiguous pending" not in str(error):
            raise


__all__ = ["allocation", "duplicate_state", "killed_child_reservation",
           "persistence_failure", "stale_pending_state"]
