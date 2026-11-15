"""Managed sandbox image, recipe, and volume accounting for pgvector test profiles."""

from __future__ import annotations

import hashlib
from typing import Mapping

from test_sandbox_contract import IMAGE_OWNER_LABEL, IMAGE_RECIPE_LABEL
from runner_pgvector_profile import EXPECTED_VERSION, PGVECTOR_PROFILE
from runner_pgvector_prebuilt import (
    BASE_MANIFEST, OWNER_LABEL, OWNER_VALUE, PACKAGE_SHA256, PROVIDER_LABEL,
    RECIPE_LABEL, recipe_bytes,
)

PGVECTOR_SANDBOX_TAG = f"repomap-test-postgres:16-pgvector{EXPECTED_VERSION.replace('.', '')}-v1"
PGVECTOR_PROFILE_LABEL = "org.repomap.test.profile"


def generate_pgvector_recipe_definition(*, base_image: str = BASE_MANIFEST) -> str:
    """Describe the exact offline layer recipe; never acquire or compile a provider."""
    if base_image != BASE_MANIFEST:
        raise RuntimeError("pgvector recipe requires the exact admitted ARM Trixie base")
    return recipe_bytes().decode("utf-8")


def compute_recipe_hash(recipe_definition: str | None = None) -> str:
    """Compute deterministic SHA-256 hash of the recipe definition."""
    content = recipe_definition or generate_pgvector_recipe_definition()
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def pgvector_sandbox_labels(
    *,
    recipe_hash: str | None = None,
    run_id: str | None = None,
) -> dict[str, str]:
    """Produce labels for pgvector container, image, and volume materialization."""
    effective_recipe = recipe_hash or compute_recipe_hash()
    labels = {
        "org.repomap.test": "true",
        "org.repomap.component": "postgres",
        PGVECTOR_PROFILE_LABEL: PGVECTOR_PROFILE,
        IMAGE_OWNER_LABEL: "true",
        IMAGE_RECIPE_LABEL: effective_recipe,
        OWNER_LABEL: OWNER_VALUE,
        PROVIDER_LABEL: PACKAGE_SHA256,
        RECIPE_LABEL: effective_recipe,
    }
    if run_id:
        labels["org.repomap.test.run_id"] = run_id
    return labels


def verify_pgvector_asset_ownership(labels: object) -> bool:
    """Verify that a Docker asset possesses valid RepoMap pgvector ownership labels."""
    if not isinstance(labels, Mapping):
        return False
    if labels.get(IMAGE_OWNER_LABEL) != "true":
        return False
    if labels.get(PGVECTOR_PROFILE_LABEL) != PGVECTOR_PROFILE:
        return False
    recipe = labels.get(IMAGE_RECIPE_LABEL)
    if recipe != compute_recipe_hash():
        return False
    return (labels.get(OWNER_LABEL) == OWNER_VALUE
            and labels.get(PROVIDER_LABEL) == PACKAGE_SHA256
            and labels.get(RECIPE_LABEL) == recipe)
