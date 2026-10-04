#!/usr/bin/env python3
"""Integration test for Scenario O (transactional outbox, v1.2.0) against a
REAL PostgreSQL with in-memory stand-ins for MongoDB and Qdrant.

The relay's correctness is in its SQL -- claiming pending rows, honouring
next_attempt_at, marking done, abandoning leftovers -- so mocking the
database would test the mock. MongoDB and Qdrant are replaced because the
relay only calls db.revoke_nosql / db.revoke_vector on them, and what those
do is already exercised by the existing store tests.

Needs a PostgreSQL to connect to as a superuser that can create the
database and roles. Point it with the usual POSTGRES_* variables, e.g.
    POSTGRES_PORT=5499 POSTGRES_USER=mldb python3 experiments/test_scenario_o.py
It creates and drops its own database (mldb_test_o) and never touches mldb.

Checks:
  1. revoke() commits the ACL update and the outbox rows in one transaction.
  2. The relay applies pending rows, marks them done, records attempts.
  3. Under the PAIRED schedule the relay's attempt k for a layer is the same
     decision Scenario A's single attempt (k = 0) and the bridge's k-th
     attempt would take -- verified against paired_fault_u directly.
  4. A failed attempt is rescheduled with backoff and eventually succeeds
     (unbounded retry), and outbox_status() reports contained only then.
  5. abandon_event() leaves nothing pending for a later trial's relay.
  6. run_trial_o() end to end through the real poller: three layer records,
     the relational window at 0 (closed in the revoke's own transaction),
     the remote windows bounded by the relay cadence, and every label the
     ablation table needs present in extra.
  7. The structural guarantee: the relay is stopped even when the revoke
     raises mid-trial.
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

HOST = os.environ.get("POSTGRES_HOST", "localhost")
PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
SUPER = os.environ.get("POSTGRES_USER", "mldb")
SUPER_PW = os.environ.get("POSTGRES_PASSWORD", "")
TEST_DB = "mldb_test_o"

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  -- {detail}"))


def admin(dbname="postgres"):
    return psycopg.connect(host=HOST, port=PORT, dbname=dbname, user=SUPER, password=SUPER_PW, autocommit=True)


def setup_db():
    with admin() as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        c.execute(f"CREATE DATABASE {TEST_DB}")
    with admin(TEST_DB) as c:
        c.execute("""DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
                CREATE ROLE app_user WITH LOGIN PASSWORD 'app_user_dev_only_change_me';
            END IF; END $$;""")
        c.execute("""CREATE TABLE resources_layer_relational (
            resource_id TEXT PRIMARY KEY, payload JSONB NOT NULL,
            acl TEXT[] NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        c.execute("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON resources_layer_relational TO app_user")
        c.execute((ROOT / "postgres" / "primary" / "outbox.sql").read_text())


def teardown_db():
    with admin() as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


# --- in-memory stand-ins for the two remote stores --------------------------
class FakeStores:
    """acl per (store, resource). Thread-safe enough for this test: the
    relay writes, the pollers read, and Python's dict ops are atomic under
    the GIL for these shapes."""

    def __init__(self):
        self.acl = {"nosql": {}, "vector": {}}
        self.lock = threading.Lock()

    def seed(self, resource_id, acl):
        for s in self.acl:
            self.acl[s][resource_id] = list(acl)

    def revoke(self, store, resource_id, principal):
        with self.lock:
            self.acl[store][resource_id] = [p for p in self.acl[store].get(resource_id, []) if p != principal]

    def grant(self, store, resource_id, principal):
        with self.lock:
            acl = self.acl[store].setdefault(resource_id, [])
            if principal not in acl:
                acl.append(principal)

    def retrievable(self, store, resource_id, principal):
        return principal in self.acl[store].get(resource_id, [])


class _Closable:
    def close(self):
        pass


def patch_remote_stores(db_mod, fake):
    db_mod.connect_mongo = lambda: _Closable()
    db_mod.connect_qdrant = lambda: _Closable()
    db_mod.revoke_nosql = lambda m, r, p: fake.revoke("nosql", r, p)
    db_mod.revoke_vector = lambda q, r, p, collection="resources": fake.revoke("vector", r, p)
    db_mod.grant_nosql = lambda m, r, p: fake.grant("nosql", r, p)
    db_mod.grant_vector = lambda q, r, p, collection="resources": fake.grant("vector", r, p)
    db_mod.is_retrievable_nosql = lambda m, r, p: fake.retrievable("nosql", r, p)
    db_mod.is_retrievable_vector = lambda q, r, p, collection="resources": fake.retrievable("vector", r, p)


def main():
    setup_db()
    os.environ["POSTGRES_HOST"] = HOST
    os.environ["POSTGRES_PORT"] = str(PORT)
    os.environ["POSTGRES_DB"] = TEST_DB
    os.environ["POSTGRES_APP_USER"] = "app_user"
    os.environ["POSTGRES_APP_PASSWORD"] = "app_user_dev_only_change_me"
    try:
        from scenarios.common import db, faults
        from scenarios import scenario_o
        import experiments.run_experiment as rx
        fake = FakeStores()
        patch_remote_stores(db, fake)
        # scenario_o and run_experiment imported db by module, so the
        # patched attributes are what they call.
        # The relay connects as app_user; pg_hba on a dev cluster may be
        # trust or password -- either works with the password given.

        def seed_pair(rid, acl):
            with psycopg.connect(host=HOST, port=PORT, dbname=TEST_DB, user=SUPER, password=SUPER_PW, autocommit=True) as c:
                c.execute("INSERT INTO resources_layer_relational (resource_id, payload, acl) VALUES (%s, '{}', %s) "
                          "ON CONFLICT (resource_id) DO UPDATE SET acl = EXCLUDED.acl", (rid, list(acl)))
            fake.seed(rid, acl)

        conns = scenario_o.open_connections()

        # 1. one transaction: ACL row updated AND outbox rows present
        seed_pair("r1", ["p1", "p2", "p3"])
        t0, info = scenario_o.revoke("r1", "p1", conns)
        check("revoke committed", info["committed"] and info["inline_attempts"] == 1, info)
        check("relational closed in the revoke's own transaction",
              not db.is_retrievable_relational(conns["pg"], "r1", "p1"))
        st = scenario_o.outbox_status(conns["pg"], "r1", "p1")
        check("outbox rows pending for both remote layers before any relay runs",
              st["contained"] is False and sorted(st["missingLayers"]) == ["nosql", "vector"], st)
        check("remote stores still open (nothing relayed yet)",
              fake.retrievable("nosql", "r1", "p1") and fake.retrievable("vector", "r1", "p1"))

        # 2. the relay applies them
        w = scenario_o.OutboxWorker(0.01)
        w.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not scenario_o.outbox_status(conns["pg"], "r1", "p1")["contained"]:
            time.sleep(0.01)
        w.stop()
        st = scenario_o.outbox_status(conns["pg"], "r1", "p1")
        check("relay marked both rows done", st["contained"] is True and st["missingLayers"] == [], st)
        check("remote stores closed by the relay",
              not fake.retrievable("nosql", "r1", "p1") and not fake.retrievable("vector", "r1", "p1"))
        check("each row took one attempt", all(r["attempts"] == 1 for r in st["rows"]), st)
        check("worker stats report both layers applied",
              w.stats()["layers"].get("nosql", {}).get("applied") and w.stats()["layers"].get("vector", {}).get("applied"),
              w.stats())

        # 3 + 4. paired schedule: find a seed where attempt 0 of nosql fails
        # and attempt 1 succeeds at p=0.5, then watch the relay retry.
        p = 0.5
        seed = next(s for s in range(1, 10_000)
                    if faults.paired_fault_u(s, "nosql", 0) < p
                    and faults.paired_fault_u(s, "nosql", 1) >= p
                    and faults.paired_fault_u(s, "vector", 0) >= p
                    and faults.paired_fault_u(s, "relational", 0) >= p)
        fi = faults.FaultInjector(p, seed, schedule="paired")
        fa = faults.FaultInjector(p, seed, schedule="paired")   # what Scenario A would draw
        a_dec = {l: fa.should_fail(l) for l in ("relational", "nosql", "vector")}
        check("Scenario A under the same seed loses exactly the nosql write",
              a_dec == {"relational": False, "nosql": True, "vector": False}, a_dec)
        seed_pair("r2", ["q1", "q2", "q3"])
        w = scenario_o.OutboxWorker(0.01, faults=fi)
        w.start()
        t0, info = scenario_o.revoke("r2", "q1", conns, faults=fi)
        check("relational attempt 0 succeeded (paired: same as A)", info["inline_attempts"] == 1, info)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not scenario_o.outbox_status(conns["pg"], "r2", "q1")["contained"]:
            time.sleep(0.01)
        w.stop()
        st = scenario_o.outbox_status(conns["pg"], "r2", "q1")
        rows = {r["layer"]: r for r in st["rows"]}
        check("nosql needed two attempts, vector one (unbounded retry closed what A dropped)",
              st["contained"] and rows["nosql"]["attempts"] == 2 and rows["vector"]["attempts"] == 1, st)
        check("the failed attempt was recorded by the relay",
              any(l == "nosql" and k == 0 for _, l, k, _ in w.failures), w.failures)
        # the bridge's k-th attempt would take the same decisions:
        check("bridge attempt 0/1 for nosql match (paired_fault_u)",
              faults.paired_fault_u(seed, "nosql", 0) < p and faults.paired_fault_u(seed, "nosql", 1) >= p)
        t_applied = max(t for _, l, _, t in w.applied if l == "nosql")
        check("retry happened after the 50 ms backoff, not before", t_applied - t0 >= 0.05, t_applied - t0)

        # 5. abandon
        seed_pair("r3", ["z1", "z2", "z3"])
        t0, info = scenario_o.revoke("r3", "z1", conns)            # no relay running
        n = scenario_o.abandon_event(conns["pg"], info["event_id"])
        check("abandon_event marks both pending rows", n == 2, n)
        w = scenario_o.OutboxWorker(0.01)
        w.start(); time.sleep(0.1); w.stop()
        check("an abandoned row is never applied by a later relay",
              fake.retrievable("nosql", "r3", "z1") and not w.applied, w.applied)

        # 6. run_trial_o end to end with the real poller
        rx.REGIME.update({"name": "faulty", "fault_p": p, "fault_schedule": "paired"})
        rx.OUTBOX["poll_interval_s"] = 0.01
        rx.DEFAULT_TIMEOUT_S = 5.0
        scale = 20
        resource, principal = rx.pick_trial_subject(scale, seed)
        seed_pair(resource.resource_id, resource.acl)
        # the seeded resource's acl must contain the picked principal
        check("pick_trial_subject picked an ACL member", principal in resource.acl)
        out = Path(tempfile.mkdtemp()) / "o.jsonl"
        with rx.ResultsWriter(out) as writer:
            rx.run_trial_o(scale, seed, writer)
        recs = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        layers = {r["layer"]: r for r in recs}
        check("three layer records written under scenario 'o'",
              sorted(layers) == ["nosql", "relational", "vector"] and all(r["scenario"] == "o" for r in recs), sorted(layers))
        check("every layer confirmed contained", all(r["confirmed_contained"] for r in recs),
              {l: r["confirmed_contained"] for l, r in layers.items()})
        e = layers["relational"]["extra"]
        check("labels present: regime/fault_p/fault_schedule/outbox_*",
              e.get("regime") == "faulty" and e.get("fault_p") == p and e.get("fault_schedule") == "paired"
              and "outbox_status" in e and "outbox_worker" in e and "outbox_revoke" in e, sorted(e))
        # The harness seeds the injector with fault_seed(scale, seed), not the
        # bare seed, so the expected attempt counts are derived from the same
        # function the bridge would use -- the prediction is the pairing.
        fs = rx.fault_seed(scale, seed)

        def expected_attempts(layer):
            k = 0
            while faults.paired_fault_u(fs, layer, k) < p:
                k += 1
            return k + 1
        exp = {l: expected_attempts(l) for l in ("relational", "nosql", "vector")}
        got = {"relational": e["outbox_revoke"]["inline_attempts"],
               "nosql": e["outbox_worker"]["layers"]["nosql"]["attempts"],
               "vector": e["outbox_worker"]["layers"]["vector"]["attempts"]}
        check(f"attempt counts equal the paired prediction from fault_seed ({exp})", got == exp, got)
        check("self-report says contained at trial end", e["outbox_status"]["contained"] is True, e["outbox_status"])
        for l in ("nosql", "vector"):
            if exp[l] > 1:
                check(f"{l} window >= the backoff its {exp[l] - 1} dropped attempt(s) cost",
                      layers[l]["leak_window_s"] >= sum(scenario_o._next_backoff(k) for k in range(exp[l] - 1)),
                      layers[l]["leak_window_s"])
        check("ACL restored for the next trial",
              db.is_retrievable_relational(conns["pg"], resource.resource_id, principal)
              and fake.retrievable("nosql", resource.resource_id, principal))
        with conns["pg"].cursor() as cur:
            cur.execute("SELECT count(*) FROM revoke_outbox WHERE status = 'pending'")
            pending = cur.fetchone()[0]
        conns["pg"].commit()
        check("no pending outbox rows left behind", pending == 0, pending)

        # 6b. outbox_status scoped to the trial's own event: a second revoke
        # on the same pair whose transaction never committed must not be
        # answered by the earlier, finished event.
        seed_pair("r6", ["s1", "s2", "s3"])
        w = scenario_o.OutboxWorker(0.01); w.start()
        t0, info_ok = scenario_o.revoke("r6", "s1", conns)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not scenario_o.outbox_status(conns["pg"], "r6", "s1", info_ok["event_id"])["contained"]:
            time.sleep(0.01)
        w.stop()
        st_old = scenario_o.outbox_status(conns["pg"], "r6", "s1")                        # latest for the pair
        st_new = scenario_o.outbox_status(conns["pg"], "r6", "s1", event_id="obx-never-committed")
        check("pair-level status still reports the finished event contained", st_old["contained"] is True, st_old)
        check("event-scoped status for an uncommitted revoke is NOT contained",
              st_new["contained"] is False and "not committed" in st_new["reason"], st_new)

        # 6c. abandon_all_pending clears rows an interrupted run left behind
        seed_pair("r7", ["u1", "u2", "u3"])
        scenario_o.revoke("r7", "u1", conns)                 # no relay running: rows stay pending
        n = scenario_o.abandon_all_pending(conns["pg"])
        check("abandon_all_pending marks the leftover rows", n == 2, n)
        w = scenario_o.OutboxWorker(0.01); w.start(); time.sleep(0.1); w.stop()
        check("a later relay never applies them", fake.retrievable("nosql", "r7", "u1") and not w.applied)

        # 7. relay stopped even when the revoke raises
        started, stopped = [], []
        real_worker = scenario_o.OutboxWorker

        class Spy(real_worker):
            def start(self):
                started.append(1); super().start()

            def stop(self):
                stopped.append(1); super().stop()
        scenario_o.OutboxWorker = Spy
        real_revoke = scenario_o.revoke
        scenario_o.revoke = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom mid-trial"))
        try:
            with rx.ResultsWriter(Path(tempfile.mkdtemp()) / "o2.jsonl") as writer:
                try:
                    rx.run_trial_o(scale, seed, writer)
                except RuntimeError:
                    pass
        finally:
            scenario_o.OutboxWorker = real_worker
            scenario_o.revoke = real_revoke
        check("relay stopped after the revoke raised", started == [1] and stopped == [1], (started, stopped))

        scenario_o.close_connections(conns)
    finally:
        teardown_db()

    failed = [c for c in CHECKS if not c[1]]
    print()
    if failed:
        raise SystemExit(f"FAIL: {len(failed)} of {len(CHECKS)} checks failed")
    print(f"ALL {len(CHECKS)} SCENARIO-O CHECKS PASSED")


if __name__ == "__main__":
    main()
