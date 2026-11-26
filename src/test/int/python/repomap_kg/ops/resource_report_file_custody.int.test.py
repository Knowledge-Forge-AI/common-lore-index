"""Real report reader custody, private files and nonregular-input refusals."""

from __future__ import annotations

import os
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from common_lore_index.ops.resource_reports._codec import decode_report, encode_report
from common_lore_index.ops.resource_reports._files import read_report_file
from common_lore_index.ops.resource_view._context_projection import unfinished_compact_context
from common_lore_index_test_support.resource_report_cli_files import private_directory, run_cloe


ROOT = Path(__file__).resolve().parents[6]


def _data() -> bytes:
    return encode_report(unfinished_compact_context())


def _check(path: Path) -> CompletedProcess[str]:
    return run_cloe("ops", "resource-report", "check", "--input", str(path), cwd=ROOT)


def test_real_nonregular_inputs_refuse_without_blocking_or_path_leakage(tmp_path: Path) -> None:
    reports = private_directory(tmp_path / "reports")
    outside = tmp_path / "outside.txt"
    outside.write_text("foreign-preserved", encoding="utf-8")
    fifo = reports / "fifo.json"
    os.mkfifo(fifo, 0o600)
    link = reports / "link.json"
    link.symlink_to(outside)
    directory = reports / "directory.json"
    directory.mkdir(mode=0o700)
    shared = reports / "shared.json"
    shared.write_bytes(_data())
    shared.chmod(0o644)
    for path in (fifo, link, directory, shared):
        completed = _check(path)
        assert completed.returncode == 1
        assert completed.stdout == ""
        assert completed.stderr == "ERROR: resource-report-invalid: unsafe_input\n"
        assert str(tmp_path) not in completed.stderr
    assert outside.read_text(encoding="utf-8") == "foreign-preserved"


def test_private_hardlink_is_readable_and_cli_json_is_byte_exact(tmp_path: Path) -> None:
    reports = private_directory(tmp_path / "reports")
    original = reports / "installed.json"
    hard = reports / "hardlink.json"
    data = _data()
    original.write_bytes(data)
    original.chmod(0o600)
    os.link(original, hard)
    assert os.stat(original).st_nlink == 2
    assert read_report_file(hard) == data
    assert encode_report(decode_report(read_report_file(hard))) == data
    checked = _check(hard)
    assert checked.returncode == 0 and checked.stderr == ""
    shown = run_cloe("ops", "resource-report", "show", "--input", str(hard), "--json", cwd=ROOT)
    assert shown.returncode == 0 and shown.stdout.encode("utf-8") == data
    assert original.read_bytes() == data


def test_foreign_file_and_symlink_target_are_never_modified(tmp_path: Path) -> None:
    reports = private_directory(tmp_path / "reports")
    foreign = reports / "foreign.json"
    foreign.write_bytes(b"foreign\n")
    foreign.chmod(0o600)
    target = tmp_path / "target.json"
    target.write_bytes(b"target-preserved")
    target.chmod(0o600)
    link = reports / "target-link.json"
    link.symlink_to(target)
    first = _check(foreign)
    second = _check(link)
    assert first.returncode == second.returncode == 1
    assert first.stderr == "ERROR: resource-report-invalid: malformed_json\n"
    assert second.stderr == "ERROR: resource-report-invalid: unsafe_input\n"
    assert str(tmp_path) not in first.stderr and str(tmp_path) not in second.stderr
    assert foreign.read_bytes() == b"foreign\n"
    assert target.read_bytes() == b"target-preserved"


@pytest.mark.parametrize("size", (8193, 8194))
def test_reader_size_boundary_is_a_closed_cli_refusal(tmp_path: Path, size: int) -> None:
    reports = private_directory(tmp_path / "reports")
    path = reports / f"size-{size}.json"
    path.write_bytes(b"x" * size)
    path.chmod(0o600)
    completed = _check(path)
    expected = "too_large" if size > 8193 else "not_canonical"
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == f"ERROR: resource-report-invalid: {expected}\n"
    assert str(tmp_path) not in completed.stderr


def test_missing_and_dangling_inputs_do_not_create_or_follow_anything(tmp_path: Path) -> None:
    reports = private_directory(tmp_path / "reports")
    dangling = reports / "dangling.json"
    dangling.symlink_to(tmp_path / "does-not-exist")
    missing = reports / "missing.json"
    for path in (missing, dangling):
        completed = _check(path)
        assert completed.returncode == 1 and completed.stdout == ""
        expected = "unreadable" if path == missing else "unsafe_input"
        assert completed.stderr == f"ERROR: resource-report-invalid: {expected}\n"
        assert str(tmp_path) not in completed.stderr
    assert not (tmp_path / "does-not-exist").exists()


def test_cli_rejects_an_inconsistent_private_report_read(tmp_path: Path, monkeypatch, capsys) -> None:
    from common_lore_index.cli.main import main
    from common_lore_index.ops.resource_reports import _files

    path = private_directory(tmp_path / "reports") / "test.json"
    data = _data()
    path.write_bytes(data)
    path.chmod(0o600)
    read = os.read
    identity = (path.stat().st_dev, path.stat().st_ino)

    def shorter_read(descriptor: int, count: int) -> bytes:
        info = os.fstat(descriptor)
        chunk = read(descriptor, count)
        if (info.st_dev, info.st_ino) == identity:
            return chunk[:-1]
        return chunk

    with monkeypatch.context() as local:
        local.setattr(_files.os, "read", shorter_read)
        status = main(["ops", "resource-report", "check", "--input", str(path)])
    captured = capsys.readouterr()
    assert status == 1 and captured.out == ""
    assert captured.err == "ERROR: resource-report-invalid: unsafe_input\n"
    assert path.read_bytes() == data


def test_cli_refuses_same_size_report_mutation_with_restored_mtime(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    from common_lore_index.cli.main import main
    from common_lore_index.ops.resource_reports import _files

    path = private_directory(tmp_path / "reports") / "test.json"
    data = _data()
    path.write_bytes(data)
    path.chmod(0o600)
    before = path.stat()
    changed = data[:-1] + b" "
    assert changed != data
    read = os.read
    mutations = 0

    def overwrite_after_read(descriptor: int, count: int) -> bytes:
        nonlocal mutations
        info = os.fstat(descriptor)
        chunk = read(descriptor, count)
        if chunk and (info.st_dev, info.st_ino) == (before.st_dev, before.st_ino):
            path.write_bytes(changed)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            mutations += 1
        return chunk

    with monkeypatch.context() as local:
        local.setattr(_files.os, "read", overwrite_after_read)
        status = main(["ops", "resource-report", "check", "--input", str(path)])
    captured = capsys.readouterr()
    assert mutations == 1
    assert status == 1 and captured.out == ""
    assert captured.err == "ERROR: resource-report-invalid: unsafe_input\n"
    after = path.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (before.st_ino, before.st_size, before.st_mtime_ns)
    assert after.st_ctime_ns != before.st_ctime_ns and path.read_bytes() == changed


def test_cli_refuses_report_that_grows_during_the_admitted_read(tmp_path, monkeypatch, capsys):
    from common_lore_index.cli.main import main
    from common_lore_index.ops.resource_reports import _files

    path = private_directory(tmp_path / "reports") / "report.json"
    data = _data()
    path.write_bytes(data)
    path.chmod(0o600)
    before = path.stat()
    read = os.read
    read_sizes: list[int] = []

    def append_before_read(descriptor, count):
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == (before.st_dev, before.st_ino):
            if not read_sizes:
                with path.open("ab") as output:
                    output.write(b" ")
            read_sizes.append(count)
        return read(descriptor, count)

    with monkeypatch.context() as local:
        local.setattr(_files.os, "read", append_before_read)
        status = main(["ops", "resource-report", "check", "--input", str(path)])
    captured = capsys.readouterr()
    assert status == 1 and captured.out == ""
    assert captured.err == "ERROR: resource-report-invalid: unsafe_input\n"
    assert read_sizes == [len(data)]
    assert path.read_bytes() == data + b" "
