"""Private model-response bundle assembly for the body host."""
from __future__ import annotations

from collections.abc import Callable
import io
import zipfile
from retrieval_semantic_bridge_transport import RESPONSE_MAX_BYTES, encode_response_bundle
from retrieval_body_study_model_contract import (
    ModelHostPaths, ModelHostRefusal, ValidatedStudyRequest, _fixed_path, _json_object, _read_stable,
)

RESPONSE_MEMBERS = frozenset({"request.json", "admission.json", "declaration.json", "source-manifest.json",
                             "sources.zip", "report.json", "terminal.json"})
WORKER_RESPONSE_MEMBERS = RESPONSE_MEMBERS - {"request.json", "admission.json"}
MAX_RESPONSE_BYTES = RESPONSE_MAX_BYTES


def _validate_source_archive(raw: bytes, request_raw: bytes, admission_raw: bytes) -> None:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_RESPONSE_BYTES:
        raise ModelHostRefusal("source_archive_bounds")
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            if archive.comment:
                raise ModelHostRefusal("source_archive_comment")
            infos = archive.infolist()
            if len(infos) > 1024:
                raise ModelHostRefusal("source_archive_member_count")
            names: set[str] = set()
            total = 0
            for info in infos:
                name = info.filename
                if (name in names or not name or "\\" in name or
                        any(part in {"", ".", ".."} for part in name.split("/")) or
                        not (name.startswith("sources/") or name.startswith("inputs/")) or
                        info.is_dir() or info.flag_bits & 0x1 or
                        info.compress_type != zipfile.ZIP_STORED):
                    raise ModelHostRefusal("source_archive_members")
                mode = (info.external_attr >> 16) & 0xFFFF
                file_type = mode & 0o170000
                if file_type not in (0, 0o100000):
                    raise ModelHostRefusal("source_archive_members")
                if info.file_size > MAX_RESPONSE_BYTES - total:
                    raise ModelHostRefusal("source_archive_members")
                names.add(name)
                total += info.file_size
            for name, expected in (("inputs/request.json", request_raw), ("inputs/admission.json", admission_raw)):
                matches = [info for info in infos if info.filename == name]
                if len(matches) != 1 or archive.read(matches[0]) != expected:
                    raise ModelHostRefusal("source_archive_inputs")
            if {name.split("/", 1)[1] for name in names if name.startswith("inputs/")} != {"request.json", "admission.json"}:
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
        if len(entries) != 1 or entries[0].name != expected.attempt_id or entries[0].is_symlink():
            raise ModelHostRefusal("response_attempt_set")
        root = entries[0]
        _fixed_path(root, directory=True, code="response_root")
        children = tuple(root.iterdir())
    except ModelHostRefusal:
        raise
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
    for name in ("declaration.json", "source-manifest.json", "report.json"):
        _json_object(files[name], MAX_RESPONSE_BYTES, "response_" + name[:-5].replace("-", "_"))
    terminal = _json_object(files["terminal.json"], MAX_RESPONSE_BYTES, "response_terminal")
    if terminal.get("observation_count") != 0 or terminal.get("status") != "completed":
        raise ModelHostRefusal("response_terminal")
    _validate_source_archive(files["sources.zip"], request_raw, admission_raw)
    try:
        bundle = encode_response_bundle(files)
    except Exception:
        raise ModelHostRefusal("response_bundle")
    charge(len(bundle))
    return bundle


__all__ = ["MAX_RESPONSE_BYTES", "RESPONSE_MEMBERS", "WORKER_RESPONSE_MEMBERS", "_response_bundle"]
