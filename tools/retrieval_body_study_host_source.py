"""Fresh source custody and byte proofs for the BODY host."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, cast

from retrieval_body_study_closure import build_closure, read_source_member
from retrieval_body_study_worker_closure import fresh_worker_closure
from retrieval_body_study_contracts import (
    BODY_ARTIFACT_FILE_COUNT, BODY_ARTIFACT_PIN_DOCUMENT_SHA256,
    BODY_ARTIFACT_SOURCE_IDENTITY_SHA256, BODY_ARTIFACT_TOTAL_BYTES,
    BODY_INPUT_PIN_SHA256, BODY_REPRESENTATIONS, SOURCE_VERIFICATION_LIMIT,
    V1_QUERIES, V2_QUERIES, REGISTERED_SNAPSHOT_READ_BYTES, refuse,
)
from retrieval_body_study_host_meter import HostMeter

_MAX_SOURCE_MEMBER_BYTES = 4 * 1024 * 1024


def _size_bounds(root: Path, names: tuple[str, ...]) -> dict[str, int]:
    result: dict[str, int] = {}
    for name in names:
        path = root / name
        for ancestor in path.parents:
            if ancestor == root:
                break
            refuse(not ancestor.is_symlink(), "body source parent alias")
        info = path.lstat()
        refuse(stat.S_ISREG(info.st_mode) and 0 <= info.st_size <= _MAX_SOURCE_MEMBER_BYTES,
               "body source member bounds")
        result[name] = int(info.st_size)
    return result


def _metered_closure(root: Path, names: tuple[str, ...], *, name: str,
                     expected_bytes: int, meter: HostMeter) -> dict[str, Any]:
    maximums = _size_bounds(root, names)
    refuse(sum(maximums.values()) == expected_bytes, "body source byte binding")
    meter.read(sum(maximums.values()) + len(maximums))
    return build_closure(root, names, name=name, maximums=maximums)


def _metered_worker(root: Path, meter: HostMeter) -> dict[str, Any]:
    from retrieval_semantic_generator_worker_contracts import WORKER_SOURCE_FILES
    names = tuple("tools/" + name for name in WORKER_SOURCE_FILES)
    maximums = _size_bounds(root, names)
    meter.read(sum(maximums.values()) + len(maximums))
    return fresh_worker_closure(root)


def private_directory(path: Path) -> None:
    refuse(isinstance(path, Path) and path.is_absolute() and not path.is_symlink(),
           "body source directory")
    info = path.stat()
    refuse(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
           and stat.S_IMODE(info.st_mode) == 0o700, "body source directory custody")


def put_private(path: Path, raw: bytes, *, meter: HostMeter) -> None:
    private_directory(path.parent)
    refuse(type(raw) is bytes and len(raw) <= 4 * 1024 * 1024, "body source private bytes")
    meter.transfer(len(raw))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as target:
            descriptor = -1
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _query_rows(cases: Any, *, allowed_kinds: frozenset[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for case in cases:
        if case.kind not in allowed_kinds:
            continue
        task = case.task
        # Keep this projection independent of authored case order.  The inner
        # query ledger uses the same task-id ordering before constructing its
        # wire descriptor, while the question digest binds the live catalog.
        rows.append({
            "record_id": "query:" + task.task_id,
            "text_sha256": "sha256:" + hashlib.sha256(
                task.question.encode("utf-8")).hexdigest(),
        })
    rows.sort(key=lambda row: row["record_id"])
    return rows


def query_manifest(inputs: Any) -> dict[str, list[dict[str, str]]]:
    """Project current registered questions into both wire representations."""
    cases = inputs.catalog.cases
    manifest = {
        BODY_REPRESENTATIONS[0]: _query_rows(
            cases, allowed_kinds=frozenset({"metadata_anchor"})),
        BODY_REPRESENTATIONS[1]: _query_rows(
            cases, allowed_kinds=frozenset({
                "body_positive", "metadata_anchor", "absent", "ambiguous",
            })),
    }
    refuse(len(manifest[BODY_REPRESENTATIONS[0]]) == V1_QUERIES,
           "body v1 query manifest census")
    refuse(len(manifest[BODY_REPRESENTATIONS[1]]) == V2_QUERIES,
           "body v2 query manifest census")
    return manifest


def query_manifest_sha256(inputs: Any) -> str:
    manifest = query_manifest(inputs)
    raw = json.dumps({"schema": "cloe-w5-body-query-manifest-v1", "rows": manifest},
                     ensure_ascii=True, sort_keys=True,
                     separators=(",", ":")).encode("ascii")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def require_body_artifact_profile(profile: Any) -> str:
    """Bind the live artifact verifier to the BODY artifact hash domain."""
    verified_value = getattr(profile, "verified_files", None)
    refuse(type(verified_value) is tuple and len(verified_value) == BODY_ARTIFACT_FILE_COUNT,
           "body artifact file census")
    verified = cast(tuple[object, ...], verified_value)
    refuse(all(bool(getattr(item, "sha256", None)) for item in verified),
           "body artifact file digest")
    sizes = [getattr(item, "size", None) for item in verified]
    refuse(all(type(size) is int and size >= 0 for size in sizes), "body artifact byte census")
    typed_sizes = cast(list[int], sizes)
    refuse(sum(typed_sizes) == BODY_ARTIFACT_TOTAL_BYTES, "body artifact byte census")
    identity = getattr(profile, "source_identity_sha256", None)
    refuse(identity == BODY_ARTIFACT_SOURCE_IDENTITY_SHA256
           and identity != BODY_ARTIFACT_PIN_DOCUMENT_SHA256,
           "body artifact source identity")
    pin = getattr(profile, "pinned_profile_sha256", getattr(profile, "profile_sha256", None))
    refuse(pin is not None, "body artifact pin missing")
    normalized = pin if str(pin).startswith("sha256:") else "sha256:" + str(pin)
    refuse(normalized == BODY_ARTIFACT_PIN_DOCUMENT_SHA256
           and normalized != identity, "body artifact pin identity")
    return str(identity)


@dataclass(repr=False)
class BodySourceGuard:
    source_root: Path
    plan: Mapping[str, Any]
    meter: HostMeter
    closure: dict[str, Any] = field(default_factory=dict, init=False)
    treatment: dict[str, Any] = field(default_factory=dict, init=False)
    worker: dict[str, Any] = field(default_factory=dict, init=False)
    fingerprints: dict[str, tuple[int, ...]] = field(default_factory=dict, init=False)
    directories: dict[Path, tuple[int, ...]] = field(default_factory=dict, init=False)
    byte_verifications: int = field(default=0, init=False)

    def _fingerprint(self, name: str) -> tuple[int, ...]:
        path = self.source_root / name
        info = path.lstat()
        return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _candidate_bytes(self) -> int:
        refuse(self.source_root.is_absolute() and not self.source_root.is_symlink(),
               "body candidate root")
        total = 0
        for name, row in self.plan["candidate_snapshot"].items():
            if "symlink" not in row:
                path = self.source_root / name
                for ancestor in path.parents:
                    if ancestor == self.source_root:
                        break
                    refuse(not ancestor.is_symlink(), "body candidate parent alias")
                info = path.lstat()
                refuse(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                       "body candidate member custody")
                total += info.st_size
        refuse(0 < total <= 128 * 1024**2, "body candidate bytes")
        return total

    def read_member(self, name: str) -> bytes:
        maximums = _size_bounds(self.source_root, (name,))
        maximum = maximums[name]
        self.meter.read(maximum + 1)
        return read_source_member(self.source_root, name, maximum=maximum)

    def verify_whole_candidate(self) -> None:
        total = self._candidate_bytes()
        self.meter.read(total + 4 * 1024**2)
        try:
            from offline_qualification_checkout import verify_checkout
            verify_checkout(self.source_root, dict(self.plan["candidate_snapshot"]),
                            self.plan["candidate_head"])
        except (ImportError, OSError, ValueError) as error:
            raise ValueError("body checkout verifier unavailable") from error

    def _registered_inputs(self) -> Any:
        root = self.source_root / "src/test/fixtures/retrieval_evaluation/body-v2"
        predecessor = self.source_root / "src/test/fixtures/retrieval_evaluation"
        expected_bytes = self.plan["derived"]["byte_forecast_binding"]["registered_input_bytes"]
        refuse(expected_bytes >= REGISTERED_SNAPSHOT_READ_BYTES, "body registered input byte reservation")
        # The loader and the immutable post-load projection are separately
        # charged.  Do not turn this data authority into a cached freshness
        # read: callers intentionally invoke it only at semantic boundaries.
        self.meter.read(2 * (expected_bytes + 1))
        try:
            from retrieval_body_pin import load_registered_body_inputs
            value = load_registered_body_inputs(root, predecessor_root=predecessor)
        except (ImportError, OSError, ValueError) as error:
            raise ValueError("body registered inputs unavailable") from error
        snapshot = value._snapshot()
        refuse(snapshot.pin_sha256 == BODY_INPUT_PIN_SHA256, "body registered input pin")
        manifest = query_manifest_sha256(snapshot)
        declaration = self.plan["declaration"]
        planned = tuple(dict.fromkeys(row["task_id"] for row in declaration["slot_manifest"]))
        current = tuple(case.task.task_id for case in snapshot.catalog.cases)
        refuse(manifest == declaration["query_manifest_sha256"]
               and declaration.get("query_manifest") == query_manifest(snapshot)
               and set(planned) == set(current) and len(planned) == len(current),
               "body registered query manifest")
        return value

    def require_registered_inputs(self) -> Any:
        """Perform the bounded semantic input check at an authority boundary."""
        refuse(bool(self.fingerprints), "body source not frozen")
        return self._registered_inputs()

    def freeze(self) -> None:
        refuse(not self.fingerprints, "body source already frozen")
        self.verify_whole_candidate()
        self._registered_inputs()
        binding = self.plan["derived"]["byte_forecast_binding"]
        self.closure = _metered_closure(
            self.source_root, tuple(self.plan["source_members"]), name="body-source",
            expected_bytes=binding["source_bytes"], meter=self.meter)
        self.treatment = _metered_closure(
            self.source_root, tuple(self.plan["treatment_members"]), name="body-treatment",
            expected_bytes=binding["treatment_bytes"], meter=self.meter)
        self.worker = _metered_worker(self.source_root, self.meter)
        refuse(self.worker["total_bytes"] == binding["worker_bytes"], "body worker byte binding")
        refuse(self.closure["identity"] == self.plan["source_closure_sha256"]
               and self.treatment["identity"] == self.plan["treatment_sha256"]
               and self.worker["identity"] == self.plan["worker_closure_sha256"],
               "body source closure identity")
        names = set(self.closure["digests"]) | set(self.treatment["digests"])
        names.update(self.plan["candidate_snapshot"])
        self.fingerprints = {name: self._fingerprint(name) for name in names}
        parents = {parent for name in self.fingerprints
                   for parent in (self.source_root / name).parents
                   if parent == self.source_root or parent.is_relative_to(self.source_root)}
        for parent in parents:
            info = parent.lstat()
            refuse(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode),
                   "body source parent alias")
            self.directories[parent] = (info.st_dev, info.st_ino, info.st_mode,
                                        info.st_mtime_ns, info.st_ctime_ns)

    def metadata_current(self) -> None:
        refuse(bool(self.fingerprints), "body source not frozen")
        self.meter.remaining()
        for name, expected in self.fingerprints.items():
            refuse(self._fingerprint(name) == expected, "body source metadata drift")
        for path, expected in self.directories.items():
            info = path.lstat()
            refuse((info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns, info.st_ctime_ns)
                   == expected, "body source directory drift")

    def require_current(self) -> None:
        """Re-read closure bytes at every boundary; source bytes are never cached."""
        self.metadata_current()
        refuse(self.byte_verifications < SOURCE_VERIFICATION_LIMIT, "body source verification ceiling")
        self.byte_verifications += 1
        binding = self.plan["derived"]["byte_forecast_binding"]
        current_source = _metered_closure(
            self.source_root, tuple(self.plan["source_members"]), name="body-source",
            expected_bytes=binding["source_bytes"], meter=self.meter)
        current_treatment = _metered_closure(
            self.source_root, tuple(self.plan["treatment_members"]), name="body-treatment",
            expected_bytes=binding["treatment_bytes"], meter=self.meter)
        refuse(current_source["identity"] == self.closure["identity"]
               and current_treatment["identity"] == self.treatment["identity"],
               "body source byte drift")
        current_worker = _metered_worker(self.source_root, self.meter)
        refuse(current_worker["identity"] == self.worker["identity"]
               and current_worker["digests"] == self.worker["digests"],
               "body worker byte drift")
        self.metadata_current()

    def copy_worker(self, destination: Path) -> dict[str, str]:
        private_directory(destination)
        self.require_current()
        current = _metered_worker(self.source_root, self.meter)
        refuse(current["identity"] == self.worker["identity"], "body worker copy drift")
        for name, raw in current["files"].items():
            target = destination / name
            missing: list[Path] = []
            parent = target.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir(mode=0o700)
            put_private(target, raw, meter=self.meter)
        self.require_current()
        return dict(current["digests"])


StudySourceGuard = BodySourceGuard

__all__ = ["BodySourceGuard", "StudySourceGuard", "private_directory", "put_private",
           "query_manifest", "query_manifest_sha256", "require_body_artifact_profile"]
