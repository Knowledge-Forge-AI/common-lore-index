"""Installed command entry points for Common Lore Index.

Each command names its brand explicitly; nothing is inferred from ``argv[0]``.
Both delegate, at call time, to the one canonical ``common_lore_index.cli``
``main``, so importing this module loads no CLI implementation or driver.
"""

from __future__ import annotations

CLOE_PROG = "cloe"
LONG_PROG = "common-lore-index"


def _canonical_main(argv: list[str] | None, prog: str) -> int:
    from common_lore_index import cli

    return cli.main(argv, prog=prog)


def cloe_main(argv: list[str] | None = None) -> int:
    return _canonical_main(argv, CLOE_PROG)


def long_main(argv: list[str] | None = None) -> int:
    return _canonical_main(argv, LONG_PROG)
