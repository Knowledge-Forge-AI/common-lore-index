"""Continuously join a live runner, its host receipt and exact outer SDK."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import json
import time

from retrieval_semantic_bridge_ownership import RunnerIdentity, verify_outer_inspection
from retrieval_supplement_model_host import _bounded_docker
from retrieval_supplement_runner_process import RunnerProcess
from retrieval_supplement_runner_input import bounded_input
from retrieval_supplement_study_contracts import SDK_IMAGE_SHA256, STUDY_SELECTOR, refuse

_MARKER = ("from pathlib import Path;import hashlib,re;"
    "v=Path('/run/repomap-test-sandbox-owner').read_bytes().strip();"
    "assert re.fullmatch(b'[0-9a-f]{64}',v);print('sha256:'+hashlib.sha256(v).hexdigest())")


@dataclass(repr=False)
class RunnerLease:
    runner: RunnerProcess
    fresh_source_guard: Callable[[], None]
    timeout: Callable[[], float]
    charge_bytes: Callable[[int], None]
    charge_operation: Callable[[str], None]
    charge_transfer: Callable[[int], None] | None = None
    custody_source_guard: Callable[[], None] | None = None
    observed_cid: str | None = field(default=None, init=False)

    def _docker(self, command: list[str], maximum: int, *, deadline: float | None = None) -> bytes:
        self.runner.require_live()
        remaining = self.timeout()
        if deadline is not None:
            remaining = min(remaining, deadline - time.monotonic())
        refuse(type(remaining) in (float, int) and remaining > 0, "runner_lease:deadline")
        # Reserve both bounded output streams and one terminating read chunk
        # before execution. Retain the reservation even on timeout/refusal.
        reserved = 2 * (maximum + 1) + 65536
        self.charge_bytes(reserved)
        def observed(count: int) -> None:
            refuse(type(count) is int and 0 <= count <= reserved, "runner_lease:read_reservation")
        result = _bounded_docker(tuple(command), timeout=min(30, remaining), limit=maximum,
            charge_operation=self.charge_operation, charge_bytes=observed)
        refuse(result.returncode == 0 and len(result.stdout) <= maximum
               and len(result.stderr) <= maximum, "runner_lease:inspection_failed")
        self.runner.require_live()
        return result.stdout

    def _docker_input(self, command: list[str], payload: bytes, maximum: int,
                      *, deadline: float | None = None) -> bytes:
        self.runner.require_live()
        refuse(self.observed_cid is not None and command[:4] == ["docker", "exec", "-i", self.observed_cid],
               "runner_lease:input_scope")
        refuse(self.charge_transfer is not None, "runner_lease:input_meter_required")
        remaining = self.timeout()
        if deadline is not None:
            remaining = min(remaining, deadline - time.monotonic())
        refuse(remaining > 0, "runner_lease:deadline")
        assert self.charge_transfer is not None
        self.charge_transfer(len(payload))
        self.charge_bytes(2 * (maximum + 1) + 65536)
        self.charge_operation("docker.exec")
        result = bounded_input(tuple(command), payload, timeout=min(30., remaining), limit=maximum)
        refuse(result.returncode == 0, "runner_lease:input_failed")
        self.runner.require_live()
        return result.stdout

    def require_outer(self) -> str:
        self.fresh_source_guard()
        self.runner.require_live()
        channel = self.runner.config.channel
        receipt = channel.poll()
        refuse(receipt is not None and channel.control is not None, "runner_lease:receipt_pending")
        assert receipt is not None and channel.control is not None
        refuse(all(receipt[key] == value for key, value in channel.control.items() if key != "schema"),
               "runner_lease:control_join")
        refuse(receipt["pid"] == self.runner.process.pid and receipt["birth"] == self.runner.birth
               and receipt["source_root"] == str(self.runner.config.source_root.resolve())
               and receipt["sdk_image_id"] == SDK_IMAGE_SHA256, "runner_lease:identity_join")
        cid = receipt["cid"]
        refuse(self.observed_cid in (None, cid), "runner_lease:cid_changed")
        raw = self._docker(["docker", "inspect", cid], 256 * 1024)
        inspection, = json.loads(raw)
        identity = RunnerIdentity(self.runner.process.pid, self.runner.birth,
            self.runner.config.source_root, SDK_IMAGE_SHA256, (STUDY_SELECTOR,))
        verify_outer_inspection(identity, cid, inspection)
        marker = self._docker(["docker", "exec", cid, "python3", "-c", _MARKER], 128)
        refuse(marker == (receipt["marker_sha256"] + "\n").encode(), "runner_lease:marker_join")
        self.runner.require_live()
        # Recheck custody after inspection without repeating the byte proof.
        # Legacy callers retain the strict full recheck unless they supply it.
        self.observed_cid = cid
        (self.custody_source_guard or self.fresh_source_guard)()
        return cid

    def receipt_commitment(self) -> str:
        refuse(self.observed_cid is not None, "runner_lease:not_verified")
        return "sha256:" + hashlib.sha256(bytes(self.runner.config.channel.raw)).hexdigest()
