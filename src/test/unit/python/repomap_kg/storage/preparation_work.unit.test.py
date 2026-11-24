"""Preparation cancellation is cooperative at the actual owned row boundary."""
from __future__ import annotations

import json
import threading
from enum import IntEnum
from pathlib import Path
from types import MappingProxyType

import pytest

from common_lore_index.artifacts._canonical import canonical_json
from common_lore_index.coordinator._portable_preparation_control import _PreparedRowAdmission
from common_lore_index.observations.raw import RawObservation
from common_lore_index.storage import staged_rows as module
from common_lore_index.storage.row_spool import RowSpool
from common_lore_index.storage.staging_checksums import checksum_family
from common_lore_index.storage.staging_family_contracts import STAGING_FAMILY_DESCRIPTORS


class Interrupted(BaseException):
    """A deterministic control exception without an operating-system signal."""


class ExistingIntegerBound(IntEnum):
    BYTES = 10
    RECORDS = 1


def _observations() -> tuple[RawObservation, ...]:
    return tuple(
        RawObservation(
            kind="file", source_id=f"public/file-{index}.txt", path=f"public/file-{index}.txt",
            confidence="extracted", extractor="preparation-fixture", extractor_version="1.0.0",
            metadata={"extension": ".txt", "size_bytes": index + 1},
        )
        for index in range(6)
    )


def test_cancel_during_row_acquisition_stops_before_the_next_row_and_cleans_spools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = Interrupted()
    event = threading.Event()
    consumed: list[str] = []
    original = module._indexed
    sentinel = tmp_path / "unrelated"
    sentinel.write_bytes(b"preserved")

    def indexed(descriptor, stage_id, rows, adapter):
        for row in original(descriptor, stage_id, rows, adapter):
            consumed.append(descriptor.family)
            if len(consumed) == 2:
                event.set()
            yield row

    def check() -> None:
        if event.is_set():
            raise marker

    monkeypatch.setattr(module, "_indexed", indexed)
    monkeypatch.setattr(module, "_ROW_SPOOL_THRESHOLD", 1)
    with pytest.raises(Interrupted) as caught:
        module.build_staged_rows(
            _observations(), repository_name="public", stage_id="stage-preparation",
            spool_dir=tmp_path, cancel_check=check,
        )
    assert caught.value is marker
    assert consumed == ["files", "files"]
    assert tuple(tmp_path.iterdir()) == (sentinel,)
    assert sentinel.read_bytes() == b"preserved"


def test_spooled_admission_shares_equality_and_does_not_charge_a_refused_row() -> None:
    admit = _PreparedRowAdmission(10, 3)
    admit("files", 4)
    admit("raw_observations", 4)
    with pytest.raises(ValueError, match="publication bundle byte bounds exceeded"):
        admit("files", 7)
    admit("files", 6)
    for _ in range(2):
        with pytest.raises(ValueError, match="publication bundle record bounds exceeded"):
            admit("raw_observations", 5)


def test_admission_retains_the_capability_integer_subclass_domain() -> None:
    admit = _PreparedRowAdmission(ExistingIntegerBound.BYTES, ExistingIntegerBound.RECORDS)
    admit("files", 10)
    with pytest.raises(ValueError, match="publication bundle record bounds exceeded"):
        admit("files", 11)


@pytest.mark.parametrize("value", [True, -1])
def test_invalid_growth_does_not_change_the_next_admitted_projection(value: int) -> None:
    admit = _PreparedRowAdmission(2, 1)
    with pytest.raises(ValueError, match="invalid prepared spool growth"):
        admit("files", value)
    admit("files", 2)


@pytest.mark.parametrize("text", ["plain", '"\\\n\t', "\x7f", "Héllo Ελληνικά 中文", "🐦", "\x00\b\f\r"])
def test_actual_utf8_spool_payload_is_smaller_than_its_canonical_bundle_line(
    tmp_path: Path, text: str,
) -> None:
    row: dict[str, object] = {"stage_id": "stage-unassigned", "value": text, "nested": {"key": [None, True, 1, text]}}
    observed: list[int] = []
    line = canonical_json({"family": "raw_observations", "frame": "record", "record": row})
    with RowSpool.from_rows((row,), dir=tmp_path, artifact_observer=observed.append) as spool:
        assert spool.byte_count == spool.path.stat().st_size
        assert spool.byte_count < len(line)
        assert observed == [spool.byte_count]
        assert tuple(spool) == (row,)
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("metadata", [{1: "public"}, {float("nan"): "one", float("nan"): "two"}])
def test_existing_checksum_rejects_ambiguous_keys_before_spool_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: dict[object, str],
) -> None:
    observed: list[int] = []
    monkeypatch.setattr(module, "_ROW_SPOOL_THRESHOLD", 0)
    row: dict[str, object] = {"stage_id": "stage-unassigned", "path": "public.py", "metadata_json": {"nested": metadata}}
    with pytest.raises(ValueError, match="unsupported checksum value"):
        module._materialize_or_spool(
            (row,), descriptor=STAGING_FAMILY_DESCRIPTORS["files"], staging_measurements=None,
            spool_artifact_observer=lambda _family, size: observed.append(size), spool_dir=tmp_path,
        )
    assert observed == []
    assert not tuple(tmp_path.iterdir())


def test_cancellation_interrupts_buffered_replay_before_another_spool_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = Interrupted()
    event = threading.Event()
    observed: list[tuple[str, int]] = []

    def check() -> None:
        if event.is_set():
            raise marker

    def observe(family: str, size: int) -> None:
        observed.append((family, size))
        event.set()

    monkeypatch.setattr(module, "_ROW_SPOOL_THRESHOLD", 3)
    with pytest.raises(Interrupted) as caught:
        module.build_staged_rows(
            _observations(), repository_name="public", stage_id="stage-preparation",
            spool_dir=tmp_path, cancel_check=check, spool_artifact_observer=observe,
        )
    assert caught.value is marker
    assert len(observed) == 1 and observed[0][0] == "files"
    assert not tuple(tmp_path.iterdir())


def test_explicit_no_control_keeps_default_rows_checksums_and_public_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_ROW_SPOOL_THRESHOLD", 1)
    with module.build_staged_rows(
        _observations(), repository_name="public", stage_id="stage-preparation", spool_dir=tmp_path,
    ) as ordinary:
        with module.build_staged_rows(
            _observations(), repository_name="public", stage_id="stage-preparation",
            spool_dir=tmp_path, cancel_check=None,
        ) as explicit:
            assert type(ordinary) is type(explicit) is module.PreparedStageRows
            assert ordinary.checksums == explicit.checksums
            assert ordinary.normalized_byte_counts == explicit.normalized_byte_counts
            assert ordinary.row_counts == explicit.row_counts
            assert ordinary.files == explicit.files
            assert {name: tuple(rows) for name, rows in ordinary.family_rows.items()} == {
                name: tuple(rows) for name, rows in explicit.family_rows.items()
            }
    assert not tuple(tmp_path.iterdir())


def _compact_jsonl_bytes(row: dict[str, object]) -> int:
    encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return len(encoded.encode("utf-8")) + 1


def _sized_file_row(byte_length: int, *, index: int = 0) -> dict[str, object]:
    row: dict[str, object] = {
        "stage_id": f"stage-{index}",
        "path": f"public/file-{index}.py",
        "metadata_json": {"utf8": "🐦é", "pad": ""},
    }
    remaining = byte_length - _compact_jsonl_bytes(row)
    assert remaining >= 0
    birds, ascii_tail = divmod(remaining, 4)
    row["metadata_json"] = {"utf8": "🐦é", "pad": ("🐦" * birds) + ("x" * ascii_tail)}
    assert _compact_jsonl_bytes(row) == byte_length
    return row


@pytest.mark.parametrize("part_sizes", [(1024 * 1024,), (600_000, 1024 * 1024 - 600_000)])
def test_exact_utf8_payload_equality_remains_materialized_without_spool(
    tmp_path: Path, part_sizes: tuple[int, ...],
) -> None:
    assert module._ROW_SPOOL_THRESHOLD == 4096
    assert getattr(module, "_ROW_SPOOL_BYTE_THRESHOLD", None) == 1024 * 1024
    sentinel = tmp_path / "unrelated"
    sentinel.write_bytes(b"preserved")
    observed: list[tuple[str, int]] = []
    rows = tuple(_sized_file_row(size, index=index) for index, size in enumerate(part_sizes))
    prepared = module._materialize_or_spool(
        rows, descriptor=STAGING_FAMILY_DESCRIPTORS["files"], staging_measurements=None,
        spool_artifact_observer=lambda family, size: observed.append((family, size)),
        spool_dir=tmp_path,
    )
    assert isinstance(prepared.rows, tuple)
    assert prepared.rows == rows
    assert prepared.checksum is None
    assert observed == []
    assert tuple(tmp_path.iterdir()) == (sentinel,)


def test_unchanged_count_threshold_admits_equality_and_spills_at_4097(tmp_path: Path) -> None:
    exact_rows: tuple[dict[str, object], ...] = tuple({"stage_id": "stage-count", "path": f"public/f-{i}.py"} for i in range(4096))
    exact = module._materialize_or_spool(
        exact_rows, descriptor=STAGING_FAMILY_DESCRIPTORS["files"],
        staging_measurements=None, spool_artifact_observer=None, spool_dir=tmp_path,
    )
    assert isinstance(exact.rows, tuple) and len(exact.rows) == 4096
    spilled = module._materialize_or_spool(
        (*exact_rows, {"stage_id": "stage-count", "path": "public/f-4096.py"}),
        descriptor=STAGING_FAMILY_DESCRIPTORS["files"],
        staging_measurements=None, spool_artifact_observer=None, spool_dir=tmp_path,
    )
    try:
        assert isinstance(spilled.rows, RowSpool) and len(spilled.rows) == 4097
    finally:
        if isinstance(spilled.rows, RowSpool):
            spilled.rows.close()
    assert not tuple(tmp_path.iterdir())


def test_one_byte_utf8_payload_overflow_replays_buffered_crossing_and_streamed_rows(tmp_path: Path) -> None:
    row0 = _sized_file_row(600_000, index=0)
    row1 = _sized_file_row(1024 * 1024 - 600_000 + 1, index=1)
    row2 = _sized_file_row(256, index=2)
    observed: list[int] = []
    prepared = module._materialize_or_spool(
        iter((row0, row1, row2)), descriptor=STAGING_FAMILY_DESCRIPTORS["files"],
        staging_measurements=None, spool_artifact_observer=lambda _family, size: observed.append(size),
        spool_dir=tmp_path,
    )
    try:
        assert isinstance(prepared.rows, RowSpool)
        assert prepared.rows.byte_count == prepared.rows.path.stat().st_size == 1_048_577 + 256
        assert observed == [600_000, 1_048_577, 1_048_577 + 256]
        assert tuple(prepared.rows) == (row0, row1, row2)
        expected_checksum = checksum_family(
            ({k: v for k, v in row.items() if k != "stage_id"} for row in (row0, row1, row2)),
            identity_fields=STAGING_FAMILY_DESCRIPTORS["files"].identity_columns,
        )
        assert prepared.checksum == expected_checksum
    finally:
        if isinstance(prepared.rows, RowSpool):
            prepared.rows.close()
    assert not tuple(tmp_path.iterdir())


def test_crossing_first_row_spills_immediately_before_advancing_iterator(tmp_path: Path) -> None:
    rows = (_sized_file_row(1024 * 1024 + 1, index=0), _sized_file_row(200, index=1), _sized_file_row(300, index=2))
    consumed: list[int] = []
    snapshots: list[tuple[int, tuple[int, ...]]] = []

    def source():
        for index, row in enumerate(rows):
            consumed.append(index)
            yield row

    prepared = module._materialize_or_spool(
        source(), descriptor=STAGING_FAMILY_DESCRIPTORS["files"], staging_measurements=None,
        spool_artifact_observer=lambda _family, size: snapshots.append((size, tuple(consumed))),
        spool_dir=tmp_path,
    )
    try:
        assert isinstance(prepared.rows, RowSpool)
        assert snapshots == [(1_048_577, (0,)), (1_048_577 + 200, (0, 1)), (1_048_577 + 500, (0, 1, 2))]
        assert tuple(prepared.rows) == rows
    finally:
        if isinstance(prepared.rows, RowSpool):
            prepared.rows.close()
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("invalid_metadata", "pad_chars"),
    [
        ({1: "public"}, 1024 * 1024),
        ({"bad": float("nan")}, 1024 * 1024),
        ({"bad": float("inf")}, 1024 * 1024),
        ({"bad": float("-inf")}, 1024 * 1024),
        ({1: "public", "valid": "two"}, 0),
        ({"bad": object()}, 0),
    ],
)
def test_probe_and_byte_spill_keep_canonical_checksum_precedence_without_write(
    tmp_path: Path, invalid_metadata: dict[object, object], pad_chars: int,
) -> None:
    sentinel = tmp_path / "unrelated"
    sentinel.write_bytes(b"preserved")
    first = _sized_file_row(256, index=0)
    bad: dict[str, object] = {
        "stage_id": "stage-1", "path": "public/bad.py",
        "metadata_json": {"nested": invalid_metadata, "pad": "x" * pad_chars},
    }
    observed: list[int] = []
    with pytest.raises(ValueError, match="unsupported checksum value"):
        module._materialize_or_spool(
            (bad,), descriptor=STAGING_FAMILY_DESCRIPTORS["files"], staging_measurements=None,
            spool_artifact_observer=lambda _family, size: observed.append(size), spool_dir=tmp_path,
        )
    assert observed == []
    assert tuple(tmp_path.iterdir()) == (sentinel,)
    with pytest.raises(ValueError, match="unsupported checksum value"):
        module._materialize_or_spool(
            (first, bad), descriptor=STAGING_FAMILY_DESCRIPTORS["files"], staging_measurements=None,
            spool_artifact_observer=lambda _family, size: observed.append(size), spool_dir=tmp_path,
        )
    assert observed == [256]
    assert tuple(tmp_path.iterdir()) == (sentinel,)


@pytest.mark.parametrize(
    ("unencodable", "expected_error"),
    [(MappingProxyType({"k": "v"}), TypeError), ("\ud800", UnicodeEncodeError)],
)
def test_probe_unencodable_values_accepted_by_checksum_fail_in_spool_and_clean_up(
    tmp_path: Path, unencodable: object, expected_error: type[Exception],
) -> None:
    sentinel = tmp_path / "unrelated"
    sentinel.write_bytes(b"preserved")
    first = _sized_file_row(256, index=0)
    bad: dict[str, object] = {"stage_id": "stage-1", "path": "public/bad.py", "metadata_json": {"v": unencodable}}
    observed: list[int] = []
    with pytest.raises(expected_error):
        module._materialize_or_spool(
            (first, bad), descriptor=STAGING_FAMILY_DESCRIPTORS["files"], staging_measurements=None,
            spool_artifact_observer=lambda _family, size: observed.append(size), spool_dir=tmp_path,
        )
    assert observed == [256]
    assert tuple(tmp_path.iterdir()) == (sentinel,)
