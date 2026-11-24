"""Real pipe-pressure, EOF and bounded-output proof with stdlib-only children."""
from __future__ import annotations

import subprocess
import sys

import pytest

import retrieval_supplement_runner_input as owner


def redirect(monkeypatch, program):
    original = subprocess.Popen
    children = []
    def launch(argv, **kwargs):
        assert argv[:3] == ("docker", "exec", "-i")
        child = original([sys.executable, "-I", "-B", "-c", program], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(owner.subprocess, "Popen", launch)
    return children


def command():
    return ("docker", "exec", "-i", "a" * 64, "python3", "-I", "-B", "-c", "fixed")


def test_pipe_pressure_on_both_outputs_does_not_deadlock_input(monkeypatch):
    children = redirect(monkeypatch,
        "import hashlib,sys;sys.stdout.buffer.write(b'o'*65536);sys.stdout.buffer.flush();"
        "sys.stderr.buffer.write(b'e'*65536);sys.stderr.buffer.flush();"
        "raw=sys.stdin.buffer.read();print(hashlib.sha256(raw).hexdigest())")
    payload = b"public-input" * 100000
    result = owner.bounded_input(command(), payload, timeout=10., limit=128 * 1024)
    import hashlib
    assert result.returncode == 0 and result.stdout.endswith(hashlib.sha256(payload).hexdigest().encode() + b"\n")
    assert result.stderr == b"e" * 65536
    assert len(children) == 1 and children[0].poll() == 0


def test_input_eof_is_observed_before_reader_exits(monkeypatch):
    children = redirect(monkeypatch, "import sys;raw=sys.stdin.buffer.read();print(len(raw))")
    result = owner.bounded_input(command(), b"public", timeout=5., limit=64)
    assert result.stdout == b"6\n" and result.returncode == 0
    assert children[0].poll() == 0


def test_output_overflow_closes_only_the_created_child(monkeypatch):
    children = redirect(monkeypatch, "import sys,time;sys.stdout.write('x'*5000);sys.stdout.flush();time.sleep(30)")
    with pytest.raises(ValueError, match="output_bounds"):
        owner.bounded_input(command(), b"public", timeout=5., limit=128)
    assert len(children) == 1 and children[0].poll() is not None


def test_timeout_is_bounded_and_reaps_the_created_process(monkeypatch):
    children = redirect(monkeypatch, "import time;time.sleep(30)")
    with pytest.raises(ValueError, match="timeout"):
        owner.bounded_input(command(), b"public", timeout=.1, limit=128)
    assert len(children) == 1 and children[0].poll() is not None


@pytest.mark.parametrize("argv,payload,timeout,limit", [
    (("docker", "run", "-i"), b"public", 1., 128),
    (("docker", "exec", "-i"), b"", 1., 128),
    (("docker", "exec", "-i"), b"public", 0., 128),
    (("docker", "exec", "-i"), b"public", 1., 1024**2 + 1),
])
def test_invalid_transport_refuses_before_start(monkeypatch, argv, payload, timeout, limit):
    children = redirect(monkeypatch, "raise SystemExit(0)")
    with pytest.raises(ValueError, match="invocation"):
        owner.bounded_input(argv, payload, timeout=timeout, limit=limit)
    assert not children
