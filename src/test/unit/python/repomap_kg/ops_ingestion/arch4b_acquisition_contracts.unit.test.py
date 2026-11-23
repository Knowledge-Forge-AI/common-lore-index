from __future__ import annotations

from pathlib import Path

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from common_lore_index.cli.parser import build_parser
else:
    from common_lore_index.cli import build_parser
from common_lore_index.ops.ingestion.acquisition_contracts import non_publication_result


def test_non_publication_result_is_explicit_and_public_safe() -> None:
    result = non_publication_result()

    assert result.to_jsonable() == {
        "contract_version": 1,
        "result": "acquisition_only",
        "publication_state": "not_published",
        "graph_mutated": False,
        "freshness_updated": False,
    }


@pytest.mark.parametrize(
    ("command", "selector"),
    (
        (("sources", "ingest-feed"), "--repository-name"),
        (("sources", "import-archive"), "--git-commit"),
        (("sources", "import-warc"), "--psql-command"),
        (("bulk", "import"), "--repository-name"),
        (("api", "acquire"), "--pg-host"),
        (("github", "acquire"), "--pg-database"),
    ),
)
def test_acquisition_commands_reject_mutation_only_selectors(
    command: tuple[str, str], selector: str
) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([*command, "--config", "fixture.toml", selector, "value"])


def test_canonical_only_load_command_is_retired() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["storage", "load-canonical"])


def test_scale7_report_is_version_two_and_staged_only() -> None:
    source = Path("tools/scale7_measure_ingestion.py").read_text(encoding="utf-8")

    assert '"schema": "scale7.synthetic.v2"' in source
    assert '"staged": staged' in source
    assert '"current": current' not in source
    assert "def _run_current(" not in source


def test_acquisition_loaders_report_invalid_utf8_as_policy_error(tmp_path: Path) -> None:
    from common_lore_index.ops.ingestion.api import ApiPolicyError, load_api_source_config
    from common_lore_index.ops.ingestion.bulk import BulkPolicyError, load_bulk_source_config
    from common_lore_index.ops.ingestion.github_api import GitHubApiPolicyError, load_github_api_source_config

    config = tmp_path / "config.toml"
    config.write_bytes(b"# fixture\xff")
    for loader, error in (
        (load_api_source_config, ApiPolicyError),
        (load_bulk_source_config, BulkPolicyError),
        (load_github_api_source_config, GitHubApiPolicyError),
    ):
        with pytest.raises(error, match="invalid UTF-8") as raised:
            loader(config)
        assert "fixture" not in str(raised.value)
