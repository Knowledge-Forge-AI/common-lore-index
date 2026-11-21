"""High-level BODY worker validation using the maintained official helper."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from retrieval_body_study_closure import _digest_map, worker_closure_sha256
from retrieval_body_study_closure_paths import BODY_WORKER_SOURCE_FILES
from retrieval_body_study_contracts import MAX_SOURCE_CLOSURE_BYTES, WORKER_CLOSURE_FILES, refuse


def fresh_worker_closure(root: Path) -> dict[str, Any]:
    """Use the maintained worker helper and expose its exact bytes/digests."""
    try:
        from retrieval_public_text_sources import worker_closure
        closure = worker_closure(root)
    except (OSError, ValueError, ImportError) as error:
        raise ValueError("body worker closure unavailable") from error
    files = dict(closure.files)
    digests = _digest_map(closure.digests, expected=WORKER_CLOSURE_FILES)
    refuse(set(files) == set(digests) == set(BODY_WORKER_SOURCE_FILES)
           and len(BODY_WORKER_SOURCE_FILES) == WORKER_CLOSURE_FILES,
           "body worker closure names")
    refuse(closure.name == "worker" and worker_closure_sha256(digests) == closure.identity,
           "body worker closure identity")
    total = sum(len(raw) for raw in files.values())
    refuse(total <= MAX_SOURCE_CLOSURE_BYTES, "body worker closure bytes")
    return {"name": "worker", "files": files, "digests": digests,
            "member_count": len(files), "total_bytes": total,
            "identity": closure.identity}



def require_worker_closure(root: Path, expected: str) -> dict[str, Any]:
    closure = fresh_worker_closure(root)
    refuse(closure["identity"] == expected, "body worker closure drift")
    return closure
