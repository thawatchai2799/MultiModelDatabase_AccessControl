# Drives the REAL run_experiment.py grid loop with mocked scenario/db modules (no Docker),
# then analyzes the JSONL it actually wrote. Run from mldb/:  python3 experiments/test_run_experiment_integration.py
"""Drive the real run_experiment.py grid loop with mocked scenario modules,
then analyze the JSONL it actually wrote."""
import sys, types, time, json, random
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from unittest import mock

# --- stub heavy imports before run_experiment imports them
import scenarios, scenarios.common
mods = {}
for name in ["scenarios.common.db", "scenarios.common.data", "scenarios.scenario_a",
             "scenarios.scenario_b", "scenarios.scenario_c"]:
    m = types.ModuleType(name); sys.modules[name] = m; mods[name] = m
    parent, child = name.rsplit(".", 1); setattr(sys.modules[parent], child, m)
db, data = mods["scenarios.common.db"], mods["scenarios.common.data"]
sa, sb, sc = mods["scenarios.scenario_a"], mods["scenarios.scenario_b"], mods["scenarios.scenario_c"]

class R:  # resource stand-in
    def __init__(s, i): s.resource_id=f"res-{i}"; s.acl=[f"p{i}-0", f"p{i}-1", f"p{i}-2"]
data.make_resources = lambda n, seed, acl_size=3, embed=False: [R(i) for i in range(min(n, 50))]

class FakeConn:
    def close(s): pass
    def commit(s): pass
    def cursor(s): return s
    def __enter__(s): return s
    def __exit__(s,*a): pass
    def execute(s,*a): pass
    def fetchone(s): return ("[0.1,0.2]",)
db.connect_pg_primary = lambda: FakeConn()
db.connect_pg_replica = lambda: FakeConn()
db.connect_pg_admin = lambda: FakeConn()
restored = []
db.grant_relational = lambda c, rid, pid: restored.append(("relational", rid, pid))
db.grant_nosql = lambda c, rid, pid: restored.append(("nosql", rid, pid))
db.grant_vector = lambda c, rid, pid: restored.append(("vector", rid, pid))
db.connect_qdrant = lambda: FakeConn()
WORKER_REVOKES = []
def _worker_revoke_vector(c, rid, pid):
    WORKER_REVOKES.append((rid, pid)); A["vector"][0]()       # the batch worker closes the vector layer
db.revoke_vector = _worker_revoke_vector
granted_b = []
sb_grant_log = granted_b

# a "layer" that becomes unretrievable `delay` seconds after revoke
def make_layer(delay):
    state = {"t": None}
    def revoke(): state["t"] = time.monotonic()
    def check(): return state["t"] is None or (time.monotonic() - state["t"]) < delay
    def reset(): state["t"] = None     # fresh (granted) state at the start of every trial
    return revoke, check, reset

# Scenario A: relational closes at 0.05, nosql 0.3, vector 0.15 (sequential polling => vector polled 3rd -> censored)
A = {l: make_layer(d) for l, d in (("relational",0.05),("nosql",0.3),("vector",0.15))}
def _open_a():
    for l in A: A[l][2]()
    return {"pg": FakeConn(), "mongo": FakeConn(), "qdrant": FakeConn()}
sa.open_connections = _open_a
sa.close_connections = lambda c: None
A_REVOKE_CONNS, A_POLL_CONNS = [], []
A_FAULT_CALLS = []
def a_revoke(rid, pid, conns, faults=None, async_vector=None):
    A_REVOKE_CONNS.append(conns["pg"])   # keep the object alive so ids cannot be recycled
    t = time.monotonic(); time.sleep(0.02)
    for l in A:
        if faults is not None and faults.should_fail(l):
            A_FAULT_CALLS.append(l); continue          # naive: skipped, no retry
        if l == "vector" and async_vector is not None:
            async_vector.enqueue_revoke(rid, pid); continue
        A[l][0]()
    return t
sa.revoke = a_revoke
def _mk_a(rid, pid, conns):
    A_POLL_CONNS.append(conns["pg"]); return {l: A[l][1] for l in A}
sa.make_check_fns = _mk_a

# Scenario C: all close within 0.03; bridge says contained; seed 2 -> rejected
C = {l: make_layer(0.0) for l in ("relational","nosql","vector")}
def _open_c():
    for l in C: C[l][2]()
    return {"pg": FakeConn(), "mongo": FakeConn(), "qdrant": FakeConn()}
sc.open_connections = _open_c
sc.close_connections = lambda c: None
def c_revoke(rid, pid, url):
    t = time.monotonic(); time.sleep(0.03)
    if pid.endswith("-1"):  # deterministic "rejected" case for some trials
        return t, {"error": "HTTPConnectionPool: refused"}
    for l in C: C[l][0]()
    # eventId included because the real bridge always returns one and the
    # harness uses it to read the per-event anchoring state.
    # Shaped like the real bridge response: eventId (the harness reads the
    # per-event anchoring state with it), per-layer timings, and the
    # top-level timing breakdown.
    return t, {"eventId": f"evt-{rid}-{pid}", "fullyPropagated": True,
               "propagation": [{"layer": l, "ok": True, "anchored": True, "storeMs": 40,
                                "anchorPropMs": 60, "anchorPropAttempts": 1} for l in C],
               "timing": {"anchorEventMs": 500, "anchorEventAttempts": 1,
                          "propagateMs": 120, "totalMs": 640}}
sc.revoke = c_revoke
sc.bridge_reported_status = lambda rid, pid, url: {"contained": not pid.endswith("-1"), "missingLayers": []}
FAULT_CFG = []
sc.set_fault_config = lambda url, p, seed, schedule="stream": FAULT_CFG.append((p, seed)) or {"p": p, "seed": seed, "schedule": schedule}
sc.get_fault_config = lambda url: {"p": FAULT_CFG[-1][0] if FAULT_CFG else 0, "injected": 0, "attempts": 3}
ANCHOR_CFG = []
sc.set_anchor_config = lambda url, use_async: ANCHOR_CFG.append(use_async) or {"async": use_async}
sc.anchor_status = lambda url, event_id: {"pending": 0, "done": 3, "failed": []} if event_id else None
FABRIC_ON = [True]
AUDIT_MODE = ["ledger"]
sc.get_anchor_config = lambda url: {"async": ANCHOR_CFG[-1] if ANCHOR_CFG else False,
                                    "fabricEnabled": AUDIT_MODE[0] == "ledger",
                                    "auditMode": AUDIT_MODE[0]}
sc.make_check_fns = lambda rid, pid, conns: {l: C[l][1] for l in C}

# Scenario B
B = {"t_rev": None}            # live 'resources' table state: revoke time
def b_revoke(rid, pid):
    B["t_rev"] = time.monotonic(); return B["t_rev"]
sb.revoke = b_revoke
def b_live(): return B["t_rev"] is None        # RLS view of the live table
def b_grant(rid, pid):
    granted_b.append((rid, pid)); B["t_rev"] = None      # ACL restored -> live table retrievable again
sb.grant = b_grant
refreshes = []
refresher_ref = {"r": None}
class Refresher:
    def __init__(s, interval_s): s.interval=interval_s; s.t0=None; refresher_ref["r"]=s
    def start(s): s.t0=time.monotonic()
    def stop(s): pass
    def refresh_once(s): refreshes.append(1)
    def remaining_until_next_tick(s):
        # seconds from the revoke until the first tick strictly after it
        if s.t0 is None or B["t_rev"] is None: return 0.0
        elapsed = B["t_rev"] - s.t0
        return s.interval - (elapsed % s.interval)
sb.MaterializedViewRefresher = Refresher
sb.LogicalConsumer = lambda poll_interval_s: Refresher(poll_interval_s)   # same lag model as B1
# B1: matview refreshed every `interval`: stale until the refresher's next tick after the revoke
sb.is_retrievable_b1 = lambda conn, rid, pid: B["t_rev"] is None or (time.monotonic() - B["t_rev"]) < refresher_ref["r"].remaining_until_next_tick()
sb.is_retrievable_b2 = lambda conn, rid, pid: b_live()          # replica lag ~0 in the mock
sb.is_retrievable_b3 = lambda conn, rid, pid, emb, relaxed: b_live()
class Snap:
    def __init__(s, pid): pass
    def is_retrievable(s, rid): return True   # B4 snapshot leaks forever -> timeout
    def close(s): pass
sb.OpenSnapshot = Snap
Refresher.is_retrievable = lambda s, rid, pid: B["t_rev"] is None or (time.monotonic() - B["t_rev"]) < s.remaining_until_next_tick()
_b6 = iter([True, False, True] * 100)
sb.leak_via_pgbouncer_session = lambda att, rid: next(_b6)

import experiments.run_experiment as re_
re_.DEFAULT_TIMEOUT_S = 0.6   # keep B4's forced timeout short
# short B1/B5 intervals so the random phase + lag is fast in the test
re_.SCENARIO_RUNNERS["b1"] = lambda scale, seed, w, **kw: re_.run_trial_b1(scale, seed, w, refresh_interval_s=0.3)
re_.SCENARIO_RUNNERS["b5"] = lambda scale, seed, w, **kw: re_.run_trial_b5(scale, seed, w, poll_interval_s=0.3)
re_.B6_REPEAT_COUNT = 6
out = "/tmp/integ.jsonl"
import os; os.path.exists(out) and os.remove(out)
sys.argv = ["run_experiment.py", "--scenarios", "a,c,b1,b2,b3,b4,b5,b6", "--scales", "1000,10000",
            "--seeds", "1,2", "--out", out]
re_.main()

from experiments import analyze
recs, errs, sk = analyze.load_results([out])
assert not errs, [e["extra"] for e in errs]
s = analyze.analyse(recs, errs)

# A: drift ~ 0.3 (nosql), timeouts 0
for sc_ in ("1000","10000"):
    d = s["ac"]["a"]["drift"][sc_]; assert d["n"]==2 and 0.28 < d["median"] < 1.5, d   # upper bounds loose: host jitter
# A: every record has both new fields
for r in recs:
    if r["scenario"] in ("a","c"):
        assert "revoke_latency_s" in r["extra"] and "first_check_after_issue_s" in r["extra"] \
            and r["extra"]["retrievable_before_revoke"] is True and r["extra"]["poller_error"] is None, r
        up = r["extra"]["leak_window_upper_s"]
        if r["confirmed_contained"]:
            assert up is not None and up >= r["leak_window_s"], ("bracket must be present and ordered", r)
        else:
            assert up is None, ("no containment observed -> no upper bound", r)
# vector is polled concurrently from before the revoke: its 0.15 s window is now MEASURED, not censored
pv = s["ac"]["a"]["per_layer"]["1000|vector"]; assert pv["n"]==2 and 0.15 <= pv["median"] <= 1.2 and pv["censoring"]["n_zero"]==0, pv
pr = s["ac"]["a"]["per_layer"]["1000|relational"]; assert 0.05 <= pr["median"] <= 1.0, pr
for sc_ in ("1000","10000"):
    assert s["ac"]["a"]["drift"][sc_]["n_invalid_setup_trials"] == 0
# latency ~0.02 for A, 0.03 for C
assert 0.02 <= s["overhead"]["a"]["1000"]["median"] < 1.0
assert 0.03 <= s["overhead"]["c"]["1000"]["median"] < 1.0
# C: which seeds hit the "-1" principal? count rejected vs classified consistently
fc = s["false_containment"]["c"]; c = fc["cells"]
n_c_trials = 4
assert sum(c.values()) - c["incomplete_trials"] == n_c_trials, c
assert c["false_containment"] == 0 and c["false_non_containment_other"] == 0, c
assert fc["time_resolved"]["n_post_claim_leaks"] == 0
# B4 timeouts on every trial
assert s["b"]["b4"]["all"]["n"]==0 and s["b"]["b4"]["all"]["n_timeout"]==4
# B3 has both groups, 4 each
assert s["b"]["b3"]["strict"]["n"]==4 and s["b"]["b3"]["relaxed_order"]["n"]==4
# B6: 6 calls x 4 (scale,seed) with pattern T,F,T -> 4/6 each
for v in s["b"]["b6"]["per_scale_seed"].values(): assert v["calls"]==50 and v["leaked"] in (33,34), v  # repeats default bound at def time; pattern T,F,T
b1 = s["b"]["b1"]["all"]; assert b1["n"]==4 and b1["n_not_retrievable_before_revoke"]==0 and 0 < b1["max"] <= 1.5, b1
assert b1["configured_interval_s"]==[0.3] and b1["n_exceeding_interval"]==0
b5 = s["b"]["b5"]["all"]; assert b5["n"]==4 and 0 < b5["max"] <= 1.5, b5
for r in recs:
    if r["scenario"] in ("b1","b5"): assert 0 <= r["extra"]["phase_s"] <= 0.3
b2 = s["b"]["b2"]["all"]; assert b2["n"]==4 and b2["max"] < 1.0, b2
assert s["overhead"]["b6"] if False else "b6" not in s["overhead"]
# restore ran for every A and C trial (8 trials x 3 layers), including rejected ones (harmless re-grant)
assert len(restored) == 8*3, len(restored)
# B grant-back: b1(4)+b2(4)+b4(4)+b5(4)+b3(8, pre-existing)+b6(200, pre-existing)
assert len(granted_b) == 4+4+4+4+8, len(granted_b)   # b1,b2,b4,b5 once each; b3 twice; b6 never grants (and never revokes)
assert all("victim" in r["extra"] for r in recs if r["scenario"] == "b6")
assert len(refreshes) == 8  # 2 per b1 trial (before + after)
assert A_REVOKE_CONNS and A_POLL_CONNS and not any(a is b for a in A_REVOKE_CONNS for b in A_POLL_CONNS), "A must not share a pg conn between revoke and poller threads"
# the anchoring mode is applied per trial and reset afterwards
assert ANCHOR_CFG and ANCHOR_CFG[-1] is False, ANCHOR_CFG[-5:]
assert all(r["extra"].get("anchor_mode") == "sync" for r in recs if r["scenario"] == "c"), "default must be sync"
print("INTEGRATION OK; cells =", c)

# ---------------- regime: faulty (p=1 -> every A layer skipped -> timeouts; C configured with p=1 too)
out2 = "/tmp/integ_faulty.jsonl"; os.path.exists(out2) and os.remove(out2)
sys.argv = ["run_experiment.py", "--scenarios", "a,c", "--scales", "1000", "--seeds", "1", "--out", out2,
            "--regime", "faulty", "--fault-p", "1.0"]
re_.main()
recs2, errs2, _ = analyze.load_results([out2]); assert not errs2, errs2
s2 = analyze.analyse(recs2, errs2)
assert set(s2["ac"]) == {"a@faulty", "c@faulty"}, set(s2["ac"])
da = s2["ac"]["a@faulty"]["drift"]["1000"]; assert da["n"] == 0 and da["n_timeout"] == 1, da   # nothing revoked -> timeout
assert all(r["extra"]["regime"] == "faulty" and r["extra"]["fault_p"] == 1.0 for r in recs2)
assert sorted(A_FAULT_CALLS) == ["nosql", "relational", "vector"], A_FAULT_CALLS
assert [r["extra"]["injected_failed_layers"] for r in recs2 if r["scenario"] == "a"][0] == ["relational", "nosql", "vector"]
assert (1.0, re_.fault_seed(1000, 1)) in FAULT_CFG and FAULT_CFG[-1] == (0.0, 0), FAULT_CFG   # set for the trial, reset after
assert "c@faulty" in s2["false_containment"]
print("REGIME faulty OK")

# ---------------- regime: async (vector closed only by the worker every 0.3 s)
out3 = "/tmp/integ_async.jsonl"; os.path.exists(out3) and os.remove(out3)
A_POLL_CONNS.clear(); A_REVOKE_CONNS.clear()
sys.argv = ["run_experiment.py", "--scenarios", "a,c", "--scales", "1000", "--seeds", "1,2", "--out", out3,
            "--regime", "async", "--async-interval-s", "0.3"]
re_.main()
recs3, errs3, _ = analyze.load_results([out3]); assert not errs3, errs3
s3 = analyze.analyse(recs3, errs3)
assert set(s3["ac"]) == {"a@async", "c"}, set(s3["ac"])            # C has no async regime: stays healthy
pv = s3["ac"]["a@async"]["per_layer"]["1000|vector"]
assert pv["n"] == 2 and 0.0 < pv["max"] <= 1.5 and pv["n_timeout"] == 0, pv   # closed by the worker (upper bound loose: jitter)
assert len(WORKER_REVOKES) == 2, WORKER_REVOKES
for r in recs3:
    if r["scenario"] == "a": assert 0 <= r["extra"]["phase_s"] <= 0.3 and r["extra"]["async_interval_s"] == 0.3
print("REGIME async OK")

# ---------------- Scenario C with --anchor-async
out4 = "/tmp/integ_anchor_async.jsonl"; os.path.exists(out4) and os.remove(out4)
ANCHOR_CFG.clear()
sc.anchor_status = lambda url, event_id: {"pending": 2, "done": 1, "failed": []} if event_id else None
sc.bridge_reported_status = lambda rid, pid, url: {"contained": False, "missingLayers": ["nosql", "vector"]}
sys.argv = ["run_experiment.py", "--scenarios", "c", "--scales", "1000", "--seeds", "1",
            "--out", out4, "--anchor-async"]
re_.main()
recs4, errs4, _ = analyze.load_results([out4]); assert not errs4, errs4
assert True in ANCHOR_CFG, ANCHOR_CFG          # applied for the trial
assert ANCHOR_CFG[-1] is False, ANCHOR_CFG     # and reset afterwards
assert all(r["extra"]["anchor_mode"] == "async" for r in recs4), [r["extra"].get("anchor_mode") for r in recs4]
s4 = analyze.analyse(recs4, errs4)
cells4 = s4["false_containment"]["c"]["cells"]
# stores closed, ledger not caught up yet -> the PENDING bucket, not a failure
assert cells4["false_non_containment_anchor_pending"] == 1, cells4
assert cells4["false_non_containment_anchor_failed"] == 0 and cells4["false_non_containment_other"] == 0, cells4
assert s4["bridge_timing"]["c"]["anchor_mode"] == ["async"], s4["bridge_timing"]["c"]["anchor_mode"]
print("ANCHOR-ASYNC OK; cells =", {k: v for k, v in cells4.items() if v})

# ---------------- the ledger-free ablation
# With Fabric off the bridge has no containment claim, so /status is
# unavailable and every trial must land in that bucket rather than being
# silently scored as a containment success or failure.
out5 = "/tmp/integ_no_ledger.jsonl"; os.path.exists(out5) and os.remove(out5)
AUDIT_MODE[0] = "none"
sc.anchor_status = lambda url, event_id: None
sc.bridge_reported_status = lambda rid, pid, url: {"error": "status 501", "body": "Fabric anchoring is disabled"}
sys.argv = ["run_experiment.py", "--scenarios", "c", "--scales", "1000", "--seeds", "1", "--out", out5]
re_.main()
recs5, errs5, _ = analyze.load_results([out5]); assert not errs5, errs5
assert all(r["extra"]["fabric_enabled"] is False and r["extra"]["audit_mode"] == "none"
           for r in recs5), "the ablation must label itself"
s5 = analyze.analyse(recs5, errs5)
cells5 = s5["false_containment"]["c"]["cells"]
assert cells5["bridge_status_unavailable"] == 1, cells5
assert cells5["true_containment"] == 0 and cells5["false_containment"] == 0, cells5
assert s5["bridge_timing"]["c"]["fabric_enabled"] == [False], s5["bridge_timing"]["c"]["fabric_enabled"]
AUDIT_MODE[0] = "ledger"
print("NO-LEDGER ABLATION OK; cells =", {k: v for k, v in cells5.items() if v})

# ---------------- the log-table backend
# Same containment verdict as the ledger, so the trials must score as
# ordinary successes -- the difference between the two is not visible in
# this metric, which is exactly the point Section 6.3.3 makes.
out6 = "/tmp/integ_logtable.jsonl"; os.path.exists(out6) and os.remove(out6)
AUDIT_MODE[0] = "log"
sc.anchor_status = lambda url, event_id: {"pending": 0, "done": 3, "failed": []} if event_id else None
sc.bridge_reported_status = lambda rid, pid, url: {"contained": True, "missingLayers": []}
sys.argv = ["run_experiment.py", "--scenarios", "c", "--scales", "1000", "--seeds", "1", "--out", out6]
re_.main()
recs6, errs6, _ = analyze.load_results([out6]); assert not errs6, errs6
assert all(r["extra"]["audit_mode"] == "log" for r in recs6), "log mode must label itself"
assert all(r["extra"]["fabric_enabled"] is False for r in recs6), "log mode is not the ledger"
s6 = analyze.analyse(recs6, errs6)
cells6 = s6["false_containment"]["c"]["cells"]
assert cells6["true_containment"] == 1 and cells6["bridge_status_unavailable"] == 0, cells6
assert s6["bridge_timing"]["c"]["audit_mode"] == ["log"], s6["bridge_timing"]["c"]["audit_mode"]
AUDIT_MODE[0] = "ledger"
print("LOG-TABLE BACKEND OK; cells =", {k: v for k, v in cells6.items() if v})
analyze.write_report("/tmp/an_i", s, len(recs), len(errs), sk, errs)
