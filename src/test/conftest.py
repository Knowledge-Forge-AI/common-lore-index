"""Common pytest scratch ownership.

``tools/run_tests.py`` is the primary owner and exports
``REPOMAP_TEST_RUN_ROOT`` before pytest starts. This conftest exists so that a
direct invocation such as ``pytest src/test/unit/python/...`` receives the same
authority instead of writing to the platform temporary directory.

The layout is applied at import time, not from ``pytest_configure``. pytest's
own tmpdir plugin builds its ``TempPathFactory`` from a ``tryfirst``
``pytest_configure`` hook, and a conftest's hook runs after that, which would
be too late to redirect ``basetemp``. Applying the environment while this
module is imported — during initial conftest collection — happens first, so
``TMPDIR`` is already the run root's own directory by the time pytest chooses
where to put its temporary tree.

That gives two different but equally owned spellings, and only one of them is
pinned:

* under ``tools/run_tests.py``, the runner passes an explicit
  ``--basetemp <run>/pt``;
* a standalone ``pytest`` uses pytest's own generated basename beneath
  ``<run>/tmp``.

Both stay inside the one run root, which is what the ownership contract
actually requires. A conftest cannot pin the standalone spelling to ``<run>/pt``
— see ``pytest_sessionfinish`` below for what it can own — and forcing it would
mean shipping this file as an installed plugin, which is not worth a cosmetic
path difference.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SUPPORT_ROOT = Path(__file__).resolve().parent / "support" / "python"
if str(_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUPPORT_ROOT))


def _load_test_support():
    """Import the scratch owners after the support root is available."""
    from common_lore_index_test_support import test_scratch
    from common_lore_index_test_support import build_profile_debt
    from common_lore_index_test_support import resource_run_test_support

    return test_scratch, build_profile_debt, resource_run_test_support


_test_scratch, _build_profile_debt, _resource_run_test_support = _load_test_support()
# Reuses an inherited REPOMAP_TEST_RUN_ROOT when the runner already allocated
# one, so a direct pytest invocation under the runner never creates a second
# run root.
os.environ.pop("CLOE_HOME", None)  # Isolate collection/session fixtures as well as tests.
_LAYOUT = _test_scratch.establish_run().apply()


@pytest.fixture(autouse=True)
def isolate_cloe_home():
    """A caller's new home selector must not redirect test-owned state."""
    # Own this patch without moving a test's shared monkeypatch teardown.
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("CLOE_HOME", raising=False)
        yield


def pytest_configure(config):
    from common_lore_index_test_support import d12_core_image as d12
    from common_lore_index_test_support import retrieval_supplement_study as study

    config.addinivalue_line("markers", f"{d12.MARKER}: {d12.DESELECTION_REASON}")
    config.addinivalue_line("markers", f"{study.MARKER}: {study.DESELECTION_REASON}")
    from common_lore_index_test_support import retrieval_body_study as body_study
    config.addinivalue_line("markers", f"{body_study.MARKER}: {body_study.DESELECTION_REASON}")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(session, config, items):
    """Reconcile the optional D12 owner with its one-node registry.

    Under the runner-selected profile the population must be exactly that
    node. Every ordinary population visibly deselects it, and a population
    whose path arguments cover its file must have collected it, so a renamed
    or deleted owner cannot disappear silently. The raw collected IDs, the
    excluded IDs, their count and the exact reason are recorded for runner
    discovery, separately from the deferred build-profile registry.
    """
    from common_lore_index_test_support import d12_core_image as d12

    raw = [item.nodeid for item in items]
    optional = set(d12.OPTIONAL_NODE_IDS)
    marked = [item for item in items if item.get_closest_marker(d12.MARKER) is not None]
    marked_ids = {item.nodeid for item in marked}
    collected = {item.nodeid for item in items} & optional
    if marked_ids - optional or collected - marked_ids:
        raise d12.D12ProfileError("D12 optional marker and one-node registry disagree")
    if optional & set(_build_profile_debt.DEFERRED_BUILD_PROFILE_NODE_IDS) or any(
        item.get_closest_marker("requires_build_profile") is not None for item in marked
    ):
        raise d12.D12ProfileError("the D12 optional owner must not enter deferred build debt")
    active = os.environ.get(d12.ENV_PROFILE)
    if active is not None:
        if active not in d12.PLATFORMS or raw != [d12.NODE_ID]:
            raise d12.D12ProfileError("D12 profile population must be exactly the one named node")
        _record_optional_population(config, raw, ())
        return
    if _covers_d12_owner(config) and collected != optional:
        raise d12.D12ProfileError(
            "a population covering the D12 owner must collect exactly its one named node"
        )
    if marked:
        items[:] = [item for item in items if item.nodeid not in marked_ids]
        config.hook.pytest_deselected(items=marked)
    _record_optional_population(config, raw, marked_ids)
    _reconcile_supplement_study(config, items)
    _reconcile_body_study(config, items)


def _reconcile_body_study(config, items):
    from common_lore_index_test_support import retrieval_body_study as body_study
    body_study.reconcile_body_study(
        config, items, deferred_build_node_ids=_build_profile_debt.DEFERRED_BUILD_PROFILE_NODE_IDS,
    )


def _reconcile_supplement_study(config, items):
    """Visibly deselect the actual-study owner unless authenticated alone inside the study SDK."""
    from common_lore_index_test_support import retrieval_supplement_study as study

    study.reconcile_supplement_study(
        config, items, deferred_build_node_ids=_build_profile_debt.DEFERRED_BUILD_PROFILE_NODE_IDS,
    )


def _record_optional_population(config, raw, excluded_ids):
    """Each exclusion keeps its raw collection index, so raw collected IDs can be rebuilt exactly."""
    from common_lore_index_test_support import d12_core_image as d12

    excluded = [{"nodeid": nodeid, "raw_index": index}
                for index, nodeid in enumerate(raw) if nodeid in excluded_ids]
    setattr(config, d12.POPULATION_ATTR, {
        "excluded": excluded, "count": len(excluded), "raw_count": len(raw), "reason": d12.DESELECTION_REASON,
    })


def _covers_d12_owner(config):
    """Whether a path argument (not a node selection) contains the optional owner file."""
    root = Path(getattr(config, "rootpath", Path.cwd()))
    base = Path(getattr(getattr(config, "invocation_params", None), "dir", root))
    from common_lore_index_test_support.d12_core_image import NODE_FILE

    node = (root / NODE_FILE).resolve()
    paths = [(base / arg).resolve() for arg in getattr(config, "args", ()) if "::" not in str(arg)]
    return any(path == node or path in node.parents for path in paths)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    from common_lore_index_test_support import d12_core_image as d12
    from common_lore_index_test_support import retrieval_supplement_study as study

    record = getattr(config, d12.POPULATION_ATTR, {})
    ids = [entry["nodeid"] for entry in record.get("excluded", ())]
    if ids:
        terminalreporter.write_line(
            f"D12 optional core-image owners deselected: {len(ids)} "
            f"{', '.join(ids)} ({d12.DESELECTION_REASON})"
        )
    study_record = getattr(config, study.POPULATION_ATTR, {})
    study_ids = [entry["nodeid"] for entry in study_record.get("excluded", ())]
    if study_ids:
        terminalreporter.write_line(
            f"Supplement study actual owner deselected: {len(study_ids)} "
            f"{', '.join(study_ids)} ({study.DESELECTION_REASON})"
        )
    from common_lore_index_test_support import retrieval_body_study as body_study
    body_record = getattr(config, body_study.POPULATION_ATTR, {})
    body_ids = [entry["nodeid"] for entry in body_record.get("excluded", ())]
    if body_ids:
        terminalreporter.write_line(
            f"Body study actual owner deselected: {len(body_ids)} "
            f"{', '.join(body_ids)} ({body_study.DESELECTION_REASON})"
        )


def pytest_report_header(config):
    return f"repomap test scratch: {_LAYOUT.run_root.name} ({_test_scratch.ENV_RUN_ROOT})"


@pytest.fixture
def isolate_test_resource_run_process_state():
    """Prevent allocating-owner unit seams from poisoning later tests."""
    with _resource_run_test_support.preserve_resource_run_process_state():
        yield


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    from common_lore_index_test_support.test_scratch_cleanup import (
        TestScratchCleanupError, clean_test_node_scratch, record_test_report,
    )

    outcome = yield
    rep = outcome.get_result()
    record_test_report(item, rep)
    if rep.when == "teardown":
        try:
            clean_test_node_scratch(item, layout=_LAYOUT)
        except TestScratchCleanupError as error:
            rep.outcome = "failed"
            rep.longrepr = f"Test scratch cleanup failed for {item.nodeid}: {error}"


@pytest.fixture(autouse=True)
def clean_test_scratch(request):
    """Restore run-owned environment before the final teardown report."""
    yield
    _LAYOUT.apply()



def pytest_runtest_setup(item):
    """Refuse direct execution of visible build debt without build authority."""
    if item.get_closest_marker("requires_build_profile") is not None:
        _build_profile_debt.enforce_build_profile_authority()
    from common_lore_index_test_support import d12_core_image as d12

    if item.get_closest_marker(d12.MARKER) is not None:
        _require_d12_admission(d12, item.nodeid)
    from common_lore_index_test_support import retrieval_supplement_study as study

    if item.get_closest_marker(study.MARKER) is not None:
        if item.nodeid != study.NODE_ID:
            raise study.SupplementStudyProfileError("study marker on foreign node")
        study.require_study_admission()
    from common_lore_index_test_support import retrieval_body_study as body_study
    if item.get_closest_marker(body_study.MARKER) is not None:
        if item.nodeid != body_study.NODE_ID:
            raise body_study.BodyStudyProfileError("body-study marker on foreign node")
        body_study.require_body_admission()


def _require_d12_admission(d12, nodeid):
    """Build admission, or else the separately admitted E1 exploration of exactly this node; never both.

    E1 keeps its own slot in the runner's ``tools`` module (ADR 0089 D9), imported here only for the D12
    marker, as the owner imports it. ``d12.current_admission()`` refuses throughout E1, so a lost
    exploration refuses rather than reaching build authority.
    """
    import d12_dependency_preflight as inner

    exploration = inner.current_exploration()
    if exploration is None:
        d12.current_admission()
        return
    if d12._ADMISSION is not None:
        raise d12.D12ProfileError("mixed build and exploration admission in one process")
    if nodeid != d12.NODE_ID or exploration.node_id != nodeid:
        raise d12.D12ProfileError(f"E1 exploration admits only {exploration.node_id}, not {nodeid}")


def pytest_sessionfinish(session, exitstatus):
    """Close the run this pytest process allocated.

    This is the one lifecycle owner for a standalone invocation. When pytest
    was launched by ``tools/run_tests.py`` the layout was inherited rather than
    allocated, so ``finalize_run`` returns without writing and the runner stays
    the terminal owner of its own run — there are never two finalizers
    competing for one manifest.

    Deliberately not an ``atexit`` handler: a process killed outright should
    leave its manifest ``running`` for an operator to find, rather than being
    reported as a clean finish it never had.
    """
    _test_scratch.finalize_run(
        _LAYOUT,
        "passed" if int(exitstatus) == 0 else "failed",
        exit_status=int(exitstatus),
    )
