"""Pure BODY host call-map and Docker reservation arithmetic.

The controller's frame waiters do not lease-poll: a lease is required only
when a bounded Docker/model authority hop or an emitted control frame is
accepted.  This module keeps that call map explicit so a cadence change cannot
silently reuse the old five-second source ceiling or stream allowance.
"""
from __future__ import annotations

from math import ceil, isfinite
from typing import Any, Final

from retrieval_body_study_contracts import (
    BODY_MODEL_POLL_SECONDS, MAX_DOCKER_OPERATIONS, MAX_DOCKER_READ_BYTES,
    MAX_DOCKER_STREAM_FORECAST_BYTES, MAX_MODEL_WORKERS,
    MAX_REQUEST_STREAM_BYTES, SOURCE_VERIFICATION_LIMIT, WORKER_MODEL_SECONDS,
)

_DOCKER_CAPTURE_OVERHEAD: Final = 65_536
_STREAM_SENTINEL: Final = 1

# Counts are the maintained call sites, not experimentally observed events.
# Model fixed guards cover initialization, admission, image/create/start,
# readiness, wait, response, artifact/source closure and response bundle.
MODEL_FIXED_GUARD_CALLS: Final = 105
MODEL_POLL_GUARDS_PER_POLL: Final = 2
EXCHANGE_OUTER_GUARD_CALLS: Final = 15
EPOCH_FRAME_ACK_OUTER_GUARD_CALLS: Final = 3
DIRECT_SOURCE_GUARD_CALLS: Final = 30

MODEL_FIXED_DOCKER_COMMANDS: Final = 10
EXCHANGE_DOCKER_COMMANDS_PER_EPOCH: Final = 3
EPOCH_ACK_DOCKER_OPERATION_CHARGES_PER_EPOCH: Final = 1
SETTLEMENT_DOCKER_COMMANDS: Final = 5

# These are conservative slots for SDK setup and boundary ownership calls.
# Each setup/boundary slot is bounded at the request-stream maximum.
# The canonical runner owns report transfer separately; it is not a host
# Docker-stream reservation and is forecast by the report readback term.
SDK_SETUP_STREAM_SLOTS: Final = 10
BOUNDARY_STREAM_SLOTS: Final = 15
REPORT_RECEIVER_STREAM_SLOTS: Final = 0


def _stream(limit: int) -> int:
    return 2 * (limit + _STREAM_SENTINEL) + _DOCKER_CAPTURE_OVERHEAD


def _term(limit: int, repeat: int) -> dict[str, int]:
    return {"limit_bytes": limit, "repeat": repeat, "reserved_bytes": repeat * _stream(limit)}


def _positive_int(name: str, value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"body host limit {name}")
    return value


def _positive_seconds(name: str, value: float) -> float:
    if type(value) not in (int, float) or not isfinite(value) or value <= 0:
        raise ValueError(f"body host limit {name}")
    return float(value)


def direct_fixed_stream_terms(*, workers: int = MAX_MODEL_WORKERS) -> dict[str, dict[str, int]]:
    """Return the fixed Docker stream terms before lease polls.

    The values sum to 43,868,482 bytes for two workers.  Keeping every limit
    and repetition visible prevents a future call-site from being hidden in a
    single allowance constant.
    """
    workers = _positive_int("workers", workers)
    terms = {
        "model.image_inspect": _term(256 * 1024, workers),
        "model.create": _term(4096, workers),
        "model.container_inspect": _term(512 * 1024, workers),
        "model.start": _term(4096, workers),
        "model.readiness_inspect": _term(512 * 1024, workers),
        "model.wait": _term(4096, workers),
        "model.cleanup_inspect": _term(512 * 1024, workers),
        "model.cleanup_stop": _term(4096, workers),
        "model.cleanup_remove": _term(4096, workers),
        "model.cleanup_absence_inspect": _term(4096, workers),
        "exchange.request_read": _term(MAX_REQUEST_STREAM_BYTES + 20 * 1024, 2),
        "exchange.admission_publish": _term(8192, 2),
        "exchange.response_publish": _term(8192, 2),
        "epoch.ack": _term(4096, 2),
        "settlement.sdk_inspect": _term(256 * 1024, 1),
        "settlement.marker": _term(128, 1),
        "settlement.stop": _term(4096, 1),
        "settlement.remove": _term(4096, 1),
        "settlement.absence_inspect": _term(4096, 1),
    }
    return terms


def _sum_terms(terms: dict[str, dict[str, int]]) -> int:
    return sum(item["reserved_bytes"] for item in terms.values())


def derive_body_host_limits(*, model_seconds: int = WORKER_MODEL_SECONDS,
                            model_poll_seconds: float = BODY_MODEL_POLL_SECONDS,
                            workers: int = MAX_MODEL_WORKERS,
                            source_limit: int = SOURCE_VERIFICATION_LIMIT,
                            operation_limit: int = MAX_DOCKER_OPERATIONS,
                            stream_limit: int = MAX_DOCKER_STREAM_FORECAST_BYTES) -> dict[str, Any]:
    """Derive bounded host guards, operations, and Docker stream reservations."""
    model_seconds = _positive_int("model_seconds", model_seconds)
    model_poll_seconds = _positive_seconds("model_poll_seconds", model_poll_seconds)
    workers = _positive_int("workers", workers)
    source_limit = _positive_int("source_limit", source_limit)
    operation_limit = _positive_int("operation_limit", operation_limit)
    stream_limit = _positive_int("stream_limit", stream_limit)
    polls = ceil(model_seconds / model_poll_seconds)
    outer_terms = {
        "model.fixed": workers * MODEL_FIXED_GUARD_CALLS,
        "model.poll": workers * MODEL_POLL_GUARDS_PER_POLL * polls,
        "exchange": workers * EXCHANGE_OUTER_GUARD_CALLS,
        "epoch.frame_and_ack": workers * EPOCH_FRAME_ACK_OUTER_GUARD_CALLS,
    }
    source_terms = {**outer_terms, "controller.direct_source_callbacks": DIRECT_SOURCE_GUARD_CALLS}
    outer_guards = sum(outer_terms.values())
    source_guards = sum(source_terms.values())
    if source_guards > source_limit:
        raise ValueError("body host source guard underreservation")

    operation_terms = {
        "runner.outer_lease": 2 * outer_guards,
        "model.fixed": workers * MODEL_FIXED_DOCKER_COMMANDS,
        "model.poll": workers * polls,
        "exchange": workers * EXCHANGE_DOCKER_COMMANDS_PER_EPOCH,
        "epoch.ack": workers * EPOCH_ACK_DOCKER_OPERATION_CHARGES_PER_EPOCH,
        "settlement": SETTLEMENT_DOCKER_COMMANDS,
    }
    operations = sum(operation_terms.values())
    if operations > operation_limit:
        raise ValueError("body host Docker operation underreservation")

    fixed_terms = direct_fixed_stream_terms(workers=workers)
    direct_fixed = _sum_terms(fixed_terms)
    dynamic_terms = {
        "runner.outer_lease": outer_guards * (_stream(256 * 1024) + _stream(128)),
        "model.poll": workers * polls * _stream(512 * 1024),
        "sdk_setup.allowance": SDK_SETUP_STREAM_SLOTS * MAX_REQUEST_STREAM_BYTES,
        "boundary.allowance": BOUNDARY_STREAM_SLOTS * MAX_REQUEST_STREAM_BYTES,
    }
    streams = direct_fixed + sum(dynamic_terms.values())
    if streams > stream_limit or streams > MAX_DOCKER_READ_BYTES:
        raise ValueError("body host Docker stream underreservation")
    return {
        "model_seconds": model_seconds,
        "model_poll_seconds": model_poll_seconds,
        "model_poll_count": polls,
        "workers": workers,
        "source_guard_terms": source_terms,
        "source_guard_calls": source_guards,
        "outer_guard_calls": outer_guards,
        "docker_operation_terms": operation_terms,
        "docker_operation_calls": operations,
        "direct_fixed_stream_terms": fixed_terms,
        "direct_fixed_stream_bytes": direct_fixed,
        "docker_stream_terms": dynamic_terms,
        "docker_stream_bytes": streams,
        "docker_stream_allowance_bytes": stream_limit,
        "docker_operation_allowance": operation_limit,
        "source_guard_allowance": source_limit,
    }


__all__ = ["BOUNDARY_STREAM_SLOTS", "DIRECT_SOURCE_GUARD_CALLS",
           "EPOCH_FRAME_ACK_OUTER_GUARD_CALLS", "EXCHANGE_OUTER_GUARD_CALLS",
           "MODEL_FIXED_GUARD_CALLS", "MODEL_POLL_GUARDS_PER_POLL",
           "REPORT_RECEIVER_STREAM_SLOTS", "SDK_SETUP_STREAM_SLOTS",
           "direct_fixed_stream_terms", "derive_body_host_limits"]
