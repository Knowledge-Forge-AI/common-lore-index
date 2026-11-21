"""Selector-drained bounded stdin/stdout/stderr for fixed Docker exec transports."""
from __future__ import annotations

import os
import selectors
import subprocess
import time

from retrieval_supplement_study_contracts import MAX_RESPONSE_STREAM_BYTES, refuse


def bounded_input(argv: tuple[str, ...], payload: bytes, *, timeout: float,
                  limit: int) -> subprocess.CompletedProcess[bytes]:
    refuse(argv[:3] == ("docker", "exec", "-i") and type(payload) is bytes
           and 0 < len(payload) <= MAX_RESPONSE_STREAM_BYTES + 1024**2
           and timeout > 0 and type(limit) is int and 0 <= limit <= 1024**2,
           "runner_input:invocation")
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, close_fds=True)
    assert child.stdin is not None and child.stdout is not None and child.stderr is not None
    selector = selectors.DefaultSelector()
    streams = (child.stdin, child.stdout, child.stderr)
    buffers = {child.stdout.fileno(): bytearray(), child.stderr.fileno(): bytearray()}
    output_fd, error_fd = child.stdout.fileno(), child.stderr.fileno()
    input_fd, sent = child.stdin.fileno(), 0
    by_descriptor = {stream.fileno(): stream for stream in streams}
    deadline = time.monotonic() + timeout
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_WRITE if stream is child.stdin else selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            refuse(remaining > 0, "runner_input:timeout")
            for key, _mask in selector.select(min(.1, remaining)):
                fd = key.fd
                if fd == input_fd:
                    try:
                        count = os.write(fd, payload[sent:sent + 65536])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        raise RuntimeError("runner_input:premature_stdin_close") from None
                    refuse(count > 0, "runner_input:short_write")
                    sent += count
                    if sent == len(payload):
                        selector.unregister(child.stdin)
                        child.stdin.close()
                else:
                    try:
                        raw = os.read(fd, min(65536, limit + 1 - len(buffers[fd])))
                    except BlockingIOError:
                        continue
                    if not raw:
                        selector.unregister(key.fileobj)
                        by_descriptor[fd].close()
                    else:
                        refuse(len(buffers[fd]) + len(raw) <= limit, "runner_input:output_bounds")
                        buffers[fd].extend(raw)
        refuse(sent == len(payload), "runner_input:incomplete_input")
        child.wait(timeout=max(.001, deadline - time.monotonic()))
        return subprocess.CompletedProcess(argv, child.returncode, bytes(buffers[output_fd]), bytes(buffers[error_fd]))
    finally:
        selector.close()
        if child.poll() is None:
            child.kill()
        try:
            child.wait(timeout=2)
        finally:
            for stream in streams:
                if not stream.closed:
                    stream.close()
