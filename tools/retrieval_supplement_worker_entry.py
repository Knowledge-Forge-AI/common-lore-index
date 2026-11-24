"""Trusted composition root for the isolated offline W5 model container.

The supervisor mounts this file read-only beside the frozen Q worker source.
It performs the hash-locked local install, injects the typed native module
bundle, and delegates the actual journal/response contract to Q's worker.
"""

from __future__ import annotations

import argparse
import importlib.metadata
from importlib import import_module
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable


SOURCE_ROOT = Path("/worker-ro")
ARTIFACT_ROOT = Path("/artifacts")
OUTPUT_ROOT = Path("/output")
REQUEST_PATH = Path("/request.json")
ADMISSION_PATH = Path("/admission.json")
DEPS_ROOT = Path("/scratch/deps")
TMP_ROOT = Path("/scratch/tmp")
PIP_TIMEOUT_SECONDS = 120


class WorkerBootstrapError(RuntimeError):
    """A pre-inference refusal that can be journaled by Q's worker."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _source_imports() -> tuple[
    Callable[..., Any], Callable[..., Any], type[BaseException], tuple[str, str, str]
]:
    if str(SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(SOURCE_ROOT))
    from retrieval_semantic_generator_artifacts import verify_artifact_directory
    from retrieval_semantic_generator_kernel_contracts import (
        NUMPY_VERSION,
        ONNX_RUNTIME_VERSION,
        TOKENIZERS_VERSION,
    )
    from retrieval_semantic_generator_worker import run_worker
    from retrieval_semantic_generator_worker_protocol import WorkerRequestError
    return (
        verify_artifact_directory,
        run_worker,
        WorkerRequestError,
        (NUMPY_VERSION, ONNX_RUNTIME_VERSION, TOKENIZERS_VERSION),
    )


def _requirements(profile: Any) -> str:
    wheels = getattr(profile, "wheels", None)
    if not isinstance(wheels, tuple) or len(wheels) != 20:
        raise WorkerBootstrapError("artifact_wheel_closure")
    lines = ["--no-index", "--only-binary=:all:", "--require-hashes"]
    for wheel in wheels:
        name = getattr(wheel, "name", None)
        version = getattr(wheel, "version", None)
        digest = getattr(wheel, "sha256", None)
        if (
            type(name) is not str
            or type(version) is not str
            or type(digest) is not str
            or not digest or ":" in digest
        ):
            raise WorkerBootstrapError("artifact_wheel_pin")
        lines.append(f"{name}=={version} --hash=sha256:{digest}")
    return "\n".join(lines) + "\n"


def _install(profile: Any) -> None:
    TMP_ROOT.mkdir(parents=False, exist_ok=False)
    DEPS_ROOT.mkdir(parents=False, exist_ok=False)
    requirements = TMP_ROOT / "requirements.txt"
    requirements.write_text(_requirements(profile), encoding="ascii")
    environment = os.environ.copy()
    environment.update(
        {
            "TMPDIR": str(TMP_ROOT),
            "TMP": str(TMP_ROOT),
            "TEMP": str(TMP_ROOT),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_CACHE_DIR": "1",
        }
    )
    command = (
        sys.executable,
        "-I",
        "-B",
        "-m",
        "pip",
        "--isolated",
        "install",
        "--no-index",
        "--find-links",
        "/artifacts/wheels",
        "--only-binary=:all:",
        "--require-hashes",
        "--no-deps",
        "--no-compile",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--target",
        str(DEPS_ROOT),
        "-r",
        str(requirements),
    )
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=PIP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkerBootstrapError("offline_dependency_install_failed") from exc
    if completed.returncode != 0:
        raise WorkerBootstrapError("offline_dependency_install_failed")
    expected = {
        getattr(wheel, "name").lower().replace("_", "-"): getattr(wheel, "version")
        for wheel in getattr(profile, "wheels")
    }
    observed = {
        str(distribution.metadata["Name"]).lower().replace("_", "-"): distribution.version
        for distribution in importlib.metadata.distributions(path=[str(DEPS_ROOT)])
        if distribution.metadata.get("Name") is not None
    }
    if observed != expected:
        raise WorkerBootstrapError("offline_dependency_closure")
    sys.path.insert(0, str(DEPS_ROOT))


def _failure_factory(code: str):
    from retrieval_semantic_generator_runtime import RuntimeAdapterError

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeAdapterError(code, "bounded component preflight failed")

    return fail


def _run_worker(admission_sha256: str, *, runtime_factory: Any | None) -> int:
    (
        _verify_artifacts,
        run_worker,
        worker_error,
        versions,
    ) = _source_imports()
    try:
        profile = _verify_artifacts(ARTIFACT_ROOT)
        if runtime_factory is None:
            _install(profile)
            np = import_module("numpy")
            ort = import_module("onnxruntime")
            tokenizers = import_module("tokenizers")

            if (np.__version__, ort.__version__, tokenizers.__version__) != versions:
                raise WorkerBootstrapError("runtime_version_mismatch")
            from retrieval_semantic_generator_kernel_runtime_types import NativeRuntimeModules
            from retrieval_semantic_generator_kernel_runtime_factory import make_native_runtime_factory

            modules = NativeRuntimeModules(np, ort, tokenizers, tokenizers.__version__)
            runtime_factory = make_native_runtime_factory(modules)
    except WorkerBootstrapError as exc:
        runtime_factory = _failure_factory(exc.code)
    except Exception:
        runtime_factory = _failure_factory("offline_component_preflight_failed")
    try:
        run_worker(
            REQUEST_PATH,
            ARTIFACT_ROOT,
            OUTPUT_ROOT,
            admission_path=ADMISSION_PATH,
            expected_admission_sha256=admission_sha256,
            runtime_factory=runtime_factory,
        )
    except (worker_error, OSError, RuntimeError, ValueError):
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--admission", required=True)
    parser.add_argument("--admission-sha256", required=True)
    args = parser.parse_args()
    if (
        args.request != str(REQUEST_PATH)
        or args.artifacts != str(ARTIFACT_ROOT)
        or args.output != str(OUTPUT_ROOT)
        or args.admission != str(ADMISSION_PATH)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", args.admission_sha256) is None
    ):
        return 2
    return _run_worker(args.admission_sha256, runtime_factory=None)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
