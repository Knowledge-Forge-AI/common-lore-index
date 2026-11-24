"""Close the lexical-only launcher around one offline core sandbox."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from offline_qualification_checkout import MAX_PLAN, verify_checkout
from offline_qualification_files import private_file, read_regular
from offline_qualification_values import (
    BASE_CONFIG, LEXICAL_REPLAY_CAPS, SDK_IMAGE, digest, json_bytes, safe_path, strict_object,
)
from runner_pgvector_budget import Allocation, MAX_ARCHIVE
from runner_pgvector_context import OfflineContext
from runner_pgvector_prebuilt import provider_facts
from runner_pgvector_sandbox import OfflineSandbox, checked_image
from runner_pgvector_transport import ROOT, send_file, send_plan
from retrieval_supplement_files import publish_member
from test_sandbox_arguments import SandboxArgumentsResult


LEXICAL_REPLAY_ARG = "--repomap-lexical-replay"
ROLE = "lexical-replay"
PHASE = "CLOE-W5-LEXICAL-REPLAY2"
PLAN_SCHEMA = "cloe-lexical-replay-plan-v1"
INNER_SCHEMA = "cloe-lexical-replay-inner-v1"
INPUT_PIN = "sha256:0b62374c086e96309d66c32999a8928981942eea38cca2f976880f851c6e378b"
INNER_PLAN = Path("/sandbox-scratch/lexical-replay-plan.json")
PROTOCOL = "docs/specs/cloe-w5-lexical-replay-v1.md"
MAX_SOURCE_BYTES = 128 * 1024**2
PARENT_FIELDS = {"archive", "approval_sha256", "review_sha256"}
SHARED_FIELDS = {"schema", "phase", "role", "attempt", "sdk_image", "core_image", "candidate_head",
                 "candidate_snapshot", "protocol_sha256", "input_pin_sha256", "schedule"}
LIMITS = {
    "outer_seconds": 1200, "scratch_bytes": 8 * 1024**3, "query_timeout_ms": 2000,
    "build_timeout_ms": 30000, "ranked_calls": 48, "protocol_readbacks": 2,
    "publication_starts": 1, "lexical_builds": 1, "pg_lifecycles": 1, "sdk_starts": 1,
    "read_bytes": LEXICAL_REPLAY_CAPS["read_bytes"], "transfer_bytes": LEXICAL_REPLAY_CAPS["transfer_bytes"],
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def gate(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(f"lexical replay refused: {reason}")


def validate_plan(plan: dict[str, Any], *, inner: bool = False) -> None:
    fields = SHARED_FIELDS if inner else SHARED_FIELDS | PARENT_FIELDS
    gate(set(plan) == fields and plan["schema"] == (INNER_SCHEMA if inner else PLAN_SCHEMA)
         and plan["phase"] == PHASE and plan["role"] == ROLE, "closed plan shape")
    gate(type(plan["attempt"]) is str and re.fullmatch(r"lexical-[a-z0-9-]{1,40}", plan["attempt"]) is not None,
         "attempt identity")
    gate(plan["sdk_image"] == SDK_IMAGE and plan["core_image"] == BASE_CONFIG
         and plan["input_pin_sha256"] == INPUT_PIN, "runtime or input identity")
    gate(type(plan["candidate_head"]) is str and re.fullmatch(r"[0-9a-f]{40}", plan["candidate_head"]) is not None
         and isinstance(plan["candidate_snapshot"], dict) and bool(plan["candidate_snapshot"]),
         "source identity")
    gate(type(plan["protocol_sha256"]) is str and _DIGEST.fullmatch(plan["protocol_sha256"]) is not None,
         "protocol identity")
    schedule = plan["schedule"]
    gate(isinstance(schedule, dict) and schedule == LIMITS
         and all(type(schedule[key]) is int for key in LIMITS), "finite schedule")
    if inner:
        return
    for key in ("approval_sha256", "review_sha256"):
        gate(type(plan[key]) is str and _DIGEST.fullmatch(plan[key]) is not None, "receipt identity")
    descriptor = plan["archive"]
    gate(isinstance(descriptor, dict) and set(descriptor) == {"bytes", "sha256"}
         and type(descriptor["bytes"]) is int and 0 < descriptor["bytes"] <= MAX_ARCHIVE
         and isinstance(descriptor["sha256"], str) and _DIGEST.fullmatch(descriptor["sha256"]) is not None,
         "core archive descriptor")


def check_source(root: Path, plan: dict[str, Any]) -> None:
    verify_checkout(root, plan["candidate_snapshot"], plan["candidate_head"])
    gate(digest(read_regular(root / PROTOCOL, 32 * 1024)) == plan["protocol_sha256"],
         "protocol bytes changed")


class LexicalReplayContext(OfflineContext):
    def allocation(self) -> Allocation:
        return Allocation(self.plan_path.parent / "allocation.json", LEXICAL_REPLAY_CAPS)


def _receipt(path: Path, expected_digest: str) -> dict[str, Any]:
    private_file(path)
    raw = read_regular(path, 64 * 1024)
    gate(digest(raw) == expected_digest, "authority receipt changed")
    return strict_object(raw)


def host_context(path: Path, root: Path) -> LexicalReplayContext:
    gate(path.is_absolute() and path == path.resolve() and path.name == "plan.json",
         "private plan path")
    gate(path.parent.stat().st_uid == os.getuid() and path.parent.stat().st_mode & 0o777 == 0o700,
         "private attempt directory")
    private_file(path)
    raw = read_regular(path, MAX_PLAN)
    plan = strict_object(raw)
    validate_plan(plan)
    approval = _receipt(path.parent / "approval.json", plan["approval_sha256"])
    review = _receipt(path.parent / "review.json", plan["review_sha256"])
    gate(approval == {"schema": "cloe-lexical-replay-approval-v1",
                      "selection": "A", "protocol_sha256": plan["protocol_sha256"],
                      "input_pin_sha256": INPUT_PIN}, "operator selection")
    expected = {key: plan[key] for key in ("attempt", "candidate_head", "protocol_sha256", "sdk_image",
                                          "core_image", "archive", "schedule")}
    expected["candidate_snapshot_sha256"] = digest(json_bytes(plan["candidate_snapshot"]))
    gate(review == {"schema": "cloe-lexical-replay-review-v1", "disposition": "accepted",
                    "candidate": expected}, "independent source and preflight receipt")
    archive = path.parent / "core-image.tar"
    private_file(archive)
    gate(archive.stat().st_size == plan["archive"]["bytes"], "core archive size")
    gate(not any((path.parent / name).exists() for name in ("report", "plan.inner.json", "consumed.json")),
         "attempt already used")
    context = LexicalReplayContext(path, plan, digest(raw), root)
    count = sum((root / safe_path(name)).lstat().st_size for name in plan["candidate_snapshot"])
    gate(0 < count <= MAX_SOURCE_BYTES, "source byte bound")
    with context.allocation().session() as meter:
        gate(not any(meter.used.values()) and not meter.records, "allocation already used")
        # Reserve both host source checks, the inner source check and its final verification.
        meter.charge("read_bytes", 4 * count + MAX_PLAN)
        meter.charge("read_bytes", archive.stat().st_size)
        with archive.open("rb") as file:
            gate(hashlib.file_digest(file, "sha256").hexdigest() == plan["archive"]["sha256"],
                 "core archive digest")
        check_source(root, plan)
        publish_member(path.parent, "consumed.json", json_bytes({
            "attempt": plan["attempt"], "plan_sha256": context.raw_sha256,
        }))
    return context


def load_inner(expected: str, *, root: Path) -> dict[str, Any]:
    from test_sandbox import active_sandbox

    gate(active_sandbox() and root == Path("/workspace"), "authenticated inner sandbox required")
    private_file(INNER_PLAN)
    raw = read_regular(INNER_PLAN, MAX_PLAN)
    gate(digest(raw) == expected, "sealed inner plan digest")
    plan = strict_object(raw)
    validate_plan(plan, inner=True)
    check_source(root, plan)
    return plan


class LexicalReplaySandbox(OfflineSandbox):
    """Reuse canonical ownership, offline transport, boundary and exact cleanup."""

    def report_arguments(self, argv: list[str], *, repo_root: Path) -> SandboxArgumentsResult:
        self.context.fixed()
        gate(argv == [LEXICAL_REPLAY_ARG] and repo_root == self.context.repo_root, "exact launcher argv")
        return SandboxArgumentsResult(argv, self.context.plan_path.parent / "report")

    def record_owner(self, container_id: str, image_id: str, marker: str) -> None:
        """Retain the identity emitted by the canonical sandbox owner at creation."""
        self.context.fixed()
        gate(_DIGEST.fullmatch(container_id) is not None
             and _DIGEST.fullmatch(marker) is not None and image_id == SDK_IMAGE, "owner identity")
        publish_member(self.context.plan_path.parent, "outer-owner.json", json_bytes({
            "plan_sha256": self.context.raw_sha256, "sdk_image": image_id,
            "container_id": container_id, "marker_sha256": digest(marker.encode()),
        }))

    def inner_document(self) -> dict[str, Any]:
        projected = {key: self.context.plan[key] for key in SHARED_FIELDS}
        projected["schema"] = INNER_SCHEMA
        validate_plan(projected, inner=True)
        return projected

    def outer_command(self, command: list[str]) -> list[str]:
        self.context.fixed()
        gate(command.count(SDK_IMAGE) == 1, "exact sdk")
        with self.context.allocation().session() as meter:
            meter.charge("sdk_lexical_replay", 1)
        index = command.index(SDK_IMAGE)
        return [*command[:index], "--pull=never", "--network", "none", "--env", "GOTOOLCHAIN=local",
                "--env", "GOPROXY=off", "--env", "PIP_NO_INDEX=1",
                "--env", "CLOE_LEXICAL_REPLAY_SEAL=" + digest(json_bytes(self.inner_document())),
                *command[index:]]

    def prepare(self, _runner: Any, outer: str) -> None:
        """Import only the exact core member in the fresh inner daemon."""
        self.context.fixed()
        gate(self.command(outer, "docker", "info", "--format", "{{.Driver}} {{.Architecture}}").stdout.strip()
             in {"overlay2 aarch64", "overlay2 arm64"}, "native core daemon identity")
        existing = self.command(outer, "docker", "image", "ls", "--all", "--quiet", "--no-trunc").stdout
        gate(not existing.strip(), "fresh daemon inventory")
        # This is a shared bounded transport directory. Its pgvector entrypoint
        # is unavailable to the lexical launcher.
        self.command(outer, "python3", "-c", f"import os; os.mkdir({str(ROOT)!r}, mode=0o700)")
        path = self.context.plan_path.parent / "core-image.tar"
        descriptor = self.context.plan["archive"]
        with self.context.allocation().session() as meter:
            meter.charge("core_imports", 1)
            send_file(outer, path, "image.tar", descriptor, meter)
            meter.charge("read_bytes", descriptor["bytes"])
            self.command(outer, "docker", "image", "load", "--quiet", "--input", str(ROOT / "image.tar"))
            current = self.command(outer, "docker", "image", "ls", "--all", "--quiet", "--no-trunc").stdout
            gate(set(current.split()) == {BASE_CONFIG}, "core-only daemon inventory")
            image, = json.loads(self.command(outer, "docker", "image", "inspect", BASE_CONFIG).stdout)
            raw = "".join(provider_facts()["base_config_utf8_chunks"]).encode()
            gate("sha256:" + digest(raw) == BASE_CONFIG, "core configuration provenance")
            checked_image(image, {"derived": {"base_config_document": strict_object(raw)}}, base=True)
            send_plan(outer, self.inner_document(), self.context.plan_path.with_suffix(".inner.json"), meter)
            self.command(outer, "mv", str(ROOT / "plan.json"), str(INNER_PLAN))
        self.probe_image = BASE_CONFIG
        self.command(outer, "rm", str(ROOT / "image.tar"))

    def run_tests(self, runner: Any, outer: str) -> int:
        with self.context.allocation().session() as meter:
            meter.charge("postgres_lifecycles", 1)
        return super().run_tests(runner, outer)

    def finished(self, result: int) -> None:
        self.context.fixed()
        check_source(self.context.repo_root, self.context.plan)
        with self.context.allocation().session() as meter:
            meter.bind("lexical_replay_sdk_result", {"exit": result, "cleanup_proven": result == 0,
                       "candidate_head": self.context.plan["candidate_head"],
                       "candidate_snapshot_sha256": digest(json_bytes(self.context.plan["candidate_snapshot"]))})
