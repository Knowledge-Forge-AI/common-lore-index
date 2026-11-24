"""Check-only front-end boundaries; no registered workload is executed."""

from __future__ import annotations

import pytest

from retrieval_body_check import _parser


def test_parser_has_only_check_and_verify_freeze_commands():
    parser = _parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run"])
    assert parser.parse_args(["check", "--input-root", "/x", "--predecessor-root", "/y",
                              "--source-root", "/z"]).command == "check"
    assert parser.parse_args(["verify-freeze", "--input-root", "/x", "--predecessor-root", "/y",
                              "--source-root", "/z", "--freeze-root", "/f",
                              "--freeze-member", "freeze.json"]).command == "verify-freeze"


def test_check_parser_requires_explicit_roots_and_does_not_offer_execution_flags():
    parser = _parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["check"])
    with pytest.raises(SystemExit):
        parser.parse_args(["check", "--input-root", "/x", "--predecessor-root", "/y",
                           "--source-root", "/z", "--run-model"])
