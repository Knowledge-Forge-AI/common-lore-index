"""Bounded settlement of the one controller-owned canonical runner and SDK."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
import signal
import subprocess
import time

from retrieval_semantic_bridge_ownership import RunnerIdentity, verify_outer_inspection
from retrieval_supplement_model_host import _bounded_docker
from retrieval_supplement_runner_process import RunnerProcess
from retrieval_supplement_study_contracts import SDK_IMAGE_SHA256, STUDY_SELECTOR, refuse


def settle_runner(runner: RunnerProcess, *, cleanup_required: bool,
                  charge_bytes: Callable[[int], None], charge_operation: Callable[[str], None],
                  deadline: float) -> dict[str, object]:
    """Interrupt only this fresh process group; remove only its attested SDK.

    Escalation is part of the attempt's declared cleanup, never a discovery of
    another dispatcher's processes. An unavailable PID/birth join stops signals.
    The canonical runner gets the first opportunity to perform its own cleanup.
    """
    signals: list[str] = []
    if cleanup_required and runner.process.poll() is None:
        for number, name, maximum in ((signal.SIGINT, "SIGINT", 45),
                                       (signal.SIGTERM, "SIGTERM", 15),
                                       (signal.SIGKILL, "SIGKILL", 10)):
            runner.require_live()
            refuse(os.getpgid(runner.process.pid) == runner.process.pid,
                   "runner_settlement:group_identity")
            os.killpg(runner.process.pid, number)
            signals.append(name)
            remaining = deadline - time.monotonic()
            refuse(remaining > 0, "runner_settlement:deadline")
            try:
                runner.process.wait(timeout=min(maximum, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    refuse(runner.process.poll() is not None, "runner_settlement:runner_alive")
    channel = runner.config.channel
    receipt = channel.poll()
    cid = None if receipt is None else receipt["cid"]

    def docker(argv: tuple[str, ...], maximum: int = 256 * 1024):
        remaining = deadline - time.monotonic()
        refuse(remaining > 0, "runner_settlement:deadline")
        return _bounded_docker(argv, min(30., remaining), maximum, charge_operation, charge_bytes)

    if cid is not None:
        result = docker(("docker", "container", "inspect", cid))
        if result.returncode == 0:
            inspection, = json.loads(result.stdout)
            # The receipt was obtained through our one-use host pipe. Recheck
            # all immutable image, source, selector and label ownership fields
            # even if the SDK has already stopped. Running is irrelevant here.
            state = dict(inspection.get("State", {}), Running=True)
            identity = RunnerIdentity(runner.process.pid, runner.birth,
                runner.config.source_root, SDK_IMAGE_SHA256, (STUDY_SELECTOR,))
            verify_outer_inspection(identity, cid, dict(inspection, State=state))
            if inspection.get("State", {}).get("Running") is True:
                stopped = docker(("docker", "stop", "--time", "10", cid), 4096)
                refuse(stopped.returncode == 0, "runner_settlement:sdk_stop")
            removed = docker(("docker", "rm", cid), 4096)
            refuse(removed.returncode == 0, "runner_settlement:sdk_remove")
            result = docker(("docker", "container", "inspect", cid), 4096)
        refuse(result.returncode != 0 and b"No such container" in result.stderr,
               "runner_settlement:sdk_absence_unproven")
    channel.close()
    return {"runner_exited": True, "runner_exit": runner.process.returncode,
        "signals": signals, "attested_sdk_observed": cid is not None,
        "attested_sdk_absent": True if cid is not None else None,
        "cleanup": "proven" if cid is not None else "unknown"}
