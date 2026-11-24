"""The worker clock starts after ACK, with full pre-request preparation room."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from retrieval_body_study_contracts import BODY_INPUT_PIN_SHA256, BodyStudyRefusal
from retrieval_body_study_epoch import EPOCH_SCHEMA, parse_epoch
import retrieval_body_study_host_epoch as subject


def _epoch():
    return parse_epoch({'schema': EPOCH_SCHEMA, 'attempt_id': 'unit-body', 'ordinal': 1,
        'representation': 'canonical-node-v1', 'nonce': 'a' * 64,
        'request_attempt_id': 'unit-body-v1', 'input_pin_sha256': BODY_INPUT_PIN_SHA256,
        'evaluator_closure_sha256': 'sha256:' + 'b' * 64})


def test_initial_preparation_does_not_borrow_worker_readiness_time():
    assert subject.epoch_frame_window(1) == 710
    assert subject.epoch_frame_window(2) == 120


@pytest.mark.parametrize('ordinal', [True, False, 0, 3, '1'])
def test_unrecognized_epoch_never_gets_a_time_window(ordinal):
    with pytest.raises(BodyStudyRefusal):
        subject.epoch_frame_window(ordinal)


def test_acknowledgement_time_precedes_the_returned_worker_start(monkeypatch):
    now = [100.0]
    calls = []
    lease = SimpleNamespace(require_outer=lambda: 'c' * 64)
    meter = SimpleNamespace(deadline=8000.0, read=lambda n: None, operation=lambda label: None)

    def acknowledge(epoch, **kwargs):
        calls.append(epoch)
        assert kwargs['require_outer']() == 'c' * 64
        assert kwargs['deadline'] == 220.0
        now[0] = 250.0

    monkeypatch.setattr(subject, 'publish_epoch_ack', acknowledge)
    epoch = _epoch()
    assert subject.acknowledged_epoch_start(epoch, lease=lease, meter=meter,
        clock=lambda: now[0]) == 250.0
    assert calls == [epoch]


def test_ack_failure_cannot_return_a_worker_start(monkeypatch):
    def failed(*args, **kwargs):
        raise RuntimeError('ack_refused')

    monkeypatch.setattr(subject, 'publish_epoch_ack', failed)
    lease = SimpleNamespace(require_outer=lambda: 'c' * 64)
    meter = SimpleNamespace(deadline=8000.0, read=lambda n: None, operation=lambda label: None)
    with pytest.raises(RuntimeError, match='ack_refused'):
        subject.acknowledged_epoch_start(_epoch(), lease=lease, meter=meter, clock=lambda: 100.0)
