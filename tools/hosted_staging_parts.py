"""Closed selection and identities for the concurrent hosted staging jobs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

MEASURED_PARTS = ("m0", "m1", "m2")
INTEGRATION_PARTS = (*MEASURED_PARTS, "a")
PARTS = ("smoke", *INTEGRATION_PARTS)


def invocation_id(run: str, attempt: str, commit: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run) or not re.fullmatch(r"[1-9][0-9]{0,9}", attempt):
        raise ValueError("invalid hosted staging run identity")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("invalid hosted staging candidate")
    return hashlib.sha256(f"hosted-staging-v1:{run}:{attempt}:{commit}".encode()).hexdigest()[:32]


def nodes_for_part(part: str, measured: list[str], abrupt: list[str]) -> tuple[str, ...]:
    if part == "a":
        return tuple(abrupt)
    index = MEASURED_PARTS.index(part)
    return tuple(
        node for node in measured
        if int.from_bytes(hashlib.sha256(node.split("::", 1)[0].encode()).digest()[:8])
        % len(MEASURED_PARTS) == index
    )


def validate_options(args: Any) -> None:
    part = getattr(args, "hosted_staging_part", None)
    marker = getattr(args, "hosted_staging_invocation", None)
    if part is None and marker is None:
        return
    if (
        part not in PARTS or not isinstance(marker, str) or not re.fullmatch(r"[0-9a-f]{32}", marker)
        or args.suite != "staging" or args.no_coverage or args.threshold is not None or not args.report
        or getattr(args, "smoke_image_reference", None) is not None
    ):
        raise RuntimeError("hosted staging parts require the covered canonical staging profile")


def record_smoke(args: Any) -> None:
    from runner_integration_execution import candidate_identity
    from staging_report_contract import persist_report

    persist_report(Path(args.report_dir) / "smoke.json", {
        "invocation_id": args.hosted_staging_invocation,
        "candidate": candidate_identity(Path(__file__).resolve().parents[1]),
        "smoke": "passed",
    })
