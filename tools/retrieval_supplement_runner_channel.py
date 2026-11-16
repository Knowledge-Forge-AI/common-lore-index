"""One live host-pipe challenge for the owned canonical runner."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
from typing import Any

from retrieval_supplement_ownership_wire import (
    MAX_WIRE_BYTES, decode_receipt as decode_receipt_v2,
    encode_control as encode_control_v2, scope_sha256,
)
from retrieval_supplement_study_contracts import refuse
from test_sandbox_ownership import (
    CHANNEL_ENV, CONTROL_SCHEMA, MAX_FRAME_BYTES, RECEIPT_SCHEMA, SCOPE_ENV,
    decode_frame, encode_frame,
)


@dataclass(repr=False)
class RunnerChannel:
    source_root: Path
    runner_argv: tuple[str, ...]
    source_identity: str
    sdk_image_id: str
    challenge: str = field(default_factory=lambda: secrets.token_hex(32), repr=False)
    control: dict[str, Any] | None = field(default=None, init=False, repr=False)
    receipt: dict[str, Any] | None = field(default=None, init=False, repr=False)
    raw: bytearray = field(default_factory=bytearray, init=False, repr=False)
    eof: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.control_read, self.control_write = os.pipe()
        try:
            self.receipt_read, self.receipt_write = os.pipe()
            os.set_blocking(self.receipt_read, False)
        except BaseException:
            os.close(self.control_read)
            os.close(self.control_write)
            raise

    def _close(self, name: str) -> None:
        descriptor = getattr(self, name, -1)
        if descriptor >= 0:
            setattr(self, name, -1)
            os.close(descriptor)

    def child_settings(self) -> tuple[dict[str, str], tuple[int, ...]]:
        refuse(self.control is None, "runner_channel:already_started")
        settings = dict(zip(CHANNEL_ENV, (str(self.control_read), str(self.receipt_write))))
        settings.update(dict(zip(SCOPE_ENV, (self.source_identity, self.sdk_image_id))))
        return settings, (
            self.control_read, self.receipt_write)

    def started(self, pid: int, birth: float) -> None:
        refuse(self.control is None, "runner_channel:already_started")
        self._close("control_read")
        self._close("receipt_write")
        self.control = {"schema": CONTROL_SCHEMA, "challenge": self.challenge,
            "pid": pid, "birth": birth, "source_identity": self.source_identity,
            "source_root": str(self.source_root.resolve()), "argv": list(self.runner_argv[4:]),
            "sdk_image_id": self.sdk_image_id}
        try:
            scope = scope_sha256(self.control["argv"], self.control["source_root"],
                                 self.control["source_identity"], self.control["sdk_image_id"])
            raw = encode_control_v2(self.challenge, pid, birth, scope)
            refuse(len(raw) <= MAX_WIRE_BYTES, "runner_channel:control_bounds")
            os.set_blocking(self.control_write, False)
            try:
                written = os.write(self.control_write, raw)
            except BlockingIOError:
                raise RuntimeError("runner_channel:control_write_blocked") from None
            refuse(written == len(raw), "runner_channel:short_write")
        finally:
            self._close("control_write")

    def poll(self) -> dict[str, Any] | None:
        refuse(self.control is not None, "runner_channel:not_started")
        while not self.eof:
            try:
                chunk = os.read(self.receipt_read, MAX_FRAME_BYTES + 1 - len(self.raw))
            except BlockingIOError:
                return None
            if not chunk:
                self.eof = True
                self._close("receipt_read")
                break
            self.raw.extend(chunk)
            refuse(len(self.raw) <= MAX_FRAME_BYTES, "runner_channel:receipt_bounds")
        if self.receipt is None:
            if not self.raw:
                return None
            raw = bytes(self.raw)
            try:
                compact = decode_receipt_v2(raw)
            except ValueError:
                self.receipt = decode_frame(raw, receipt=True)
            else:
                refuse(self.control is not None, "runner_channel:control_missing")
                assert self.control is not None
                scope = scope_sha256(self.control["argv"], self.control["source_root"],
                                     self.control["source_identity"], self.control["sdk_image_id"])
                refuse(compact["challenge"] == self.control["challenge"],
                       "runner_channel:challenge_join")
                refuse(compact["scope_sha256"] == scope, "runner_channel:scope_join")
                refuse(compact["sdk_image_id"] == self.control["sdk_image_id"],
                       "runner_channel:image_join")
                full = dict(self.control, schema=RECEIPT_SCHEMA, cid=compact["cid"],
                            sdk_image_id=compact["sdk_image_id"],
                            marker_sha256=compact["marker_sha256"])
                self.receipt = decode_frame(encode_frame(full, receipt=True), receipt=True)
        return dict(self.receipt)

    def close(self) -> None:
        for name in ("control_read", "control_write", "receipt_read", "receipt_write"):
            self._close(name)
