"""Closed static body facts and matching-metadata distractor record shapes."""

from __future__ import annotations

from typing import Any

from retrieval_body_values import check_keys, digest_value, integer, require, safe_relative_path, text

WITNESS_FIELDS = (
    "binding_id", "source_relative_path", "source_sha256", "canonical_key", "raw_source_id",
    "kind", "display_name", "start_line", "end_line", "fact_utf8", "fact_start_line",
    "fact_end_line", "fact_source_start_byte", "fact_source_end_byte", "model_text_sha256",
    "fact_model_start_byte", "fact_model_bytes", "model_text_bytes", "tokenizer_count", "truncation_survival",
)


def parse_witness(value: object) -> dict[str, Any]:
    """Shape checks are not source correspondence or tokenizer survival evidence."""
    row = dict(check_keys(value, WITNESS_FIELDS, "body.witness"))
    for name, maximum in (("binding_id", 256), ("canonical_key", 512), ("raw_source_id", 1024), ("display_name", 256)):
        text(row[name], "body.witness." + name, maximum)
    safe_relative_path(row["source_relative_path"])
    require(row["kind"] == "python.function", "body.witness:function_span_required")
    digest_value(row["source_sha256"], "body.witness.source")
    digest_value(row["model_text_sha256"], "body.witness.model_text")
    for name in ("start_line", "end_line", "fact_start_line", "fact_end_line"):
        integer(row[name], "body.witness." + name, 1, 10_000_000)
    require(row["start_line"] < row["fact_start_line"] <= row["fact_end_line"] <= row["end_line"], "body.witness:fact_outside_function_body")
    for name in ("fact_source_start_byte", "fact_source_end_byte", "fact_model_start_byte"):
        integer(row[name], "body.witness." + name, 0, 64 * 1024)
    integer(row["fact_model_bytes"], "body.witness.fact_model_bytes", 1, 4096)
    integer(row["model_text_bytes"], "body.witness.model_text_bytes", 1, 64 * 1024)
    fact = text(row["fact_utf8"], "body.witness.fact", 4096)
    require(row["fact_source_end_byte"] - row["fact_source_start_byte"] == len(fact.encode("utf-8")), "body.witness:fact_size")
    require(row["fact_model_start_byte"] + row["fact_model_bytes"] <= row["model_text_bytes"], "body.witness:model_fact_bounds")
    require(row["tokenizer_count"] is None and row["truncation_survival"] == "unobserved", "body.witness:inert_tokenizer_required")
    return row


def parse_distractor(value: object, *, family: str) -> dict[str, Any]:
    row = dict(check_keys(value, ("family", "witness", "metadata_fit_rationale"), "body.distractor"))
    require(row["family"] == family, "body.distractor:family")
    text(row["metadata_fit_rationale"], "body.distractor.rationale", 2048)
    row["witness"] = parse_witness(row["witness"])
    return row
