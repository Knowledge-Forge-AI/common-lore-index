"""Durable bounds for BODY closure traversals, separate from whole checkouts.

These counters reserve visits before readers run. They neither verify source
bytes nor admit execution, and a receipt cannot replace the current reader.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import stat
from typing import Any

from retrieval_body_study_contracts import MAX_PLAN_BYTES
from retrieval_body_study_inner_read_plan import _canonical, _relative, _validate_plan
from retrieval_body_study_inner_reads import (
    DEFAULT_LEDGER_ROOT, InnerReadRefusal, _checked_root, _close_lock, _open_lock,
    _private_file, _publish, _refuse, _require_absent,
)
from retrieval_body_study_closure_paths import BODY_WORKER_SOURCE_FILES as WORKER_SOURCE_FILES

GUARD_LIMIT = 512
GUARD_METADATA_BYTES = 64 * 1024
MAX_RECORD_BYTES = 16 * 1024
LEDGER_SCHEMA = 'cloe-body-study-inner-closure-guard-ledger-v1'
RECEIPT_SCHEMA = 'cloe-body-study-inner-closure-guard-receipt-v1'
_NAME = 'body-study-inner-closure-guard-current.json'
_ACTIVE = 'src/test/fixtures/retrieval_evaluation/body-v2/'
_FIELDS = frozenset({'schema', 'plan_sha256', 'role', 'inner_schema', 'candidate_head',
    'source_snapshot_sha256', 'regular_file_bytes', 'file_visits', 'reserved_bytes_per_guard',
    'consumed_guards', 'guard_limit', 'bookkeeping_only'})
_RECEIPT_FIELDS = _FIELDS | {'remaining_guards', 'execution_authorized'}


def closure_guard_read_bound(plan: Mapping[str, Any], source_root: Path) -> dict[str, int]:
    """Stat the exact repeated populations without reading their payloads."""
    _refuse(plan.get('role') == 'body-study', 'closure_guard_role')
    _refuse(source_root.is_absolute() and source_root.resolve() == source_root,
            'closure_guard_root')
    snapshot = plan.get('candidate_snapshot')
    _refuse(isinstance(snapshot, Mapping), 'closure_guard_snapshot')
    assert isinstance(snapshot, Mapping)
    names: list[str] = []
    for key in ('source_members', 'treatment_members'):
        members = plan.get(key)
        _refuse(isinstance(members, list) and bool(members) and all(type(name) is str for name in members)
                and len(set(members)) == len(members), 'closure_guard_members')
        assert isinstance(members, list)
        names.extend(_relative(name) for name in members)
    names.extend('tools/' + name for name in WORKER_SOURCE_FILES)
    active = sorted(name for name in snapshot if isinstance(name, str) and name.startswith(_ACTIVE))
    _refuse(bool(active), 'closure_guard_active_inputs')
    names.extend(active)
    total = 0
    for name in names:
        name = _relative(name)
        row = snapshot.get(name)
        _refuse(isinstance(row, Mapping) and set(row) == {'sha256', 'mode'},
                'closure_guard_member_snapshot')
        assert isinstance(row, Mapping)
        path = source_root / name
        _refuse(not any(parent.is_symlink() for parent in path.parents),
                'closure_guard_parent_link')
        info = path.lstat()
        _refuse(stat.S_ISREG(info.st_mode) and not path.is_symlink() and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == row['mode']
                and 0 <= info.st_size <= 4 * 1024**2, 'closure_guard_member_type')
        total += info.st_size
        _refuse(total <= 32 * 1024**2, 'closure_guard_population_bound')
    return {'regular_file_bytes': total, 'file_visits': len(names),
            'reserved_bytes_per_guard': total + len(names) + MAX_PLAN_BYTES + GUARD_METADATA_BYTES}


def _base(info: Any, bound: Mapping[str, int]) -> dict[str, Any]:
    return {'schema': LEDGER_SCHEMA, 'plan_sha256': info.plan_sha256,
            'role': info.role, 'inner_schema': info.inner_schema,
            'candidate_head': info.candidate_head,
            'source_snapshot_sha256': info.source_snapshot_sha256, **bound,
            'guard_limit': GUARD_LIMIT, 'bookkeeping_only': True}


def _read(path: Path) -> dict[str, Any]:
    before = _private_file(path)
    assert before is not None
    _refuse(0 < before.st_size <= MAX_RECORD_BYTES, 'closure_guard_record_bound')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        _refuse((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                'closure_guard_record_changed')
        raw = os.read(descriptor, MAX_RECORD_BYTES + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current = _private_file(path)
    _refuse(len(raw) == before.st_size and current is not None
            and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            == (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns),
            'closure_guard_record_changed')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise InnerReadRefusal('closure_guard_record_json') from None
    _refuse(isinstance(value, dict) and _canonical(value) + b'\n' == raw,
            'closure_guard_record_canonical')
    return value


def _validate(value: Mapping[str, Any], info: Any,
              bound: Mapping[str, int] | None) -> dict[str, Any]:
    _refuse(isinstance(value, Mapping) and set(value) == _FIELDS, 'closure_guard_record_fields')
    expected = _base(info, {} if bound is None else bound)
    _refuse(all(value[key] == item for key, item in expected.items()), 'closure_guard_plan_or_size_changed')
    _refuse(value['bookkeeping_only'] is True and type(value['guard_limit']) is int
            and value['guard_limit'] == GUARD_LIMIT, 'closure_guard_fixed_fields')
    _refuse(type(value['consumed_guards']) is int and 1 <= value['consumed_guards'] <= GUARD_LIMIT,
            'closure_guard_count')
    for key in ('regular_file_bytes', 'file_visits', 'reserved_bytes_per_guard'):
        _refuse(type(value[key]) is int and 0 < value[key] <= 40 * 1024**2,
                'closure_guard_byte_fields')
    _refuse(value['reserved_bytes_per_guard'] == value['regular_file_bytes']
            + value['file_visits'] + MAX_PLAN_BYTES + GUARD_METADATA_BYTES,
            'closure_guard_byte_binding')
    return dict(value)


def _receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    return {**value, 'schema': RECEIPT_SCHEMA,
            'remaining_guards': GUARD_LIMIT - int(value['consumed_guards']),
            'execution_authorized': False}


def reserve_inner_source_closure_guard(plan: Mapping[str, Any], repo_root: Path,
        ledger_root: Path = DEFAULT_LEDGER_ROOT, *, lock_timeout: float = 5.0) -> dict[str, Any]:
    info = _validate_plan(plan)
    bound = closure_guard_read_bound(plan, repo_root)
    path = _checked_root(ledger_root) / _NAME
    lock = _open_lock(path.with_name(path.name + '.lock'), lock_timeout)
    try:
        _require_absent(path.with_name(path.name + '.next'), 'closure_guard_pending_journal')
        try:
            old = _read(path)
        except InnerReadRefusal as error:
            if error.code != 'private_file_missing':
                raise
            old = None
        count = 1 if old is None else int(_validate(old, info, bound)['consumed_guards']) + 1
        _refuse(count <= GUARD_LIMIT, 'closure_guard_budget_exhausted')
        value = {**_base(info, bound), 'consumed_guards': count}
        _publish(path, value)
        return _receipt(value)
    finally:
        _close_lock(lock)


def read_inner_source_closure_receipt(plan: Mapping[str, Any], *,
        ledger_root: Path = DEFAULT_LEDGER_ROOT, receipt: Mapping[str, Any] | None = None,
        lock_timeout: float = 5.0) -> dict[str, Any]:
    info = _validate_plan(plan)
    path = _checked_root(ledger_root) / _NAME
    lock = _open_lock(path.with_name(path.name + '.lock'), lock_timeout)
    try:
        _require_absent(path.with_name(path.name + '.next'), 'closure_guard_pending_journal')
        value = _validate(_read(path), info, None)
        if receipt is not None:
            checked = validate_exported_closure_receipt(plan, receipt,
                regular_file_bytes=int(value['regular_file_bytes']))
            _refuse(checked['consumed_guards'] <= value['consumed_guards'], 'closure_guard_receipt_future')
        return _receipt(value)
    finally:
        _close_lock(lock)


def validate_exported_closure_receipt(plan: Mapping[str, Any], value: Mapping[str, Any], *,
        regular_file_bytes: int) -> dict[str, Any]:
    info = _validate_plan(plan)
    _refuse(isinstance(value, Mapping) and set(value) == _RECEIPT_FIELDS
            and value.get('schema') == RECEIPT_SCHEMA, 'closure_guard_receipt_fields')
    state = {key: value[key] for key in _FIELDS}
    state['schema'] = LEDGER_SCHEMA
    checked = _validate(state, info, None)
    _refuse(type(regular_file_bytes) is int and checked['regular_file_bytes'] == regular_file_bytes
            and type(value['remaining_guards']) is int
            and value['remaining_guards'] == GUARD_LIMIT - checked['consumed_guards']
            and value['execution_authorized'] is False, 'closure_guard_receipt_binding')
    return dict(value)


__all__ = ['GUARD_LIMIT', 'closure_guard_read_bound', 'reserve_inner_source_closure_guard',
           'read_inner_source_closure_receipt', 'validate_exported_closure_receipt']
