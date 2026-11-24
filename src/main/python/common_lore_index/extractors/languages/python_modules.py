"""Python module identity and package-root helpers."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True)
class PythonModuleIndex:
    module_to_path: Mapping[str, str]
    path_to_module: Mapping[str, str]
    _repository_root: Path | None = field(default=None, repr=False, compare=False)
    _package_roots: tuple[str, ...] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def empty(cls) -> PythonModuleIndex:
        return cls(module_to_path={}, path_to_module={})

    @classmethod
    def from_modules(cls, modules: Mapping[str, str]) -> PythonModuleIndex:
        module_to_path = dict(modules)
        path_to_module = {path: module for module, path in module_to_path.items()}
        return cls(module_to_path=module_to_path, path_to_module=path_to_module)

    @classmethod
    def from_python_paths(
        cls,
        paths: Iterable[str],
        *,
        repository_root: Path | str | None = None,
    ) -> PythonModuleIndex:
        python_paths = sorted(path for path in paths if path.endswith(".py"))
        if not python_paths:
            return cls.empty()
        root = Path(repository_root) if repository_root is not None else None
        roots = package_roots(root)
        modules: dict[str, str] = {}
        for path in python_paths:
            module = _module_name_from_roots(path, roots)
            if module is not None:
                modules[module] = path
        return cls(modules, {path: module for module, path in modules.items()}, root, roots)

    def has_module(self, module: str) -> bool:
        return module in self.module_to_path

    def module_for_path(self, relative_path: str, repository_root: Path | str | None) -> str | None:
        if not relative_path.endswith(".py"):
            return None
        root = Path(repository_root) if repository_root is not None else None
        roots = self._package_roots if root == self._repository_root else None
        return _module_name_from_roots(relative_path, roots if roots is not None else package_roots(root))


def importable_module_name(
    relative_path: str,
    *,
    repository_root: Path | str | None = None,
) -> str | None:
    if not relative_path.endswith(".py"):
        return None
    return _module_name_from_roots(relative_path, package_roots(repository_root))


def _module_name_from_roots(relative_path: str, roots: tuple[str, ...]) -> str | None:
    normalized_path = relative_path.replace("\\", "/")
    for root in roots:
        prefix = f"{root}/" if root != "." else ""
        if root == "." or normalized_path.startswith(prefix):
            suffix = normalized_path.removeprefix(prefix)
            return module_name_from_suffix(suffix)
    return module_name_from_suffix(normalized_path)


def package_roots(repository_root: Path | str | None = None) -> tuple[str, ...]:
    roots = ["src/main/python"]
    roots.extend(pyproject_package_roots(repository_root))
    roots.extend(test_python_roots)
    return tuple(dict.fromkeys(roots))


def pyproject_package_roots(repository_root: Path | str | None) -> tuple[str, ...]:
    if repository_root is None:
        return ()
    pyproject_path = Path(repository_root) / "pyproject.toml"
    if not pyproject_path.exists():
        return ()
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return ()
    tool = payload.get("tool")
    if not isinstance(tool, Mapping):
        return ()
    setuptools = tool.get("setuptools")
    if not isinstance(setuptools, Mapping):
        return ()
    package_dir = setuptools.get("package-dir")
    if not isinstance(package_dir, Mapping):
        return ()
    roots = []
    for key in ("", "."):
        value = package_dir.get(key)
        if isinstance(value, str) and value.strip():
            roots.append(normalize_root(value))
    return tuple(roots)


test_python_roots = (
    "src/test/unit/python",
    "src/test/int/python",
)


def normalize_root(value: str) -> str:
    return value.strip().replace("\\", "/").strip("/")


def module_name_from_suffix(suffix: str) -> str | None:
    if not suffix.endswith(".py"):
        return None
    without_extension = suffix[:-3]
    if without_extension.endswith("/__init__"):
        without_extension = without_extension[: -len("/__init__")]
    elif without_extension == "__init__":
        return None
    parts = [part for part in without_extension.split("/") if part]
    if not parts:
        return None
    return ".".join(parts)
