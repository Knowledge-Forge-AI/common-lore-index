"""Epoch-anchored nested deadlines for the W5 model sibling host."""

from __future__ import annotations

from collections.abc import Callable
import math
import re


TIMEOUTS = {"initialization": 600.0, "readiness": 120.0, "model": 550.0,
            "admission": 120.0, "response": 600.0, "aggregate": 1700.0}


class ModelHostRefusal(RuntimeError):
    """Stable public-safe refusal without payload, path, or daemon text."""

    def __init__(self, code: str) -> None:
        self.code = code if re.fullmatch(r"[a-z0-9_]+", code) else "model_host_refused"
        super().__init__(self.code)


class _Budget:
    def __init__(self, clock: Callable[[], float], epoch_started: float,
                 response_deadline: float) -> None:
        self.clock = clock
        now = clock()
        numeric = (int, float)
        if (type(epoch_started) not in numeric or not math.isfinite(epoch_started)
                or epoch_started > now):
            raise ModelHostRefusal("epoch_invalid")
        if (type(response_deadline) not in numeric or not math.isfinite(response_deadline)
                or response_deadline <= now):
            raise ModelHostRefusal("response_deadline_invalid")
        self.epoch_started = epoch_started
        self.response_deadline = response_deadline
        self.aggregate_deadline = epoch_started + TIMEOUTS["aggregate"]
        self.phase_deadline = self.aggregate_deadline
        self.phase_name = "aggregate"
        self.phase_deadlines = {"initialization": min(epoch_started + TIMEOUTS["initialization"], response_deadline)}

    def begin(self, name: str) -> None:
        if name not in TIMEOUTS or name == "aggregate":
            raise ModelHostRefusal("phase_unknown")
        self.phase_name = name
        if name == "initialization":
            self.phase_deadline = self.phase_deadlines[name]
        else:
            self.phase_deadline = min(self.clock() + TIMEOUTS[name], self.response_deadline, self.aggregate_deadline)
            self.phase_deadlines[name] = self.phase_deadline
        self.check()

    def resume(self, name: str) -> None:
        deadline = self.phase_deadlines.get(name)
        if deadline is None:
            raise ModelHostRefusal("phase_unknown")
        self.phase_name = name
        self.phase_deadline = deadline
        self.check()

    def check(self) -> None:
        now = self.clock()
        if now >= self.response_deadline:
            raise ModelHostRefusal("response_timeout")
        if now >= self.aggregate_deadline:
            raise ModelHostRefusal("aggregate_timeout")
        if now >= self.phase_deadline:
            raise ModelHostRefusal(self.phase_name + "_timeout")

    def remaining(self) -> float:
        self.check()
        return min(self.response_deadline, self.aggregate_deadline, self.phase_deadline) - self.clock()


__all__ = ["TIMEOUTS", "ModelHostRefusal", "_Budget"]
