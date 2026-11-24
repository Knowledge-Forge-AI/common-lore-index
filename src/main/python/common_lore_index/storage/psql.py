"""psql execution and JSON result parsing for storage helpers."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from itertools import islice
from typing import Any, Literal as _Literal, TypedDict as _TypedDict

from common_lore_index.extractors.shared.scanner import iter_text_lines
from common_lore_index.storage.errors import StorageSchemaError

__all__ = (
    "run_psql",
    "psql_failure_message",
    "parse_psql_json",
    "last_output_line",
)

PSQL_LAUNCH_ERROR = "unable to launch psql command"
PSQL_INTERRUPTED_ERROR = "psql command interrupted"
_PSQL_FAILURE_DETAIL_LIMIT = 512
_PSQL_FAILURE_TRUNCATION_SUFFIX = " ... [truncated]"
_LINE_ENDINGS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")


class _RunOptions(_TypedDict, total=False):
    check: bool
    stdout: int
    stderr: int
    text: _Literal[True]
    input: str
    env: dict[str, str]


def run_psql(
    command: Sequence[str],
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    kwargs: _RunOptions = {
        "check": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if input_text is not None:
        kwargs["input"] = input_text
    if env is not None:
        kwargs["env"] = env
    try:
        return subprocess.run(list(command), **kwargs)
    except subprocess.CalledProcessError as error:
        raise StorageSchemaError(psql_failure_message(error)) from error
    except KeyboardInterrupt:
        raise StorageSchemaError(PSQL_INTERRUPTED_ERROR) from None
    except OSError as error:
        raise StorageSchemaError(PSQL_LAUNCH_ERROR) from error


def psql_failure_message(error: subprocess.CalledProcessError) -> str:
    details = (error.stderr or error.stdout or "").strip()
    if details:
        return f"psql failed: {_bounded_psql_failure_details(details)}"
    return f"psql failed with exit code {error.returncode}"


def _bounded_psql_failure_details(details: str) -> str:
    lines = list(islice(filter(None, map(str.strip, iter_text_lines(details))), 2))
    first_line = lines[0]
    if len(lines) == 1 and len(first_line) <= _PSQL_FAILURE_DETAIL_LIMIT:
        return first_line
    prefix_limit = _PSQL_FAILURE_DETAIL_LIMIT - len(
        _PSQL_FAILURE_TRUNCATION_SUFFIX
    )
    return (
        first_line[:prefix_limit].rstrip()
        + _PSQL_FAILURE_TRUNCATION_SUFFIX
    )


def parse_psql_json(stdout: str, label: str) -> Any:
    selected = last_output_line(stdout, label=label)
    try:
        return json.loads(selected)
    except (ValueError, RecursionError) as error:
        raise StorageSchemaError(f"psql did not return {label} as JSON") from error


def last_output_line(stdout: str, *, label: str | None = None) -> str:
    # Find the last nonblank line and keep any whitespace on that line.
    # A normal LF-delimited response can skip every preceding row.
    end = len(stdout)
    index = end - 1
    while index >= 0 and stdout[index].isspace():
        if stdout[index] in _LINE_ENDINGS:
            end = index
        index -= 1
    if index >= 0:
        start = stdout.rfind("\n", 0, index + 1) + 1
        tail = stdout[start:end]
        return tail.splitlines()[-1]
    if label is not None:
        raise StorageSchemaError(f"psql did not return {label} as JSON")
    raise StorageSchemaError("psql did not return a load summary")
