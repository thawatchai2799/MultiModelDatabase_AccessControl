#!/usr/bin/env python3
"""Stage-by-stage connectivity check for the VM, run BEFORE seeding or any
experiment. Each check is independent and reports PASS/FAIL with the raw
exception, so one broken service does not hide the state of the others.

Usage:
  python experiments/check_env.py            # all checks
  python experiments/check_env.py --no-bridge   # skip the bridge (not started yet)

Exit code 0 iff every non-skipped check passed. Send the full output back
when anything fails -- the exception text is what a fix needs.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((name, True, detail))
        print(f"PASS  {name}: {detail}")
    except Exception as e:  # report, never abort
        results.append((name, False, f"{type(e).__name__}: {e}"))
        print(f"FAIL  {name}: {type(e).__name__}: {e}")


def pg_check(connect, expect_table, expect_role_bypass=None):
    def _():
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                ver = cur.fetchone()[0].split(",")[0]
                cur.execute("SELECT current_user, (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)")
                user, bypass = cur.fetchone()
                cur.execute("SELECT to_regclass(%s)", (expect_table,))
                if cur.fetchone()[0] is None:
                    raise RuntimeError(f"table {expect_table} does not exist -- init.sql did not run?")
                cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                has_vec = cur.fetchone() is not None
            conn.rollback()
            if expect_role_bypass is not None and bool(bypass) != expect_role_bypass:
                raise RuntimeError(f"{user} BYPASSRLS={bypass}, expected {expect_role_bypass}")
            return f"{ver}; user={user} bypassrls={bypass} pgvector={'yes' if has_vec else 'NO'}"
        finally:
            conn.close()
    return _


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-bridge", action="store_true")
    ap.add_argument("--bridge-url", default="http://localhost:8080")
    args = ap.parse_args()

    print("=== imports (installed package versions) ===")
    def versions():
        import psycopg, pymongo, qdrant_client, numpy, requests  # noqa: F401 (import check)
        from importlib.metadata import version
        return (f"psycopg {psycopg.__version__}, pymongo {pymongo.__version__}, "
                f"qdrant-client {version('qdrant-client')}, numpy {numpy.__version__}, requests {requests.__version__}")
    check("python packages", versions)
    check("sentence-transformers import (slow first time)", lambda: __import__("sentence_transformers").__version__)

    from scenarios.common import db

    print("=== PostgreSQL ===")
    check("pg primary as app_user (RLS-enforced)", pg_check(db.connect_pg_primary, "resources", expect_role_bypass=False))
    check("pg primary as admin_user (BYPASSRLS)", pg_check(db.connect_pg_admin, "resources", expect_role_bypass=True))
    check("pg primary: resources_layer_relational (A/C)", pg_check(db.connect_pg_primary, "resources_layer_relational"))
    check("pg primary: resources_hot matview (B1)", pg_check(db.connect_pg_primary, "resources_hot"))

    def outbox():
        # Scenario O (v1.2.0). On a fresh volume docker-compose applies
        # outbox.sql; on an existing cluster it must be applied once by
        # hand, and a relay that cannot UPDATE would fail on the first row.
        conn = db.connect_pg_primary()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.revoke_outbox') IS NOT NULL")
                if not cur.fetchone()[0]:
                    raise RuntimeError("table revoke_outbox missing -- apply postgres/primary/outbox.sql "
                                       "(docker exec -i mldb-postgres-primary psql -U mldb -d mldb < postgres/primary/outbox.sql)")
                cur.execute("SELECT has_table_privilege('revoke_outbox', 'INSERT'), "
                            "has_table_privilege('revoke_outbox', 'UPDATE'), has_table_privilege('revoke_outbox', 'DELETE')")
                ins, upd, dele = cur.fetchone()
                cur.execute("SELECT count(*) FROM revoke_outbox WHERE status = 'pending'")
                pending = cur.fetchone()[0]
            conn.rollback()
            if not (ins and upd and dele):
                raise RuntimeError(f"app_user privileges on revoke_outbox: insert={ins} update={upd} delete={dele}")
            return f"present, app_user can insert/update/delete; {pending} pending row(s)" + \
                   (" -- LEFTOVER from an interrupted run, a relay would apply them" if pending else "")
        finally:
            conn.close()
    check("pg primary: revoke_outbox (O)", outbox)

    def replica():
        conn = db.connect_pg_replica()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_is_in_recovery(), pg_last_wal_replay_lsn()")
                rec, lsn = cur.fetchone()
            conn.rollback()
            if not rec:
                raise RuntimeError("port 5433 is NOT a streaming replica (pg_is_in_recovery() = false)")
            return f"in recovery, replay lsn {lsn}"
        finally:
            conn.close()
    check("pg replica (B2) streaming", replica)

    def logical():
        conn = db.connect_pg_admin()
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW wal_level")
                wl = cur.fetchone()[0]
                cur.execute("SELECT pubname FROM pg_publication WHERE pubname = 'resources_pub'")
                pub = cur.fetchone()
            conn.rollback()
            if wl != "logical":
                raise RuntimeError(f"wal_level={wl}, B5 needs 'logical'")
            if pub is None:
                raise RuntimeError("publication resources_pub missing (init.sql)")
            return f"wal_level={wl}, publication resources_pub present"
        finally:
            conn.close()
    check("pg logical replication prereqs (B5)", logical)

    def pgb(connect, label):
        def _():
            conn = connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT current_database(), inet_server_port()")
                    dbn, port = cur.fetchone()
                conn.rollback()
                return f"{label}: db={dbn} backend port={port}"
            finally:
                conn.close()
        return _
    check("pgbouncer default pool", pgb(db.connect_pg_pgbouncer, "pgbouncer"))
    check("pgbouncer mldb_b6 pool (B6, pool_size=1)", pgb(db.connect_pg_pgbouncer_b6, "pgbouncer b6"))

    print("=== MongoDB ===")
    def mongo():
        m = db.connect_mongo()
        try:
            info = m.server_info()
            return f"MongoDB {info['version']}"
        finally:
            m.close()
    check("mongo", mongo)

    print("=== Qdrant ===")
    def qdrant():
        q = db.connect_qdrant()
        cols = [c.name for c in q.get_collections().collections]
        return f"collections={cols or '[] (none yet, seed.py creates one)'}"
    check("qdrant", qdrant)

    if not args.no_bridge:
        print("=== Bridge ===")
        import requests
        def health():
            r = requests.get(f"{args.bridge_url}/health", timeout=5)
            r.raise_for_status()
            return r.json()
        check("bridge /health", health)
        def status():
            # Which backend is answering decides what a 200 here proves. A
            # 200 from a log-mode bridge proves PostgreSQL is reachable, not
            # Fabric, and reporting it as "Fabric reachable" would hide a
            # forgotten AUDIT_MODE override behind a green check.
            try:
                mode = requests.get(f"{args.bridge_url}/anchor-config", timeout=5).json().get("auditMode")
            except Exception:
                mode = None                       # older bridge: assume ledger
            r = requests.get(f"{args.bridge_url}/status/res-smoke/p-smoke", timeout=30)
            # 200 with contained=false (no event) == the audit backend answered.
            # 501 == AUDIT_MODE=none (fine for an A/B-only run).
            # 502 == backend NOT reachable -> for ledger, the host.docker.internal path.
            if r.status_code == 501:
                return "NO audit trail (AUDIT_MODE=none) -- Scenario C will run unanchored"
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            if mode == "log":
                return ("AUDIT_MODE=log: answering from the PostgreSQL audit table, NOT Fabric. "
                        f"This is the ablation backend -- restart without the override for Scenario C. {r.json()}")
            return f"Fabric reachable from bridge (audit={mode or 'ledger'}): {r.json()}"
        check("bridge -> audit backend (/status)", status)
        def paired():
            # The paired fault schedule (v1.2.0) needs a bridge built from
            # this source; an older container ignores the field silently.
            r = requests.post(f"{args.bridge_url}/fault-config", json={"p": 0, "seed": 0, "schedule": "paired"}, timeout=5)
            r.raise_for_status()
            got = r.json().get("schedule")
            requests.post(f"{args.bridge_url}/fault-config", json={"p": 0, "seed": 0}, timeout=5)
            if got != "paired":
                raise RuntimeError("bridge ignores the fault schedule -- rebuild it: docker compose up -d --build bridge")
            return "bridge accepts schedule=paired (reset to stream, p=0)"
        check("bridge paired fault schedule", paired)

    failed = [n for n, ok, _ in results if not ok]
    print("=== summary ===")
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED: " + "; ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
