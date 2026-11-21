"""Closed publication context and event-transition identity, without runtime side effects."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ci.d12_system_grant_io import D12SystemGrantError, is_hex40, is_hex64, is_strict_int

D12_CONTEXT_KEYS = frozenset({
    "source_sha256", "recipe_sha256", "dockerfile_sha256", "context_file_count"})


def validate_d12_context(ctx: object, *, sdk_recipe_sha256: str) -> dict[str, Any]:
    if not isinstance(ctx, Mapping) or set(ctx.keys()) != D12_CONTEXT_KEYS:
        raise D12SystemGrantError("d12_context keys do not match closed schema")
    src, rec, df = ctx.get("source_sha256"), ctx.get("recipe_sha256"), ctx.get("dockerfile_sha256")
    cnt = ctx.get("context_file_count")
    if not (is_hex64(src) and is_hex64(rec) and is_hex64(df)):
        raise D12SystemGrantError("d12_context digests must be 64 lowercase hex")
    if len({src, rec, df, sdk_recipe_sha256}) != 4:
        raise D12SystemGrantError("d12_context and sdk_recipe_sha256 digests must be distinct")
    if not is_strict_int(cnt) or int(cnt) <= 0:
        raise D12SystemGrantError("d12_context.context_file_count must be positive int")
    return dict(ctx)


def publication_transition(
    event: Mapping[str, Any], grant: Mapping[str, Any], *, base_sha: object, head_sha: object,
) -> tuple[str, str]:
    """Return declared baseline and observed head; never fabricate synchronize fields."""
    for label, value in (("pull_request.base.sha", base_sha), ("pull_request.head.sha", head_sha)):
        if not is_hex40(value):
            raise D12SystemGrantError(f"{label} must be a 40-hex lowercase string")
    if base_sha != grant["base"]:
        raise D12SystemGrantError("pull_request.base.sha does not match grant base")
    action = event.get("action")
    if action != grant["event_action"]:
        raise D12SystemGrantError("event action does not match grant")
    before = grant["before"]
    if action == "opened":
        if "before" in event or "after" in event:
            raise D12SystemGrantError("opened event before/after fields must be absent")
        if before != base_sha:
            raise D12SystemGrantError("initial publication baseline must equal base")
        after = head_sha
    elif action == "synchronize":
        if before == base_sha:
            raise D12SystemGrantError("synchronize before/base must be distinct")
        for key in ("before", "after"):
            if not is_hex40(event.get(key)):
                raise D12SystemGrantError(f"event.{key} must be a 40-hex lowercase string")
        if event["before"] != before:
            raise D12SystemGrantError("event before does not match grant before")
        after = event["after"]
        if after != head_sha:
            raise D12SystemGrantError("synchronize event after must equal head.sha")
    else:
        raise D12SystemGrantError("unsupported publication event action")
    if not is_hex40(before) or after == before:
        raise D12SystemGrantError("publication head must advance past before")
    return str(before), str(after)
