"""Source checkout verification for the offline qualification path."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any

from offline_qualification_files import read_regular
from offline_qualification_values import digest, refuse, safe_path


MAX_PLAN = 2 * 1024**2


def verify_checkout(root: Path, expected: dict[str, Any], head: str) -> None:
    def git(*args: str) -> bytes:
        result = subprocess.run(["git", "-c", "safe.directory=" + str(root.resolve()), *args], cwd=root, capture_output=True, check=False,
                                timeout=30, env=os.environ | {"GIT_OPTIONAL_LOCKS": "0"})
        refuse(result.returncode == 0 and len(result.stdout) <= MAX_PLAN, "source inventory command")
        return result.stdout

    refuse(git("rev-parse", "HEAD").decode().strip() == head, "source HEAD drift")
    names = {name.decode() for name in git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
             .split(b"\0") if name}
    refuse(names == set(expected), "source inventory drift")
    for name, row in expected.items():
        path = root / safe_path(name)
        if "symlink" in row:
            refuse(set(row) == {"symlink"} and path.is_symlink()
                   and os.readlink(path) == row["symlink"], "source link drift")
        else:
            refuse(set(row) == {"sha256", "mode"} and not path.is_symlink()
                   and path.stat().st_mode & 0o777 == row["mode"]
                   and digest(read_regular(path, 8 * 1024**2)) == row["sha256"], "source content drift")
