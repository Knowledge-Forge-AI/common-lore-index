"""Continuously join the BODY runner, receipt, SDK, and marker."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
import os
import selectors
import subprocess
import time

from retrieval_body_study_contracts import (
    BODY_OUTER_MARKER_PROGRAM, BODY_SDK_IMAGE_SHA256, BODY_SELECTOR,
    MAX_RESPONSE_STREAM_BYTES, refuse,
)
from retrieval_body_study_host_runner_process import RunnerProcess
from retrieval_body_study_model_host import _bounded_docker
from retrieval_semantic_bridge_ownership import RunnerIdentity, verify_outer_inspection


def _bounded_input(argv: tuple[str, ...], payload: bytes, *, timeout: float,
                   limit: int) -> subprocess.CompletedProcess[bytes]:
    refuse(argv[:3] == ("docker", "exec", "-i") and type(payload) is bytes
           and 0 < len(payload) <= MAX_RESPONSE_STREAM_BYTES + 1024**2
           and timeout > 0 and type(limit) is int and 0 <= limit <= 1024**2,
           "body runner input invocation")
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, close_fds=True)
    assert child.stdin is not None and child.stdout is not None and child.stderr is not None
    selector = selectors.DefaultSelector()
    streams = (child.stdin, child.stdout, child.stderr)
    buffers = {child.stdout.fileno(): bytearray(), child.stderr.fileno(): bytearray()}
    output_fd, error_fd = child.stdout.fileno(), child.stderr.fileno()
    input_fd, sent = child.stdin.fileno(), 0
    by_descriptor = {stream.fileno(): stream for stream in streams}
    deadline = time.monotonic() + timeout
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_WRITE if stream is child.stdin else selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic(); refuse(remaining > 0, "body runner input timeout")
            for key, _mask in selector.select(min(0.1, remaining)):
                fd = key.fd
                if fd == input_fd:
                    try:
                        count = os.write(fd, payload[sent:sent + 65536])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        raise RuntimeError("body runner input closed") from None
                    refuse(count > 0, "body runner input short write"); sent += count
                    if sent == len(payload): selector.unregister(child.stdin); child.stdin.close()
                else:
                    try:
                        raw = os.read(fd, min(65536, limit + 1 - len(buffers[fd])))
                    except BlockingIOError:
                        continue
                    if not raw:
                        selector.unregister(key.fileobj); by_descriptor[fd].close()
                    else:
                        refuse(len(buffers[fd]) + len(raw) <= limit, "body runner input output bounds")
                        buffers[fd].extend(raw)
        refuse(sent == len(payload), "body runner input incomplete")
        child.wait(timeout=max(0.001, deadline - time.monotonic()))
        return subprocess.CompletedProcess(argv, child.returncode,
                                           bytes(buffers[output_fd]), bytes(buffers[error_fd]))
    finally:
        selector.close()
        if child.poll() is None: child.kill()
        try:
            child.wait(timeout=2)
        finally:
            for stream in streams:
                if not stream.closed: stream.close()


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
    _verified_cid: str | None = field(default=None, init=False, repr=False)

    def _scope(self, command: list[str]) -> None:
        refuse(command and command[0] == "docker" and all(isinstance(arg, str) and arg for arg in command),
               "body runner docker invocation")
        refuse(self.observed_cid is not None and self.observed_cid in command,
               "body runner docker scope")

    def _remaining(self, deadline: float | None) -> float:
        remaining = self.timeout()
        if deadline is not None: remaining = min(remaining, deadline - time.monotonic())
        refuse(type(remaining) in (int, float) and remaining > 0, "body runner lease deadline")
        return remaining

    def _docker(self, command: list[str], maximum: int, *, deadline: float | None = None) -> bytes:
        self.runner.require_live(); self._scope(command); remaining = self._remaining(deadline)
        reserved = 2 * (maximum + 1) + 65536; self.charge_bytes(reserved)
        def observed(count: int) -> None:
            refuse(type(count) is int and 0 <= count <= reserved, "body runner read reservation")
        result = _bounded_docker(tuple(command), timeout=min(30.0, remaining), limit=maximum,
                                 charge_operation=self.charge_operation, charge_bytes=observed)
        refuse(result.returncode == 0 and len(result.stdout) <= maximum and len(result.stderr) <= maximum,
               "body runner docker bounded result")
        self.runner.require_live(); return result.stdout

    def _docker_input(self, command: list[str], payload: bytes, maximum: int,
                      *, deadline: float | None = None) -> bytes:
        self.runner.require_live(); self._scope(command)
        refuse(command[:4] == ["docker", "exec", "-i", self.observed_cid], "body runner input scope")
        refuse(self.charge_transfer is not None, "body runner input meter")
        remaining = self._remaining(deadline); assert self.charge_transfer is not None
        self.charge_transfer(len(payload)); self.charge_bytes(2 * (maximum + 1) + 65536)
        self.charge_operation("docker.exec")
        result = _bounded_input(tuple(command), payload, timeout=min(30.0, remaining), limit=maximum)
        refuse(result.returncode == 0, "body runner input failed")
        self.runner.require_live(); return result.stdout

    def require_outer(self) -> str:
        self._verified_cid = None
        self.fresh_source_guard(); self.runner.require_live()
        channel = self.runner.config.channel; receipt = channel.poll()
        refuse(receipt is not None and channel.control is not None, "body runner receipt pending")
        assert receipt is not None and channel.control is not None
        refuse(all(receipt[key] == value for key, value in channel.control.items()), "body runner control join")
        refuse(receipt["pid"] == self.runner.process.pid and receipt["birth"] == self.runner.birth
               and receipt["source_root"] == str(self.runner.config.source_root.resolve())
               and receipt["sdk_image_id"] == BODY_SDK_IMAGE_SHA256, "body runner identity join")
        cid = receipt["cid"]; refuse(self.observed_cid in (None, cid), "body runner cid changed"); self.observed_cid = cid
        raw = self._docker(["docker", "inspect", cid], 256 * 1024)
        try:
            inspection = json.loads(raw.decode("utf-8"))[0]
            refuse(isinstance(inspection, Mapping), "body runner inspection object")
        except (UnicodeDecodeError, json.JSONDecodeError, IndexError, TypeError):
            raise ValueError("body runner inspection") from None
        verify_outer_inspection(RunnerIdentity(self.runner.process.pid, self.runner.birth,
            self.runner.config.source_root, BODY_SDK_IMAGE_SHA256, (BODY_SELECTOR,)), cid, inspection)
        marker = self._docker(["docker", "exec", cid, "python3", "-c", BODY_OUTER_MARKER_PROGRAM], 128)
        refuse(marker == (receipt["marker_sha256"] + "\n").encode("ascii"), "body runner marker join")
        self.runner.require_live(); (self.custody_source_guard or self.fresh_source_guard)()
        self._verified_cid = cid
        return cid

    def receipt_commitment(self) -> str:
        refuse(self._verified_cid is not None and self.observed_cid == self._verified_cid,
               "body runner receipt not verified")
        return "sha256:" + hashlib.sha256(bytes(self.runner.config.channel.raw)).hexdigest()


__all__ = ["RunnerLease"]
