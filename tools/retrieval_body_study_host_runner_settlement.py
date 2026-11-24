"""Bounded settlement of the BODY-owned runner and attested SDK."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
import signal
import subprocess
import time
from typing import Any, cast

from retrieval_body_study_contracts import (
    BODY_OUTER_MARKER_PROGRAM, BODY_SDK_IMAGE_SHA256, BODY_SELECTOR, refuse,
)
from retrieval_body_study_host_runner_process import RunnerProcess
from retrieval_body_study_model_host import _bounded_docker
from retrieval_semantic_bridge_ownership import RunnerIdentity, verify_outer_inspection


def _probe_owned_process_group(runner: RunnerProcess, *, group_id_probe: Any = os.getpgid,
                               group_probe: Any = os.killpg) -> bool | None:
    """Probe the launch-bound leader group without sending a signal.

    ``RunnerProcess`` is created only after the runner's identity is captured and
    its launch uses ``start_new_session``; the leader PID is therefore the
    process-group ID owned by this runner.  A vanished leader can still be
    checked by that bound ID.  A different group or an indeterminate OS result
    remains unknown, never an invitation to signal an ambiguous process set.
    """
    process = getattr(runner, "process", None)
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 0:
        return None
    try:
        group_id = group_id_probe(pid)
    except ProcessLookupError:
        # The leader may already have been reaped; its launch-bound group ID is
        # still the leader PID, so probe that ID rather than claiming absence.
        group_id = pid
    except (OSError, TypeError, ValueError):
        return None
    if group_id != pid:
        return None
    try:
        group_probe(group_id, 0)  # signal 0 is an existence check only.
    except ProcessLookupError:
        return False
    except (OSError, TypeError, ValueError):
        return None
    return True


def worker_record(result: Any, *, unknown: bool = False) -> dict[str, Any]:
    cid = getattr(result, "model_container_id", None)
    cleanup_proven = not unknown and bool(getattr(result, "cleanup_proven", False))
    count_known = not unknown and (cid is not None or cleanup_proven)
    count = 1 if count_known and cid is not None else 0 if count_known else None
    return {"worker_count": count, "worker_count_known": count_known,
            "worker_count_upper_bound": count if count_known else 1, "worker_cid": cid,
            "status": getattr(result, "status", "failed"), "code": getattr(result, "code", "worker_failed"),
            "cleanup": "proven" if cleanup_proven and count_known else "unknown",
            "cleanup_proven": cleanup_proven and count_known,
            "primary_code": getattr(result, "primary_code", None),
            "cleanup_code": getattr(result, "cleanup_code", None)}


def merge_cleanup(settlement: Mapping[str, Any], workers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Make worker cleanup part of the attempt proof, never an SDK-only claim."""
    result = dict(settlement)
    records = [dict(item) for item in workers if isinstance(item, Mapping)]
    count = 0
    upper_bound = 0
    count_unknown = False
    worker_states: list[str] = []
    for record in records:
        value = record.get("worker_count")
        known = record.get("worker_count_known") is True
        possible = record.get("worker_count_upper_bound", 1)
        if known and type(value) is int and 0 <= value <= 2:
            count += value
            upper_bound += value
        else:
            count_unknown = True
            upper_bound += possible if type(possible) is int and 0 <= possible <= 2 else 1
        state = record.get("cleanup")
        worker_states.append(state if known and state in {"proven", "unknown"} else "unknown")
    sdk_state = result.get("cleanup") if result.get("cleanup") in {"proven", "unknown"} else "unknown"
    workers_state = "not_started" if not worker_states else (
        "proven" if all(state == "proven" for state in worker_states) else "unknown")
    result.update({"cleanup": "proven" if sdk_state == "proven" and workers_state != "unknown" else "unknown",
                   "sdk_cleanup": sdk_state, "worker_cleanup": workers_state,
                   "model_workers": count, "model_workers_known": not count_unknown,
                   "model_workers_upper_bound": upper_bound, "workers": records})
    return result


def settle_runner(runner: RunnerProcess | None, channel: Any, *,
                  cleanup_required: bool = True, charge_bytes: Any,
                  charge_operation: Any, deadline: float) -> dict[str, Any]:
    """Interrupt only this process group and remove only its attested SDK."""
    if runner is None:
        if channel is not None: channel.close()
        return {"cleanup": "proven" if channel is None else "unknown", "runner_started": False,
                "sdk_container": None, "pg_lifecycle": None, "model_workers": 0}
    signals: list[str] = []
    process = runner.process
    try:
        if cleanup_required and process.poll() is None:
            for number, name, maximum in ((signal.SIGINT, "SIGINT", 45),
                                          (signal.SIGTERM, "SIGTERM", 15),
                                          (signal.SIGKILL, "SIGKILL", 10)):
                runner.require_live()
                refuse(os.getpgid(process.pid) == process.pid, "body runner group identity")
                os.killpg(process.pid, number); signals.append(name)
                remaining = deadline - time.monotonic(); refuse(remaining > 0, "body settlement deadline")
                try:
                    process.wait(timeout=min(maximum, remaining)); break
                except subprocess.TimeoutExpired:
                    continue
        refuse(process.poll() is not None, "body runner still live")
        group_presence = _probe_owned_process_group(runner)
        receipt = channel.poll() if channel is not None else None
        cid = None if receipt is None else receipt.get("cid")
        result = {"cleanup": "unknown", "runner_started": True, "sdk_container": cid,
                  "pg_lifecycle": "unknown", "model_workers": 0, "signals": signals,
                  "runner_exit": process.returncode,
                  "process_group": ("present" if group_presence is True else
                                    "absent" if group_presence is False else "unknown")}
        # Settle the independently attested SDK even if group absence is unknown;
        # the final group probe still prevents an unsupported cleanup claim.
        if cid is None: return result

        def docker(argv: tuple[str, ...], maximum: int = 256 * 1024) -> Any:
            remaining = deadline - time.monotonic(); refuse(remaining > 0, "body settlement deadline")
            reserved = 2 * (maximum + 1) + 65536
            charge_bytes(reserved)
            def observed(count: int) -> None:
                refuse(type(count) is int and 0 <= count <= reserved,
                       "body settlement read reservation")
            return _bounded_docker(argv, timeout=min(30.0, remaining), limit=maximum,
                                   charge_operation=charge_operation, charge_bytes=observed)

        inspected = docker(("docker", "container", "inspect", cid))
        if inspected.returncode != 0:
            refuse(b"No such container" in inspected.stderr, "body sdk absence unproven")
            group_presence = _probe_owned_process_group(runner)
            result.update(cleanup="proven" if group_presence is False else "unknown",
                          pg_lifecycle="sdk-owned", sdk_absent=True,
                          process_group="absent" if group_presence is False else "present" if group_presence is True else "unknown")
            return result
        try:
            document = json.loads(inspected.stdout.decode("utf-8"))[0]
            refuse(isinstance(document, Mapping), "body sdk inspection object")
            state = document.get("State")
            refuse(isinstance(state, Mapping), "body sdk inspection state")
            verify_outer_inspection(RunnerIdentity(process.pid, runner.birth,
                runner.config.source_root, BODY_SDK_IMAGE_SHA256, (BODY_SELECTOR,)), cid,
                {**document, "State": {**state, "Running": True}})
        except (UnicodeDecodeError, json.JSONDecodeError, IndexError, TypeError):
            raise ValueError("body sdk inspection") from None
        if state.get("Running") is True:
            refuse(receipt is not None and "marker_sha256" in receipt, "body marker receipt missing")
            receipt_map = cast(Mapping[str, object], receipt)
            marker_sha256 = cast(str, receipt_map.get("marker_sha256"))
            refuse(type(marker_sha256) is str, "body marker receipt missing")
            marker = docker(("docker", "exec", cid, "python3", "-c", BODY_OUTER_MARKER_PROGRAM), 128)
            refuse(marker.returncode == 0 and marker.stdout == (marker_sha256 + "\n").encode("ascii"),
                   "body sdk marker join")
            stopped = docker(("docker", "stop", "--time", "10", cid), 4096)
            refuse(stopped.returncode == 0, "body sdk stop")
        removed = docker(("docker", "rm", cid), 4096)
        refuse(removed.returncode == 0, "body sdk remove")
        absent = docker(("docker", "container", "inspect", cid), 4096)
        refuse(absent.returncode != 0 and b"No such container" in absent.stderr,
               "body sdk removal unproven")
        group_presence = _probe_owned_process_group(runner)
        result.update(cleanup="proven" if group_presence is False else "unknown",
                      pg_lifecycle="sdk-owned", sdk_absent=True,
                      process_group="absent" if group_presence is False else "present" if group_presence is True else "unknown")
        return result
    finally:
        if channel is not None: channel.close()


__all__ = ["_probe_owned_process_group", "merge_cleanup", "settle_runner", "worker_record"]
