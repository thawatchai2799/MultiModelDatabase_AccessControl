#!/usr/bin/env python3
"""Orchestrates the full experiment grid: RQ1/RQ3 (Scenario A, C) at 5 seeds
x 3 scales (1K/10K/100K), RQ2 (Scenario B1-B6) at 3 seeds x one scale (10K)
-- both per the design agreed and recorded in PROGRESS.md.

Usage:
  python run_experiment.py --scenarios a,c --scales 1000,10000,100000 --seeds 1,2,3,4,5
  python run_experiment.py --scenarios b1,b2,b3,b4,b5,b6 --scales 10000 --seeds 1,2,3
  python run_experiment.py --scenarios a,b1,c --scales 1000 --seeds 1  # a quick smoke test

Each trial revokes ONE principal from ONE resource (chosen from that
resource's seeded ACL), polls each layer on its own thread -- already
running BEFORE the revoke is issued (see poll.measure_leak_windows_concurrent
for why) -- until containment is confirmed or timeout,
then grants the principal back so the next trial (which may target the
same pair -- see restore_acl_ac) starts from the seeded state. seed.py is
run separately, once per scale, before this script -- it is NOT called
from here.
Results are appended to results/<run_id>.jsonl as they complete, so a run
interrupted partway through loses at most its last unfinished trial.
"""
import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiments.poll import measure_leak_windows_concurrent
from experiments.results import ResultsWriter, TrialResult
from scenarios.common import db
from scenarios.common.data import make_resources
from scenarios.common.faults import AsyncVectorWorker, FaultInjector
import scenarios.scenario_a as scenario_a
import scenarios.scenario_b as scenario_b
import scenarios.scenario_c as scenario_c

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_POLL_INTERVAL_S = 0.01
DEFAULT_CONFIRM_CHECKS = 5
B6_REPEAT_COUNT = 50  # per (scale, seed) -- B6 is a boolean occurrence rate,
# not a timed window, so it needs repetition to produce a rate rather than
# a single trial's single true/false.

# Scenario A / C regimes (PROGRESS.md decision 11). Set once per run from
# the CLI; A-healthy is the default so nothing changes unless asked for.
REGIME = {"name": "healthy", "fault_p": 0.0, "async_interval_s": 0.0}
# Scenario C variant: does the bridge wait for propagation anchoring before
# replying (default, so a 200 means it is on the ledger) or anchor in the
# background? Set once per run from the CLI and applied per trial.
ANCHOR_ASYNC = {"enabled": False}
# Set once a Scenario C trial has seen the bridge running without Fabric, so
# the banner is printed once per run rather than per trial. A forgotten
# FABRIC_ENABLED=false would otherwise produce a whole grid of unanchored
# results that look exactly like ordinary Scenario C in the console.
_LEDGER_OFF_ANNOUNCED = {"done": False}


def fabric_config() -> dict:
    """What fabric/network-up.sh recorded about the network that is running:
    currently the orderer's BatchTimeout, which dominates Scenario C's Leak
    Window (decision 5 anchors before touching any store). Copied into every
    C record so a sweep cannot be mislabelled afterwards -- reconstructing
    which run used which value from memory is exactly the kind of bookkeeping
    that has already produced two uninterpretable result sets."""
    path = Path(__file__).resolve().parent.parent / ".fabric-config.json"
    try:
        import json
        return json.loads(path.read_text())
    except Exception:
        return {}


def fault_seed(scale: int, seed: int) -> int:
    """One fault-RNG seed per (scale, seed), shared in spirit by A and the
    bridge (each has its own PRNG -- different streams, same p and seed)."""
    return (seed * 17_000_003 + scale) & 0x7FFFFFFF


def regime_extra() -> dict:
    return {"regime": REGIME["name"], "fault_p": REGIME["fault_p"], "async_interval_s": REGIME["async_interval_s"]}


def restore_acl_ac(resource_id: str, principal_id: str, conns: dict) -> None:
    """Put the principal back on the resource's ACL in all three stores after
    an A or C trial. Without this, trials are not independent: the grid
    runs many trials against ONE seeding (seed.py is a separate step and is
    not re-run per trial), and pick_trial_subject deterministically picks
    the same (resource, principal) for a given (scale, seed) -- so a later
    scenario (e.g. C after A, which share stores) or a later B-mechanism
    would find the principal already revoked and measure a fake 0 s window.
    Done via the direct store clients in both A and C: this is harness
    housekeeping, not the thing under test, so it must not go through the
    bridge. (The Fabric ledger is unaffected: IsContained looks at the
    latest event, and the next C trial anchors a fresh revoke.)"""
    # Never raises: the trial's measurement is already written by the time
    # this runs, so a restore failure must be loud (it taints LATER trials
    # on this pair) but must not relabel a completed trial as a failure.
    for layer, fn_name, conn_key in (("relational", "grant_relational", "pg"),
                                     ("nosql", "grant_nosql", "mongo"),
                                     ("vector", "grant_vector", "qdrant")):
        try:
            getattr(db, fn_name)(conns[conn_key], resource_id, principal_id)
        except Exception as e:
            print(f"  [restore] WARNING: {layer} grant-back failed for {resource_id}/{principal_id}: {e} "
                  f"-- later trials on this pair may be invalid")


def pick_trial_subject(n: int, seed: int, acl_size: int = 3):
    """Deterministic per-(n, seed) choice of one resource and one of its
    ACL members -- reproducible across a re-run with the same seed, without
    needing to persist which resource/principal was picked."""
    rng = random.Random(seed * 1_000_003 + n)  # distinct stream per (n, seed)
    resources = make_resources(n, seed=seed, acl_size=acl_size, embed=False)
    resource = rng.choice(resources)
    principal = rng.choice(resource.acl)
    return resource, principal


# ---------------------------------------------------------------------------
# Shared measurement helper (concurrent, pre-revoke polling)
# ---------------------------------------------------------------------------

def measure(check_fns: dict, revoke_fn, before_revoke_fn=None):
    """Runs the concurrent poller with the experiment's default cadence.
    Returns (t_issued, revoke_return, revoke_latency_s, {layer: measurement})."""
    return measure_leak_windows_concurrent(
        check_fns, revoke_fn,
        poll_interval_s=DEFAULT_POLL_INTERVAL_S, confirm_checks=DEFAULT_CONFIRM_CHECKS,
        timeout_s=DEFAULT_TIMEOUT_S, before_revoke_fn=before_revoke_fn,
    )


def measurement_extra(m, revoke_latency_s: float) -> dict:
    """The per-layer fields analyze.py reads. first_check_after_issue_s is the
    censor bound for a 0 s window (the layer was already closed at the first
    post-issue check; true window in [0, bound]). retrievable_before_revoke
    False means the trial's setup was wrong (nothing to revoke) and the
    measurement is not evidence of anything; poller_error is the exception
    text if the poller thread died."""
    return {
        "revoke_latency_s": revoke_latency_s,
        "first_check_after_issue_s": m.first_check_after_issue_s,
        "max_gap_after_issue_s": m.max_gap_after_issue_s,
        "check_errors": m.check_errors,
        "leak_window_upper_s": m.leak_window_upper_s,
        "poll_interval_s": DEFAULT_POLL_INTERVAL_S,
        "retrievable_before_revoke": m.retrievable_before_revoke,
        "poller_error": m.error,
    }


def write_layer_results(writer, scenario, scale, seed, resource_id, principal, t_issued,
                        revoke_latency_s, results: dict, extra_common: dict | None = None):
    for layer, m in results.items():
        extra = measurement_extra(m, revoke_latency_s)
        if extra_common:
            extra.update(extra_common)
        writer.write(TrialResult(
            scenario=scenario, scale=scale, seed=seed, resource_id=resource_id,
            principal_id=principal, layer=(None if layer == "_" else layer), t_issued=t_issued,
            leak_window_s=m.leak_window_s, confirmed_contained=m.confirmed_contained,
            checks_performed=m.checks_performed, extra=extra,
        ))
        if not m.retrievable_before_revoke:
            print(f"  [{scenario}] WARNING: {layer}: resource was NOT retrievable before the revoke "
                  f"-- setup problem (not seeded? ACL already revoked?); this measurement is not evidence")
        if m.error:
            print(f"  [{scenario}] WARNING: {layer}: poller error: {m.error}")
        if m.check_errors:
            print(f"  [{scenario}] NOTE: {layer}: {m.check_errors} check(s) raised "
                  f"(last: {m.last_check_error}) -- counted, not treated as an answer")
        if m.max_gap_after_issue_s is not None and m.max_gap_after_issue_s > 20 * DEFAULT_POLL_INTERVAL_S:
            # The poller was starved (host/VM scheduling). The window is then
            # only known to within that gap, so say so rather than implying
            # poll-interval resolution.
            print(f"  [{scenario}] NOTE: {layer}: largest gap between checks was "
                  f"{m.max_gap_after_issue_s * 1000:.0f} ms (poll interval "
                  f"{DEFAULT_POLL_INTERVAL_S * 1000:.0f} ms) -- measurement resolution for this trial")


# ---------------------------------------------------------------------------
# Scenario A
# ---------------------------------------------------------------------------

def run_trial_a(scale: int, seed: int, writer: ResultsWriter) -> None:
    resource, principal = pick_trial_subject(scale, seed)
    # TWO connection sets on purpose. The pollers run on their own threads
    # while the revoke runs on this thread; a psycopg3 connection must never
    # be used by two threads at once (PROGRESS.md decision 8), so the revoke
    # (and the restore afterwards) get their own connections, and the
    # pollers get theirs. Within the poller set, each layer is a different
    # client object (pg / mongo / qdrant), so the three threads never share.
    poll_conns = scenario_a.open_connections()
    revoke_conns = scenario_a.open_connections()
    faults = FaultInjector(REGIME["fault_p"], fault_seed(scale, seed)) if REGIME["name"] == "faulty" else None
    worker = AsyncVectorWorker(REGIME["async_interval_s"]) if REGIME["name"] == "async" else None
    phase_s = None
    try:
        before = None
        if worker is not None:
            # Random phase w.r.t. the worker's flush cycle, same reasoning as
            # B1/B5: a revoke arrives at a uniformly random point in the
            # cycle. Started from inside the poller's before_revoke hook so
            # the phase is measured against the revoke and not against the
            # pollers' warm-up -- doing it before measure() under-measured
            # the vector window by the warm-up time on the first VM run
            # (seed 1 expected 1.15 s, measured 0.41 s).
            phase_s = random.Random(seed * 19_000_003 + scale).uniform(0.0, REGIME["async_interval_s"])

            def before():
                worker.start()
                time.sleep(phase_s)
        check_fns = scenario_a.make_check_fns(resource.resource_id, principal, poll_conns)
        t_issued, _, revoke_latency_s, results = measure(
            check_fns, lambda: scenario_a.revoke(resource.resource_id, principal, revoke_conns,
                                                 faults=faults, async_vector=worker),
            before_revoke_fn=before)
        extra = regime_extra()
        if faults is not None:
            extra["injected_failed_layers"] = faults.failed_layers()
        if worker is not None:
            extra["phase_s"] = phase_s
        write_layer_results(writer, "a", scale, seed, resource.resource_id, principal,
                            t_issued, revoke_latency_s, results, extra_common=extra)
    finally:
        if worker is not None:
            worker.stop()          # un-flushed revokes are dropped; ACL is restored below anyway
        try:
            restore_acl_ac(resource.resource_id, principal, revoke_conns)
        finally:
            scenario_a.close_connections(poll_conns)
            scenario_a.close_connections(revoke_conns)


# ---------------------------------------------------------------------------
# Scenario C
# ---------------------------------------------------------------------------

def run_trial_c(scale: int, seed: int, bridge_url: str, writer: ResultsWriter) -> None:
    resource, principal = pick_trial_subject(scale, seed)
    # C has no "async" regime (the bridge is what replaces the async
    # pipeline); under "async" it runs healthy and is tagged as such.
    c_regime = "faulty" if REGIME["name"] == "faulty" else "healthy"
    c_p = REGIME["fault_p"] if c_regime == "faulty" else 0.0
    # Configure the bridge BEFORE opening connections: it raises if the
    # bridge is unreachable or did not apply the regime, and nothing should
    # be left open in that case.
    scenario_c.set_fault_config(bridge_url, c_p, fault_seed(scale, seed))
    scenario_c.set_anchor_config(bridge_url, ANCHOR_ASYNC["enabled"])
    conns = scenario_c.open_connections()
    try:
        check_fns = scenario_c.make_check_fns(resource.resource_id, principal, conns)
        # scenario_c.revoke returns (t_issued, body); the poller's own t_issued
        # (taken immediately before calling it) is the one recorded.
        t_issued, (_, bridge_response), revoke_latency_s, results = measure(
            check_fns, lambda: scenario_c.revoke(resource.resource_id, principal, bridge_url))
        bridge_status = scenario_c.bridge_reported_status(resource.resource_id, principal, bridge_url)
        # Read immediately after /status: in async mode a "not contained"
        # verdict may simply mean the anchoring has not committed yet.
        anchor_st = scenario_c.anchor_status(bridge_url, (bridge_response or {}).get("eventId"))
        fault_stats = scenario_c.get_fault_config(bridge_url)   # how many attempts the bridge made / were injected
        anchor_cfg = scenario_c.get_anchor_config(bridge_url)   # includes whether Fabric is enabled at all
        mode = anchor_cfg.get("auditMode")
        if mode not in (None, "ledger") and not _LEDGER_OFF_ANNOUNCED["done"]:
            _LEDGER_OFF_ANNOUNCED["done"] = True
            what = ("no audit trail at all: retry and concurrent propagation only"
                    if mode == "none" else
                    "an append-only PostgreSQL table instead of the ledger")
            print("  [c] " + "!" * 62)
            print(f"  [c] AUDIT MODE = {mode.upper()}: the bridge is recording {what}.")
            print("  [c] This is an ablation. If you did not intend it, stop now and restart the")
            print("  [c] bridge without the override -- every record in this run is labelled")
            print(f"  [c] audit_mode={mode} and must not be reported as Scenario C.")
            print("  [c] " + "!" * 62)
        write_layer_results(writer, "c", scale, seed, resource.resource_id, principal,
                            t_issued, revoke_latency_s, results,
                            extra_common={"bridge_response": bridge_response, "bridge_status": bridge_status,
                                          "regime": c_regime, "fault_p": c_p, "async_interval_s": 0.0,
                                          "bridge_fault_stats": fault_stats,
                                          "fabric_batch_timeout": fabric_config().get("batch_timeout"),
                                          "anchor_mode": "async" if ANCHOR_ASYNC["enabled"] else "sync",
                                          # False => the ledger-free ablation: retry and concurrent
                                          # propagation without any anchoring at all.
                                          "fabric_enabled": anchor_cfg.get("fabricEnabled"),
                                          # ledger | log | none -- which backend recorded the
                                          # audit trail, for the Section 6.3.3 comparison.
                                          "audit_mode": anchor_cfg.get("auditMode"),
                                          "event_anchor_status": anchor_st})
    finally:
        try:
            restore_acl_ac(resource.resource_id, principal, conns)
        finally:
            scenario_c.close_connections(conns)
            try:
                scenario_c.set_fault_config(bridge_url, 0.0, 0)   # never leave the bridge faulty for the next run
                scenario_c.set_anchor_config(bridge_url, False)   # ...nor in async anchoring mode
            except Exception as e:
                print(f"  [c] WARNING: could not reset bridge config: {e}")


# ---------------------------------------------------------------------------
# Scenario B1-B5 (single layer: the poller dict uses the key "_" -> layer None)
# ---------------------------------------------------------------------------

def run_trial_b1(scale: int, seed: int, writer: ResultsWriter, refresh_interval_s: float = 5.0) -> None:
    resource, principal = pick_trial_subject(scale, seed)
    refresher = scenario_b.MaterializedViewRefresher(interval_s=refresh_interval_s)
    conn = db.connect_pg_primary()   # used only by the poller thread
    try:
        # Known starting state: the view must reflect the (granted) table
        # before the revoke, or the window measured is whatever the previous
        # trial left behind, not this revoke's.
        refresher.refresh_once()
        refresher.start()
        # Random phase: without this the revoke always lands at phase 0 of
        # the refresh cycle (refresh_once() just ran) and every trial
        # measures exactly one full interval. A real revoke arrives at a
        # uniformly random point in the cycle, so the expected window is
        # U(0, interval). Seeded per (scale, seed) for reproducibility.
        phase_s = random.Random(seed * 11_000_003 + scale).uniform(0.0, refresh_interval_s)
        time.sleep(phase_s)
        t_issued, _, revoke_latency_s, results = measure(
            {"_": lambda: scenario_b.is_retrievable_b1(conn, resource.resource_id, principal)},
            lambda: scenario_b.revoke(resource.resource_id, principal))
        write_layer_results(writer, "b1", scale, seed, resource.resource_id, principal,
                            t_issued, revoke_latency_s, results,
                            extra_common={"refresh_interval_s": refresh_interval_s, "phase_s": phase_s})
    finally:
        refresher.stop()   # first, so our own refresh below cannot race the thread's
        try:
            scenario_b.grant(resource.resource_id, principal)
            refresher.refresh_once()
        except Exception as e:
            print(f"  [restore] b1 grant-back/refresh failed: {e}")
        conn.close()


def run_trial_b2(scale: int, seed: int, writer: ResultsWriter) -> None:
    resource, principal = pick_trial_subject(scale, seed)
    replica_conn = db.connect_pg_replica()   # used only by the poller thread
    try:
        t_issued, _, revoke_latency_s, results = measure(
            {"_": lambda: scenario_b.is_retrievable_b2(replica_conn, resource.resource_id, principal)},
            lambda: scenario_b.revoke(resource.resource_id, principal))
        write_layer_results(writer, "b2", scale, seed, resource.resource_id, principal,
                            t_issued, revoke_latency_s, results)
    finally:
        try:
            scenario_b.grant(resource.resource_id, principal)
        except Exception as e:
            print(f"  [restore] b2 grant-back failed: {e}")
        replica_conn.close()


def run_trial_b3(scale: int, seed: int, writer: ResultsWriter) -> None:
    resource, principal = pick_trial_subject(scale, seed)
    # Use the resource's OWN already-seeded embedding as the query vector,
    # fetched directly from Postgres, rather than regenerating one via the
    # sentence-transformer model -- loading that model on every b3 trial
    # (once per scale x seed) adds real, avoidable time against the 24-hour
    # budget, and searching near the tested resource's own embedding is also
    # the more natural test: does a self-similarity search still surface it
    # after revoke, and does the ACL filter apply correctly either way.
    # admin_user: this read is harness setup, not a retrievability check --
    # through app_user the RLS policy (no principal set) returns no row and
    # the trial would be silently skipped.
    fetch_conn = db.connect_pg_admin()
    try:
        with fetch_conn.cursor() as cur:
            # ::text cast makes the wire format explicit ([v1,v2,...]) and
            # independent of whether a pgvector client-side type adapter
            # happens to be registered on this connection -- relying on
            # default type inference for an unregistered custom type is a
            # guess; the cast removes the guess entirely.
            cur.execute("SELECT embedding::text FROM resources WHERE resource_id = %s", (resource.resource_id,))
            row = cur.fetchone()
        fetch_conn.commit()
    finally:
        fetch_conn.close()
    if row is None:
        # Raise, don't return: the grid loop then records an error entry for
        # this trial instead of leaving a silent gap in the results.
        raise RuntimeError(f"[b3] resource {resource.resource_id} not found in resources table -- was seed.py --scenario b run for this scale/seed?")
    embedding = [float(x) for x in row[0].strip("[]").split(",")]

    for relaxed in (False, True):
        conn = db.connect_pg_primary()   # used only by the poller thread
        try:
            t_issued, _, revoke_latency_s, results = measure(
                {"_": lambda: scenario_b.is_retrievable_b3(conn, resource.resource_id, principal, embedding, relaxed)},
                lambda: scenario_b.revoke(resource.resource_id, principal))
            write_layer_results(writer, "b3", scale, seed, resource.resource_id, principal,
                                t_issued, revoke_latency_s, results,
                                extra_common={"relaxed_order": relaxed})
        finally:
            try:
                scenario_b.grant(resource.resource_id, principal)
            except Exception as e:
                print(f"  [restore] b3 grant-back failed: {e}")
            conn.close()


def run_trial_b4(scale: int, seed: int, writer: ResultsWriter) -> None:
    resource, principal = pick_trial_subject(scale, seed)
    snapshot = scenario_b.OpenSnapshot(principal)   # its connection is used only by the poller thread from here on
    try:
        t_issued, _, revoke_latency_s, results = measure(
            {"_": lambda: snapshot.is_retrievable(resource.resource_id)},
            lambda: scenario_b.revoke(resource.resource_id, principal))
        write_layer_results(writer, "b4", scale, seed, resource.resource_id, principal,
                            t_issued, revoke_latency_s, results)
    finally:
        snapshot.close()
        try:
            scenario_b.grant(resource.resource_id, principal)
        except Exception as e:
            print(f"  [restore] b4 grant-back failed: {e}")


def run_trial_b5(scale: int, seed: int, writer: ResultsWriter, poll_interval_s: float = 3.0) -> None:
    resource, principal = pick_trial_subject(scale, seed)
    consumer = scenario_b.LogicalConsumer(poll_interval_s=poll_interval_s)
    try:
        consumer.start()
        # Same random-phase reasoning as B1: the consumer was just primed,
        # so an immediate revoke always sees one full poll interval.
        phase_s = random.Random(seed * 13_000_003 + scale).uniform(0.0, poll_interval_s)
        time.sleep(phase_s)
        t_issued, _, revoke_latency_s, results = measure(
            {"_": lambda: consumer.is_retrievable(resource.resource_id, principal)},
            lambda: scenario_b.revoke(resource.resource_id, principal))
        write_layer_results(writer, "b5", scale, seed, resource.resource_id, principal,
                            t_issued, revoke_latency_s, results,
                            extra_common={"consumer_poll_interval_s": poll_interval_s, "phase_s": phase_s})
    finally:
        consumer.stop()
        try:
            scenario_b.grant(resource.resource_id, principal)
        except Exception as e:
            print(f"  [restore] b5 grant-back failed: {e}")


def run_trial_b6(scale: int, seed: int, writer: ResultsWriter, repeats: int = B6_REPEAT_COUNT) -> None:
    rng = random.Random(seed * 7_000_003 + scale)
    resources = make_resources(scale, seed=seed, acl_size=3, embed=False)
    leaks = 0
    for i in range(repeats):
        resource = rng.choice(resources)
        # The VICTIM is a principal that legitimately has access. Connection A
        # authenticates as the victim and leaves app.principal_id set at
        # session scope; connection B -- a different client that never set
        # any principal -- then queries through the same pooled backend. B6
        # "leaks" iff B can read the resource: it inherited the victim's
        # authorisation from a stale session variable. No revoke is issued
        # here: an earlier version revoked the victim first, which made the
        # inherited identity unauthorised and the leak impossible by
        # construction (0/50 on the first VM run).
        victim = rng.choice(resource.acl)
        leaked = scenario_b.leak_via_pgbouncer_session(victim, resource.resource_id)
        leaks += int(leaked)
        writer.write(TrialResult(
            scenario="b6", scale=scale, seed=seed, resource_id=resource.resource_id,
            principal_id=victim, layer=None, t_issued=time.monotonic(),
            leak_window_s=None, confirmed_contained=(not leaked), checks_performed=1,
            extra={"repeat_index": i, "leaked": leaked, "victim": victim},
        ))
    print(f"  [b6] scale={scale} seed={seed}: {leaks}/{repeats} calls leaked")


# ---------------------------------------------------------------------------
# Grid orchestration
# ---------------------------------------------------------------------------

SCENARIO_RUNNERS = {
    "a": lambda scale, seed, w, **kw: run_trial_a(scale, seed, w),
    "b1": lambda scale, seed, w, **kw: run_trial_b1(scale, seed, w),
    "b2": lambda scale, seed, w, **kw: run_trial_b2(scale, seed, w),
    "b3": lambda scale, seed, w, **kw: run_trial_b3(scale, seed, w),
    "b4": lambda scale, seed, w, **kw: run_trial_b4(scale, seed, w),
    "b5": lambda scale, seed, w, **kw: run_trial_b5(scale, seed, w),
    "b6": lambda scale, seed, w, **kw: run_trial_b6(scale, seed, w),
    "c": lambda scale, seed, w, bridge_url="http://localhost:8080", **kw: run_trial_c(scale, seed, bridge_url, w),
}


def main():
    global DEFAULT_POLL_INTERVAL_S
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", required=True, help="comma-separated: a,b1,b2,b3,b4,b5,b6,c")
    ap.add_argument("--scales", required=True, help="comma-separated resource counts")
    ap.add_argument("--seeds", required=True, help="comma-separated seed integers")
    ap.add_argument("--bridge-url", default="http://localhost:8080")
    ap.add_argument("--out", default=None, help="results JSONL path (default: results/run_<timestamp>.jsonl)")
    ap.add_argument("--append", action="store_true",
                    help="allow appending to an existing --out file (default: refuse, so that results "
                         "from two different builds cannot silently end up in one file)")
    ap.add_argument("--regime", choices=["healthy", "faulty", "async"], default="healthy",
                    help="Scenario A/C regime (decision 11): healthy (default), faulty (per-attempt store "
                         "failure with --fault-p, A: no retry, C: bridge retries), async (A's vector layer "
                         "written by a batch worker every --async-interval-s; C unaffected)")
    ap.add_argument("--poll-interval-s", type=float, default=DEFAULT_POLL_INTERVAL_S,
                    help="ground-truth poll cadence; sets the measurement resolution AND the load the "
                         "poller puts on the stores (a 30 s timeout at 10 ms is 3000 calls per layer)")
    ap.add_argument("--anchor-async", action="store_true",
                    help="Scenario C: reply as soon as the stores are written and anchor the propagation "
                         "records in the background. Faster, but a 200 no longer means the propagation is "
                         "on the ledger. The revoke event itself is still anchored fail-closed (decision 5).")
    ap.add_argument("--fault-p", type=float, default=0.10, help="per-attempt store-write failure probability (regime faulty)")
    ap.add_argument("--async-interval-s", type=float, default=2.0, help="vector batch-worker flush interval (regime async)")
    args = ap.parse_args()

    if args.poll_interval_s <= 0:
        print("--poll-interval-s must be > 0"); sys.exit(1)
    DEFAULT_POLL_INTERVAL_S = args.poll_interval_s
    ANCHOR_ASYNC["enabled"] = args.anchor_async
    if args.anchor_async:
        print("Scenario C: propagation anchoring is ASYNC (a 200 does not mean it is on the ledger)")
    REGIME["name"] = args.regime
    REGIME["fault_p"] = args.fault_p if args.regime == "faulty" else 0.0
    REGIME["async_interval_s"] = args.async_interval_s if args.regime == "async" else 0.0
    if args.regime == "faulty" and not 0.0 < args.fault_p <= 1.0:
        print("--fault-p must be in (0, 1] for regime faulty"); sys.exit(1)
    if args.regime == "async" and args.async_interval_s <= 0:
        print("--async-interval-s must be > 0 for regime async"); sys.exit(1)
    if args.regime != "healthy":
        print(f"Regime: {REGIME}")
        non_ac = [x for x in args.scenarios.split(",") if x not in ("a", "c")]
        if non_ac:
            print(f"  NOTE: --regime only affects scenarios a and c; {non_ac} run unchanged and their "
                  f"records carry no regime tag.")
    if args.regime == "async" and "c" in args.scenarios.split(","):
        print("  NOTE: Scenario C has no async regime (the bridge is what replaces an async pipeline); "
              "its trials run healthy and are labelled 'c'.")

    scenarios = args.scenarios.split(",")
    scales = [int(x) for x in args.scales.split(",")]
    seeds = [int(x) for x in args.seeds.split(",")]

    unknown = set(scenarios) - set(SCENARIO_RUNNERS)
    if unknown:
        print(f"Unknown scenario(s): {unknown}. Valid: {sorted(SCENARIO_RUNNERS)}")
        sys.exit(1)

    out_path = args.out or f"results/run_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    # ResultsWriter opens in append mode on purpose (a 20-hour run must not
    # lose everything if it dies), but that makes reusing a filename silently
    # merge two runs. That happened on the VM: a Scenario C file written
    # before and after a chaincode change analysed as one 6-trial group whose
    # medians belonged to neither build. Refuse by default.
    if Path(out_path).exists() and not args.append:
        n_existing = sum(1 for _ in open(out_path))
        print(f"{out_path} already exists ({n_existing} records). Refusing to append: results from two "
              f"runs would be merged and analysed as one group.\n"
              f"  Use a new filename, or pass --append if merging is what you want.")
        sys.exit(1)
    total = len(scenarios) * len(scales) * len(seeds)
    done = 0
    t_start = time.monotonic()
    t_prev_trial = t_start

    with ResultsWriter(out_path) as writer:
        for scenario in scenarios:
            for scale in scales:
                for seed in seeds:
                    done += 1
                    now = time.monotonic()
                    # A trial can be silent for 30 s (a poll timeout) or
                    # minutes (a Fabric commit plus retries). Print the wall
                    # clock, what the previous trial cost, and a projected
                    # finish, so a long trial is visibly a long trial and not
                    # a hung machine.
                    eta = ""
                    if done > 1:
                        per = (now - t_start) / (done - 1)
                        eta = f", prev {now - t_prev_trial:.0f}s, ~{per * (total - done + 1) / 60:.1f} min left"
                    t_prev_trial = now
                    print(f"[{done}/{total}] {time.strftime('%H:%M:%S')} scenario={scenario} "
                          f"scale={scale} seed={seed} (elapsed {now - t_start:.0f}s{eta})", flush=True)
                    try:
                        SCENARIO_RUNNERS[scenario](scale, seed, writer, bridge_url=args.bridge_url)
                    except Exception as e:
                        print(f"  TRIAL FAILED: {e}")
                        writer.write(TrialResult(
                            scenario=scenario, scale=scale, seed=seed, resource_id="", principal_id="",
                            layer=None, t_issued=time.monotonic(), leak_window_s=None,
                            confirmed_contained=None, checks_performed=None,
                            extra={"error": str(e)},
                        ))

    print(f"Done in {(time.monotonic() - t_start) / 60:.1f} min. Results written to {out_path}")


if __name__ == "__main__":
    main()
