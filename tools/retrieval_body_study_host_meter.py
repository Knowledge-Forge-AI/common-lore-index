"""Finite host read/transfer/docker-operation accounting for BODY."""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from runner_pgvector_budget import Allocation
from retrieval_body_study_contracts import (
    BODY_CONTROLLER_SECONDS, MAX_DOCKER_OPERATIONS, MAX_DOCKER_READ_BYTES, refuse,
)


@dataclass(repr=False)
class HostMeter:
    allocation: Allocation
    started: float = field(default_factory=time.monotonic)
    clock: Any = time.monotonic
    operations: list[str] = field(default_factory=list)
    max_operations: int = MAX_DOCKER_OPERATIONS
    docker_read_reservations: int = 0

    @property
    def deadline(self) -> float:
        return self.started + BODY_CONTROLLER_SECONDS

    def remaining(self) -> float:
        value = self.deadline - float(self.clock())
        refuse(value > 0, "body host controller deadline")
        return value

    def read(self, count: int) -> None:
        self.remaining()
        refuse(type(count) is int and count >= 0, "body host read count")
        with self.allocation.session() as ledger:
            ledger.charge("read_bytes", count)

    def transfer(self, count: int) -> None:
        self.remaining()
        refuse(type(count) is int and count >= 0, "body host transfer count")
        with self.allocation.session() as ledger:
            ledger.charge("read_bytes", count)
            ledger.charge("transfer_bytes", count)

    def docker_read(self, count: int) -> None:
        refuse(type(count) is int and count >= 0
               and self.docker_read_reservations + count <= MAX_DOCKER_READ_BYTES,
               "body docker read ceiling")
        self.docker_read_reservations += count
        self.read(count)

    def operation(self, name: str) -> None:
        self.remaining()
        refuse(type(name) is str and name.startswith("docker.")
               and name[7:] in {"docker", "inspect", "image", "container", "create",
                                "start", "wait", "stop", "rm", "exec", "cp", "logs",
                                "version", "info", "ps", "volume", "network"},
               "body docker operation class")
        refuse(len(self.operations) < self.max_operations, "body docker operation ceiling")
        self.operations.append(name)

    def worker(self) -> None:
        self.remaining()
        with self.allocation.session() as ledger:
            ledger.charge("body_study_workers", 1)

    def record_result(self, value: dict[str, Any]) -> None:
        self.remaining()
        refuse(isinstance(value, dict), "body result record")
        with self.allocation.session() as ledger:
            ledger.bind("body_study_result", value)


__all__ = ["HostMeter"]
