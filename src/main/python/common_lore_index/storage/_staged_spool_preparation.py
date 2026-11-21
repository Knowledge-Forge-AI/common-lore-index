"""Prepare one row family with the retained facade seams and owned iterators."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from functools import partial
from itertools import chain
from pathlib import Path

from common_lore_index.storage._preparation_iterators import (
    checked_preparation_rows, own_preparation_iterator,
)
from common_lore_index.storage._spool_cleanup import close_spooled_rows
from common_lore_index.storage.row_spool import RowSpool, _spool_row_byte_count
from common_lore_index.storage.staging_checksums import FamilyChecksum, _FamilyChecksumAccumulator
from common_lore_index.storage.staging_family_contracts import StageFamilyDescriptor
from common_lore_index.storage.staging_observability import StagingMeasurementCategory, StagingMeasurements

_ROW_SPOOL_BYTE_THRESHOLD = 1024 * 1024


@dataclass(frozen=True)
class _PreparedFamilyRows:
    rows: Iterable[dict[str, object]]
    checksum: FamilyChecksum | None = None
    checksum_elapsed_ns: int = 0


@dataclass(frozen=True, slots=True)
class _SpoolPreparation:
    descriptor: StageFamilyDescriptor
    measurements: StagingMeasurements | None
    artifact_observer: Callable[[str, int], None] | None
    directory: Path | str | None
    threshold: int
    spool_type: type[RowSpool]
    checksum_type: type[_FamilyChecksumAccumulator]
    byte_threshold: int = _ROW_SPOOL_BYTE_THRESHOLD


def prepare_family_rows(
    rows: Iterable[dict[str, object]], options: _SpoolPreparation,
    cancel_check: Callable[[], None] | None,
) -> _PreparedFamilyRows:
    if cancel_check is None:
        return _materialize_rows(rows, options, None)
    iterator = checked_preparation_rows(rows, cancel_check)
    prepared: _PreparedFamilyRows | None = None
    try:
        with own_preparation_iterator(iterator):
            prepared = _materialize_rows(iterator, options, cancel_check)
        return prepared
    except BaseException:
        if prepared is not None:
            close_spooled_rows((prepared.rows,), preserve_primary=True)
        raise


def _spool_observer(
    options: _SpoolPreparation, cancel_check: Callable[[], None] | None,
) -> Callable[[int], None] | None:
    observer = options.artifact_observer
    if cancel_check is None:
        return None if observer is None else lambda size: observer(options.descriptor.family, size)

    def observe(size: int) -> None:
        cancel_check()
        if observer is not None:
            observer(options.descriptor.family, size)

    return observe


def _timing_observer(options: _SpoolPreparation) -> Callable[[int], None] | None:
    measurements = options.measurements
    if measurements is None:
        return None
    return lambda elapsed: measurements.record_duration(
        StagingMeasurementCategory.FAMILY_SPOOL_WRITE, elapsed, family=options.descriptor.family,
    )


def _checksum_phase(options: _SpoolPreparation) -> AbstractContextManager[None]:
    if options.measurements is None:
        return nullcontext()
    return options.measurements.phase(f"staging.family_spool.{options.descriptor.family}")


def _buffer_until_spill(
    iterator: Iterator[dict[str, object]], threshold: int, byte_threshold: int,
) -> tuple[deque[dict[str, object]], bool]:
    buffered: deque[dict[str, object]] = deque()
    buffered_bytes = 0
    while True:
        try:
            row = next(iterator)
        except StopIteration:
            return buffered, False
        if len(buffered) >= threshold:
            buffered.append(row)
            return buffered, True
        try:
            row_bytes = _spool_row_byte_count(row)
        except (TypeError, ValueError):
            buffered.append(row)
            return buffered, True
        buffered_bytes += row_bytes
        buffered.append(row)
        if buffered_bytes > byte_threshold:
            return buffered, True


def _drain_buffered_rows(
    buffered: deque[dict[str, object]],
) -> Iterator[dict[str, object]]:
    while buffered:
        yield buffered.popleft()


def _materialize_rows(
    rows: Iterable[dict[str, object]], options: _SpoolPreparation,
    cancel_check: Callable[[], None] | None,
) -> _PreparedFamilyRows:
    iterator = iter(rows)
    buffered, spilled = _buffer_until_spill(
        iterator, options.threshold, options.byte_threshold,
    )
    if not spilled:
        return _PreparedFamilyRows(tuple(buffered))
    accumulator = options.checksum_type(options.descriptor.identity_columns)
    checksum_elapsed_ns = 0

    def rows_with_checksum() -> Iterator[dict[str, object]]:
        nonlocal checksum_elapsed_ns
        try:
            for row in chain(_drain_buffered_rows(buffered), iterator):
                if cancel_check is not None:
                    cancel_check()
                checksum_row = {key: value for key, value in row.items() if key != "stage_id"}
                if options.measurements is None:
                    accumulator.add(checksum_row)
                else:
                    _, elapsed = options.measurements.measure_elapsed(
                        partial(accumulator.add, checksum_row),
                    )
                    checksum_elapsed_ns += elapsed
                if cancel_check is not None:
                    cancel_check()
                yield row
        finally:
            buffered.clear()

    spool: RowSpool | None = None
    try:
        with _checksum_phase(options):
            checksum_rows = rows_with_checksum()
            with (
                nullcontext(checksum_rows) if cancel_check is None
                else own_preparation_iterator(checksum_rows)
            ):
                spool = options.spool_type.from_rows(
                    checksum_rows, dir=options.directory,
                    artifact_observer=_spool_observer(options, cancel_check),
                    timing_observer=_timing_observer(options),
                )
            if options.measurements is None:
                checksum = accumulator.finish()
            else:
                checksum, elapsed = options.measurements.measure_elapsed(accumulator.finish)
                checksum_elapsed_ns += elapsed
        return _PreparedFamilyRows(spool, checksum, checksum_elapsed_ns)
    except BaseException:
        if spool is not None:
            close_spooled_rows((spool,), preserve_primary=True)
        raise
