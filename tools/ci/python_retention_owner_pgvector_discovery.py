"""Complete inert pgvector discovery owner contract; never executed."""
SOURCE = r'''
"""Synthetic discovery boundaries and one harmless real child; no Docker or SQL."""
from __future__ import annotations
import base64
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from textwrap import dedent
from types import SimpleNamespace
from typing import Any
import pytest
import runner_pgvector_discovery as handoff
from runner_pgvector_context import CORE_SELECTOR, NATIVE_SELECTOR
from runner_pgvector_prebuilt import ENV_OFFLINE_PLAN, INNER_PLAN, digest, json_bytes
from runner_staging_discovery import filter_child_environment

def request_fixture(role: str='core') -> tuple[dict[str, Any], dict[str, Any]]:
    plan = {'role': role, 'candidate_head': 'a' * 40}
    request = {'environment': {ENV_OFFLINE_PLAN: INNER_PLAN, 'REPOMAP_TEST_PG_PROFILE': 'core' if role == 'core' else 'pgvector'}, 'suite': 'int', 'scoped': True, 'cwd': '/workspace', 'candidate': {'commit': 'a' * 40}, 'pytest_args': ['--basetemp', '/sandbox-scratch/public-pytest', '-o', 'cache_dir=/sandbox-scratch/public-pytest/cache', CORE_SELECTOR if role == 'core' else NATIVE_SELECTOR]}
    return (plan, request)

def projection_case(monkeypatch: Any, role: str) -> None:
    plan, request = request_fixture(role)
    observed = []

    def load(environment: Any) -> dict[str, Any]:
        observed.append(dict(environment))
        return plan
    monkeypatch.setattr(handoff, 'load_inner_plan', load)
    environment = {**request['environment'], 'PATH': '/public/bin', 'CLOE_UNRELATED': 'discard'}
    projected = filter_child_environment(environment)
    assert projected[ENV_OFFLINE_PLAN] == INNER_PLAN and 'CLOE_UNRELATED' not in projected
    request['environment'] = projected
    assert handoff.plan_for_request(request) == plan and len(observed) == 2
    request['pytest_args'] = ['/workspace/' + request['pytest_args'][-1]]
    assert handoff.plan_for_request(request) == plan

def refusal_case(monkeypatch: Any, case: str) -> None:
    plan, request = request_fixture()

    def load(_environment: Any) -> dict[str, Any]:
        if case in {'unauthenticated', 'stale', 'malformed'}:
            raise RuntimeError('public sealed-plan refusal')
        return plan
    monkeypatch.setattr(handoff, 'load_inner_plan', load)
    if case == 'ordinary':
        assert filter_child_environment({'PATH': '/public/bin', 'CLOE_UNRELATED': 'discard'}) == {'PATH': '/public/bin'}
        assert handoff.plan_for_request({'environment': {}}) is None
        return
    if case == 'profile':
        request['environment']['REPOMAP_TEST_PG_PROFILE'] = 'pgvector'
    elif case == 'role':
        plan['role'] = 'native'
    elif case == 'head':
        request['candidate']['commit'] = 'b' * 40
    elif case == 'suite':
        request['suite'] = 'staging'
    elif case == 'unscoped':
        request['scoped'] = False
    elif case == 'cwd':
        request['cwd'] = '/public/other'
    elif case == 'extra-selector':
        request['pytest_args'].append(NATIVE_SELECTOR)
    elif case == 'scratch':
        request['pytest_args'][1] = '/public/escape'
    elif case == 'cache':
        request['pytest_args'][3] = 'cache_dir=/public/other'
    elif case == 'node':
        request['pytest_args'][-1] += '::test_subset'
    elif case == 'marker':
        request['pytest_args'] = ['-m', 'requires_pgvector', CORE_SELECTOR]
    if case in {'unauthenticated', 'stale', 'malformed'}:
        with pytest.raises(RuntimeError):
            filter_child_environment(request['environment'])
    with pytest.raises(RuntimeError):
        handoff.plan_for_request(request)

def environment_mismatch_case(tmp_path: Path, monkeypatch: Any) -> None:
    from runner_integration_obligations import MAINTAINED_ABRUPT_DECLARATIONS
    import runner_staging_discovery_child as child
    from runner_staging_discovery_contract import REQUEST_SCHEMA, write_document
    monkeypatch.delenv(ENV_OFFLINE_PLAN, raising=False)
    request = {'schema': REQUEST_SCHEMA, 'invocation_id': 'public-mismatch', 'candidate': {'commit': 'a' * 40, 'source_sha256': 'b' * 64}, 'suite': 'int', 'scoped': True, 'cwd': str(Path.cwd()), 'pytest_args': [CORE_SELECTOR], 'python_paths': list(sys.path), 'declarations': [asdict(x) for x in MAINTAINED_ABRUPT_DECLARATIONS], 'environment': {**{k: v for k, v in os.environ.items() if k not in {'PYTHONNOUSERSITE', '__CF_USER_TEXT_ENCODING'}}, ENV_OFFLINE_PLAN: INNER_PLAN}}
    path = tmp_path / 'request.json'
    write_document(path, request)
    calls = []
    monkeypatch.setattr(pytest, 'main', lambda *_a, **_k: calls.append('forbidden'))
    assert child._collect(path, tmp_path / 'receipt.json') == 2 and (not calls)

def harmless_child_case(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[5]
    driver = tmp_path / 'public_driver.py'
    driver.write_text(dedent("\n        import json, os, sys\n        from pathlib import Path\n        from dataclasses import asdict\n        source, root = Path(sys.argv[1]), Path(sys.argv[2])\n        sys.path[:0] = [str(source/'tools'), str(source/'src/main/python'), str(source/'src/test/support/python')]\n        import runner_staging_discovery_child as child\n        import runner_pgvector_discovery as handoff\n        import runner_pgvector_discovery_capture as capture\n        from runner_staging_discovery_contract import REQUEST_SCHEMA, write_document\n        from runner_integration_obligations import MAINTAINED_ABRUPT_DECLARATIONS\n        import pytest\n        capture.REPORT_ROOT = root/'private-report'\n        handoff.plan_for_request = lambda request: {'role':'core','candidate_head':'a'*40}\n        def fail(*args, **kwargs):\n            print('public-stdout-prefix')\n            print('s'*70000)\n            print('public-stderr-prefix', file=sys.stderr)\n            print('e'*70000, file=sys.stderr)\n            raise RuntimeError('public intentional collection failure')\n        pytest.main = fail\n        environment = {k:v for k,v in os.environ.items() if k not in {'PYTHONNOUSERSITE','__CF_USER_TEXT_ENCODING'}}\n        request = {'schema':REQUEST_SCHEMA, 'invocation_id':'public-failed-child',\n            'candidate':{'commit':'a'*40,'source_sha256':'b'*64},'suite':'int','scoped':True,\n            'cwd':str(Path.cwd()),'pytest_args':['public-test.py'],'python_paths':list(sys.path),\n            'declarations':[asdict(x) for x in MAINTAINED_ABRUPT_DECLARATIONS], 'environment':environment}\n        write_document(root/'request.json', request)\n        raise SystemExit(child.main([str(root/'request.json'),str(root/'receipt.json')]))\n    "))
    environment = dict(os.environ)
    environment[ENV_OFFLINE_PLAN] = INNER_PLAN
    completed = subprocess.run([sys.executable, '-I', '-B', str(driver), str(source), str(tmp_path)], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 2, (completed.returncode, completed.stderr[:4096])
    receipt = json.loads((tmp_path / 'receipt.json').read_bytes())
    assert receipt['exit_code'] == 2 and receipt['error'] == 'RuntimeError'
    typed = json.loads((tmp_path / 'child_error.json').read_bytes())
    assert typed['error']['type'] == 'RuntimeError'
    path = tmp_path / 'private-report/offline-discovery-capture.public-failed-child.json'
    facts = json.loads(path.read_bytes())
    for stream in ('stdout', 'stderr'):
        row = facts[stream]
        assert row['retained_bytes'] == 65536 and row['truncated'] and (row['dropped_characters'] > 0)
        assert base64.b64decode(row['prefix_base64']).startswith(('public-' + stream + '-prefix').encode())
    assert path.stat().st_mode & 63 == 0 and facts['qualification_evidence'] is False
    assert facts['native_fd_or_subprocess_capture'] is False and facts['private_only'] is True

def optional_report_case(tmp_path: Path, monkeypatch: Any, case: str) -> None:
    import test_sandbox_execution
    from runner_pgvector_sandbox import OfflineSandbox
    from common_lore_index_test_support.pgvector_prebuilt_budget_cases import allocation
    budget = allocation(tmp_path)
    context: Any = SimpleNamespace(fixed=lambda: None, allocation=lambda: budget)
    observed = []

    def reader(container: str, required: bool, **kwargs: Any) -> bytes | None:
        state = json.loads(budget.path.read_bytes())
        assert state['used']['read_bytes'] == state['used']['transfer_bytes'] == 4 * 1024 ** 2
        assert container == 'owned' and kwargs['max_archive_bytes'] == 4 * 1024 ** 2
        observed.append(required)
        if case == 'error':
            raise RuntimeError('public diagnostic channel failure')
        if case in {'optional-absent', 'required-absent'}:
            return None
        return b'x' * (4 * 1024 ** 2 + 1) if case == 'oversize' else b'public diagnostic'
    monkeypatch.setattr(test_sandbox_execution, 'read_container_report_archive', reader)
    callback = OfflineSandbox(context, lambda *_a: None, lambda *_a, **_k: subprocess.CompletedProcess([], 0, '', ''))
    required = case == 'required-absent'
    if case in {'error', 'required-absent', 'oversize'}:
        with pytest.raises(RuntimeError):
            callback.read_report_archive('owned', required)
        expected = 4 * 1024 ** 2
    else:
        result = callback.read_report_archive('owned', False)
        assert result == (None if case == 'optional-absent' else b'public diagnostic')
        expected = 0 if result is None else len(result)
    assert observed == [required]
    with budget.session() as meter:
        assert meter.used['read_bytes'] == meter.used['transfer_bytes'] == expected

def nested_lineage_case(tmp_path: Path, monkeypatch: Any, case: str) -> None:
    import runner_pgvector_recovery as recovery
    from common_lore_index_test_support.pgvector_prebuilt_recovery_cases import fixture
    source, declaration, previous, session, _frozen = fixture(tmp_path, monkeypatch, 'none')
    document = json.loads(declaration.read_bytes())
    selection = document['authority']['corrective_selection_sha256']
    authority_path = Path(selection['path'])
    authority = json.loads(authority_path.read_bytes())
    old = json.loads((previous / 'allocation.json').read_bytes())
    core = json.loads((previous / 'core.plan.json').read_bytes())
    if case == 'nested-failure':
        old['records']['recovery_parent']['original_records']['core_result']['exit'] = 0
    elif case == 'nested-counter':
        old['records']['recovery_parent']['original_used']['read_bytes'] = 1001
    elif case == 'ordinal4':
        core['core_attempt'] = 4
    elif case == 'skip-core2':
        authority['previous_caps'] = old['records']['recovery_parent']['original_caps']
    core['recovery_parent_sha256'] = digest(json_bytes(old['records']['recovery_parent']))
    result = json.loads((previous / 'result.json').read_bytes())
    result['runtime_allocation_state'] = old
    for name, payload in (('allocation.json', old), ('core.plan.json', core), ('result', result)):
        row = authority['previous_files'][name]
        raw = json_bytes(payload)
        Path(row['path']).write_bytes(raw)
        row.update(bytes=len(raw), sha256=digest(raw))
    raw = json_bytes(authority)
    authority_path.write_bytes(raw)
    selection.update(bytes=len(raw), sha256=digest(raw))
    document['payload']['corrective_selection_sha256'] = digest(raw)
    declaration.write_bytes(json_bytes(document))
    with pytest.raises(RuntimeError):
        recovery.recover_core(source=source, declaration=declaration, previous=previous, session=session)
    assert not session.exists()

def lost_authority_case(tmp_path: Path, monkeypatch: Any, marker: str) -> None:
    import importlib.util
    import runner_pgvector_context as context
    marker_path = tmp_path / 'sealed.json'
    if marker == 'file':
        marker_path.write_text('not admission authority')
    elif marker == 'link':
        marker_path.symlink_to(tmp_path / 'missing')
    elif marker == 'unreadable':
        original_lstat = Path.lstat

        def lstat(path: Path) -> os.stat_result:
            if path == marker_path:
                raise PermissionError('public marker access refusal')
            return original_lstat(path)
        monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(context, 'INNER_PLAN', str(marker_path))
    monkeypatch.delenv(ENV_OFFLINE_PLAN, raising=False)
    source = Path(context.__file__).resolve().parents[1] / 'src/test/int/python/conftest.py'
    spec = importlib.util.spec_from_file_location('public_session_boundary', source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class Session:

        def __enter__(self) -> None:
            calls.append('ordinary-start')

        def __exit__(self, *args: Any) -> None:
            calls.append('ordinary-finish')
    monkeypatch.setattr(module, 'postgres_container_session', lambda **_kwargs: Session())
    session = SimpleNamespace(config=SimpleNamespace(args=[CORE_SELECTOR]))
    if marker == 'absent':
        assert filter_child_environment({'PATH': '/public/bin'}) == {'PATH': '/public/bin'}
        assert handoff.plan_for_request({'environment': {}}) is None
        module.pytest_sessionstart(session)
        module.pytest_sessionfinish(session, 0)
        assert calls == ['ordinary-start', 'ordinary-finish']
    else:
        message = 'marker unavailable' if marker == 'unreadable' else 'authority lost'
        with pytest.raises(RuntimeError, match=message):
            filter_child_environment({'PATH': '/public/bin'})
        with pytest.raises(RuntimeError, match=message):
            handoff.plan_for_request({'environment': {}})
        with pytest.raises(RuntimeError, match=message):
            context.offline_pytest_owns_runtime(SimpleNamespace(), [])
        with pytest.raises(pytest.exit.Exception, match=message):
            module.pytest_sessionstart(session)
        assert not calls

def canonical_request_case(tmp_path: Path, monkeypatch: Any) -> None:
    from common_lore_index_test_support.test_scratch_layout import TestScratchLayout
    from runner_arguments import build_pytest_args
    import runner_integration_execution as integration
    plan, request = request_fixture()
    owner = tmp_path / CORE_SELECTOR
    owner.parent.mkdir(parents=True)
    owner.write_text('# public fixture\n')

    def git(args: list[str], **kwargs: Any) -> str | bytes:
        assert kwargs['cwd'] == tmp_path
        return plan['candidate_head'] + '\n' if args[1] == 'rev-parse' else (CORE_SELECTOR + '\x00').encode()
    monkeypatch.setattr(integration.subprocess, 'check_output', git)
    monkeypatch.setattr(handoff, 'load_inner_plan', lambda _environment: plan)
    layout = TestScratchLayout(Path('/sandbox-scratch/r/public'), Path('/sandbox-scratch'))
    request['candidate'] = integration.candidate_identity(tmp_path)
    request['pytest_args'] = build_pytest_args(('int',), [CORE_SELECTOR], test_roots={'int': tmp_path / 'src/test/int/python'}, repo_root=tmp_path, basetemp=layout.pytest_basetemp)
    assert len(request['pytest_args']) == 5 and '-n' not in request['pytest_args']
    assert handoff.plan_for_request(request) == plan

def maximal_capture_export_case(tmp_path: Path, monkeypatch: Any) -> None:
    import runner_pgvector_discovery_capture as capture
    from common_lore_index_test_support.pgvector_prebuilt_test_fixtures import tar_bytes
    from test_sandbox_contract import MAX_REPORT_CONTENT_BYTES, MAX_REPORT_MEMBERS
    from test_sandbox_report import export_sandbox_report
    report = tmp_path / 'inner/report'
    monkeypatch.setattr(capture, 'REPORT_ROOT', report)
    stream = capture.CollectionCapture({'invocation_id': 'public-maximal'}, {'role': 'core', 'candidate_head': 'a' * 40})
    stream.stdout.write('s' * 70000)
    stream.stderr.write('e' * 70000)
    stream.persist(2, 'RuntimeError')
    source = report / 'offline-discovery-capture.public-maximal.json'
    archive = tar_bytes([('report/' + source.name, source.read_bytes(), 'file'), ('int/latest/staging_contract_report.json', b'{"errors":["public synthetic failure"]}', 'file')])
    assert 128 * 1024 < len(source.read_bytes()) < 256 * 1024 and len(archive) < 4 * 1024 ** 2
    destination = tmp_path / 'exported'
    export_sandbox_report(archive, destination, max_members=MAX_REPORT_MEMBERS, max_content_bytes=MAX_REPORT_CONTENT_BYTES)
    assert (destination / 'report' / source.name).read_bytes() == source.read_bytes()

def partial_optional_report_case(tmp_path: Path, monkeypatch: Any) -> None:
    import runner_pgvector_sandbox as sandbox
    from common_lore_index_test_support.pgvector_prebuilt_budget_cases import allocation
    budget = allocation(tmp_path)
    real_popen = subprocess.Popen

    def popen(command: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        assert command[:2] == ['docker', 'cp']
        return real_popen([sys.executable, '-I', '-B', '-c', "import sys;sys.stdout.buffer.write(b'public partial');raise SystemExit(1)"], **kwargs)
    monkeypatch.setattr(sandbox.subprocess, 'Popen', popen)
    context: Any = SimpleNamespace(fixed=lambda: None, allocation=lambda: budget)
    callback = sandbox.OfflineSandbox(context, lambda *_a: None, lambda *_a, **_k: subprocess.CompletedProcess([], 0, '', ''))
    with pytest.raises(RuntimeError, match='consumed bytes before failure'):
        callback.read_report_archive('public-owned', False)
    with budget.session() as meter:
        assert meter.used['read_bytes'] == meter.used['transfer_bytes'] == 4 * 1024 ** 2
'''
