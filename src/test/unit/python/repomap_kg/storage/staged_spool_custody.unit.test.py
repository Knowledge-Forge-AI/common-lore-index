"""Prepared row ownership survives measurement and cleanup-chain failures."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from common_lore_index.coordinator._portable_preparation_control import _PreparedRowAdmission
from common_lore_index.observations.raw import RawObservation
from common_lore_index.storage import staged_rows as module
from common_lore_index.storage.row_spool import RowSpool
from common_lore_index.storage.staging_family_contracts import STAGING_FAMILY_DESCRIPTORS
from common_lore_index.storage.staging_family_contracts import StageFamily
from common_lore_index.storage.staging_observability import (
    StagingMeasurementCategory,
    StagingMeasurements,
)


class Interrupted(BaseException):
    """A deterministic in-process interruption."""


def _observations() -> tuple[RawObservation, ...]:
    return tuple(
        RawObservation(
            kind="file", source_id=f"public/file-{index}.txt", path=f"public/file-{index}.txt",
            confidence="extracted", extractor="spool-custody-fixture", extractor_version="1.0.0",
            metadata={"extension": ".txt", "size_bytes": index + 1},
        )
        for index in range(3)
    )


def test_family_measurement_exit_cannot_lose_its_returned_spool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_measure = module.measure_family_preparation
    marker = Interrupted()
    observed: list[Path] = []

    def measure(measurements, family, operation):
        prepared = original_measure(measurements, family, operation)
        if isinstance(prepared.rows, RowSpool):
            observed.append(prepared.rows.path)
            raise marker
        return prepared

    monkeypatch.setattr(module, "_ROW_SPOOL_THRESHOLD", 1)
    monkeypatch.setattr(module, "measure_family_preparation", measure)
    with pytest.raises(Interrupted) as caught:
        module.build_staged_rows(_observations(), repository_name="public", stage_id="stage-custody", spool_dir=tmp_path)
    assert caught.value is marker
    assert len(observed) == 1
    assert all(not path.exists() for path in observed)
    assert not tuple(tmp_path.iterdir())


def test_checksum_phase_exit_reclaims_the_already_written_spool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = Interrupted()
    observed: list[Path] = []
    original_from_rows = RowSpool.from_rows

    def spool(cls, rows, **kwargs):
        result = original_from_rows(rows, **kwargs)
        observed.append(result.path)
        return result

    class Measurements(StagingMeasurements):
        @contextmanager
        def phase(self, phase_code):
            with super().phase(phase_code):
                yield
            if phase_code == "staging.family_spool.files":
                raise marker

    monkeypatch.setattr(module, "_ROW_SPOOL_THRESHOLD", 1)
    monkeypatch.setattr(RowSpool, "from_rows", classmethod(spool))
    with pytest.raises(Interrupted) as caught:
        module.build_staged_rows(
            _observations(), repository_name="public", stage_id="stage-custody", spool_dir=tmp_path,
            staging_measurements=Measurements(lambda _: None),
        )
    assert caught.value is marker
    assert len(observed) == 1
    assert all(not path.exists() for path in observed)
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("existing_failure", [False, True])
def test_every_family_cleanup_is_attempted_without_replacing_a_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_failure: bool,
) -> None:
    monkeypatch.setattr(module, "_ROW_SPOOL_THRESHOLD", 1)
    prepared = module.build_staged_rows(
        _observations(), repository_name="public", stage_id="stage-custody", spool_dir=tmp_path,
    )
    spools = [rows for rows in prepared.family_rows.values() if isinstance(rows, RowSpool)]
    assert len(spools) == 5
    closed: list[Path] = []
    original_close = RowSpool.close
    primary = Interrupted()
    secondary = OSError("synthetic cleanup refusal after unlink")

    def close(spool):
        original_close(spool)
        closed.append(spool.path)
        if len(closed) == 1:
            raise secondary

    monkeypatch.setattr(RowSpool, "close", close)
    expected = Interrupted if existing_failure else OSError
    with pytest.raises(expected) as caught:
        with prepared:
            if existing_failure:
                raise primary
    assert caught.value is (primary if existing_failure else secondary)
    assert len(closed) == len(set(closed)) == len(spools)
    assert all(not spool.path.exists() for spool in spools)
    assert not tuple(tmp_path.iterdir())


def _sized_row(byte_length: int, *, index: int) -> dict[str, object]:
    row: dict[str, object] = {
        "stage_id": "stage-custody",
        "path": f"public/file-{index}.py",
        "metadata_json": {"pad": ""},
    }
    encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    pad = byte_length - (len(encoded.encode("utf-8")) + 1)
    assert pad >= 0
    row["metadata_json"] = {"pad": "x" * pad}
    return row


def test_early_byte_threshold_observer_refusal_stops_closes_iterator_and_preserves_sentinel(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "unrelated"
    sentinel.write_bytes(b"preserved")
    admit = _PreparedRowAdmission(byte_budget=700_000, record_budget=10)
    consumed: list[int] = []
    closed_source = False

    def source():
        nonlocal closed_source
        try:
            for index, size in enumerate((600_000, 600_000, 256)):
                consumed.append(index)
                yield _sized_row(size, index=index)
        finally:
            closed_source = True

    with pytest.raises(ValueError, match="publication bundle byte bounds exceeded"):
        module._materialize_or_spool(
            source(), descriptor=STAGING_FAMILY_DESCRIPTORS["files"], staging_measurements=None,
            spool_artifact_observer=admit, spool_dir=tmp_path, cancel_check=lambda: None,
        )
    assert consumed == [0, 1]
    assert closed_source is True
    assert tuple(tmp_path.iterdir()) == (sentinel,)
    assert sentinel.read_bytes() == b"preserved"


@pytest.mark.parametrize("cancel_on_second_family", [False, True])
def test_early_byte_threshold_cancellation_stops_cleans_spools_and_preserves_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_on_second_family: bool,
) -> None:
    marker = Interrupted()
    event = threading.Event()
    sentinel = tmp_path / "unrelated"
    sentinel.write_bytes(b"preserved")
    observed_families: list[str] = []

    def check() -> None:
        if event.is_set():
            raise marker

    def observe(family: str, _size: int) -> None:
        observed_families.append(family)
        if not cancel_on_second_family or family != "files":
            event.set()

    monkeypatch.setattr(module, "_ROW_SPOOL_BYTE_THRESHOLD", 64)
    with pytest.raises(Interrupted) as caught:
        module.build_staged_rows(
            _observations(), repository_name="public", stage_id="stage-custody",
            spool_dir=tmp_path, cancel_check=check, spool_artifact_observer=observe,
        )
    assert caught.value is marker
    if not cancel_on_second_family:
        assert observed_families == ["files"]
    else:
        assert observed_families == ["files", "files", "files", "raw_observations"]
    assert tuple(tmp_path.iterdir()) == (sentinel,)
    assert sentinel.read_bytes() == b"preserved"


def test_checksum_and_row_order_parity_across_materialized_count_and_byte_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = _observations()
    with module.build_staged_rows(
        observations, repository_name="public", stage_id="stage-parity", spool_dir=tmp_path,
    ) as materialized:
        assert isinstance(materialized.family_rows["files"], tuple)
        files_bytes = sum(
            len(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")) + 1
            for row in materialized.family_rows["files"]
        )
        baseline_rows = {name: tuple(rows) for name, rows in materialized.family_rows.items()}

        monkeypatch.setattr(module, "_ROW_SPOOL_BYTE_THRESHOLD", files_bytes)
        with module.build_staged_rows(
            observations, repository_name="public", stage_id="stage-parity", spool_dir=tmp_path,
        ) as exact_byte_equal:
            assert isinstance(exact_byte_equal.family_rows["files"], tuple)
            assert exact_byte_equal.checksums == materialized.checksums

        monkeypatch.setattr(module, "_ROW_SPOOL_BYTE_THRESHOLD", files_bytes - 1)
        with module.build_staged_rows(
            observations, repository_name="public", stage_id="stage-parity", spool_dir=tmp_path,
        ) as byte_spilled:
            assert isinstance(byte_spilled.family_rows["files"], RowSpool)
            assert byte_spilled.family_rows["files"].byte_count == files_bytes
            assert byte_spilled.checksums == materialized.checksums
            assert byte_spilled.row_counts == materialized.row_counts
            assert byte_spilled.normalized_byte_counts == materialized.normalized_byte_counts
            assert {name: tuple(rows) for name, rows in byte_spilled.family_rows.items()} == baseline_rows

        monkeypatch.setattr(module, "_ROW_SPOOL_BYTE_THRESHOLD", 1024 * 1024)
        monkeypatch.setattr(module, "_ROW_SPOOL_THRESHOLD", 1)
        with module.build_staged_rows(
            observations, repository_name="public", stage_id="stage-parity", spool_dir=tmp_path,
        ) as count_spilled:
            assert isinstance(count_spilled.family_rows["files"], RowSpool)
            assert count_spilled.checksums == byte_spilled.checksums
            assert {name: tuple(rows) for name, rows in count_spilled.family_rows.items()} == baseline_rows
    assert not tuple(tmp_path.iterdir())


def test_byte_threshold_spill_preserves_spool_and_checksum_timing_categories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_measured(
        *, count_threshold: int, byte_threshold: int,
    ) -> tuple[
        list[str],
        list[tuple[object, int, object]],
        list[tuple[object, int, object]],
        list[tuple[object, object]],
        bool,
        int,
    ]:
        phases: list[str] = []
        durations: list[tuple[object, int, object]] = []
        recorded_bytes: list[tuple[object, int, object]] = []
        unavailable: list[tuple[object, object]] = []

        class Measurements(StagingMeasurements):
            @contextmanager
            def phase(self, phase_code):
                phases.append(phase_code)
                with super().phase(phase_code):
                    yield

            def record_duration(
                self, category: StagingMeasurementCategory, value: int, *, family: StageFamily | None = None,
            ) -> None:
                durations.append((category, value, family))
                super().record_duration(category, value, family=family)

            def record_bytes(
                self, category: StagingMeasurementCategory, value: int, *, family: StageFamily | None = None,
            ) -> None:
                recorded_bytes.append((category, value, family))
                super().record_bytes(category, value, family=family)

            def record_unavailable(self, category, *args, **kwargs):
                unavailable.append((category, kwargs.get("family")))
                return super().record_unavailable(category, *args, **kwargs)

        monkeypatch.setattr(module, "_ROW_SPOOL_THRESHOLD", count_threshold)
        monkeypatch.setattr(module, "_ROW_SPOOL_BYTE_THRESHOLD", byte_threshold)
        with module.build_staged_rows(
            _observations(), repository_name="public", stage_id="stage-measured",
            spool_dir=tmp_path, staging_measurements=Measurements(lambda _: None),
        ) as prepared:
            files_rows = prepared.family_rows["files"]
            spooled = isinstance(files_rows, RowSpool)
            size = files_rows.byte_count if isinstance(files_rows, RowSpool) else sum(
                len(json.dumps(r, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")) + 1
                for r in files_rows
            )
            return phases, durations, recorded_bytes, unavailable, spooled, size

    _, _, _, _, spooled_base, files_bytes = run_measured(count_threshold=4096, byte_threshold=1024 * 1024)
    assert spooled_base is False
    eq_phases, eq_durations, _, eq_unavailable, eq_spooled, _ = run_measured(
        count_threshold=4096, byte_threshold=files_bytes,
    )
    assert eq_spooled is False
    assert "staging.family_spool.files" not in eq_phases
    assert "staging.family_checksum.files" in eq_phases
    assert (StagingMeasurementCategory.FAMILY_SPOOL_WRITE, "files") in eq_unavailable
    assert (StagingMeasurementCategory.FAMILY_SPOOL_WRITE, "files") not in [
        (cat, fam) for cat, _, fam in eq_durations
    ]

    byte_phases, byte_durations, byte_recorded, byte_unavailable, byte_spooled, byte_size = run_measured(
        count_threshold=4096, byte_threshold=files_bytes - 1,
    )
    count_phases, count_durations, _, _, count_spooled, _ = run_measured(
        count_threshold=1, byte_threshold=1024 * 1024,
    )
    assert byte_spooled is True and count_spooled is True and byte_size == files_bytes
    assert [p for p in byte_phases if p.endswith(".files")] == [
        "staging.family_preparation.files",
        "staging.family_spool.files",
    ] == [p for p in count_phases if p.endswith(".files")]
    byte_file_cats = [cat for cat, ns, fam in byte_durations if fam == "files" and isinstance(ns, int) and ns >= 0]
    assert StagingMeasurementCategory.FAMILY_SPOOL_WRITE in byte_file_cats
    assert StagingMeasurementCategory.CHECKSUM in byte_file_cats
    assert byte_file_cats == [cat for cat, _, fam in count_durations if fam == "files"]
    assert (StagingMeasurementCategory.SPOOL_BYTES, files_bytes, "files") in byte_recorded
    assert (StagingMeasurementCategory.FAMILY_SPOOL_WRITE, "files") not in byte_unavailable
    assert not tuple(tmp_path.iterdir())
