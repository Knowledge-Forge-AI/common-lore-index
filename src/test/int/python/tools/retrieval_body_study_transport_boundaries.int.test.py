"""Core transport custody and epoch joins with real private files.

The epoch files and acknowledgements are written on disk.  A deterministic
local acknowledgement stands in for the unavailable Docker worker only for
the file-transport contract; it is not an origin or model-execution claim.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from retrieval_body_study_contracts import BODY_INPUT_PIN_SHA256, BodyStudyRefusal
from retrieval_body_study_epoch import (
    await_acknowledgement,
    prepare_epoch,
    publish_acknowledgement,
)
from retrieval_body_study_host_transport import HostTransportError, write_private_once


_EVALUATOR = "sha256:" + "a" * 64


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def test_epoch_acknowledgement_is_private_current_and_source_bound(tmp_path: Path) -> None:
    """A real epoch round-trip rejects changed source identity and stale acks."""
    root = tmp_path / "epochs"
    charged: list[int] = []
    guards: list[str] = []
    epoch = prepare_epoch(
        attempt_id="transport-boundary",
        ordinal=1,
        evaluator_closure_sha256=_EVALUATOR,
        root=root,
        charge=charged.append,
        nonce="b" * 64,
    )
    epoch_path = root / epoch.relative_directory / "epoch.json"
    ack_path = root / epoch.relative_directory / "ack.json"
    assert epoch_path.stat().st_mode & 0o777 == 0o600

    publish_acknowledgement(epoch, root=root, charge=charged.append)
    await_acknowledgement(
        epoch,
        root=root,
        charge=charged.append,
        require_current=lambda: guards.append("current"),
    )
    assert ack_path.stat().st_mode & 0o777 == 0o600
    assert charged and len(guards) >= 2

    changed = dict(epoch.to_dict())
    changed["evaluator_closure_sha256"] = "sha256:" + "c" * 64
    epoch_path.write_bytes(_canonical(changed))
    with pytest.raises(BodyStudyRefusal, match="body_epoch:published_join"):
        publish_acknowledgement(epoch, root=root, charge=charged.append)

    second = prepare_epoch(
        attempt_id="transport-boundary",
        ordinal=2,
        evaluator_closure_sha256=_EVALUATOR,
        root=root,
        charge=charged.append,
        nonce="d" * 64,
    )
    publish_acknowledgement(second, root=root, charge=charged.append)
    stale_ack = root / second.relative_directory / "ack.json"
    stale_ack.write_bytes(b"{}\n")
    with pytest.raises(BodyStudyRefusal, match="body_epoch:ack_join"):
        await_acknowledgement(
            second,
            root=root,
            charge=charged.append,
            require_current=lambda: None,
        )

    assert BODY_INPUT_PIN_SHA256.startswith("sha256:")


def test_private_transport_evidence_is_exclusive_and_bounded(tmp_path: Path) -> None:
    evidence = tmp_path / "private"
    evidence.mkdir(mode=0o700)
    target = evidence / "response.bin"
    charged: list[int] = []
    write_private_once(
        target,
        b"public-safe-response",
        maximum=1024,
        charge_bytes=charged.append,
    )
    assert target.read_bytes() == b"public-safe-response"
    assert target.stat().st_mode & 0o777 == 0o600
    assert charged == [len(b"public-safe-response")]
    with pytest.raises(HostTransportError, match="private_exists"):
        write_private_once(
            target,
            b"replacement",
            maximum=1024,
            charge_bytes=charged.append,
        )
