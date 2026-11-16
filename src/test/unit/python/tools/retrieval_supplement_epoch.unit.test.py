"""Real owned-file epoch/ACK boundaries; no model, process or Docker starts."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

import pytest

import retrieval_supplement_epoch as owner


def epoch(root: Path, *, ordinal=1):
    return owner.prepare_epoch(attempt_id="synthetic-study", ordinal=ordinal,
        evaluator_closure_sha256="sha256:" + "a" * 64, nonce="b" * 64,
        root=root, charge=lambda _count: None)


def test_fresh_epoch_frame_and_ack_join_on_real_private_files(tmp_path, capsys):
    root = tmp_path / "owned"
    value = epoch(root)
    frame = next(line for line in capsys.readouterr().out.splitlines() if line.startswith(owner.EPOCH_PREFIX))
    assert owner.decode_epoch_line(frame.encode()) == value
    observed: list[int] = []
    owner.publish_acknowledgement(value, root=root, charge=observed.append)
    guards: list[str] = []
    owner.await_acknowledgement(value, root=root, charge=observed.append,
        require_current=lambda: guards.append("current"))
    directory = root / value.relative_directory
    assert {p.name for p in directory.iterdir()} == {"epoch.json", "ack.json"}
    assert (directory / "ack.json").read_bytes() == owner.acknowledgement(value)
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in directory.iterdir())
    assert len(guards) == 2 and observed


def test_reused_epoch_or_ack_never_overwrites_owned_evidence(tmp_path):
    root = tmp_path / "owned"
    value = epoch(root)
    directory = root / value.relative_directory
    before = (directory / "epoch.json").read_bytes()
    with pytest.raises(FileExistsError):
        epoch(root)
    assert (directory / "epoch.json").read_bytes() == before
    owner.publish_acknowledgement(value, root=root, charge=lambda _n: None)
    saved = (directory / "ack.json").read_bytes()
    with pytest.raises(FileExistsError):
        owner.publish_acknowledgement(value, root=root, charge=lambda _n: None)
    assert (directory / "ack.json").read_bytes() == saved


def test_foreign_nonce_cannot_ack_existing_epoch(tmp_path):
    root = tmp_path / "owned"
    value = epoch(root)
    with pytest.raises(ValueError, match="published_join"):
        owner.publish_acknowledgement(replace(value, nonce="c" * 64), root=root, charge=lambda _n: None)
    assert not (root / value.relative_directory / "ack.json").exists()


@pytest.mark.parametrize("kind", ["parent_symlink", "ack_symlink", "ack_hardlink", "wrong_ack"])
def test_alias_or_foreign_ack_refuses_without_following(tmp_path, kind):
    root = tmp_path / "owned"
    if kind == "parent_symlink":
        outside = tmp_path / "outside"
        outside.mkdir(mode=0o700)
        root.symlink_to(outside, target_is_directory=True)
        with pytest.raises(RuntimeError):
            epoch(root)
        assert list(outside.iterdir()) == []
        return
    value = epoch(root)
    directory = root / value.relative_directory
    foreign = tmp_path / "foreign"
    foreign.write_bytes(owner.acknowledgement(value) if kind != "wrong_ack" else b"foreign")
    foreign.chmod(0o600)
    if kind == "ack_symlink":
        (directory / "ack.json").symlink_to(foreign)
    else:
        os.link(foreign, directory / "ack.json")
        if kind == "wrong_ack":
            foreign.unlink()
    with pytest.raises((ValueError, RuntimeError, OSError)):
        owner.await_acknowledgement(value, root=root, charge=lambda _n: None,
            require_current=lambda: None)


def test_incomplete_ack_waits_but_never_admits_before_full_exact_bytes(tmp_path):
    root = tmp_path / "owned"
    value = epoch(root)
    ack = root / value.relative_directory / "ack.json"
    expected = owner.acknowledgement(value)
    ack.write_bytes(expected[:12]);ack.chmod(0o600)
    steps: list[str] = []
    def finish(_seconds):
        steps.append("complete")
        ack.write_bytes(expected)
    owner.await_acknowledgement(value, root=root, charge=lambda _n: None,
        require_current=lambda: None, sleep=finish)
    assert steps == ["complete"]


def test_ack_wait_has_exact_timeout_and_lost_source_refuses(tmp_path):
    root = tmp_path / "owned"
    value = epoch(root)
    readings = iter((0.0, 121.0))
    with pytest.raises(RuntimeError, match="ack_timeout"):
        owner.await_acknowledgement(value, root=root, charge=lambda _n: None,
            require_current=lambda: None, clock=lambda: next(readings))
    owner.publish_acknowledgement(value, root=root, charge=lambda _n: None)
    def lost():
        raise RuntimeError("synthetic_source_lost")
    with pytest.raises(RuntimeError, match="synthetic_source_lost"):
        owner.await_acknowledgement(value, root=root, charge=lambda _n: None,
            require_current=lost)


@pytest.mark.parametrize("key,value", [("ordinal", True), ("ordinal", 3),
    ("attempt_id", "../foreign"), ("nonce", "short"), ("input_pin_sha256", "sha256:" + "f" * 64),
    ("evaluator_closure_sha256", "invalid"), ("request_attempt_id", "old-cal5")])
def test_closed_epoch_identity_refuses_wrong_roles_and_hashes(tmp_path, key, value):
    original = epoch(tmp_path / "owned").to_dict()
    original[key] = value
    with pytest.raises(ValueError):
        owner.parse_epoch(original)
