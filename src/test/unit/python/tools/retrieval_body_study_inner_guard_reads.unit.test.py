"""Separate durable closure bounds do not consume whole-checkout reservations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os

import pytest

from retrieval_body_study_inner_guard_reads import (
    GUARD_LIMIT, closure_guard_read_bound, read_inner_source_closure_receipt,
    reserve_inner_source_closure_guard, validate_exported_closure_receipt,
)
from retrieval_body_study_inner_reads import (
    InnerReadRefusal, read_receipt, reserve_inner_source_verification,
)
from retrieval_semantic_generator_worker_contracts import WORKER_SOURCE_FILES


def _case(tmp_path):
    source, ledger = tmp_path / 'source', tmp_path / 'ledger'
    source.mkdir(mode=0o700)
    ledger.mkdir(mode=0o700)
    names = ['tools/guard-source.py', 'tools/guard-treatment.py',
             *('tools/' + name for name in WORKER_SOURCE_FILES),
             'src/test/fixtures/retrieval_evaluation/body-v2/inert.json']
    snapshot = {}
    for name in names:
        path = source / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw = b'{}' if name.endswith('.json') else b'# functional\n'
        path.write_bytes(raw)
        path.chmod(0o644)
        snapshot[name] = {'mode': 0o644, 'sha256': hashlib.sha256(raw).hexdigest()}
    plan = {'schema': 'cloe-body-study-inner-plan-v1', 'role': 'body-study',
            'candidate_head': 'a' * 40, 'candidate_snapshot': snapshot,
            'source_members': names[:1], 'treatment_members': names[1:2]}
    return plan, source, ledger


def test_closure_guards_are_distinct_from_full_checkout_reads(tmp_path):
    plan, source, ledger = _case(tmp_path)
    whole = reserve_inner_source_verification(plan, source, ledger)
    first = reserve_inner_source_closure_guard(plan, source, ledger)
    second = reserve_inner_source_closure_guard(plan, source, ledger)
    assert read_receipt(plan, ledger_root=ledger, receipt=whole)['consumed_reads'] == 1
    current = read_inner_source_closure_receipt(plan, ledger_root=ledger, receipt=first)
    assert current['consumed_guards'] == second['consumed_guards'] == 2
    assert current['bookkeeping_only'] is True and current['execution_authorized'] is False
    assert current['regular_file_bytes'] == closure_guard_read_bound(plan, source)['regular_file_bytes']


def test_closed_guard_budget_cannot_wrap_or_restart(tmp_path):
    plan, source, ledger = _case(tmp_path)
    for count in range(1, GUARD_LIMIT + 1):
        value = reserve_inner_source_closure_guard(plan, source, ledger)
        assert value['consumed_guards'] == count
    with pytest.raises(InnerReadRefusal, match='budget_exhausted'):
        reserve_inner_source_closure_guard(plan, source, ledger)
    assert read_inner_source_closure_receipt(plan, ledger_root=ledger)['remaining_guards'] == 0


def test_simultaneous_reservations_keep_one_monotonic_counter(tmp_path):
    plan, source, ledger = _case(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(lambda _: reserve_inner_source_closure_guard(plan, source, ledger), range(12)))
    assert sorted(row['consumed_guards'] for row in rows) == list(range(1, 13))


def test_changed_plan_and_population_never_reset_the_spent_counter(tmp_path):
    plan, source, ledger = _case(tmp_path)
    reserve_inner_source_closure_guard(plan, source, ledger)
    with pytest.raises(InnerReadRefusal, match='plan_or_size_changed'):
        reserve_inner_source_closure_guard(dict(plan, candidate_head='b' * 40), source, ledger)
    member = source / plan['source_members'][0]
    member.write_bytes(b'# a larger functional source\n')
    with pytest.raises(InnerReadRefusal, match='plan_or_size_changed'):
        reserve_inner_source_closure_guard(plan, source, ledger)
    assert read_inner_source_closure_receipt(plan, ledger_root=ledger)['consumed_guards'] == 1


def test_equal_sized_content_is_not_verified_by_bookkeeping(tmp_path):
    plan, source, ledger = _case(tmp_path)
    first = reserve_inner_source_closure_guard(plan, source, ledger)
    member = source / plan['source_members'][0]
    member.write_bytes(b'# substituted')
    assert len(member.read_bytes()) == len(b'# functional\n')
    second = reserve_inner_source_closure_guard(plan, source, ledger)
    assert first['regular_file_bytes'] == second['regular_file_bytes']
    assert second['bookkeeping_only'] is True and second['execution_authorized'] is False
    assert hashlib.sha256(member.read_bytes()).hexdigest() != plan['candidate_snapshot'][plan['source_members'][0]]['sha256']


def test_pending_interruption_and_bad_private_custody_block_more_work(tmp_path):
    plan, source, ledger = _case(tmp_path)
    reserve_inner_source_closure_guard(plan, source, ledger)
    path = ledger / 'body-study-inner-closure-guard-current.json'
    pending = path.with_name(path.name + '.next')
    pending.write_bytes(b'interrupted')
    with pytest.raises(InnerReadRefusal, match='pending_journal'):
        reserve_inner_source_closure_guard(plan, source, ledger)
    pending.unlink()
    path.chmod(0o644)
    with pytest.raises(InnerReadRefusal, match='private_file_custody'):
        read_inner_source_closure_receipt(plan, ledger_root=ledger)


@pytest.mark.parametrize('kind', ['root-link', 'source-link', 'hardlink', 'wrong-mode'])
def test_aliased_or_changed_source_custody_is_not_counted(tmp_path, kind):
    plan, source, ledger = _case(tmp_path)
    member = source / plan['source_members'][0]
    if kind == 'root-link':
        alias = tmp_path / 'alias'
        alias.symlink_to(source, target_is_directory=True)
        source = alias
    elif kind == 'source-link':
        replacement = source / 'replacement'
        replacement.write_bytes(member.read_bytes())
        member.unlink()
        member.symlink_to(replacement)
    elif kind == 'hardlink':
        os.link(member, source / 'alias')
    else:
        member.chmod(0o600)
    with pytest.raises(InnerReadRefusal):
        reserve_inner_source_closure_guard(plan, source, ledger)
    assert not (ledger / 'body-study-inner-closure-guard-current.json').exists()


@pytest.mark.parametrize('change', [
    {'consumed_guards': True}, {'guard_limit': 512.0}, {'bookkeeping_only': 1},
    {'execution_authorized': True}, {'regular_file_bytes': 1},
    {'schema': 'cloe-supplement-inner-source-read-receipt-v1'},
])
def test_exported_receipt_has_exact_class_types_and_byte_binding(tmp_path, change):
    plan, source, ledger = _case(tmp_path)
    value = reserve_inner_source_closure_guard(plan, source, ledger)
    with pytest.raises(InnerReadRefusal):
        validate_exported_closure_receipt(plan, dict(value, **change),
            regular_file_bytes=value['regular_file_bytes'])


def test_a_future_or_whole_read_receipt_cannot_become_current_guard_state(tmp_path):
    plan, source, ledger = _case(tmp_path)
    value = reserve_inner_source_closure_guard(plan, source, ledger)
    future = dict(value, consumed_guards=2, remaining_guards=GUARD_LIMIT - 2)
    with pytest.raises(InnerReadRefusal, match='receipt_future'):
        read_inner_source_closure_receipt(plan, ledger_root=ledger, receipt=future)
    whole = reserve_inner_source_verification(plan, source, ledger)
    with pytest.raises(InnerReadRefusal, match='receipt_fields'):
        validate_exported_closure_receipt(plan, whole, regular_file_bytes=value['regular_file_bytes'])


def test_equal_float_remaining_guard_count_is_not_an_integer_receipt(tmp_path):
    plan, source, ledger = _case(tmp_path)
    original = reserve_inner_source_closure_guard(plan, source, ledger)
    changed = dict(original, remaining_guards=float(original["remaining_guards"]))
    with pytest.raises(InnerReadRefusal):
        validate_exported_closure_receipt(plan, changed,
            regular_file_bytes=original["regular_file_bytes"])
    assert read_inner_source_closure_receipt(plan, ledger_root=ledger)["consumed_guards"] == 1
