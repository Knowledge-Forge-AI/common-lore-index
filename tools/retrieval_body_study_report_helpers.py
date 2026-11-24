"""Public-safe report serialization helpers for the actual body owner."""
from __future__ import annotations

import json
from pathlib import Path
from retrieval_body_study_contracts import BODY_REPORT_MEMBER
from retrieval_body_study_files import publish_member
from retrieval_body_study_report import validate_actual_body_report
from retrieval_body_study_values import require


def encode_body_report(report: object) -> bytes:
    checked = validate_actual_body_report(report)
    return json.dumps(checked, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def body_report_sha256(report: object) -> str:
    checked = validate_actual_body_report(report)
    return str(checked["report_identity"])


def write_body_report(
    report_root: Path, report: object,
) -> str:
    """Publish one validated report and return its pre-journal identity."""
    require(isinstance(report_root, Path) and report_root.is_absolute(), "body.report:root")
    raw = encode_body_report(report)
    publish_member(report_root, BODY_REPORT_MEMBER, raw)
    return body_report_sha256(report)


__all__ = ["body_report_sha256", "encode_body_report", "write_body_report"]
