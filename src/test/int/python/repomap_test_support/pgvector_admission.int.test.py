"""Subprocess integration tests for host-side pgvector admission before sandbox dispatch."""

from __future__ import annotations

import os
import hashlib
import json
import platform
from pathlib import Path
import stat
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
RUN_TESTS = REPO_ROOT / "tools" / "run_tests.py"
for extra_path in (
    REPO_ROOT,
    REPO_ROOT / "tools",
    REPO_ROOT / "src" / "main" / "python",
    REPO_ROOT / "src" / "test" / "support" / "python",
):
    if str(extra_path) not in sys.path:
        sys.path.insert(0, str(extra_path))

from common_lore_index_test_support.test_scratch import ENV_RUN_ROOT, ENV_SCRATCH_ROOT
from runner_coverage_execution import prepare_child_coverage_environment
from test_sandbox_contract import ENV_ACTIVE, ENV_TOKEN, IMAGE_TAG


def _make_sentinel_docker(bin_dir: Path, sentinel_file: Path) -> None:
    docker_script = bin_dir / "docker"
    docker_script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{sentinel_file}"\n'
        "exit 99\n",
        encoding="utf-8",
    )
    docker_script.chmod(docker_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _host_subprocess_env(bin_dir: Path, scratch_root: Path) -> dict[str, str]:
    env = prepare_child_coverage_environment(base_env=os.environ, family="unmeasured")
    for sandbox_var in (
        ENV_ACTIVE,
        ENV_TOKEN,
        ENV_RUN_ROOT,
        "REPOMAP_UNIT_PURITY_GUARD",
        "CLOE_PGVECTOR_OFFLINE_PLAN",
    ):
        env.pop(sandbox_var, None)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env[ENV_SCRATCH_ROOT] = str(scratch_root)
    return env


@pytest.mark.parametrize(
    ("argv", "expected_stderr"),
    [
        (
            ["--suite", "int", "--no-coverage", "--pg-test-profile", "pgvector"],
            "canonical pgvector profile requires scoped test selector(s) after '--'",
        ),
        (
            ["--suite", "int", "--no-coverage", "--pg-test-profile", "pgvector", "--"],
            "canonical pgvector profile requires scoped test selector(s) after '--'",
        ),
        (
            [
                "--suite",
                "int",
                "--no-coverage",
                "--pg-test-profile",
                "pgvector",
                "--",
                "-m",
                "requires_pgvector",
                "src/test/int/python/repomap_kg/storage/semantic_candidates.int.test.py",
            ],
            "canonical marker selection is closed; forwarded -m/--markexpr is refused",
        ),
        (
            [
                "--suite",
                "int",
                "--no-coverage",
                "--pg-test-profile",
                "pgvector",
                "--",
                "--markexpr=requires_pgvector",
                "src/test/int/python/repomap_kg/storage/semantic_candidates.int.test.py",
            ],
            "canonical marker selection is closed; forwarded -m/--markexpr is refused",
        ),
        (
            [
                "--suite",
                "int",
                "--no-coverage",
                "--pg-test-profile",
                "pgvector",
                "--",
                "src/test/unit/python/tools/pgvector_profile.unit.test.py",
            ],
            "outside selected suite root(s): src/test/int/python",
        ),
    ],
)
def test_subprocess_host_admission_rejects_invalid_pgvector_before_docker_dispatch(
    tmp_path: Path,
    argv: list[str],
    expected_stderr: str,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sentinel = tmp_path / "docker-invoked.log"
    _make_sentinel_docker(bin_dir, sentinel)
    env = _host_subprocess_env(bin_dir, tmp_path / "scratch")

    result = subprocess.run(
        [sys.executable, str(RUN_TESTS), *argv],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert expected_stderr in result.stderr
    assert not sentinel.exists(), "Docker must not be invoked when host pgvector admission fails"
    assert not (tmp_path / "scratch").exists()


@pytest.mark.parametrize(
    "forwarded",
    [
        ["-k", "candidate"],
        ["src/test/int/python/repomap_kg/storage/semantic_candidates.int.test.py"],
    ],
)
def test_subprocess_host_admission_requires_offline_plan_before_docker_dispatch(
    tmp_path: Path,
    forwarded: list[str],
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sentinel = tmp_path / "docker-invoked.log"
    _make_sentinel_docker(bin_dir, sentinel)
    env = _host_subprocess_env(bin_dir, tmp_path / "scratch")

    result = subprocess.run(
        [
            sys.executable,
            str(RUN_TESTS),
            "--suite",
            "int",
            "--no-coverage",
            "--pg-test-profile",
            "pgvector",
            "--",
            *forwarded,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert not sentinel.exists(), "Retired source-provider route must refuse before Docker"
    assert result.returncode == 2
    assert "ERROR: integration sandbox failed:" in result.stderr
    assert "requires a sealed offline plan" in result.stderr
    assert not (tmp_path / "scratch").exists()
    assert "canonical pgvector profile requires scoped test selector(s)" not in result.stderr
    assert "canonical marker selection is closed" not in result.stderr
    assert "outside selected suite root(s)" not in result.stderr


def test_exact_offline_cohort_reaches_only_cached_sdk_admission(tmp_path: Path) -> None:
    """Real subprocess/file custody; a sentinel replaces every Docker command."""
    from runner_pgvector_archive import runtime_config_for_diff_id
    from runner_pgvector_budget import CAPS
    from runner_pgvector_context import NATIVE_SELECTOR
    from runner_pgvector_prebuilt import PROVIDER_SHA256, SDK_IMAGE, json_bytes, provider_facts
    facts = provider_facts()
    raw_base = "".join(facts["base_config_utf8_chunks"])
    base = json.loads(raw_base)
    runtime = runtime_config_for_diff_id(base, "sha256:" + "0" * 64)
    snapshot: dict[str, dict[str, str | int]] = {}
    names = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT, check=True, capture_output=True).stdout.split(b"\0")
    for name in (entry.decode() for entry in names if entry):
        path = REPO_ROOT / name
        if path.is_symlink():
            snapshot[name] = {"symlink": os.readlink(path)}
        else:
            snapshot[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                              "mode": path.stat().st_mode & 0o777}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()
    root = tmp_path / "synthetic-plan"
    root.mkdir(mode=0o700)
    archive = root / "not-a-runtime.tar"
    archive.write_bytes(b"synthetic fixture; never loaded")
    budget = root / "allocation.json"
    budget.write_bytes(json_bytes({"schema": "cloe-pgvector-allocation-v1", "caps": CAPS,
                                  "used": dict.fromkeys(CAPS, 0)}))
    plan = {"schema": "cloe-pgvector-offline-plan-v1", "phase": "CLOE-W5-PGDG1", "role": "native",
        "provider_sha256": PROVIDER_SHA256, "sdk_image": SDK_IMAGE,
        "review1_receipt_sha256": "1" * 64, "operator_decision_sha256": "2" * 64,
        "candidate_snapshot": snapshot, "candidate_head": head, "allocation_path": str(budget),
        "archive": {"path": str(archive), "bytes": archive.stat().st_size,
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
        "derived": {"base_config": facts["base_config"], "base_config_document": base,
            "base_config_raw": raw_base, "runtime_config_document": runtime,
            "runtime_config": "sha256:" + hashlib.sha256(json_bytes(runtime)).hexdigest(),
            "runtime_layers": [{"sha256": item.removeprefix("sha256:"), "bytes": 1}
                               for item in runtime["rootfs"]["diff_ids"]]}}
    declaration = {"schema": "cloe-pgvector-diagnostic-declaration-v1", "phase": "CLOE-W5-PGDG1",
        "caps": CAPS, "provider_sha256": PROVIDER_SHA256, "sdk_image": SDK_IMAGE,
        "candidate_head": head, "candidate_snapshot_sha256": hashlib.sha256(json_bytes(snapshot)).hexdigest(),
        "review1_receipt_sha256": "1" * 64, "operator_decision_sha256": "2" * 64,
        "original_allocation_sha256": "3" * 64, "corrective_selection_sha256": "4" * 64,
        "signature_disposition_sha256": "5" * 64}
    plan.update(diagnostic_declaration=declaration,
                diagnostic_declaration_sha256=hashlib.sha256(json_bytes(declaration)).hexdigest())
    path = root / "plan.json"
    path.write_bytes(json_bytes(plan))
    for private in (archive, budget, path):
        private.chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sentinel = tmp_path / "docker-invoked.log"
    _make_sentinel_docker(bin_dir, sentinel)
    env = _host_subprocess_env(bin_dir, tmp_path / "scratch")
    env["CLOE_PGVECTOR_OFFLINE_PLAN"] = str(path)
    # Only the read-only architecture RPC succeeds; cached-image admission
    # still terminates at the sentinel before any SDK/native operation.
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n" + f'printf "%s\\n" "$*" >> "{sentinel}"\n' +
        'if [ "$1" = version ]; then printf "linux/arm64\\n"; exit 0; fi\nexit 99\n')
    result = subprocess.run([sys.executable, str(RUN_TESTS), "--suite", "int", "--no-coverage",
        "--pg-test-profile", "pgvector", "--report-dir", str(root / "report"), "--", NATIVE_SELECTOR], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 2
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        assert "native ARM host required" in result.stderr and not sentinel.exists()
        assert not any(json.loads(budget.read_bytes())["used"].values())
        return
    assert sentinel.exists()
    operations = sentinel.read_text()
    assert IMAGE_TAG in operations and "inspect" in operations
    assert "build" not in operations and "pull" not in operations and "run " not in operations
    assert not any(json.loads(budget.read_bytes())["used"].values())
