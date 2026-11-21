"""Explicit bounded exec-i transfer into a single disposable SDK namespace."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from typing import Any

from runner_pgvector_budget import Allocation, MAX_ARCHIVE, private_file, refuse
from runner_pgvector_prebuilt import json_bytes, strict_object

ROOT = Path("/sandbox-scratch/pgvector")
NAMES = {"image.tar", "plan.json"}
BLOCK = 1024 * 1024


def require_receiver_namespace() -> None:
    from test_sandbox_contract import (
        ENV_ACTIVE, ENV_TOKEN, INNER_OWNER_PATH, active_sandbox,
    )
    private_file(INNER_OWNER_PATH)
    token = INNER_OWNER_PATH.read_text().strip()
    refuse(active_sandbox(os.environ | {ENV_ACTIVE: "1", ENV_TOKEN: token},
        marker_path=INNER_OWNER_PATH, host_socket_exists=None, inner_socket_exists=None),
        "unauthenticated receiver namespace")


def receive(name: str, size: int, expected: str) -> None:
    """Receive data only; no extraction, installation, daemon or target execution."""
    require_receiver_namespace()
    refuse(name in NAMES and type(size) is int and 0 < size <= MAX_ARCHIVE,
           "receive path/size")
    refuse(len(expected) == 64 and all(c in "0123456789abcdef" for c in expected),
           "receive digest shape")
    refuse(ROOT.is_dir() and not ROOT.is_symlink() and ROOT.stat().st_uid == os.getuid()
           and ROOT.stat().st_mode & 0o777 == 0o700, "receive directory")
    destination = ROOT / name
    deadline = time.monotonic() + 300
    hasher, count = hashlib.sha256(), 0
    with destination.open("xb") as output:
        destination.chmod(0o600)
        while True:
            remaining = deadline - time.monotonic()
            refuse(remaining > 0, "receive timeout")
            ready, _, _ = select.select([sys.stdin.buffer], [], [], remaining)
            refuse(bool(ready), "receive timeout")
            raw = os.read(sys.stdin.fileno(), min(BLOCK, size + 1 - count))
            if not raw:
                break
            count += len(raw)
            refuse(count <= size, "receive overlength")
            output.write(raw)
            hasher.update(raw)
        output.flush()
        os.fsync(output.fileno())
    refuse(count == size and hasher.hexdigest() == expected, "receive identity mismatch")
    print(json_bytes({"bytes": count, "sha256": hasher.hexdigest()}).decode(), end="", flush=True)


def send_file(container: str, source: Path, name: str, descriptor: dict[str, Any],
              meter: Allocation) -> None:
    """Reserve the receiver pass, then stream/hash this exact parent-owned file."""
    private_file(source)
    size, expected = descriptor["bytes"], descriptor["sha256"]
    refuse(name in NAMES and 0 < size <= MAX_ARCHIVE and source.stat().st_size == size,
           "transfer descriptor")
    # The child receive and Docker-load passes are counted separately. No
    # implicit bind or shared-cache identity is assumed.
    meter.charge("read_bytes", size + 1)
    deadline = time.monotonic() + 300
    command = ["docker", "exec", "-i", container, "python3",
               "/workspace/tools/runner_pgvector_transport.py", "receive", name,
               str(size), expected]
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    pipe = child.stdin
    if pipe is None:
        raise RuntimeError("pgvector prebuilt refused: transfer stdin")
    os.set_blocking(pipe.fileno(), False)
    hasher = hashlib.sha256()
    count = 0
    try:
        with source.open("rb") as input_stream:
            before = os.fstat(input_stream.fileno())
            while count < size:
                requested = min(BLOCK, size - count)
                raw = meter.read(input_stream.read, requested, transfer=True)
                refuse(len(raw) == requested, "short transfer source")
                count += len(raw)
                hasher.update(raw)
                offset = 0
                while offset < len(raw):
                    remaining = deadline - time.monotonic()
                    refuse(remaining > 0, "transfer timeout")
                    _, ready, _ = select.select([], [pipe], [], remaining)
                    refuse(bool(ready), "transfer timeout")
                    try:
                        offset += os.write(pipe.fileno(), raw[offset:offset + 65536])
                    except BlockingIOError:
                        continue
            after = os.fstat(input_stream.fileno())
        refuse((before.st_ino, before.st_size, before.st_mtime_ns) ==
               (after.st_ino, after.st_size, after.st_mtime_ns)
               and hasher.hexdigest() == expected, "transfer source changed")
        pipe.close()
        child.stdin = None
        out, error = finish_receipt(child, deadline)
        refuse(child.returncode == 0, "transfer receiver failed")
        refuse(strict_object(out) == {"bytes": size, "sha256": expected},
               "receiver receipt mismatch")
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        for pipe in (child.stdin, child.stdout, child.stderr):
            if pipe is not None:
                pipe.close()


def finish_receipt(child: subprocess.Popen[bytes], deadline: float) -> tuple[bytes, bytes]:
    assert child.stdout is not None and child.stderr is not None
    results = {child.stdout: bytearray(), child.stderr: bytearray()}
    active = set(results)
    while active:
        remaining = deadline - time.monotonic()
        refuse(remaining > 0, "receiver receipt timeout")
        ready, _, _ = select.select(list(active), [], [], remaining)
        refuse(bool(ready), "receiver receipt timeout")
        for pipe in ready:
            raw = os.read(pipe.fileno(), 16385 - len(results[pipe]))
            if not raw:
                active.remove(pipe)
            else:
                results[pipe].extend(raw)
                refuse(len(results[pipe]) <= 16384, "receiver receipt cap")
    child.wait(timeout=max(0.1, deadline - time.monotonic()))
    return bytes(results[child.stdout]), bytes(results[child.stderr])


def send_plan(container: str, plan: dict[str, Any], destination: Path,
              meter: Allocation) -> None:
    raw = json_bytes(plan)
    refuse(len(raw) <= 2 * 1024**2, "inner plan cap")
    with destination.open("xb") as output:
        output.write(raw)
    destination.chmod(0o600)
    send_file(container, destination, "plan.json", {"bytes": len(raw),
              "sha256": hashlib.sha256(raw).hexdigest()}, meter)


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "receive":
        raise SystemExit("Only the fixed data receiver is available")
    try:
        receive(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    except (OSError, ValueError, RuntimeError):
        raise SystemExit("pgvector prebuilt data receiver refused") from None
