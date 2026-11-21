"""Private bounded Python-stream prefixes; never native-FD or qualification evidence."""
from __future__ import annotations

import base64
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import re
from typing import Any, Iterator

from runner_pgvector_budget import refuse
from runner_pgvector_declaration import write_once
from runner_pgvector_prebuilt import json_bytes

REPORT_ROOT = Path("/sandbox-report/report")
PREFIX_LIMIT = 64 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class PrefixStream(io.TextIOBase):
    def __init__(self) -> None:
        super().__init__()
        self.prefix = bytearray()
        self.emitted_characters = 0
        self.retained_characters = 0
        self.truncated = False

    encoding = "utf-8"

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("text stream requires str")
        self.emitted_characters += len(text)
        if not self.truncated:
            room = PREFIX_LIMIT - len(self.prefix)
            bounded = text[:room].encode("utf-8", errors="replace")[:room]
            kept = bounded.decode("utf-8", errors="ignore")
            self.prefix.extend(kept.encode("utf-8"))
            self.retained_characters += len(kept)
            self.truncated = len(kept) < len(text)
        return len(text)

    def flush(self) -> None:
        pass

    def facts(self) -> dict[str, Any]:
        return {"prefix_base64": base64.b64encode(self.prefix).decode("ascii"),
            "retained_bytes": len(self.prefix), "emitted_characters": self.emitted_characters,
            "dropped_characters": self.emitted_characters - self.retained_characters,
            "truncated": self.truncated, "encoding": "utf-8-replacement"}


class CollectionCapture:
    def __init__(self, request: dict[str, Any], plan: dict[str, Any]) -> None:
        self.request, self.plan = request, plan
        self.stdout, self.stderr = PrefixStream(), PrefixStream()

    @contextmanager
    def streams(self) -> Iterator[None]:
        with redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            yield

    def persist(self, exit_code: int, error_type: str | None) -> None:
        invocation = self.request["invocation_id"]
        refuse(isinstance(invocation, str) and _ID.fullmatch(invocation) is not None,
               "offline discovery capture identity")
        refuse(not any(p.is_symlink() for p in (REPORT_ROOT, *REPORT_ROOT.parents)),
               "offline discovery capture directory link")
        REPORT_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        refuse(REPORT_ROOT.stat().st_uid == os.getuid(), "offline discovery capture directory owner")
        payload = {"schema": "cloe-offline-discovery-capture-v1", "invocation_id": invocation,
            "role": self.plan["role"], "candidate_head": self.plan["candidate_head"],
            "pytest_exit": exit_code, "error_type": error_type, "prefix_limit_bytes": PREFIX_LIMIT,
            "stdout": self.stdout.facts(), "stderr": self.stderr.facts(),
            "private_only": True, "native_fd_or_subprocess_capture": False,
            "qualification_evidence": False}
        write_once(REPORT_ROOT / ("offline-discovery-capture." + invocation + ".json"), json_bytes(payload))
