"""Explicit-policy-gated local bulk corpus ingestion."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

from common_lore_index.extractors.documents.css_html_matching import (
    extract_css_selector_match_observations,
)
from common_lore_index.extractors.languages.python import PythonModuleIndex
from common_lore_index.graph.discovery import (
    FileInfo,
    classify_path,
    extract_awk_file_observations_from_file,
    extract_bash_file_observations_from_file,
    extract_bats_file_observations_from_file,
    extract_config_file_observations_from_file,
    extract_css_file_observations_from_file,
    extract_document_file_observations_from_file,
    extract_eml_file_observations_from_file,
    extract_feed_file_observations_from_file,
    extract_html_file_observations_from_file,
    extract_javascript_file_observations_from_file,
    extract_markdown_file_observations_from_file,
    extract_mbox_file_observations_from_file,
    extract_nix_file_observations_from_file,
    extract_powershell_file_observations_from_file,
    extract_python_file_observations_from_file,
    extract_ruby_file_observations_from_file,
    extract_shell_file_observations,
    extract_zsh_file_observations_from_file,
    extract_zunit_file_observations_from_file,
    markdown_anchor_index,
)
from common_lore_index.observations.raw import RawObservation
from common_lore_index.ops.ingestion._bulk_helpers import (
    ALLOWED_CORPUS_KINDS,
    ALLOWED_POLICY_STATUSES,
    ALLOWED_SOURCE_TYPES,
    ARCHIVE_EXTENSIONS,
    DEFAULT_EXCLUDED_DIRECTORIES,
    EXTENSION_ROUTES,
    SPECIAL_FILENAME_ROUTES,
    WARC_EXTENSIONS,
    classify_bulk_route,
    depth,
    deterministic_run_id,
    ensure_contained,
    is_contained,
    is_excluded_path,
    load_bulk_source_config,
    manifest_digest,
    relative_to_root,
    root_path_summary,
    route_included,
    safe_size,
    sha256_file,
    sha256_json,
    sha256_text,
    write_json,
    write_jsonl,
)
from common_lore_index.ops.ingestion._bulk_manifest import _capture_bulk_files, build_bulk_manifest
from common_lore_index.ops.ingestion._bulk_observations import (
    annotate_observation,
    write_bulk_run_files,
)
from common_lore_index.ops.ingestion.acquisition_contracts import non_publication_result
from common_lore_index.ops.ingestion.bulk_config_values import (
    bool_value,
    normalized_path_tuple,
    optional_string,
    required_bool,
    required_positive_int,
    required_string,
    required_table,
    resolve_config_root,
    string_tuple,
    table,
)
from common_lore_index.ops.ingestion.bulk_records import (
    BulkImportSummary,
    BulkIncludedFile,
    BulkManifest,
    BulkPolicyError,
    BulkSkippedFile,
    BulkSourceConfig,
)

EXTRACTOR = "bulk-local-ingestion"
EXTRACTOR_VERSION = "0.1.0"


def observations_for_file_info(
    repo_root: Path,
    file_info: FileInfo,
    *,
    module_index: PythonModuleIndex,
    repository_paths: frozenset[str],
    markdown_anchors: Mapping[str, set[str] | frozenset[str]],
    python_context_root: Path | None = None,
    source_identity_root: Path | None = None,
) -> tuple[RawObservation, ...]:
    if file_info.language == "markdown":
        return extract_markdown_file_observations_from_file(
            repo_root,
            file_info,
            repository_paths=repository_paths,
            markdown_anchors=markdown_anchors,
        )
    if file_info.language == "shell":
        return extract_shell_file_observations(repo_root, file_info.path)
    if file_info.language == "bash":
        return extract_bash_file_observations_from_file(repo_root, file_info.path)
    if file_info.language == "bats":
        return extract_bats_file_observations_from_file(repo_root, file_info.path)
    if file_info.language == "awk":
        return extract_awk_file_observations_from_file(repo_root, file_info.path)
    if file_info.language == "zsh":
        return extract_zsh_file_observations_from_file(repo_root, file_info.path)
    if file_info.language == "zunit":
        return extract_zunit_file_observations_from_file(repo_root, file_info.path)
    if file_info.language == "python":
        return extract_python_file_observations_from_file(
            repo_root,
            file_info.path,
            module_index=module_index,
            module_context_root=python_context_root,
        )
    if file_info.language == "ruby":
        return extract_ruby_file_observations_from_file(
            repo_root,
            file_info.path,
            repository_paths=repository_paths,
        )
    if file_info.language == "javascript":
        return extract_javascript_file_observations_from_file(
            repo_root,
            file_info.path,
            repository_paths=repository_paths,
        )
    if file_info.language == "eml":
        return extract_eml_file_observations_from_file(repo_root, file_info.path)
    if file_info.language == "mbox":
        return extract_mbox_file_observations_from_file(repo_root, file_info.path)
    if file_info.language == "nix":
        return extract_nix_file_observations_from_file(
            repo_root, file_info.path, source_identity_root=source_identity_root,
        )
    if file_info.language == "powershell":
        return extract_powershell_file_observations_from_file(
            repo_root,
            file_info.path,
        )
    if file_info.language in ("json", "xml"):
        feed_observations = extract_feed_file_observations_from_file(
            repo_root,
            file_info.path,
        )
        if feed_observations:
            return feed_observations
    if file_info.language in (
        "json",
        "jsonc",
        "jsonl",
        "toml",
        "plist",
        "xml",
        "yaml",
    ):
        return extract_config_file_observations_from_file(repo_root, file_info.path)
    if file_info.language == "html":
        return extract_html_file_observations_from_file(repo_root, file_info.path)
    if file_info.language == "css":
        return extract_css_file_observations_from_file(repo_root, file_info.path)
    if file_info.language in ("text", "csv", "tsv", "latex", "odf"):
        return extract_document_file_observations_from_file(
            repo_root,
            file_info.path,
            repository_paths=repository_paths,
        )
    return ()



def build_bulk_plan_from_config(config_path: Path | str) -> BulkManifest:
    config = load_bulk_source_config(config_path)
    return build_bulk_plan(config, repository_root=config.resolved_root)


def build_bulk_plan(
    config: BulkSourceConfig,
    *,
    repository_root: Path | str | None = None,
) -> BulkManifest:
    return build_bulk_manifest(config, repository_root=repository_root)


def bulk_observations_from_plan(
    config: BulkSourceConfig,
    manifest: BulkManifest,
    *,
    repository_root: Path | str,
) -> tuple[RawObservation, ...]:
    if not manifest.included_files:
        return ()
    repo_root = Path(repository_root).resolve()
    included_by_repository_path = {item.repository_path: item for item in manifest.included_files}
    observations: list[RawObservation] = []
    with TemporaryDirectory(prefix="cloe-bulk-readback-") as directory:
        captured_files = _capture_bulk_files(config, manifest, repo_root, Path(directory).resolve())
        file_info_by_path: dict[str, FileInfo] = {}
        by_snapshot_root: dict[Path, list[FileInfo]] = defaultdict(list)
        for path, record in included_by_repository_path.items():
            captured = captured_files[path]
            info = classify_path(captured.root, captured.root / path, captured_content_hash=record.sha256,
                                 captured_executable=captured.executable)
            file_info_by_path[path] = info
            by_snapshot_root[captured.root].append(info)
        captured_project = captured_files.get("pyproject.toml")
        module_context_root = repo_root if captured_project is None else captured_project.root
        module_index = PythonModuleIndex.from_python_paths(
            (info.path for info in file_info_by_path.values() if info.language == "python"),
            repository_root=module_context_root,
        )
        repository_paths = frozenset(file_info_by_path)
        markdown_anchors = {
            path: anchors
            for captured_root, infos in by_snapshot_root.items()
            for path, anchors in markdown_anchor_index(captured_root, infos).items()
        }
        for item in manifest.included_files:
            path = item.repository_path
            file_info = file_info_by_path[path]
            routed = [file_info.to_observation()]
            routed.extend(
                observations_for_file_info(
                    captured_files[path].root, file_info, module_index=module_index,
                    repository_paths=repository_paths, markdown_anchors=markdown_anchors,
                    python_context_root=module_context_root, source_identity_root=repo_root,
                )
            )
            observations.extend(
                annotate_observation(observation, config=config, manifest=manifest, included=included_by_repository_path[path])
                for observation in routed
            )
    observations.extend(extract_css_selector_match_observations(observations))
    return tuple(observations)


def import_bulk_source(
    config_path: Path | str,
    *,
    root_path: Path | str,
) -> BulkImportSummary:
    config = load_bulk_source_config(config_path)
    repo_root = Path(root_path).resolve()
    manifest = build_bulk_plan(config, repository_root=repo_root)
    observations = bulk_observations_from_plan(
        config,
        manifest,
        repository_root=repo_root,
    )
    output_path = write_bulk_run_files(
        manifest,
        observations=observations,
        repository_root=repo_root,
    )
    return BulkImportSummary(
        source_id=config.source_id,
        source_type=config.source_type,
        corpus_kind=config.corpus_kind,
        policy_status=config.policy_status,
        bulk_run_id=manifest.bulk_run_id,
        bulk_manifest_id=manifest.bulk_manifest_id,
        observations=len(observations),
        raw_observations=tuple(observations),
        included_files=manifest.file_count_included,
        skipped_files=manifest.file_count_skipped,
        output_path=output_path,
        output_path_summary=output_path.relative_to(repo_root).as_posix(),
        manifest=manifest,
        publication=non_publication_result(),
    )


__all__ = [
    "ALLOWED_CORPUS_KINDS",
    "ALLOWED_POLICY_STATUSES",
    "ALLOWED_SOURCE_TYPES",
    "ARCHIVE_EXTENSIONS",
    "BulkImportSummary",
    "BulkIncludedFile",
    "BulkManifest",
    "BulkPolicyError",
    "BulkSkippedFile",
    "BulkSourceConfig",
    "DEFAULT_EXCLUDED_DIRECTORIES",
    "EXTENSION_ROUTES",
    "EXTRACTOR",
    "EXTRACTOR_VERSION",
    "FileInfo",
    "PythonModuleIndex",
    "SPECIAL_FILENAME_ROUTES",
    "WARC_EXTENSIONS",
    "annotate_observation",
    "bool_value",
    "build_bulk_manifest",
    "build_bulk_plan",
    "build_bulk_plan_from_config",
    "bulk_observations_from_plan",
    "classify_bulk_route",
    "classify_path",
    "depth",
    "deterministic_run_id",
    "ensure_contained",
    "extract_awk_file_observations_from_file",
    "extract_bash_file_observations_from_file",
    "extract_bats_file_observations_from_file",
    "extract_config_file_observations_from_file",
    "extract_css_file_observations_from_file",
    "extract_document_file_observations_from_file",
    "extract_eml_file_observations_from_file",
    "extract_feed_file_observations_from_file",
    "extract_html_file_observations_from_file",
    "extract_javascript_file_observations_from_file",
    "extract_markdown_file_observations_from_file",
    "extract_mbox_file_observations_from_file",
    "extract_nix_file_observations_from_file",
    "extract_powershell_file_observations_from_file",
    "extract_python_file_observations_from_file",
    "extract_ruby_file_observations_from_file",
    "extract_shell_file_observations",
    "extract_zsh_file_observations_from_file",
    "extract_zunit_file_observations_from_file",
    "import_bulk_source",
    "is_contained",
    "is_excluded_path",
    "load_bulk_source_config",
    "manifest_digest",
    "markdown_anchor_index",
    "normalized_path_tuple",
    "observations_for_file_info",
    "optional_string",
    "relative_to_root",
    "required_bool",
    "required_positive_int",
    "required_string",
    "required_table",
    "resolve_config_root",
    "root_path_summary",
    "route_included",
    "safe_size",
    "sha256_file",
    "sha256_json",
    "sha256_text",
    "string_tuple",
    "table",
    "write_bulk_run_files",
    "write_json",
    "write_jsonl",
]
