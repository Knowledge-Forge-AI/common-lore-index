"""Keep pre-request preparation outside the BODY worker epoch clock."""
from __future__ import annotations

from collections.abc import Callable
import time
from typing import Protocol

from retrieval_body_study_contracts import WORKER_READINESS_SECONDS, refuse
from retrieval_body_study_epoch import PreparationEpoch
from retrieval_body_study_host_transport import publish_epoch_ack
from retrieval_body_study_schedule import build_schedule


class _Lease(Protocol):
    def require_outer(self) -> str: ...


class _Meter(Protocol):
    @property
    def deadline(self) -> float: ...
    def read(self, count: int) -> None: ...
    def operation(self, label: str) -> None: ...


def epoch_frame_window(ordinal: int) -> int:
    refuse(type(ordinal) is int and ordinal in (1, 2), 'body epoch ordinal')
    if ordinal == 2:
        return WORKER_READINESS_SECONDS
    total = 0
    for phase in build_schedule()['sdk_phases']:
        if phase['worker_representation'] == 'canonical-node-v1':
            return total
        total += int(phase['budget_seconds'])
    raise ValueError('body first epoch schedule missing')


def acknowledged_epoch_start(epoch: PreparationEpoch, *, lease: _Lease, meter: _Meter,
        clock: Callable[[], float] = time.monotonic) -> float:
    """Return the clock only after the exact SDK epoch ACK was published."""
    publish_epoch_ack(epoch, require_outer=lease.require_outer,
        charge_bytes=meter.read, charge_operation=meter.operation,
        deadline=min(meter.deadline - 240, clock() + 120))
    return clock()


__all__ = ['acknowledged_epoch_start', 'epoch_frame_window']
