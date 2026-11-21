"""Host Docker conservation observations never confer cleanup ownership."""
from __future__ import annotations

from collections.abc import Callable
import re

from retrieval_supplement_model_host import _bounded_docker
from retrieval_supplement_study_contracts import refuse

_IDENTITY = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")


def snapshot(*, charge_bytes: Callable[[int], None], charge_operation: Callable[[str], None],
             timeout: Callable[[], float]) -> dict[str, list[str]]:
    commands = {
        "containers": ("docker", "container", "ls", "--all", "--quiet", "--no-trunc"),
        "images": ("docker", "image", "ls", "--all", "--quiet", "--no-trunc"),
        "volumes": ("docker", "volume", "ls", "--quiet"),
        "networks": ("docker", "network", "ls", "--quiet", "--no-trunc"),
    }
    result = {}
    for name, command in commands.items():
        output = _bounded_docker(command, min(30., timeout()), 2 * 1024**2, charge_operation, charge_bytes)
        refuse(output.returncode == 0, "host_inventory:unavailable")
        values = output.stdout.decode("ascii").split()
        # Volume names can be user-selected and are retained only privately.
        # All other surfaces use Engine's exact immutable IDs.
        if name != "volumes":
            refuse(all(_IDENTITY.fullmatch(value) is not None for value in values),
                   "host_inventory:identity_shape")
        result[name] = sorted(set(values))
    return result
