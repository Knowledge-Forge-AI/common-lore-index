from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import threading
import pytest
from retrieval_body_study_snapshot_counter import (
    BodySnapshotCounter,
    SnapshotCounterBinding,
    SnapshotCounterRefusal,
)
def _binding(root: Path) -> SnapshotCounterBinding:
    digest = "sha256:" + "a" * 64
    return SnapshotCounterBinding(
        "body-attempt", digest, digest, digest, digest, root,
    )
def _snapshot_reader(path: Path) -> bytes:
    with path.open("rb") as source:
        return source.read()
def _private_root(path: Path) -> Path:
    path.mkdir()
    os.chmod(path, 0o700)
    return path
def test_counts_and_persists_bounded_receipt(tmp_path: Path) -> None:
    root = _private_root(tmp_path / "counter")
    source = tmp_path / "source.txt"
    source.write_bytes(b"body")
    binding = _binding(root)
    with BodySnapshotCounter(_snapshot_reader.__code__, binding=binding, reserved_count=2) as counter:
        assert _snapshot_reader(source) == b"body"
        assert _snapshot_reader(source) == b"body"
    assert counter.receipt is not None and counter.receipt.valid
    durable = json.loads((root / binding.receipt_name).read_text(encoding="utf-8"))
    assert durable["reserved_count"] == 2
    assert durable["observed_count"] == 2
    assert durable["profile_attested"] and durable["profile_restored"]
def test_active_checkpoint_is_measured_but_never_an_attestation(tmp_path: Path) -> None:
    root = _private_root(tmp_path / "counter")
    source = tmp_path / "source.txt"
    source.write_bytes(b"body")
    with BodySnapshotCounter(_snapshot_reader.__code__, binding=_binding(root), reserved_count=1) as counter:
        checkpoint = counter.checkpoint()
        assert not checkpoint.valid and not checkpoint.profile_restored
        _snapshot_reader(source)
    assert counter.receipt is not None and counter.receipt.valid
def test_limit_refusal_survives_caught_profile_overflow(tmp_path: Path) -> None:
    root = _private_root(tmp_path / "counter")
    source = tmp_path / "source.txt"
    source.write_bytes(b"body")
    counter = BodySnapshotCounter(_snapshot_reader.__code__, binding=_binding(root), reserved_count=1)
    with pytest.raises(SnapshotCounterRefusal, match="snapshot_limit_exceeded"):
        with counter:
            _snapshot_reader(source)
            with pytest.raises(SnapshotCounterRefusal, match="snapshot_limit_exceeded"):
                _snapshot_reader(source)
            with pytest.raises(SnapshotCounterRefusal, match="snapshot_limit_exceeded"):
                _snapshot_reader(source)
    assert counter.receipt is not None and not counter.receipt.valid
    assert counter.receipt.observed_count == 2
    assert counter.receipt.audit_rejections >= 1
def test_existing_foreign_thread_is_rejected_by_audit_backstop(tmp_path: Path) -> None:
    root = _private_root(tmp_path / "counter")
    source = tmp_path / "source.txt"
    source.write_bytes(b"body")
    ready = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []
    def foreign_reader() -> None:
        ready.set()
        release.wait(5)
        try:
            _snapshot_reader(source)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()
    thread = threading.Thread(target=foreign_reader)
    thread.start()
    assert ready.wait(5)
    counter = BodySnapshotCounter(_snapshot_reader.__code__, binding=_binding(root), reserved_count=1)
    with pytest.raises(SnapshotCounterRefusal):
        with counter:
            release.set()
            assert finished.wait(5)
            assert _snapshot_reader(source) == b"body"
    thread.join(5)
    assert errors and isinstance(errors[0], SnapshotCounterRefusal)
def test_prior_profile_is_refused_without_mutation(tmp_path: Path) -> None:
    root = _private_root(tmp_path / "counter")
    old_sys = sys.getprofile()
    old_thread = threading.getprofile()
    def marker(_frame: object, _event: str, _argument: object) -> None:
        return None
    try:
        sys.setprofile(marker)
        threading.setprofile(marker)
        with pytest.raises(SnapshotCounterRefusal, match="prior_profile_present"):
            BodySnapshotCounter(_snapshot_reader.__code__, binding=_binding(root)).__enter__()
        assert sys.getprofile() is marker
        assert threading.getprofile() is marker
    finally:
        sys.setprofile(old_sys)
        threading.setprofile(old_thread)
def test_profile_replacement_fails_closed_and_restores_slots(tmp_path: Path) -> None:
    root = _private_root(tmp_path / "counter")
    source = tmp_path / "source.txt"
    source.write_bytes(b"body")
    def replacement(_frame: object, _event: str, _argument: object) -> None:
        return None
    counter = BodySnapshotCounter(_snapshot_reader.__code__, binding=_binding(root), reserved_count=1)
    with pytest.raises(SnapshotCounterRefusal):
        with counter:
            sys.setprofile(replacement)
            _snapshot_reader(source)
    assert sys.getprofile() is None
    assert threading.getprofile() is None
    assert counter.receipt is not None and not counter.receipt.valid
def test_inherited_callback_is_inert_after_close(tmp_path: Path) -> None:
    root = _private_root(tmp_path / "counter")
    source = tmp_path / "source.txt"
    source.write_bytes(b"body")
    counter = BodySnapshotCounter(_snapshot_reader.__code__, binding=_binding(root), reserved_count=1)
    with counter:
        callback = counter.profile_hook
        _snapshot_reader(source)
    assert counter.receipt is not None and counter.receipt.valid
    observed = counter.observed_count
    assert callback is not None
    callback(_snapshot_reader.__code__, "call", None)
    assert counter.observed_count == observed
def test_nonhex_binding_digest_is_refused(tmp_path: Path) -> None:
    root = _private_root(tmp_path / "counter")
    digest = "sha256:" + "g" * 64
    binding = SnapshotCounterBinding("attempt", digest, digest, digest, digest, root)
    with pytest.raises(SnapshotCounterRefusal, match="snapshot.binding.plan"):
        binding.validate()
def test_ancestor_symlink_binding_is_refused(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    _private_root(real_parent / "counter")
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(SnapshotCounterRefusal, match="snapshot.binding.root"):
        _binding(alias_parent / "counter").validate()
