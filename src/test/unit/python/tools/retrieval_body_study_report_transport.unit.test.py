"""BODY export growth, original-byte retention and unchanged legacy cap."""
from contextlib import nullcontext
from io import BytesIO
from types import SimpleNamespace
from typing import cast
from runner_pgvector_context import OfflineContext

import pytest
import runner_body_study_sandbox as body
import runner_pgvector_sandbox as legacy


def _adapter(monkeypatch, raw, status=0):
    calls, reservations = [], []
    def popen(argv, **kwargs):
        calls.append(tuple(argv))
        return SimpleNamespace(stdout=BytesIO(raw), wait=lambda **kw: status,
                               poll=lambda: status, kill=lambda: None)
    class Reader:
        def __init__(self, process, maximum):
            self.process, self.remaining = process, maximum
        @property
        def stdout(self):
            return self
        def read(self, amount):
            if self.remaining <= 0:
                raise RuntimeError("bounded synthetic report cap")
            block = self.process.stdout.read(min(amount, self.remaining))
            self.remaining -= len(block)
            return block
        def wait(self):
            return self.process.wait()
        def poll(self):
            return self.process.poll()
        def kill(self):
            self.process.kill()
    class Meter:
        def read(self, operation, maximum, *, transfer=False):
            reservations.append((maximum, transfer))
            value = operation(maximum)
            assert len(value) <= maximum
            return value
    monkeypatch.setattr(body.subprocess, "Popen", popen)
    monkeypatch.setattr(legacy, "_ReportProcess", Reader)
    subject = object.__new__(body.BodyOfflineSandbox)
    subject.context = cast(OfflineContext, SimpleNamespace(fixed=lambda: None,
        allocation=lambda: SimpleNamespace(session=lambda: nullcontext(Meter()))))
    return subject, calls, reservations


def test_body_archive_preserves_originals_above_old_four_mib_cap(monkeypatch):
    raw = b"sealed-original-observation\n" * 200000
    assert len(raw) > 4 * 1024**2
    subject, calls, reservations = _adapter(monkeypatch, raw)
    assert subject.read_report_archive("c" * 64, True) == raw
    assert reservations == [(72 * 1024**2, True)]
    assert calls == [("docker", "cp", "c" * 64 + ":/sandbox-report/report/.", "-")]


def test_legacy_adapter_retains_its_existing_four_mib_limit(monkeypatch):
    subject, _calls, reservations = _adapter(monkeypatch, b"x" * (5 * 1024**2))
    with pytest.raises(RuntimeError, match="cap"):
        legacy.OfflineSandbox.read_report_archive(subject, "c" * 64, True)
    assert reservations == [(4 * 1024**2, True)]


@pytest.mark.parametrize("raw,allowed", [(b"", True), (b"partial-original", False)])
def test_optional_failure_cannot_discard_already_read_originals(monkeypatch, raw, allowed):
    subject, _calls, reservations = _adapter(monkeypatch, raw, status=1)
    if allowed:
        assert subject.read_report_archive("c" * 64, False) is None
    else:
        with pytest.raises(RuntimeError, match="consumed bytes"):
            subject.read_report_archive("c" * 64, False)
    assert reservations == [(72 * 1024**2, True)]


def test_body_report_guard_precedes_any_foreign_reader(monkeypatch):
    subject, calls, reservations = _adapter(monkeypatch, b"unread")
    def refuse():
        raise RuntimeError("source changed")
    subject.context.fixed = refuse
    with pytest.raises(RuntimeError, match="source changed"):
        subject.read_report_archive("c" * 64, True)
    assert not calls and not reservations
