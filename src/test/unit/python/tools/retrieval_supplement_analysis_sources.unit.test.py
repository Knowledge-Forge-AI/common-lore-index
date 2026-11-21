"""Source identities cover transitive local imports without executing them."""

from pathlib import Path

import pytest

from retrieval_supplement_analysis_sources import source_closure_sha256
from retrieval_supplement_analysis_values import AnalysisRefusal


def _repo(tmp_path: Path, source: bytes) -> Path:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/owner.py").write_bytes(source)
    return tmp_path


def test_function_local_transitive_dependency_changes_the_identity(tmp_path: Path) -> None:
    root = _repo(tmp_path, b"def run():\n    import helper\n    return helper.VALUE\n")
    helper = root / "tools/helper.py"
    helper.write_bytes(b"import nested\nVALUE = 1\n")
    nested = root / "tools/nested.py"
    nested.write_bytes(b"raise RuntimeError('must never execute')\n")
    before = source_closure_sha256(root, ("tools/owner.py",))
    assert source_closure_sha256(root, ("tools/owner.py",)) == before
    nested.write_bytes(b"raise RuntimeError('changed without executing')\n")
    assert source_closure_sha256(root, ("tools/owner.py",)) != before


@pytest.mark.parametrize("source,code", [
    (b"from . import helper\n", "analysis:source_relative_import_unsupported"),
    (b"import private.missing\n", "analysis:source_package_import_unsupported"),
    (b"__import__('hidden')\n", "analysis:source_dynamic_import_unsupported"),
    (b"import absent_dependency\n", "analysis:analyzer_source_custody"),
])
def test_unsupported_or_missing_imports_refuse(tmp_path: Path, source: bytes, code: str) -> None:
    root = _repo(tmp_path, source)
    with pytest.raises(AnalysisRefusal, match=code):
        source_closure_sha256(root, ("tools/owner.py",))


def test_symlinked_dependency_cannot_be_bound(tmp_path: Path) -> None:
    root = _repo(tmp_path, b"import helper\n")
    outside = root / "outside.py"
    outside.write_bytes(b"VALUE = 1\n")
    (root / "tools/helper.py").symlink_to(outside)
    with pytest.raises(AnalysisRefusal, match="analysis:analyzer_source_custody"):
        source_closure_sha256(root, ("tools/owner.py",))
