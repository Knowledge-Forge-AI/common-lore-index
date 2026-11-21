"""Source custody for a frozen supplement run; metadata and byte proofs differ."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
from typing import Any

from retrieval_public_text_sources import worker_closure
from retrieval_supplement_host_meter import HostMeter
from retrieval_supplement_study_closure import discover_study_evaluator_closure
from retrieval_supplement_study_contracts import SOURCE_VERIFICATION_LIMIT, WORKER_CLOSURE_SHA256, refuse
from offline_qualification_checkout import verify_checkout


def private_directory(path: Path) -> None:
    refuse(path.is_absolute() and all(not p.is_symlink() for p in (path, *path.parents)),
           "host_source:directory_path")
    info = path.stat()
    refuse(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
           and stat.S_IMODE(info.st_mode) == 0o700, "host_source:directory_custody")


def put_private(path: Path, raw: bytes, *, meter: HostMeter) -> None:
    private_directory(path.parent)
    meter.transfer(len(raw))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@dataclass(repr=False)
class StudySourceGuard:
    source_root: Path
    plan: Mapping[str, Any]
    meter: HostMeter
    closure: dict[str, Any] = field(default_factory=dict, init=False)
    fingerprints: dict[str, tuple[int, ...]] = field(default_factory=dict, init=False)
    directories: dict[Path, tuple[int, ...]] = field(default_factory=dict, init=False)
    byte_verifications: int = field(default=0, init=False)

    def _fingerprint(self, name: str) -> tuple[int, ...]:
        path = self.source_root / name
        info = path.lstat()
        return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def freeze(self) -> None:
        refuse(not self.fingerprints, "host_source:already_frozen")
        self.verify_whole_candidate()
        # Discovery reads the evaluator once to assemble supplied bytes and
        # once to verify them. Reserve the complete candidate twice first;
        # this conservative reservation is not released after success.
        total = sum((self.source_root / name).stat().st_size
                    for name, row in self.plan["candidate_snapshot"].items() if "symlink" not in row)
        self.meter.read(2 * total)
        self.closure = discover_study_evaluator_closure(self.source_root)
        refuse(self.closure["evaluator_closure_sha256"] == self.plan["evaluator_closure_sha256"],
               "host_source:evaluator_closure")
        self.fingerprints = {name: self._fingerprint(name) for name in self.plan["candidate_snapshot"]}
        parents = {parent for name in self.fingerprints for parent in (self.source_root / name).parents
                   if parent == self.source_root or parent.is_relative_to(self.source_root)}
        for parent in parents:
            info = parent.lstat()
            refuse(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), "host_source:parent_alias")
            self.directories[parent] = (info.st_dev, info.st_ino, info.st_mode,
                info.st_mtime_ns, info.st_ctime_ns)

    def verify_whole_candidate(self) -> None:
        total = sum((self.source_root / name).stat().st_size
                    for name, row in self.plan["candidate_snapshot"].items() if "symlink" not in row)
        self.meter.read(total + 4 * 1024**2)
        verify_checkout(self.source_root, dict(self.plan["candidate_snapshot"]), self.plan["candidate_head"])

    def metadata_current(self) -> None:
        """Fast polling proves unchanged file custody, not a new whole-tree hash."""
        refuse(bool(self.fingerprints), "host_source:not_frozen")
        self.meter.remaining()
        for name, expected in self.fingerprints.items():
            refuse(self._fingerprint(name) == expected, "host_source:metadata_changed")
        for path, expected in self.directories.items():
            info = path.lstat()
            refuse((info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns, info.st_ctime_ns) == expected,
                   "host_source:directory_changed")

    def require_current(self) -> None:
        """Each authority hop re-reads the small evaluator closure, metered first."""
        self.metadata_current()
        refuse(self.byte_verifications < SOURCE_VERIFICATION_LIMIT, "host_source:verification_ceiling")
        self.byte_verifications += 1
        self.meter.read(int(self.closure["total_bytes"]) + int(self.closure["member_count"]))
        for name, row in self.closure["files"].items():
            descriptor = os.open(self.source_root / name, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                raw = stream.read(int(row["bytes"]) + 1)
            refuse(len(raw) == row["bytes"] and "sha256:" + hashlib.sha256(raw).hexdigest()
                   == row["sha256"], "host_source:source_changed")
        self.metadata_current()

    def copy_worker(self, destination: Path) -> dict[str, str]:
        private_directory(destination)
        self.require_current()
        # worker_closure reads each of the same seventeen maintained files.
        self.meter.read(sum(self.closure["files"]["tools/" + name]["bytes"]
                            for name in self._worker_names()))
        worker = worker_closure(self.source_root)
        refuse(worker.identity == WORKER_CLOSURE_SHA256, "host_source:worker_closure")
        for name, raw in worker.files.items():
            put_private(destination / name, raw, meter=self.meter)
        self.require_current()
        return dict(worker.digests)

    @staticmethod
    def _worker_names() -> tuple[str, ...]:
        from retrieval_semantic_generator_worker_contracts import WORKER_SOURCE_FILES
        return tuple(WORKER_SOURCE_FILES)
