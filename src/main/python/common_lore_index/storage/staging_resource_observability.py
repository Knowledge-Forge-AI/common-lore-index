"""Bounded aggregate resource sampling for staged ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import resource
import sys
from typing import Any

import psycopg

from common_lore_index.storage.staging_observability import (
    StagingMeasurementCategory,
    StagingMeasurements,
)


@dataclass(frozen=True)
class StagingResourceSnapshot:
    """Private intermediate values used only to derive aggregate deltas."""

    wal_position: int | None
    temporary_bytes: int | None
    postgresql_memory_bytes: int | None
    temporary_counter_identity: tuple[int, datetime | None] | None = None


def capture_staging_resources(connection: Any) -> StagingResourceSnapshot:
    """Capture bounded current-database and current-backend aggregate values."""

    database_row = _safe_row(
        connection,
        """
SELECT pg_current_wal_insert_lsn()::text, temp_bytes, datid, stats_reset
FROM pg_stat_database
WHERE datname = current_database()
""",
    )
    memory_row = _safe_row(
        connection,
        "SELECT coalesce(sum(total_bytes), 0) FROM pg_backend_memory_contexts",
    )
    wal_position: int | None = None
    temporary_bytes: int | None = None
    temporary_counter_identity: tuple[int, datetime | None] | None = None
    if database_row is not None and len(database_row) == 4:
        wal_position = _parse_wal_position(database_row[0])
        temporary_bytes = _nonnegative_int(database_row[1])
        database_id, reset = database_row[2:]
        if type(database_id) is int and database_id > 0 and (reset is None or isinstance(reset, datetime)):
            temporary_counter_identity = (database_id, reset)
    postgresql_memory_bytes = None
    if memory_row is not None and len(memory_row) == 1:
        postgresql_memory_bytes = _nonnegative_int(memory_row[0])
    return StagingResourceSnapshot(
        wal_position,
        temporary_bytes,
        postgresql_memory_bytes,
        temporary_counter_identity,
    )


def emit_staging_resource_measurements(
    measurements: StagingMeasurements,
    before: StagingResourceSnapshot,
    after: StagingResourceSnapshot,
) -> None:
    """Emit upper-bound deltas and safely available memory aggregates."""

    _record_delta(
        measurements,
        StagingMeasurementCategory.WAL_UPPER_BOUND,
        before.wal_position,
        after.wal_position,
    )
    temporary_identity = before.temporary_counter_identity
    same_temporary_counter = (
        temporary_identity is not None and temporary_identity == after.temporary_counter_identity
    )
    _record_delta(
        measurements,
        StagingMeasurementCategory.TEMPORARY_BYTE_UPPER_BOUND,
        before.temporary_bytes,
        after.temporary_bytes if same_temporary_counter else None,
    )
    memory_values = tuple(
        value
        for value in (
            before.postgresql_memory_bytes,
            after.postgresql_memory_bytes,
        )
        if value is not None
    )
    if memory_values:
        measurements.record_bytes(
            StagingMeasurementCategory.POSTGRESQL_MEMORY,
            max(memory_values),
        )
    else:
        measurements.record_unavailable(
            StagingMeasurementCategory.POSTGRESQL_MEMORY
        )
    client_memory = _client_peak_rss_bytes()
    if client_memory is None:
        measurements.record_unavailable(StagingMeasurementCategory.CLIENT_MEMORY)
    else:
        measurements.record_bytes(StagingMeasurementCategory.CLIENT_MEMORY, client_memory)


def _safe_row(connection: Any, query: str) -> tuple[object, ...] | None:
    try:
        with connection.transaction():
            row = connection.execute(query).fetchone()
    except psycopg.Error:
        return None
    if row is None:
        return None
    return tuple(row)


_HEX_DIGITS = frozenset("0123456789ABCDEFabcdef")


def _parse_wal_position(value: object) -> int | None:
    if not isinstance(value, str) or len(value) > 17:
        return None
    high, _, low = value.partition("/")
    if not (
        1 <= len(high) <= 8 and 1 <= len(low) <= 8
        and _HEX_DIGITS.issuperset(high) and _HEX_DIGITS.issuperset(low)
    ):
        return None
    return (int(high, 16) << 32) + int(low, 16)


def _nonnegative_int(value: Any) -> int | None:
    if type(value) is int:
        return value if value >= 0 else None
    # PostgreSQL SUM(bigint) is NUMERIC through Psycopg. Preserve exact
    # integral samples and refuse values that would be rounded or coerced.
    if isinstance(value, Decimal) and value.is_finite() and value >= 0 and value == value.to_integral_value():
        return int(value)
    return None


def _record_delta(
    measurements: StagingMeasurements,
    category: StagingMeasurementCategory,
    before: int | None,
    after: int | None,
) -> None:
    if before is None or after is None or after < before:
        measurements.record_unavailable(category)
        return
    measurements.record_bytes(category, after - before)


def _client_peak_rss_bytes() -> int | None:
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
    except OSError:
        return None
    peak = int(usage.ru_maxrss)
    if sys.platform != "darwin":
        peak *= 1024
    return max(0, peak)
