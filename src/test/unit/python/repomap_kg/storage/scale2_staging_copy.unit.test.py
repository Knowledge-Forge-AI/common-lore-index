from __future__ import annotations

from collections.abc import Iterable, Mapping
from inspect import GEN_CLOSED, GEN_SUSPENDED, getgeneratorstate
from pathlib import Path

import pytest

from common_lore_index.storage.errors import StorageSchemaError
from common_lore_index.storage.row_spool import RowSpool
from common_lore_index.storage.staging_copy import (
    STAGING_COPY_TABLES,
    CopyContractError,
    CopyTransferResult,
    StagingCopyTable,
    _prepared_copy_rows,
    copy_stage_rows,
)


def stage_file_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "stage_id": "stage-001",
        "family_ordinal": 0,
        "path": "fixture/readme.md",
        "language": "markdown",
        "role": "documentation",
        "confidence": "extracted",
        "content_hash": None,
        "executable": False,
        "generated": False,
        "metadata_json": {"title": "fixture"},
    }
    row.update(overrides)
    return row


class FakeCopy:
    def __init__(self) -> None:
        self.rows: list[tuple[object, ...]] = []
        self.exit_exception: BaseException | None = None

    def __enter__(self) -> "FakeCopy":
        return self

    def __exit__(self, _type: object, value: BaseException | None, _traceback: object) -> None:
        self.exit_exception = value

    def write_row(self, row: tuple[object, ...]) -> None:
        self.rows.append(row)


class FakeCursor:
    def __init__(self) -> None:
        self.copy_operation = FakeCopy()
        self.statement: object | None = None
        self.exit_exception: BaseException | None = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, _type: object, value: BaseException | None, _traceback: object) -> None:
        self.exit_exception = value

    def copy(self, statement: object) -> FakeCopy:
        self.statement = statement
        return self.copy_operation


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.commit_calls = 0
        self.rollback_calls = 0

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def test_catalog_covers_only_the_seven_active_stage_families() -> None:
    assert set(STAGING_COPY_TABLES) == {
        "files",
        "raw_observations",
        "canonical_nodes",
        "canonical_edges",
        "canonical_evidence",
        "canonical_node_evidence",
        "canonical_edge_evidence",
    }
    assert all(table.table_name.startswith("stage_") for table in STAGING_COPY_TABLES.values())
    assert all("stage_id" in table.columns for table in STAGING_COPY_TABLES.values())
    assert all("files" != table.table_name for table in STAGING_COPY_TABLES.values())


def test_catalog_rejects_unlisted_staging_targets() -> None:
    with pytest.raises(CopyContractError):
        StagingCopyTable(
            family="files",
            table_name="stage_custom",
            ordinal_column="family_ordinal",
            column_types={"stage_id": "text", "family_ordinal": "integer"},
        )


def test_row_adaptation_has_closed_column_order_and_jsonb_wrapping() -> None:
    table = STAGING_COPY_TABLES["files"]
    adapted = table.adapt_row(stage_file_row(), expected_stage_id="stage-001")

    assert len(adapted) == len(table.columns)
    assert adapted[:6] == (
        "stage-001",
        0,
        "fixture/readme.md",
        "markdown",
        "documentation",
        "extracted",
    )
    assert adapted[6] is None
    assert adapted[7:9] == (False, False)
    assert getattr(adapted[9], "obj", None) == {"title": "fixture"}


@pytest.mark.parametrize(
    "row",
    [
        stage_file_row(stage_id="stage-002"),
        stage_file_row(family_ordinal=True),
        stage_file_row(family_ordinal=-1),
        stage_file_row(path=42),
        {"stage_id": "stage-001"},
        {**stage_file_row(), "unexpected": "value"},
    ],
)
def test_row_adaptation_rejects_ownership_and_type_errors(
    row: Mapping[str, object],
) -> None:
    with pytest.raises(CopyContractError):
        STAGING_COPY_TABLES["files"].adapt_row(row, expected_stage_id="stage-001")


def test_copy_transport_streams_rows_without_owning_transaction() -> None:
    connection = FakeConnection()
    result = copy_stage_rows(
        connection,
        STAGING_COPY_TABLES["files"],
        (stage_file_row(), stage_file_row(family_ordinal=1)),
        expected_stage_id="stage-001",
    )

    assert result == CopyTransferResult(family="files", row_count=2)
    assert len(connection.cursor_instance.copy_operation.rows) == 2
    assert connection.commit_calls == 0
    assert connection.rollback_calls == 0
    assert connection.cursor_instance.copy_operation.exit_exception is None


def test_copy_transport_propagates_source_failure_and_does_not_rollback() -> None:
    connection = FakeConnection()

    def rows() -> Iterable[Mapping[str, object]]:
        yield stage_file_row()
        raise RuntimeError("synthetic cancellation")

    with pytest.raises(RuntimeError, match="synthetic cancellation"):
        copy_stage_rows(
            connection,
            STAGING_COPY_TABLES["files"],
            rows(),
            expected_stage_id="stage-001",
        )

    assert len(connection.cursor_instance.copy_operation.rows) == 1
    assert connection.cursor_instance.copy_operation.exit_exception is not None
    assert connection.commit_calls == 0
    assert connection.rollback_calls == 0


@pytest.mark.parametrize("invalid_json", [
    {"nested": [float("nan")]},
    {"nested": [float("inf")]},
    {"nested": [{1: "non-string key"}]},
    {"nested": [object()]},
])
def test_copy_rejects_nested_json_after_partial_transfer_without_transaction_ownership(
    invalid_json: object,
) -> None:
    connection = FakeConnection()
    cursor = connection.cursor_instance
    table = STAGING_COPY_TABLES["files"]
    with pytest.raises(CopyContractError, match="staging COPY JSON value is invalid") as caught:
        copy_stage_rows(
            connection, table,
            (stage_file_row(), stage_file_row(family_ordinal=1, metadata_json=invalid_json)),
            expected_stage_id="stage-001",
        )
    assert len(cursor.copy_operation.rows) == 1
    assert cursor.copy_operation.rows[0][table.columns.index("path")] == "fixture/readme.md"
    assert cursor.copy_operation.exit_exception is caught.value
    assert cursor.exit_exception is caught.value
    assert connection.commit_calls == connection.rollback_calls == 0


def test_copy_adapts_nested_json_without_changing_caller_rows() -> None:
    metadata = {"nested": [None, True, 1, 1.25, ("value", {"key": "text"})]}
    row = stage_file_row(metadata_json=metadata)
    connection = FakeConnection()
    result = copy_stage_rows(
        connection, STAGING_COPY_TABLES["files"], (row,), expected_stage_id="stage-001",
    )
    adapted = connection.cursor_instance.copy_operation.rows[0]
    assert getattr(adapted[-1], "obj") == metadata
    assert row["metadata_json"] is metadata
    assert metadata == {"nested": [None, True, 1, 1.25, ("value", {"key": "text"})]}
    assert result.row_count == 1
    assert connection.commit_calls == connection.rollback_calls == 0


def test_copy_rejects_uncatalogued_table_before_acquiring_cursor() -> None:
    from dataclasses import replace

    connection = FakeConnection()
    uncatalogued = replace(STAGING_COPY_TABLES["files"])
    with pytest.raises(CopyContractError, match="staging COPY table is invalid"):
        copy_stage_rows(connection, uncatalogued, (), expected_stage_id="stage-001")
    assert connection.cursor_instance.statement is None
    assert connection.cursor_instance.copy_operation.rows == []
    assert connection.commit_calls == connection.rollback_calls == 0


@pytest.mark.parametrize("spooled", [False, True])
@pytest.mark.parametrize(("available", "expected", "transferred"), [
    (0, 0, 0), (2, 2, 2), (1, 2, 1), (3, 1, 1), (1, 0, 0),
])
def test_prepared_copy_admits_only_the_declared_prefix(
    tmp_path: Path, spooled: bool, available: int, expected: int, transferred: int,
) -> None:
    files = [stage_file_row(family_ordinal=index) for index in range(available)]
    spool = RowSpool.from_rows(files, dir=tmp_path) if spooled else None
    connection = FakeConnection()
    operation = connection.cursor_instance.copy_operation
    rows = files if spool is None else spool
    try:
        if available == expected:
            with _prepared_copy_rows(rows, expected) as bounded:
                assert copy_stage_rows(connection, STAGING_COPY_TABLES["files"], bounded,
                                       expected_stage_id="stage-001").row_count == transferred
            assert operation.exit_exception is None
            assert spool is None or not spool.path.exists()
        else:
            with pytest.raises(StorageSchemaError, match="staged family completeness failed") as caught:
                with _prepared_copy_rows(rows, expected) as bounded:
                    copy_stage_rows(connection, STAGING_COPY_TABLES["files"], bounded,
                                    expected_stage_id="stage-001")
            assert operation.exit_exception is caught.value
            assert spool is None or spool.path.exists()
        assert len(operation.rows) == transferred
        assert connection.commit_calls == connection.rollback_calls == 0
    finally:
        if spool is not None:
            spool.close()


def test_prepared_copy_closes_retained_spool_iterator_on_written_prefix_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spool = RowSpool.from_rows((stage_file_row(), stage_file_row(family_ordinal=1)), dir=tmp_path)
    reader = spool.__iter__()
    monkeypatch.setattr(RowSpool, "__iter__", lambda _self: reader)
    connection = FakeConnection()
    error = OSError("synthetic COPY output failure")

    def fail_write(_row: object) -> None:
        assert getgeneratorstate(reader) == GEN_SUSPENDED
        raise error

    monkeypatch.setattr(connection.cursor_instance.copy_operation, "write_row", fail_write)
    try:
        with pytest.raises(OSError) as caught:
            with _prepared_copy_rows(spool, 2) as bounded:
                copy_stage_rows(connection, STAGING_COPY_TABLES["files"], bounded,
                                expected_stage_id="stage-001")
        assert caught.value is error and caught.value.__traceback__ is not None
        assert getgeneratorstate(reader) == GEN_CLOSED
        assert spool.path.exists()
    finally:
        reader.close()
        spool.close()


def test_prepared_copy_does_not_close_a_caller_owned_nonspool_iterator() -> None:
    rows = (stage_file_row(family_ordinal=ordinal) for ordinal in range(3))
    connection = FakeConnection()
    try:
        with pytest.raises(StorageSchemaError, match="staged family completeness failed"):
            with _prepared_copy_rows(rows, 1) as bounded:
                copy_stage_rows(connection, STAGING_COPY_TABLES["files"], bounded,
                                expected_stage_id="stage-001")
        assert getgeneratorstate(rows) == GEN_SUSPENDED
        assert next(rows)["family_ordinal"] == 2
    finally:
        rows.close()


@pytest.mark.parametrize("expected", [None, True, -1, 1.5])
def test_prepared_copy_rejects_an_invalid_count_without_consuming_rows(expected: int | None) -> None:
    rows = (stage_file_row() for _ in range(1))
    try:
        with pytest.raises(StorageSchemaError, match="staged family completeness failed"):
            with _prepared_copy_rows(rows, expected):
                pytest.fail("malformed count admitted")
        assert next(rows)["family_ordinal"] == 0
    finally:
        rows.close()
