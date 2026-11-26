#!/usr/bin/env python3
"""Run one no-product-test probe of the owned integration sandbox boundary."""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
TEST_SUPPORT_ROOT = REPO_ROOT / "src" / "test" / "support" / "python"
for path in (REPO_ROOT, TOOLS_ROOT, TEST_SUPPORT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

DOCKERFILE = REPO_ROOT / "tools" / "test_sandbox" / "Dockerfile"


def main() -> int:
    from common_lore_index_test_support.unit_purity import require_live_resource_allowed

    require_live_resource_allowed("integration sandbox infrastructure probe")
    from test_sandbox import ensure_sandbox_image, run_in_sandbox
    from test_sandbox_entrypoint import INFRASTRUCTURE_PROBE_FLAG, INFRASTRUCTURE_PROBE_PAYLOAD

    image_id = ensure_sandbox_image(dockerfile=DOCKERFILE)
    return run_in_sandbox(
        [INFRASTRUCTURE_PROBE_FLAG, *INFRASTRUCTURE_PROBE_PAYLOAD],
        repo_root=REPO_ROOT,
        image_id=image_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
