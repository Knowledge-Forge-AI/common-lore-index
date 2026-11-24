"""SCALE stage transition and COPY completeness checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from common_lore_index.storage.errors import StorageSchemaError
from common_lore_index.storage.staging_family_contracts import STAGING_FAMILY_DESCRIPTORS
from common_lore_index.storage.staging_observability import StagingMeasurements


_FAMILY_COUNTS = {
    family: f"SELECT count(*) FROM {descriptor.stage_table} WHERE stage_id = %s"
    for family, descriptor in STAGING_FAMILY_DESCRIPTORS.items()
}
_ALL_FAMILY_COUNTS = "SELECT " + ", ".join(f"({sql})" for sql in _FAMILY_COUNTS.values())


def mark_validating(connection: Any, stage_id: str) -> None:
    """Move a prepared stage into set-based validation."""

    cursor = connection.execute(
        """
UPDATE ingestion_stages
SET state = 'validating', validation_status = 'running', updated_at = now()
WHERE stage_id = %s AND state = 'prepared'
""",
        (stage_id,),
    )
    if cursor.rowcount != 1:
        raise StorageSchemaError("staged validating transition failed")


def validate_stage(
    connection: Any,
    stage_id: str,
    expected_counts: Mapping[str, int],
    *,
    staging_measurements: StagingMeasurements | None = None,
) -> None:
    """Verify every typed stage table contains its expected COPY rows."""

    if staging_measurements is None:
        # One statement keeps the counts in the same database snapshot and
        # avoids a round trip for each family on ordinary refreshes.
        row = connection.execute(_ALL_FAMILY_COUNTS, (stage_id,) * len(_FAMILY_COUNTS)).fetchone()
        expected = tuple(expected_counts[family] for family in _FAMILY_COUNTS)
        if row is None or any(type(count) is not int for count in row) or row != expected:
            raise StorageSchemaError("staged family completeness failed")
        return

    for family, sql in _FAMILY_COUNTS.items():
        with staging_measurements.operation(f"completeness.{family}"):
            row = connection.execute(sql, (stage_id,)).fetchone()
        if row is None or len(row) != 1 or type(row[0]) is not int or row[0] != expected_counts[family]:
            raise StorageSchemaError("staged family completeness failed")


def mark_validated(connection: Any, stage_id: str) -> None:
    """Record successful set-based validation."""

    cursor = connection.execute(
        """
UPDATE ingestion_stages
SET state = 'validated', validation_status = 'passed', updated_at = now()
WHERE stage_id = %s AND state = 'validating'
""",
        (stage_id,),
    )
    if cursor.rowcount != 1:
        raise StorageSchemaError("staged validated transition failed")
