"""Verification acquisition failures release the exact descriptor without GC."""
from __future__ import annotations

import io
import os
from pathlib import Path
from typing import BinaryIO

import pytest

from common_lore_index.artifacts import _store_filesystem as filesystem
from common_lore_index.artifacts import _store_stream as streams
from common_lore_index.artifacts.store import ArtifactIntegrityError, FileSystemArtifactStore


class _VerificationCase:
    """Retain actual owners and their original identity until assertions finish."""

    def __init__(self, root: Path, failure: str, error_type: type[BaseException]) -> None:
        self.store = FileSystemArtifactStore(root / "store")
        self.reference = self.store.put(b"public verification payload")
        self.path = self.store.root / self.reference.locator.value
        self.failure = failure
        self.primary = error_type("synthetic verification acquisition refusal")
        self.acquired: list[tuple[int, os.stat_result]] = []
        self.raw_owners: list[BinaryIO] = []
        self.real_open, self.real_stat = os.open, os.fstat
        self.real_fdopen, self.real_close = os.fdopen, os.close

    @property
    def expected(self) -> type[BaseException]:
        if self.failure == "identity" or isinstance(self.primary, OSError):
            return ArtifactIntegrityError
        return type(self.primary)

    def _open(self, candidate, flags, *args, **kwargs):
        descriptor = self.real_open(candidate, flags, *args, **kwargs)
        if Path(candidate) == self.path:
            self.acquired.append((descriptor, self.real_stat(descriptor)))
        return descriptor

    def _stat(self, descriptor):
        if self.failure in {"fstat", "cleanup"} and self.acquired and descriptor == self.acquired[0][0]:
            raise self.primary
        return self.real_stat(descriptor)

    def _fdopen(self, descriptor, *args, **kwargs):
        if self.failure == "fdopen" and self.acquired and descriptor == self.acquired[0][0]:
            raise self.primary
        raw = self.real_fdopen(descriptor, *args, **kwargs)
        self.raw_owners.append(raw)
        return raw

    def _make_reference(self, *_args):
        if self.failure == "reference":
            raise self.primary
        return self.reference

    def _refuse_wrapper(self, raw, **_kwargs):
        # Keep the raw owner reachable so collection cannot hide a lifetime bug.
        self.raw_owners.append(raw)
        raise self.primary

    def _close(self, descriptor):
        self.real_close(descriptor)
        if self.acquired and descriptor == self.acquired[0][0]:
            raise RuntimeError("synthetic secondary close refusal")

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "open", self._open)
        monkeypatch.setattr(os, "fstat", self._stat)
        monkeypatch.setattr(os, "fdopen", self._fdopen)
        if self.failure == "cleanup":
            monkeypatch.setattr(os, "close", self._close)
        if self.failure == "identity":
            monkeypatch.setattr(streams, "_same_file_stat", lambda *_: False)
        if self.failure == "wrapper":
            monkeypatch.setattr(streams, "VerifyingArtifactStream", self._refuse_wrapper)

    def run(self) -> None:
        streams.verify_stream_path(
            self.path, digest=self.reference.content_digest, length=self.reference.size_bytes,
            bound=self.reference.size_bytes, expected_version=self.reference.store_version,
            owner_uid=os.getuid(), private_details_fn=filesystem._private_details,
            reference_factory=self._make_reference,
        )

    def leaked(self) -> bool:
        assert len(self.acquired) == 1
        descriptor, before = self.acquired[0]
        try:
            after = self.real_stat(descriptor)
        except OSError:
            return False
        return (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)

    def cleanup(self) -> None:
        for raw in self.raw_owners:
            raw.close()
        if self.acquired and self.leaked():
            self.real_close(self.acquired[0][0])


@pytest.mark.parametrize(("failure", "error_type"), [
    ("fstat", OSError), ("identity", OSError), ("reference", OSError),
    ("fdopen", OSError), ("wrapper", OSError),
    ("reference", KeyboardInterrupt), ("wrapper", RuntimeError), ("cleanup", OSError),
])
def test_verification_acquisition_keeps_descriptor_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, error_type: type[BaseException],
) -> None:
    case = _VerificationCase(tmp_path, failure, error_type)
    case.install(monkeypatch)
    try:
        with pytest.raises(case.expected) as caught:
            case.run()
        if isinstance(case.primary, OSError) and failure != "identity":
            assert caught.value.__cause__ is case.primary
        elif failure != "identity":
            assert caught.value is case.primary
        leaked = case.leaked()
    finally:
        case.cleanup()
    assert not leaked


def test_read_all_uses_bounded_requests_and_zero_line_reads_are_noops(tmp_path: Path):
    store = FileSystemArtifactStore(tmp_path / "store")
    payload = b"x" * 70000 + b"\nsecond"
    reference = store.put(payload)
    path = store.object_path(reference)
    requests = []

    class TrackedReader(io.BufferedReader):
        def read(self, size=-1):
            requests.append(size)
            assert 0 < size <= 64 * 1024
            return super().read(size)

    with TrackedReader(path.open("rb", buffering=0)) as raw:
        with streams.VerifyingArtifactStream(
            raw, reference=reference, bound=reference.size_bytes,
            before_stat=os.fstat(raw.fileno()), expected_version=reference.store_version,
        ) as stream:
            assert stream.readline(0) == b""
            assert not requests
            assert stream.read() == payload and stream.verified
    assert len(requests) >= 3


@pytest.mark.parametrize("operation", ("verify", "read", "open_stream", "collision"))
def test_artifact_reads_refuse_fifo_replacement_without_blocking(tmp_path: Path, monkeypatch, operation):
    case = _VerificationCase(tmp_path, "fifo", OSError)
    original = case.path.with_name("preserved")
    temporary = tmp_path / "collision-input"
    temporary.write_bytes(b"public verification payload")
    switched = False

    def switch_at_open(path, flags, *args, **kwargs):
        nonlocal switched
        if Path(path) == case.path:
            assert flags & os.O_NONBLOCK and not switched
            switched = True
            case.path.rename(original)
            os.mkfifo(case.path, 0o400)
        return case._open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", switch_at_open)
    try:
        with pytest.raises(ArtifactIntegrityError, match="artifact changed during read"):
            if operation == "verify":
                case.run()
            elif operation == "read":
                case.store.read(case.reference)
            elif operation == "open_stream":
                with case.store.open_stream(case.reference):
                    pytest.fail("opened a replacement FIFO")
            else:
                streams.compare_and_verify_collision(
                    case.path, temporary, case.reference.size_bytes, case.reference.content_digest,
                    case.reference.size_bytes, owner_uid=os.getuid(), private_details_fn=filesystem._private_details,
                )
        assert switched and not case.leaked()
        assert original.read_bytes() == b"public verification payload"
    finally:
        case.cleanup()
        if switched:
            case.path.unlink(missing_ok=True)
