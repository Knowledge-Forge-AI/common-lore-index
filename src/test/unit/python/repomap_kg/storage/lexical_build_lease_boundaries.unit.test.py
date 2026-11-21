"""Unit proofs for owned cloe_lexical claims, leases, fences, and cleanup."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from common_lore_index.storage import lexical_lifecycle as lifecycle
from common_lore_index.storage.lexical_build_lease import (
    acquire_repository_lifecycle_lock, cleanup_owned_lexical_generation, refresh_lexical_build_lease,
)
from common_lore_index.storage.lexical_driver import (
    LexicalConnectionParameters, LexicalStorageError, lexical_connection_scope,
)
from common_lore_index.storage.lexical_population import (
    INSERT_CHUNK_SQL, INSERT_DOCUMENT_SQL, INSERT_OMISSION_SQL,
    claim_lexical_generation, populate_lexical_generation_rows,
)


class _DriverTimeoutError(Exception):
    sqlstate = "57014"


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.current: Any = None
        self.rowcount = 1
        self.description = (("value",),)

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.connection.executed.append((sql, params))
        self.connection.calls.append(("execute", sql, params))
        compact = " ".join(sql.split())
        self.rowcount = 1
        self.current = None
        if compact.startswith("SELECT pg_try_advisory_xact_lock"):
            self.current = self.connection.lock_row
        elif compact.startswith("INSERT INTO cloe_lexical.generations"):
            self.rowcount = self.connection.insert_rowcount
            self.current = ("claimed",) if self.rowcount else None
        elif "SET lease_expires_at" in compact:
            self.current = self.connection.lease_row
            self.rowcount = 1 if self.current is not None else 0
        elif "SELECT state, builder_owner_id" in compact:
            self.current = self.connection.cleanup_row
        elif "SET state = 'invalidated'" in compact:
            self.rowcount = self.connection.cleanup_update_rowcount
        elif compact.startswith("SELECT") and "FROM public.runs" in compact:
            self.current = self.connection.verify_row
        elif compact.startswith("DELETE FROM"):
            self.rowcount = 1

    def executemany(self, sql: str, params_seq: Any) -> None:
        rows = tuple(tuple(row) for row in params_seq)
        call_no = len(self.connection.executemany_calls) + 1
        self.connection.executemany_calls.append((sql, rows))
        self.connection.calls.append(("executemany", sql, rows))
        if self.connection.fail_on_executemany_call == call_no:
            raise self.connection.executemany_error
        self.rowcount = len(rows)
        self.current = None

    def fetchone(self) -> Any:
        return self.current

    def fetchall(self) -> list[Any]:
        return list(self.current) if isinstance(self.current, (list, tuple)) else []


class _Connection:
    def __init__(self) -> None:
        self.lock_row: Any = (True,)
        self.insert_rowcount = 1
        self.lease_row: Any = ("lexgen1:" + "1" * 64,)
        self.cleanup_row: Any = ("building", "owner-a")
        self.cleanup_update_rowcount = 1
        self.verify_row: Any = None
        self.executed: list[tuple[str, Any]] = []
        self.executemany_calls: list[tuple[str, tuple[tuple[Any, ...], ...]]] = []
        self.calls: list[tuple[str, str, Any]] = []
        self.fail_on_executemany_call: int | None = None
        self.executemany_error: BaseException = _DriverTimeoutError("statement timeout")
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1
        self.calls.append(("commit", "", None))

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        return None


def _plan(*, documents: tuple[Any, ...] = (), omissions: tuple[Any, ...] = ()) -> Any:
    binding_id, snapshot_id = "bind1:" + "2" * 64, "snap1:" + "3" * 64
    return SimpleNamespace(
        lexical_generation_id="lexgen1:" + "4" * 64, repository_id=17, run_id=9,
        publication_bundle_id="bundle1:" + "5" * 64, snapshot_manifest_id="snapmanifest1:" + "6" * 64,
        extraction_receipt_id="receipt1:" + "7" * 64, graph_candidate_id="cand1:" + "8" * 64,
        snapshot_vector=((binding_id, 1, snapshot_id),),
        representation_version="cloe-lexical-pg-fts-v1", text_config_regconfig="pg_catalog.simple",
        ranking_function="ts_rank_cd(weight={0.1,0.2,0.4,1.0},normalization=32)",
        normalization_flags=32, alias_split_rule_version="identifier-path-split-v1",
        policy_digest="sha256:" + "9" * 64, resolved_graph_privacy="public-dev",
        binding_policy_digests={binding_id: "sha256:" + "a" * 64},
        admitted_binding_ids=(binding_id,), excluded_binding_ids=(),
        documents=documents, omissions=omissions,
        aggregate_input_bytes=0, aggregate_projected_bytes=0,
        projected_document_count=len(documents),
        projected_chunk_count=sum(len(doc.chunks) for doc in documents),
        omitted_record_count=len(omissions), publication_receipt_json="{}",
    )


def _fence_row(*, run_id: int = 9, no_competing: bool = True, counts_valid: bool = True) -> tuple[Any, ...]:
    plan = _plan()
    vec_json = '[["' + plan.snapshot_vector[0][0] + '",1,"' + plan.snapshot_vector[0][2] + '"]]'
    return (
        run_id, "portable-worker-v1", plan.publication_bundle_id, plan.snapshot_manifest_id,
        plan.extraction_receipt_id, plan.graph_candidate_id, vec_json, True, "{}", True,
        no_competing, counts_valid,
    )


def test_lifecycle_lock_fails_closed_for_false_and_malformed_rows() -> None:
    """A missing, malformed, or false advisory-lock result cannot admit mutation."""
    for lock_row in ((False,), (None,), ()):
        connection = _Connection()
        connection.lock_row = lock_row
        with pytest.raises(LexicalStorageError) as error:
            acquire_repository_lifecycle_lock(connection, 17)
        assert error.value.code == "cloe_lexical_competing_builder_in_progress"


def test_claim_rejects_active_or_reused_generation_without_destructive_delete() -> None:
    """A real claim conflict is surfaced and never replaces an existing ready identity."""
    connection = _Connection()
    connection.insert_rowcount = 0
    with pytest.raises(LexicalStorageError) as error:
        claim_lexical_generation(connection, _plan(), builder_owner_id="owner-b")
    assert error.value.code == "cloe_lexical_competing_builder_in_progress"
    assert not any("DELETE FROM cloe_lexical.generations" in sql for sql, _ in connection.executed)
    insert_sql = next(sql for sql, _ in connection.executed if "INSERT INTO cloe_lexical.generations" in sql)
    assert "state = 'building'" in insert_sql
    assert "lease_expires_at" in insert_sql and "clock_timestamp()" in insert_sql


def test_expired_owner_lease_and_cleanup_owner_fences_are_distinct() -> None:
    """Expiry blocks the old owner, while cleanup rejects cross-owner custody."""
    connection = _Connection()
    connection.lease_row = None
    with pytest.raises(LexicalStorageError) as error:
        refresh_lexical_build_lease(
            connection,
            repository_id=17,
            lexical_generation_id=_plan().lexical_generation_id,
            builder_owner_id="owner-a",
        )
    assert error.value.code == "cloe_lexical_build_lease_lost"
    assert any("builder_owner_id" in sql and "clock_timestamp()" in sql for sql, _ in connection.executed)

    connection = _Connection()
    connection.cleanup_row = ("building", "owner-b")
    with pytest.raises(LexicalStorageError) as error:
        cleanup_owned_lexical_generation(
            connection,
            repository_id=17,
            lexical_generation_id=_plan().lexical_generation_id,
            builder_owner_id="owner-a",
        )
    assert error.value.code == "cloe_lexical_cleanup_owner_mismatch"
    assert not any(sql.lstrip().startswith("DELETE FROM") for sql, _ in connection.executed)


def test_cleanup_clears_owned_custody_and_all_body_families() -> None:
    """Owned failed rows purge authority/body families and commit invalidation custody."""
    connection = _Connection()
    cleanup_owned_lexical_generation(
        connection,
        repository_id=17,
        lexical_generation_id=_plan().lexical_generation_id,
        builder_owner_id="owner-a",
    )
    deleted = " ".join(sql for sql, _ in connection.executed)
    for table in ("authority", "chunks", "documents", "omissions"):
        assert f"cloe_lexical.{table}" in deleted
    update_sql = next(sql for sql, _ in connection.executed if "SET state = 'invalidated'" in sql)
    assert "builder_owner_id = NULL" in update_sql
    assert "lease_expires_at = NULL" in update_sql
    assert connection.commits == 1

    published = _Connection()
    published.cleanup_row = ("published", None)
    with pytest.raises(LexicalStorageError) as error:
        cleanup_owned_lexical_generation(
            published,
            repository_id=17,
            lexical_generation_id=_plan().lexical_generation_id,
            builder_owner_id="owner-a",
        )
    assert error.value.code == "cloe_lexical_cleanup_published_generation"
    assert not any(sql.lstrip().startswith("DELETE FROM") for sql, _ in published.executed)


@pytest.mark.parametrize(
    ("code", "fence"),
    (
        ("cloe_lexical_concurrent_build_active", _fence_row(no_competing=False)),
        ("cloe_lexical_count_verification_failed", _fence_row(counts_valid=False)),
        ("cloe_lexical_publication_fence_mismatch", _fence_row(run_id=10)),
    ),
)
def test_final_activation_fences_competition_expiry_and_newer_publication(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    fence: tuple[Any, ...],
) -> None:
    """Actual publication logic refuses each owned-state race before activation writes."""
    connection = _Connection()
    connection.verify_row = fence
    monkeypatch.setattr(lifecycle, "claim_lexical_generation", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "populate_lexical_generation_rows", lambda *_a, **_k: None)
    with pytest.raises(LexicalStorageError) as error:
        lifecycle.publish_lexical_generation(
            connection,
            _plan(),
            builder_owner_id="owner-a",
        )
    assert error.value.code == code
    verify_sql = next(sql for sql, _ in connection.executed if "FROM public.runs lc" in sql)
    assert "builder_owner_id" in verify_sql and "clock_timestamp()" in verify_sql
    assert not any("INSERT INTO cloe_lexical.authority" in sql for sql, _ in connection.executed)


class _ProbeSeq:
    def __init__(self, conn: _Connection, items: tuple[Any, ...]) -> None:
        self.conn, self.items = conn, items
        self.call_counts: list[int] = []

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Any:
        for item in self.items:
            self.call_counts.append(len(self.conn.executemany_calls))
            yield item


def _chunk(doc_ord: int, chunk_ord: int, *, text: str = "alpha", alias: str = "tok") -> Any:
    return SimpleNamespace(
        chunk_id=f"pchunk1:{doc_ord:032x}{chunk_ord:032x}", binding_id="bind1:" + "2" * 64,
        binding_revision=1, snapshot_id="snap1:" + "3" * 64, source_relative_path=f"src/f{doc_ord}.py",
        chunk_ordinal=chunk_ord, start_line=1, end_line=2, byte_length=len(text.encode("utf-8")),
        chunk_sha256="sha256:" + "b" * 64, chunk_text=text, alias_tokens_text=alias, artifact_privacy="public-dev",
    )


def _doc(ordinal: int, *, text: str = "body", chunks: Any = ()) -> Any:
    return SimpleNamespace(
        document_ordinal=ordinal, binding_id="bind1:" + "2" * 64, binding_revision=1,
        snapshot_id="snap1:" + "3" * 64, source_relative_path=f"src/f{ordinal}.py",
        file_content_sha256="sha256:" + "c" * 64, normalized_byte_length=len(text.encode("utf-8")),
        line_count=1 if text else 0, chunk_count=len(chunks), normalized_text=text,
        artifact_privacy="public-dev", evidence_retention="full-authorized", chunks=chunks,
    )


def _omission(detail: str = "excluded", *, rel_path: str | None = "src/skip.bin") -> Any:
    return SimpleNamespace(
        binding_id="bind1:" + "2" * 64, source_relative_path=rel_path,
        reason_code="unsupported_source_format", detail=detail,
    )


def test_population_ordered_sql_parent_before_chunks_and_binding_parity() -> None:
    """Parents precede chunks per batch; SQL, FTS expressions, ordinals, and binds match."""
    c00, c01, c64 = _chunk(0, 0, text="fn_a", alias="fn a"), _chunk(0, 1, text="fn_b", alias="fn b"), _chunk(64, 0)
    docs = (_doc(0, text="doc0", chunks=(c00, c01)), *(_doc(i) for i in range(1, 64)), _doc(64, chunks=(c64,)))
    plan = _plan(documents=docs, omissions=(_omission("om0", rel_path=None),))
    conn = _Connection()
    populate_lexical_generation_rows(conn, plan, builder_owner_id="owner-a")
    assert not any(sql in {INSERT_DOCUMENT_SQL, INSERT_CHUNK_SQL, INSERT_OMISSION_SQL} for sql, _ in conn.executed)
    assert [(sql, len(rows)) for sql, rows in conn.executemany_calls] == [
        (INSERT_DOCUMENT_SQL, 64), (INSERT_DOCUMENT_SQL, 1), (INSERT_CHUNK_SQL, 3), (INSERT_OMISSION_SQL, 1),
    ]
    phases = [
        kind if kind != "execute" else ("lock" if "advisory" in sql else "lease" if "SET lease_expires_at" in sql else "claim")
        for kind, sql, _ in conn.calls
    ]
    assert phases == [
        "lock", "claim", "commit", "lock", "lease", "executemany", "executemany", "executemany",
        "lock", "lease", "commit", "lock", "lease", "executemany", "lock", "lease", "commit",
    ]
    assert (
        "setweight(to_tsvector('pg_catalog.simple'::regconfig, %s), 'A')\n"
        "        || setweight(to_tsvector('pg_catalog.simple'::regconfig, %s), 'D'),\n"
        "    to_tsvector('pg_catalog.simple'::regconfig, %s),"
    ) in INSERT_CHUNK_SQL
    assert conn.executemany_calls[0][1][0] == (
        17, plan.lexical_generation_id, 0, docs[0].binding_id, 1, docs[0].snapshot_id,
        "src/f0.py", docs[0].file_content_sha256, 4, 1, 2, "doc0", "public-dev", "full-authorized",
    )
    assert [row[2] for row in conn.executemany_calls[2][1]] == [c00.chunk_id, c01.chunk_id, c64.chunk_id]
    assert conn.executemany_calls[2][1][0] == (
        17, plan.lexical_generation_id, c00.chunk_id, c00.binding_id, 1, c00.snapshot_id, "src/f0.py",
        0, 1, 2, 4, c00.chunk_sha256, "fn_a", "fn a", "fn a", "fn_a", "fn_a", "public-dev",
    )
    assert conn.executemany_calls[3][1][0] == (
        17, plan.lexical_generation_id, 0, "bind1:" + "2" * 64, None, "unsupported_source_format", "om0",
    )


def test_population_streams_groups_without_eager_chunk_materialization() -> None:
    """First 64-row group flushes before pulling row 65; chunks stay lazy during document writes."""
    conn = _Connection()
    probe0, probe1 = _ProbeSeq(conn, tuple(_chunk(0, i) for i in range(65))), _ProbeSeq(conn, (_chunk(1, 0),))
    populate_lexical_generation_rows(conn, _plan(documents=(_doc(0, chunks=probe0), _doc(1, chunks=probe1))), claim_generation=False)
    assert [(sql, len(rows)) for sql, rows in conn.executemany_calls] == [
        (INSERT_DOCUMENT_SQL, 2), (INSERT_CHUNK_SQL, 64), (INSERT_CHUNK_SQL, 2),
    ]
    assert probe0.call_counts == [1] * 64 + [2] and probe1.call_counts == [2]


def test_population_utf8_byte_boundary_equality_and_repeated_chunk_params() -> None:
    """Groups admit exact 1 MiB of UTF-8 parameter bytes, split at +1 byte, and count every repeat."""
    plan = _plan()
    base = len(plan.lexical_generation_id) + len("bind1:" + "2" * 64) + len("src/skip.bin") + len("unsupported_source_format")
    first_detail = "é" * 200_000  # 400,000 UTF-8 bytes across 200,000 code points
    second_len = 1024 * 1024 - (2 * base + len(first_detail.encode("utf-8")))
    for extra, groups in ((0, (2,)), (1, (1, 1))):
        om_conn = _Connection()
        populate_lexical_generation_rows(om_conn, _plan(omissions=(_omission(first_detail), _omission("a" * (second_len + extra)))), claim_generation=False)
        assert [(s, len(r)) for s, r in om_conn.executemany_calls] == [(INSERT_OMISSION_SQL, n) for n in groups]
    c_base = sum(len(v) for v in (*_chunk(0, 0, text="", alias="").__dict__.values(), plan.lexical_generation_id) if isinstance(v, str))
    c_text, alias_len = "z" * 100_000, (512 * 1024 - c_base - 300_000) // 2
    for d_text, d_alias, groups in ((0, 0, (2,)), (1, 0, (1, 1)), (0, 1, (1, 1))):
        pair = (_chunk(0, 0, text=c_text, alias="k" * alias_len), _chunk(0, 1, text=c_text + "z" * d_text, alias="k" * (alias_len + d_alias)))
        c_conn = _Connection()
        populate_lexical_generation_rows(c_conn, _plan(documents=(_doc(0, chunks=pair),)), claim_generation=False)
        assert [(s, len(r)) for s, r in c_conn.executemany_calls] == [(INSERT_DOCUMENT_SQL, 1), *((INSERT_CHUNK_SQL, n) for n in groups)]


def test_population_omission_offsets_and_empty_groups() -> None:
    """Empty families emit no executemany calls; omission ordinals continue across outer batches."""
    empty_conn = _Connection()
    populate_lexical_generation_rows(empty_conn, _plan(), claim_generation=False)
    assert empty_conn.executemany_calls == [] and empty_conn.executed == [] and empty_conn.commits == 0
    chunkless_conn = _Connection()
    populate_lexical_generation_rows(chunkless_conn, _plan(documents=(_doc(0), _doc(1))), claim_generation=False)
    assert [(s, len(r)) for s, r in chunkless_conn.executemany_calls] == [(INSERT_DOCUMENT_SQL, 2)] and chunkless_conn.commits == 1
    omission_conn = _Connection()
    populate_lexical_generation_rows(omission_conn, _plan(omissions=tuple(_omission(f"d{i}") for i in range(135))), batch_size=65, claim_generation=False)
    assert [len(rows) for _, rows in omission_conn.executemany_calls] == [64, 1, 64, 1, 5]
    assert [row[2] for _, rows in omission_conn.executemany_calls for row in rows] == list(range(135)) and omission_conn.commits == 3


def test_population_oversized_singleton_sends_alone_and_continues() -> None:
    """A single row above 1 MiB parameter bytes flushes alone without blocking neighbors."""
    docs = (_doc(0, chunks=(_chunk(0, 0), _chunk(0, 1, text="q" * (400 * 1024)), _chunk(0, 2))), _doc(1, text="m" * (1024 * 1024 + 32)), _doc(2))
    conn = _Connection()
    populate_lexical_generation_rows(conn, _plan(documents=docs), claim_generation=False)
    assert [(sql, len(rows)) for sql, rows in conn.executemany_calls] == [(INSERT_DOCUMENT_SQL, 1)] * 3 + [(INSERT_CHUNK_SQL, 1)] * 3
    assert conn.executemany_calls[1][1][0][2] == 1 and conn.executemany_calls[4][1][0][7] == 1 and conn.commits == 1


def test_population_inner_batch_failure_stops_before_later_groups_lease_or_commit() -> None:
    """An inner executemany error aborts remaining groups, post-batch lease refresh, and commit."""
    conn = _Connection()
    conn.fail_on_executemany_call = 1
    with pytest.raises(LexicalStorageError) as error:
        populate_lexical_generation_rows(conn, _plan(documents=tuple(_doc(i, chunks=(_chunk(i, 0),)) for i in range(65)), omissions=(_omission(),)), claim_generation=False)
    assert error.value.code == "lexical_statement_timeout" and len(conn.executemany_calls) == 1 and conn.commits == 0
    assert sum("SET lease_expires_at" in sql for sql, _ in conn.executed) == 1
    rollback_conn = _Connection()
    rollback_conn.fail_on_executemany_call = 2
    fail_probe = _ProbeSeq(rollback_conn, tuple(_chunk(0, i) for i in range(65)))
    params = LexicalConnectionParameters(host="127.0.0.1", port=5432, database="cloe", user="cloe_owner")
    with pytest.raises(LexicalStorageError):
        with lexical_connection_scope(params, connection_factory=lambda _p: rollback_conn, operation="lexical_populate") as scoped:
            populate_lexical_generation_rows(scoped, _plan(documents=(_doc(0, chunks=fail_probe),)), claim_generation=False)
    assert [(sql, len(rows)) for sql, rows in rollback_conn.executemany_calls] == [(INSERT_DOCUMENT_SQL, 1), (INSERT_CHUNK_SQL, 64)]
    assert fail_probe.call_counts == [1] * 64 and rollback_conn.commits == 0 and rollback_conn.rollbacks == 1
    assert sum("SET lease_expires_at" in sql for sql, _ in rollback_conn.executed) == 1
