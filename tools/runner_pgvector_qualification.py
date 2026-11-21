"""One disposable native PostgreSQL lifecycle, separate from image import."""
from __future__ import annotations

import json
import os
import re
import select
import subprocess
import time
from typing import Any, Callable, Mapping
import uuid

from runner_pgvector_budget import refuse
from runner_pgvector_prebuilt import OWNER_LABEL, OWNER_VALUE, provider_facts

SQL = """LOAD '$libdir/vector';
CREATE EXTENSION vector;
SELECT current_setting('server_version_num')::int / 10000, extversion,
 '[1,0]'::vector <=> '[1,0]'::vector, '[1,0]'::vector <=> '[0,1]'::vector
 FROM pg_extension WHERE extname='vector';
SELECT setting FROM pg_config WHERE name IN ('PKGLIBDIR','SHAREDIR') ORDER BY name;
SHOW jit;
"""
BEGIN_SQL = """BEGIN;
CREATE TABLE cloe_native_vectors (id integer PRIMARY KEY, embedding vector(2));
INSERT INTO cloe_native_vectors VALUES (1,'[1,0]'),(2,'[0,1]'),(3,'[-1,0]');
CREATE INDEX cloe_native_vectors_cosine ON cloe_native_vectors USING hnsw (embedding vector_cosine_ops);
SET LOCAL enable_seqscan=off;
EXPLAIN (FORMAT JSON, COSTS OFF) SELECT id FROM cloe_native_vectors ORDER BY embedding <=> '[1,0]' LIMIT 1;
ROLLBACK;
SELECT to_regclass('cloe_native_vectors') IS NULL;
"""
START = """set -eu
initdb -D /var/lib/postgresql/data/pgdata -A trust -U postgres --no-instructions
exec postgres -D /var/lib/postgresql/data/pgdata -c listen_addresses= -c unix_socket_directories=/tmp \
 -c jit=off -c shared_buffers=16MB -c max_connections=10 -c max_parallel_workers=0
"""
READY = """set -eu
attempt=0
until pg_isready -q -h /tmp -U postgres -d postgres -t 1; do
 attempt=$((attempt+1)); test "$attempt" -lt 30; sleep 1
done
"""


def bounded_native_run(command: list[str], *, env: Mapping[str, str], capture_output: bool,
                       text: bool, input: object, timeout: int, check: bool) -> subprocess.CompletedProcess[str]:
    """Bound both diagnostic streams while retaining the normal runner seam."""
    refuse(capture_output and text and input is None and not check and 0 < timeout <= 300,
           "native command options")
    child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert child.stdout is not None and child.stderr is not None
    streams = {child.stdout: bytearray(), child.stderr: bytearray()}
    active = set(streams)
    deadline = time.monotonic() + timeout
    try:
        while active:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout,
                    output=bytes(streams[child.stdout]), stderr=bytes(streams[child.stderr]))
            ready, _, _ = select.select(list(active), [], [], remaining)
            if not ready:
                raise subprocess.TimeoutExpired(command, timeout,
                    output=bytes(streams[child.stdout]), stderr=bytes(streams[child.stderr]))
            for source in ready:
                raw = os.read(source.fileno(), min(65536, 65537 - len(streams[source])))
                if not raw:
                    active.remove(source)
                else:
                    streams[source].extend(raw)
                    refuse(len(streams[source]) <= 65536, "native command output cap")
        code = child.wait(timeout=max(0.1, deadline - time.monotonic()))
        return subprocess.CompletedProcess(command, code,
            streams[child.stdout].decode("utf-8", errors="replace"),
            streams[child.stderr].decode("utf-8", errors="replace"))
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()


def qualify(execute: Callable[..., subprocess.CompletedProcess[str]],
            event: Callable[..., None], image_id: str) -> dict[str, Any]:
    """Only the separately admitted native integration owner calls this once."""
    facts = provider_facts()
    refuse(facts["depends"] == "postgresql-16 (>> 16.16), libc6 (>= 2.38)"
           and facts["breaks"] == "postgresql-16-jit-llvm (<< 19)", "unsupported dependency clauses")
    name = "cloe-pgvector-native-" + uuid.uuid4().hex
    identity: str | None = None
    primary: BaseException | None = None
    create_attempted = False
    deadline = time.monotonic() + 1200

    def call(stage: str, args: list[str], *, check: bool = True,
             timeout: int = 300) -> subprocess.CompletedProcess[str]:
        remaining = deadline - time.monotonic()
        refuse(remaining > 0, "native qualifier deadline")
        return execute(stage, args, check=check, timeout=max(1, min(timeout, int(remaining))))

    def in_container(stage: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        assert identity is not None
        return call(stage, ["docker", "exec", "--user", "postgres", identity, *args], check=check)

    try:
        absent = call("native_name_inventory", ["docker", "ps", "--all", "--filter",
            "name=^" + name + "$", "--format", "{{.ID}}"])
        refuse(not absent.stdout.strip(), "native name already exists")
        create_attempted = True
        created = call("native_create", ["docker", "create", "--pull=never", "--name", name,
            "--label", OWNER_LABEL + "=" + OWNER_VALUE, "--network", "none", "--user", "postgres",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
            "--memory", "2g", "--cpus", "2", "--pids-limit", "256", "--shm-size", "64m",
            "--tmpfs", "/tmp:rw,exec,mode=1777,size=32m",
            "--tmpfs", "/var/lib/postgresql/data:rw,mode=1777,size=160m",
            "--entrypoint", "/bin/sh", image_id, "-c", START], timeout=30)
        returned = created.stdout.strip()
        refuse(re.fullmatch(r"[0-9a-f]{64}", returned) is not None, "native returned identity")
        identity = returned
        call("native_start", ["docker", "start", identity], timeout=30)
        in_container("native_readiness", "/bin/sh", "-c", READY)
        status = in_container("native_status_before", "sha256sum", "/var/lib/dpkg/status").stdout.split()[0]
        refuse(status == facts["target_dpkg_status_sha256"], "target status drift")
        postgres_version = in_container("native_postgres_version", "dpkg-query", "-W",
            "-f=${Version}", "postgresql-16").stdout.strip()
        libc_version = in_container("native_libc_version", "dpkg-query", "-W",
            "-f=${Version}", "libc6").stdout.strip()
        provides = in_container("native_provides", "dpkg-query", "-W",
            "-f=${Provides}", "postgresql-16").stdout.strip()
        match = re.search(r"(?:^|,\s*)postgresql-16-jit-llvm \(= ([^)]+)\)(?:,|$)", provides)
        if match is None:
            raise RuntimeError("pgvector prebuilt refused: versioned JIT provide missing")
        in_container("native_postgres_constraint", "dpkg", "--compare-versions", postgres_version, "gt", "16.16")
        in_container("native_libc_constraint", "dpkg", "--compare-versions", libc_version, "ge", "2.38")
        jit_break = in_container("native_breaks_constraint", "dpkg", "--compare-versions",
                                 match[1], "lt", "19", check=False)
        refuse(jit_break.returncode == 1, "versioned JIT Breaks matches or errors")
        psql = ("psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-h", "/tmp",
                "-U", "postgres", "-d", "postgres", "-c")
        observed = in_container("native_extension", *psql, SQL).stdout.strip().splitlines()
        refuse(observed == ["16|0.8.7|0|1", "/usr/lib/postgresql/16/lib",
                             "/usr/share/postgresql/16", "off"], "native extension/path/distance/JIT proof")
        transaction = in_container("native_index_rollback", *psql, BEGIN_SQL).stdout.strip()
        plan_json, separator, rolled_back = transaction.rpartition("\n")
        refuse(separator == "\n" and rolled_back == "t", "native rollback proof")
        query_plan = json.loads(plan_json)
        def index_used(node: object) -> bool:
            return isinstance(node, dict) and (
                node.get("Index Name") == "cloe_native_vectors_cosine" or
                any(index_used(child) for child in node.get("Plans", [])))
        refuse(isinstance(query_plan, list) and len(query_plan) == 1
               and isinstance(query_plan[0], dict)
               and index_used(query_plan[0].get("Plan")), "native index execution plan")
        after = in_container("native_status_after", "sha256sum", "/var/lib/dpkg/status").stdout.split()[0]
        refuse(after == status, "target status changed")
        event("native_qualification", "completed", dependencies=True, extension=True,
              vector_distances=True, index=True, rollback=True, jit="off")
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        if identity is None and create_attempted:
            try:
                inspected = execute("native_creation_cleanup_identity",
                    ["docker", "container", "inspect", name], check=False, timeout=10)
                if inspected.returncode == 0:
                    candidate, = json.loads(inspected.stdout)
                    refuse(candidate.get("Image") == image_id
                           and candidate.get("Config", {}).get("Labels", {}).get(OWNER_LABEL) == OWNER_VALUE
                           and re.fullmatch(r"[0-9a-f]{64}", candidate.get("Id", "")) is not None,
                           "native cleanup ownership unproven")
                    identity = candidate["Id"]
                else:
                    absent = execute("native_creation_absence", ["docker", "ps", "--all",
                        "--filter", "name=^" + name + "$", "--format", "{{.ID}}"], check=False, timeout=10)
                    refuse(absent.returncode == 0 and not absent.stdout.strip(),
                           "native creation cleanup absence unproven")
            except BaseException as error:
                cleanup_errors.append(error)
        if identity is not None:
            for stage, command in (
                ("native_diagnostics", ["docker", "logs", "--tail", "100", identity]),
                ("native_cleanup", ["docker", "rm", "--force", "--volumes", identity]),
            ):
                try:
                    execute(stage, command, timeout=20)
                except BaseException as error:
                    cleanup_errors.append(error)
            try:
                absence = execute("native_cleanup_readback", ["docker", "ps", "--all", "--no-trunc",
                    "--filter", "id=" + identity, "--format", "{{.ID}}"], check=False, timeout=10)
                refuse(absence.returncode == 0 and not absence.stdout.strip(), "native runtime cleanup unproven")
            except BaseException as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            event("native_cleanup", "failed", verified=False)
            if primary is None:
                raise RuntimeError("native diagnostics or cleanup failed") from cleanup_errors[0]
            primary.add_note("native diagnostics or cleanup also failed")
    event("native_cleanup", "completed", removed=True, verified=True)
    return {"native_qualification": "passed", "postgres_abi": "16", "pgvector_version": "0.8.7",
            "native_lifecycles": 1, "jit": "off", "cleanup_proven": True,
            "production_or_scientific_admission": False}
