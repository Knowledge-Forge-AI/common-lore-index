"""Bounded result/source bundle verification for the unchanged model worker."""
from __future__ import annotations
from collections.abc import Callable
import io
from pathlib import Path
import zipfile
from retrieval_supplement_model_contract import (
    ModelHostPaths, ModelHostRefusal, ValidatedStudyRequest, _fixed_path, _json_object, _read_stable,
)
RESPONSE_MEMBERS = frozenset({"request.json","admission.json","declaration.json","source-manifest.json","sources.zip","report.json","terminal.json"})
WORKER_RESPONSE_MEMBERS = RESPONSE_MEMBERS - {"request.json","admission.json"}
MAX_RESPONSE_BYTES = 32 * 1024**2

def _validate_source_archive(raw: bytes, request_raw: bytes, admission_raw: bytes) -> None:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ModelHostRefusal("source_archive_bounds")
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            if archive.comment:
                raise ModelHostRefusal("source_archive_comment")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or sum(info.file_size for info in infos) > MAX_RESPONSE_BYTES or any(info.is_dir() or info.flag_bits & 0x1 or ".." in Path(info.filename).parts or info.filename.startswith("/") or not (info.filename.startswith("sources/") or info.filename.startswith("inputs/")) or info.filename.endswith("/observations.jsonl") for info in infos):
                raise ModelHostRefusal("source_archive_members")
            for name, expected in (("inputs/request.json", request_raw), ("inputs/admission.json", admission_raw)):
                matches = [info for info in infos if info.filename == name]
                if len(matches) != 1 or archive.read(matches[0]) != expected:
                    raise ModelHostRefusal("source_archive_inputs")
    except ModelHostRefusal:
        raise
    except (OSError, EOFError, RuntimeError, ValueError, zipfile.BadZipFile):
        raise ModelHostRefusal("source_archive_invalid") from None


def _response_bundle(paths: ModelHostPaths, expected: ValidatedStudyRequest,
                    request_raw: bytes, admission_raw: bytes, charge: Callable[[int], None],
                    guard: Callable[[], object] | None = None) -> bytes:
    if guard is not None:
        guard()
    _fixed_path(paths.output_root, directory=True, code="response_root")
    try:
        entries = tuple(paths.output_root.iterdir())
    except OSError:
        raise ModelHostRefusal("response_root") from None
    if len(entries) != 1 or entries[0].name != expected.attempt_id or entries[0].is_symlink():
        raise ModelHostRefusal("response_attempt_set")
    root = entries[0]
    _fixed_path(root, directory=True, code="response_root")
    try:
        children = tuple(root.iterdir())
    except OSError:
        raise ModelHostRefusal("response_members") from None
    if any(item.is_symlink() for item in children) or {item.name for item in children} != WORKER_RESPONSE_MEMBERS:
        raise ModelHostRefusal("response_members")
    worker_files: dict[str, bytes] = {}
    for name in WORKER_RESPONSE_MEMBERS:
        if guard is not None:
            guard()
        worker_files[name] = _read_stable(root / name, MAX_RESPONSE_BYTES, "response_file", charge)
        if guard is not None:
            guard()
    files = {"request.json": request_raw, "admission.json": admission_raw, **worker_files}
    _json_object(files["declaration.json"], MAX_RESPONSE_BYTES, "response_declaration")
    _json_object(files["source-manifest.json"], MAX_RESPONSE_BYTES, "response_manifest")
    _json_object(files["report.json"], MAX_RESPONSE_BYTES, "response_report")
    terminal = _json_object(files["terminal.json"], MAX_RESPONSE_BYTES, "response_terminal")
    if terminal.get("observation_count") != 0 or terminal.get("status") != "completed":
        raise ModelHostRefusal("response_terminal")
    _validate_source_archive(files["sources.zip"], request_raw, admission_raw)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(RESPONSE_MEMBERS):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o600 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    bundle = output.getvalue()
    if len(bundle) > MAX_RESPONSE_BYTES:
        raise ModelHostRefusal("response_bounds")
    charge(len(bundle))
    return bundle
