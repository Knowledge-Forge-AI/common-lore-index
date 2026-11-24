"""Observation extraction and metadata annotation for archive sources."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
import os
from pathlib import Path
from typing import Any, BinaryIO

from common_lore_index.artifacts._source_sealing_io import open_source_body
from common_lore_index.extractors.languages.python import PythonModuleIndex
from common_lore_index.graph.discovery import (
    FileInfo,
    extract_awk_file_observations_from_file,
    extract_bash_file_observations_from_file,
    extract_bats_file_observations_from_file,
    extract_config_file_observations_from_file,
    extract_css_file_observations_from_file,
    extract_feed_file_observations_from_file,
    extract_html_file_observations_from_file,
    extract_javascript_file_observations_from_file,
    extract_markdown_file_observations_from_file,
    extract_nix_file_observations_from_file,
    extract_powershell_file_observations_from_file,
    extract_python_file_observations_from_file,
    extract_shell_file_observations,
    extract_zsh_file_observations_from_file,
    extract_zunit_file_observations_from_file,
)
from common_lore_index.observations.raw import RawObservation
from common_lore_index.ops.ingestion._source_archive_records import (
    ArchiveIncludedFile,
    ArchiveManifest,
    ArchiveSourceConfig,
)
from common_lore_index.ops.ingestion._source_archive_scan import _resolve_archive_artifact_path
from common_lore_index.ops.ingestion.source_common import SourcePolicyError


@dataclass(frozen=True)
class _CapturedArchiveFile:
    root: Path
    executable: bool


@contextmanager
def _open_snapshot_file(root: Path, relative: Path, ordinal: int) -> Iterator[tuple[Path, BinaryIO]]:
    captured_root = root / "common"
    destination = captured_root / relative
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        output = destination.open("xb")
    except (FileExistsError, NotADirectoryError, IsADirectoryError):
        # The temporary volume may normalize case or Unicode differently.
        # Isolate this path while keeping its original repository-relative name.
        captured_root = root / "aliases" / str(ordinal)
        destination = captured_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        output = destination.open("xb")
    with output:
        yield captured_root, output


def _capture_archive_contents(
    repository_root: Path,
    snapshot: Path,
    config: ArchiveSourceConfig,
    manifest: ArchiveManifest,
) -> dict[str, _CapturedArchiveFile]:
    if any(type(file.byte_length) is not int or file.byte_length < 0 for file in manifest.included_files):
        raise SourcePolicyError("invalid retained archive file length")
    if manifest.file_count > config.max_file_count or manifest.total_byte_count > config.max_artifact_bytes:
        raise SourcePolicyError("retained archive files exceed source policy")
    source_paths = {file.repository_path for file in manifest.included_files}
    if len(source_paths) != manifest.file_count:
        raise SourcePolicyError("duplicate retained archive file reference")
    return {
        item.repository_path: _capture_retained_file(
            repository_root, snapshot, item.repository_path, item.byte_length, item.sha256, ordinal,
        )
        for ordinal, item in enumerate(manifest.included_files)
    }


def _capture_retained_file(
    repository_root: Path,
    snapshot: Path,
    repository_path: str,
    byte_length: int,
    digest: str,
    ordinal: int,
) -> _CapturedArchiveFile:
    relative = Path(repository_path)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts or relative.as_posix() != repository_path:
        raise SourcePolicyError("invalid retained archive file reference")
    try:
        source = _resolve_archive_artifact_path(repository_root, repository_path)
        admitted = source.lstat()
        executable = os.access(source, os.X_OK)
        with open_source_body(
            repository_root, repository_path,
            expected_size=byte_length, expected_digest=digest,
            expected_executable=None,
            expected_stat=admitted, allow_hardlinks=True,
        ) as body:
            with _open_snapshot_file(snapshot, relative, ordinal) as (captured_root, output):
                for chunk in body:
                    if output.write(chunk) != len(chunk):
                        raise OSError("incomplete archive snapshot write")
    except (OSError, ValueError):
        raise SourcePolicyError("retained archive file differs from its manifest") from None
    return _CapturedArchiveFile(captured_root, executable)


def _observations_for_archive_file(
    repository_root: Path,
    file_info: FileInfo,
    *,
    module_index: PythonModuleIndex,
    repository_paths: frozenset[str],
    markdown_anchors: Mapping[str, set[str] | frozenset[str]],
    python_context_root: Path | None = None,
    source_identity_root: Path | None = None,
) -> tuple[RawObservation, ...]:
    observations = [file_info.to_observation()]
    if file_info.language == "markdown":
        observations.extend(
            extract_markdown_file_observations_from_file(
                repository_root,
                file_info,
                repository_paths=repository_paths,
                markdown_anchors=markdown_anchors,
            )
        )
    if file_info.language == "shell":
        observations.extend(extract_shell_file_observations(repository_root, file_info.path))
    if file_info.language == "bash":
        observations.extend(
            extract_bash_file_observations_from_file(repository_root, file_info.path)
        )
    if file_info.language == "bats":
        observations.extend(
            extract_bats_file_observations_from_file(repository_root, file_info.path)
        )
    if file_info.language == "awk":
        observations.extend(
            extract_awk_file_observations_from_file(repository_root, file_info.path)
        )
    if file_info.language == "zsh":
        observations.extend(
            extract_zsh_file_observations_from_file(repository_root, file_info.path)
        )
    if file_info.language == "zunit":
        observations.extend(
            extract_zunit_file_observations_from_file(repository_root, file_info.path)
        )
    if file_info.language == "python":
        observations.extend(
            extract_python_file_observations_from_file(
                repository_root,
                file_info.path,
                module_index=module_index,
                module_context_root=python_context_root,
            )
        )
    if file_info.language == "nix":
        observations.extend(
            extract_nix_file_observations_from_file(
                repository_root, file_info.path, source_identity_root=source_identity_root,
            )
        )
    if file_info.language == "powershell":
        observations.extend(
            extract_powershell_file_observations_from_file(
                repository_root,
                file_info.path,
            )
        )
    if file_info.language in ("json", "xml"):
        feed_observations = extract_feed_file_observations_from_file(
            repository_root,
            file_info.path,
        )
        if feed_observations:
            observations.extend(feed_observations)
            return tuple(observations)
    if file_info.language in ("json", "jsonc", "jsonl", "toml", "plist", "xml"):
        observations.extend(
            extract_config_file_observations_from_file(repository_root, file_info.path)
        )
    if file_info.language == "html":
        observations.extend(
            extract_html_file_observations_from_file(repository_root, file_info.path)
        )
    if file_info.language == "css":
        observations.extend(
            extract_css_file_observations_from_file(repository_root, file_info.path)
        )
    if file_info.language == "javascript":
        observations.extend(
            extract_javascript_file_observations_from_file(
                repository_root,
                file_info.path,
                repository_paths=repository_paths,
            )
        )
    return tuple(observations)


def _annotate_archive_observations(
    observations: Sequence[RawObservation],
    config: ArchiveSourceConfig,
    manifest: ArchiveManifest,
) -> tuple[RawObservation, ...]:
    by_repository_path = {
        item.repository_path: item for item in manifest.included_files
    }
    return tuple(
        replace(
            observation,
            metadata={
                **dict(observation.metadata),
                **_archive_observation_metadata(
                    observation,
                    config,
                    manifest,
                    by_repository_path,
                ),
            },
        )
        for observation in observations
    )


def _archive_observation_metadata(
    observation: RawObservation,
    config: ArchiveSourceConfig,
    manifest: ArchiveManifest,
    by_repository_path: Mapping[str, ArchiveIncludedFile],
) -> dict[str, Any]:
    included = by_repository_path.get(observation.path or "")
    artifact_relative_path = (
        included.repository_path
        if included is not None
        else observation.path or observation.source_id
    )
    metadata: dict[str, Any] = {
        "source_id": config.source_id,
        "source_id_configured": config.source_id,
        "source_type": config.source_type,
        "source_display_name": config.display_name,
        "source_policy_status": config.policy_status,
        "source_run_id": manifest.artifact_run_id,
        "source_artifact_id": manifest.artifact_manifest_id,
        "artifact_policy_status": config.policy_status,
        "artifact_run_id": manifest.artifact_run_id,
        "artifact_manifest_id": manifest.artifact_manifest_id,
        "artifact_profile": config.artifact_profile,
        "artifact_relative_path": artifact_relative_path,
        "source_artifact_path": artifact_relative_path,
        "artifact_retention_policy": config.retention_policy,
        "retention_policy": config.retention_policy,
        "config_redacted_keys": list(config.redacted_config_keys),
    }
    if included is not None:
        metadata.update(
            {
                "artifact_byte_length": included.byte_length,
                "source_artifact_bytes": included.byte_length,
                "artifact_sha256": included.sha256,
                "source_artifact_sha256": included.sha256,
                "artifact_extractor_route": included.extractor_route,
            }
        )
    return metadata
