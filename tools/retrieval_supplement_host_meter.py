"""Durable controlled-I/O reservations and finite host operation accounting."""
from __future__ import annotations

from dataclasses import dataclass, field
import time

from runner_pgvector_budget import Allocation
from retrieval_supplement_study_contracts import STUDY_CONTROLLER_SECONDS, refuse


@dataclass(repr=False)
class HostMeter:
    allocation: Allocation
    started: float = field(default_factory=time.monotonic)
    operations: list[str] = field(default_factory=list)
    max_operations: int = 2048
    docker_read_reservations: int = 0

    @property
    def deadline(self) -> float:
        return self.started + STUDY_CONTROLLER_SECONDS

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        refuse(remaining > 0, "host_meter:controller_deadline")
        return remaining

    def read(self, count: int) -> None:
        self.remaining()
        with self.allocation.session() as meter:
            meter.charge("read_bytes", count)

    def transfer(self, count: int) -> None:
        self.remaining()
        with self.allocation.session() as meter:
            meter.charge("read_bytes", count)
            meter.charge("transfer_bytes", count)

    def docker_read(self, count: int) -> None:
        refuse(type(count) is int and count >= 0
               and self.docker_read_reservations + count <= 2 * 1024**3,
               "host_meter:docker_read_ceiling")
        self.docker_read_reservations += count
        self.read(count)

    def operation(self, name: str) -> None:
        self.remaining()
        refuse(type(name) is str and name.startswith("docker.") and name[7:] in {
            "docker", "inspect", "image", "container", "create", "start", "wait",
            "stop", "rm", "exec", "cp", "logs", "version", "info", "ps", "volume", "network",
        }, "host_meter:operation_class")
        refuse(len(self.operations) < self.max_operations, "host_meter:operation_ceiling")
        self.operations.append(name)

    def worker(self) -> None:
        self.remaining()
        with self.allocation.session() as meter:
            meter.charge("study_workers", 1)
