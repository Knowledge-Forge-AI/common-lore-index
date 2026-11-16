"""Bounded incremental control frames from the owned canonical runner's log."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import os
from pathlib import Path
import stat
import time

from retrieval_semantic_bridge_transport import READY_PREFIX
from retrieval_semantic_experiment_bridge import ADMISSION_ACK_PREFIX
from retrieval_supplement_epoch import EPOCH_PREFIX
from retrieval_supplement_runner_process import RunnerProcess
from retrieval_supplement_study_contracts import refuse

MAX_LOG_BYTES = 8 * 1024**2
MAX_LINE_BYTES = 64 * 1024
CONTROL_PREFIXES = tuple(x.encode() for x in (EPOCH_PREFIX, READY_PREFIX, ADMISSION_ACK_PREFIX))


@dataclass(repr=False)
class RunnerOutput:
    runner: RunnerProcess
    charge_bytes: Callable[[int], None]
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    offset: int = field(default=0, init=False)
    pending: bytearray = field(default_factory=bytearray, init=False, repr=False)
    events: list[bytes] = field(default_factory=list, init=False, repr=False)
    _inode: tuple[int, int] | None = field(default=None, init=False)

    def _read(self) -> bytes:
        path = self.runner.config.stdout_path
        refuse(isinstance(path, Path) and path.is_absolute() and not path.is_symlink(),
               "runner_output:path")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            refuse(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                   and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
                   "runner_output:custody")
            identity = info.st_dev, info.st_ino
            refuse(self._inode in (None, identity) and self.offset <= info.st_size <= MAX_LOG_BYTES,
                   "runner_output:changed_or_bounds")
            self._inode = identity
            count = min(info.st_size - self.offset, 65536)
            self.charge_bytes(count)
            raw = os.pread(descriptor, count, self.offset)
            refuse(len(raw) == count, "runner_output:short_read")
            self.offset += len(raw)
            return raw
        finally:
            os.close(descriptor)

    def poll(self) -> None:
        self.pending.extend(self._read())
        while b"\n" in self.pending:
            line, _, remaining = self.pending.partition(b"\n")
            self.pending = bytearray(remaining)
            refuse(len(line) <= MAX_LINE_BYTES, "runner_output:line_bounds")
            if line.startswith(CONTROL_PREFIXES):
                refuse(len(self.events) < 8, "runner_output:excess_control_frames")
                self.events.append(bytes(line))
            # Pytest and canonical diagnostics remain in the private log. A
            # lookalike control token embedded inside them is never authority.
        refuse(len(self.pending) <= MAX_LINE_BYTES, "runner_output:partial_line_bounds")

    def await_frame(self, prefix: str, *, deadline: float,
                    require_current: Callable[[], object]) -> bytes:
        refuse(prefix.encode() in CONTROL_PREFIXES, "runner_output:expected_prefix")
        while self.clock() < deadline:
            self.poll()
            if self.events:
                line = self.events.pop(0)
                refuse(line.startswith(prefix.encode()), "runner_output:control_order")
                require_current()
                return line
            self.runner.require_live()
            self.sleep(min(.1, max(0., deadline - self.clock())))
        raise RuntimeError("runner_output:frame_timeout")

    def finish(self) -> None:
        self.poll()
        refuse(not self.events and not any(self.pending.startswith(p) for p in CONTROL_PREFIXES),
               "runner_output:unconsumed_control_frame")
