"""File-reading adapters for repository discovery extractors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from pathlib import Path

from common_lore_index import __version__
from common_lore_index.extractors.config.generic import extract_config_file_observations
from common_lore_index.extractors.config.nix import extract_nix_file_observations
from common_lore_index.extractors.documents.css import extract_css_file_observations
from common_lore_index.extractors.documents.email import (
    MAX_EML_BYTES,
    MAX_MBOX_BYTES,
    _oversized_mailbox_observations,
    extract_eml_file_observations,
    extract_mbox_file_observations,
)
from common_lore_index.extractors.documents.feed import extract_feed_file_observations
from common_lore_index.extractors.documents.html import extract_html_file_observations
from common_lore_index.extractors.documents.markdown import (
    extract_markdown_file_observations,
    markdown_anchors_for_content,
)
from common_lore_index.extractors.documents.office import (
    DEFAULT_MAX_PACKAGE_BYTES,
    extract_document_file_observations,
    extract_odf_file_observations,
)
from common_lore_index.extractors.languages.go_metadata import (
    extract_go_metadata_observations,
)
from common_lore_index.extractors.languages.javascript import (
    extract_javascript_file_observations,
)
from common_lore_index.extractors.languages.python import (
    PythonModuleIndex,
    extract_python_file_observations,
)
from common_lore_index.extractors.languages.ruby import extract_ruby_file_observations
from common_lore_index.extractors.shell.awk import extract_awk_file_observations
from common_lore_index.extractors.shell.base import extract_shell_observations
from common_lore_index.extractors.shell.bash import extract_bash_file_observations
from common_lore_index.extractors.shell.bats import extract_bats_file_observations
from common_lore_index.extractors.shell.powershell import (
    extract_powershell_file_observations,
)
from common_lore_index.extractors.shell.zsh import extract_zsh_file_observations
from common_lore_index.extractors.shell.zunit import extract_zunit_file_observations
from common_lore_index.graph.discovery_records import FileInfo
from common_lore_index.observations.raw import RawObservation


def _read_utf8(repository_root: Path, relative_path: str) -> str | None:
    try:
        return (repository_root / relative_path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def extract_shell_file_observations(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_shell_observations(relative_path, content)


def extract_bash_file_observations_from_file(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_bash_file_observations(relative_path, content)


def extract_bats_file_observations_from_file(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_bats_file_observations(relative_path, content)


def extract_awk_file_observations_from_file(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_awk_file_observations(relative_path, content)


def extract_zsh_file_observations_from_file(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_zsh_file_observations(relative_path, content)


def extract_zunit_file_observations_from_file(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_zunit_file_observations(relative_path, content)


def extract_python_file_observations_from_file(
    repository_root: Path,
    relative_path: str,
    *,
    module_index: PythonModuleIndex,
    module_context_root: Path | None = None,
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_python_file_observations(
        relative_path,
        content,
        module_index=module_index,
        repository_root=repository_root if module_context_root is None else module_context_root,
    )


def extract_nix_file_observations_from_file(
    repository_root: Path,
    relative_path: str,
    *,
    flake_ref: str | None = None,
    include_input_references: bool = False,
    source_identity_root: Path | None = None,
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_nix_file_observations(
        relative_path,
        content,
        flake_ref=flake_ref or (repository_root if source_identity_root is None else source_identity_root).name,
        include_input_references=include_input_references,
    )


def extract_powershell_file_observations_from_file(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_powershell_file_observations(relative_path, content)


def extract_ruby_file_observations_from_file(
    repository_root: Path,
    relative_path: str,
    *,
    repository_paths: frozenset[str] | None = None,
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_ruby_file_observations(
        relative_path,
        content,
        repository_paths=repository_paths,
    )


def extract_javascript_file_observations_from_file(
    repository_root: Path,
    relative_path: str,
    *,
    repository_paths: frozenset[str] | None = None,
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_javascript_file_observations(
        relative_path,
        content,
        repository_paths=repository_paths,
    )


def _document_read_failure(
    relative_path: str, format_name: str, *, mail: bool,
) -> tuple[RawObservation, ...]:
    family = "email" if mail else "document"
    return (RawObservation(
        kind=f"{family}.parse_error",
        source_id=f"{relative_path}#{family}-parse-error:read",
        path=relative_path,
        confidence="unknown",
        extractor="repo-email" if mail else "repo-documents",
        extractor_version=__version__,
        metadata={
            "format": format_name,
            "parser": "stdlib-email" if mail else "stdlib-document-conservative",
            "error_kind": "read-error",
            "message_summary": "file bytes could not be read",
            "recovered": False,
        },
    ),)


def extract_eml_file_observations_from_file(
    repository_root: Path,
    relative_path: str,
) -> tuple[RawObservation, ...]:
    try:
        with (repository_root / relative_path).open("rb") as stream:
            content = stream.read(MAX_EML_BYTES + 1)
    except OSError:
        return _document_read_failure(relative_path, "eml", mail=True)
    return extract_eml_file_observations(relative_path, content)


def extract_mbox_file_observations_from_file(
    repository_root: Path,
    relative_path: str,
) -> tuple[RawObservation, ...]:
    try:
        with (repository_root / relative_path).open("rb") as stream:
            content = stream.read(MAX_MBOX_BYTES + 1)
            if len(content) > MAX_MBOX_BYTES:
                digest = hashlib.sha256(content)
                byte_count = len(content)
                del content
                while chunk := stream.read(131_072):
                    digest.update(chunk)
                    byte_count += len(chunk)
                return _oversized_mailbox_observations(relative_path, byte_count, digest.hexdigest())
    except OSError:
        return _document_read_failure(relative_path, "mbox", mail=True)
    return extract_mbox_file_observations(relative_path, content)


def extract_config_file_observations_from_file(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_config_file_observations(relative_path, content)


def extract_go_metadata_observations_from_file(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_go_metadata_observations(relative_path, content)


def extract_feed_file_observations_from_file(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_feed_file_observations(relative_path, content)


def extract_html_file_observations_from_file(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_html_file_observations(relative_path, content)


def extract_css_file_observations_from_file(
    repository_root: Path, relative_path: str
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return ()
    return extract_css_file_observations(relative_path, content)


def extract_document_file_observations_from_file(
    repository_root: Path,
    relative_path: str,
    *,
    repository_paths: frozenset[str] | None = None,
) -> tuple[RawObservation, ...]:
    suffix = Path(relative_path).suffix.lower()
    if suffix in (".odt", ".ods", ".ott", ".ots"):
        try:
            with (repository_root / relative_path).open("rb") as stream:
                odf_bytes = stream.read(DEFAULT_MAX_PACKAGE_BYTES + 1)
        except OSError:
            return _document_read_failure(relative_path, suffix.lstrip("."), mail=False)
        return extract_odf_file_observations(
            relative_path,
            odf_bytes,
            repository_paths=repository_paths,
            max_package_bytes=DEFAULT_MAX_PACKAGE_BYTES,
        )
    content = _read_utf8(repository_root, relative_path)
    if content is None:
        return (
            RawObservation(
                kind="document.parse_error",
                source_id=f"{relative_path}#document-parse-error:decode",
                path=relative_path,
                confidence="unknown",
                extractor="repo-documents",
                extractor_version=__version__,
                metadata={
                    "format": Path(relative_path).suffix.lower().lstrip(".")
                    or "unknown",
                    "parser": "stdlib-document-conservative",
                    "error_kind": "decode-error",
                    "message_summary": "file is not valid UTF-8",
                    "recovered": False,
                },
            ),
        )
    return extract_document_file_observations(
        relative_path,
        content,
        repository_paths=repository_paths,
    )


def extract_markdown_file_observations_from_file(
    repository_root: Path,
    file_info: FileInfo,
    *,
    repository_paths: frozenset[str],
    markdown_anchors: Mapping[str, set[str] | frozenset[str]],
) -> tuple[RawObservation, ...]:
    content = _read_utf8(repository_root, file_info.path)
    if content is None:
        return ()
    return extract_markdown_file_observations(
        file_info.path,
        content,
        repository_paths=repository_paths,
        markdown_anchors=markdown_anchors,
        content_hash=file_info.content_hash,
        generated=file_info.generated,
    )


def markdown_anchor_index(
    repository_root: Path,
    file_infos: Sequence[FileInfo],
) -> dict[str, set[str] | frozenset[str]]:
    anchors: dict[str, set[str] | frozenset[str]] = {}
    for file_info in file_infos:
        if file_info.language != "markdown":
            continue
        content = _read_utf8(repository_root, file_info.path)
        if content is None:
            continue
        anchors[file_info.path] = frozenset(markdown_anchors_for_content(content))
    return anchors
