"""Retain before/after custody for the fixed matched attempt without replay authority."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from retrieval_body_study_files import publish_member
from retrieval_body_study_journal_io import digest, locked, members, publish, read_event, root_identity
from retrieval_w5_matched_inputs import require_match


class MatchedJournal:
    """One exclusively reserved event chain using the existing body journal IO."""

    def __init__(self, root: Path, binding: Mapping[str, Any]) -> None:
        self.root = root
        self._parent_id = root_identity(root)
        self._events = root / "events"
        self._events.mkdir(mode=0o700)
        self._events_id = root_identity(self._events)
        publish_member(self._events, ".lock", b"")
        self._digests: list[str] = []
        self.append("prepared", binding)

    def append(self, kind: str, payload: Mapping[str, Any]) -> None:
        require_match(root_identity(self.root) == self._parent_id, "journal_parent_replaced")
        with locked(self._events, self._events_id):
            entries = members(self._events)
            index = len(self._digests)
            require_match(len(entries) == index and index <= 145, "journal_event_census")
            previous = self._digests[-1] if index else None
            if entries:
                require_match(digest(read_event(self._events / entries[-1])) == previous, "journal_tail_changed")
            event = {"index": index, "previous": previous, "kind": kind, "payload": dict(payload)}
            publish(self._events, index, event)
            check = read_event(self._events / f"{index:05d}.json")
            require_match(check == event, "journal_event_readback")
            self._digests.append(digest(check))

    def seal(self) -> str:
        """Bind every retained event. Prepared-only chains are valid for refusals."""
        require_match(root_identity(self.root) == self._parent_id, "journal_parent_replaced")
        with locked(self._events, self._events_id):
            chain = _inspect_chain(self._events)
        require_match(chain == self._digests, "journal_history_changed")
        return chain[-1]


def _inspect_chain(root: Path) -> list[str]:
    names = members(root)
    require_match(1 <= len(names) <= 146, "journal_event_census")
    history: list[str] = []
    for index, name in enumerate(names):
        row = read_event(root / name)
        require_match(row.get("index") == index and row.get("previous") == (history[-1] if history else None),
                      "journal_broken_chain")
        history.append(digest(row))
    return history


def revalidate_event_seal(root: Path, expected: str) -> None:
    """Verify the closed chain beside the ranking journal before accepting grade."""
    events = root / "events"
    with locked(events, root_identity(events)):
        require_match(_inspect_chain(events)[-1] == expected, "journal_history_changed")
