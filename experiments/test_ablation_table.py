#!/usr/bin/env python3
"""Tests for experiments/ablation_table.py, in two parts.

Part 1 -- reproduction. The script must reproduce the first submission's
Table VII from the archived result files, or every number it prints for the
new campaign is suspect. Checked: 13 of 19, 11 of 19, 1 of 20, 1 of 20,
1 of 20 leaking without bound; Wilson intervals; the retry-versus-concurrency
Newcombe interval 0.53 [+0.24, +0.72] quoted in Section VI-C3.

Part 2 -- synthetic files with known answers: the exclusion rules, the
de-duplication of A across passes, the paired block (discordant counts,
exact McNemar), the self-report cells and attribution, and the refusal to
produce a paired block when any configuration ran under the stream schedule.

Run from the repository root:  python3 experiments/test_ablation_table.py
"""
import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experiments import ablation_table as at  # noqa: E402

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  -- {detail}"))


def close(a, b, tol=0.005):
    return a is not None and b is not None and abs(a - b) <= tol


# --- Part 1: reproduction ------------------------------------------------------
R = ROOT / "results"
if all((R / f"faulty20_{m}.jsonl").exists() for m in ("ledger", "none", "none-noretry", "log")):
    rep = at.build_report([("A", [R / "faulty20_ledger.jsonl"]),
                           ("C no retry", [R / "faulty20_none-noretry.jsonl"]),
                           ("C retry", [R / "faulty20_none.jsonl"]),
                           ("C log", [R / "faulty20_log.jsonl"]),
                           ("C ledger", [R / "faulty20_ledger.jsonl"])])
    b = rep["by_p"]["0.3"]["configs"]
    check("Table VII: A 13 of 19", (b["A"]["leaked_without_bound"], b["A"]["n"]) == (13, 19))
    check("Table VII: +conc 11 of 19", (b["C no retry"]["leaked_without_bound"], b["C no retry"]["n"]) == (11, 19))
    for l in ("C retry", "C log", "C ledger"):
        check(f"Table VII: {l} 1 of 20", (b[l]["leaked_without_bound"], b[l]["n"]) == (1, 20))
    check("Table VII: A Wilson [0.46, 0.85]", close(b["A"]["wilson_ci95"][0], 0.46) and close(b["A"]["wilson_ci95"][1], 0.85))
    check("Table VII: windows 0.261 / 0.101 / 0.100 / 0.123 / 2.623 s",
          close(b["A"]["drift_window_median_s"], 0.261) and close(b["C no retry"]["drift_window_median_s"], 0.101)
          and close(b["C retry"]["drift_window_median_s"], 0.100) and close(b["C log"]["drift_window_median_s"], 0.123)
          and close(b["C ledger"]["drift_window_median_s"], 2.623),
          [b[l]["drift_window_median_s"] for l in b])
    d = rep["by_p"]["0.3"]["diff_vs_reference"]
    check("no paired block on stream-schedule files", "paired" not in rep["by_p"]["0.3"])
    dd, lo, hi = at.newcombe_diff(11, 19, 1, 20)
    check("Section VI-C3: retry vs concurrency 0.53 [+0.24, +0.72]", close(dd, 0.53) and close(lo, 0.24) and close(hi, 0.72),
          (dd, lo, hi))
    check("self-report available for log and ledger, attributed 1 of 1",
          b["C ledger"]["self_report"]["attributed"] == 1 and b["C ledger"]["self_report"]["attributable"] == 1
          and b["C ledger"]["self_report"]["false_containment"] == 0)
else:
    print("  (archived result files not present; reproduction part skipped)")


# --- Part 2: synthetic ------------------------------------------------------------
def rec(scenario, seed, layer, window, contained, extra=None, t_issued=None, scale=10000):
    e = {"regime": "faulty", "fault_p": 0.3, "fault_schedule": "paired", "revoke_latency_s": 0.1,
         "retrievable_before_revoke": True, "poller_error": None}
    e.update(extra or {})
    return {"scenario": scenario, "scale": scale, "seed": seed, "resource_id": f"r{seed}", "principal_id": "p",
            "layer": layer, "t_issued": t_issued if t_issued is not None else float(seed),
            "leak_window_s": window, "confirmed_contained": contained, "checks_performed": 10, "extra": e}


def trial(scenario, seed, leaked=(), extra=None, t_issued=None, sched="paired"):
    out = []
    for l in ("relational", "nosql", "vector"):
        ex = dict(extra or {})
        ex["fault_schedule"] = sched
        out.append(rec(scenario, seed, l, 30.0 if l in leaked else 0.05, l not in leaked, ex, t_issued))
    return out


def write(rows):
    p = Path(tempfile.mkdtemp()) / "r.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


# A leaks on seeds 1,2,3,4 (of 6); C leaks on seed 4 only; O never.
rows = []
for s in range(1, 7):
    rows += trial("a", s, leaked=("nosql",) if s <= 4 else ())
    rows += trial("o", s, extra={"outbox_status": {"contained": True, "missingLayers": []}})
    st = {"contained": s != 4, "missingLayers": ["vector"] if s == 4 else []}
    rows += trial("c", s, leaked=("vector",) if s == 4 else (), extra={"bridge_status": st, "bridge_response": {"timing": {"totalMs": 120}}})
# exclusions: an invalid-setup A trial (seed 7), an incomplete O trial (seed 8), a poller-error C trial (seed 9)
rows += trial("a", 7, extra={"retrievable_before_revoke": False})
rows += trial("o", 8)[:2]
rows += trial("c", 9, extra={"poller_error": "[clock anomaly: window 400s exceeds timeout 30s]"})
# a duplicate copy of A's seed 1 (as a second pass would carry) with identical outcome
rows += trial("a", 1, leaked=("nosql",), t_issued=100.0)
f1 = write(rows)
rep = at.build_report([("A", [f1]), ("O", [f1]), ("C", [f1])])
blk = rep["by_p"]["0.3"]
c = blk["configs"]
check("A: 4 of 6 after excluding invalid setup and de-duplicating seed 1", (c["A"]["leaked_without_bound"], c["A"]["n"]) == (4, 6), c["A"])
check("O: 0 of 6 after excluding the incomplete trial", (c["O"]["leaked_without_bound"], c["O"]["n"]) == (0, 6), c["O"])
check("C: 1 of 6 after excluding the poller-error trial", (c["C"]["leaked_without_bound"], c["C"]["n"]) == (1, 6), c["C"])
check("exclusions counted by reason",
      blk["excluded"]["A"].get("invalid_setup") == 1 and blk["excluded"]["O"].get("incomplete") == 1
      and blk["excluded"]["C"].get("poller_error") == 1, blk["excluded"])
check("C self-report: true non-containment attributed to the right layer",
      c["C"]["self_report"]["attributed"] == 1 and c["C"]["self_report"]["false_containment"] == 0, c["C"]["self_report"])
check("C bridge-reported total carried through", close(c["C"]["bridge_total_median_s"], 0.12))
pb = blk["paired"]["C"]
check("paired A vs C: A-only 3 (seeds 1,2,3), C-only 0, both 1 (seed 4), neither 2",
      (pb["ref_only_leaked"], pb["cmp_only_leaked"], pb["both_leaked"], pb["neither_leaked"]) == (3, 0, 1, 2), pb)
check("paired A vs C: seeds listed", pb["seeds_ref_only"] == [1, 2, 3] and pb["seeds_cmp_only"] == [])
check("exact McNemar for 3 vs 0 discordant = 0.25", close(pb["mcnemar_exact_p"], 0.25, 1e-9), pb["mcnemar_exact_p"])
pbo = blk["paired"]["O"]
check("paired A vs O: 4 vs 0 discordant, p = 0.125", (pbo["ref_only_leaked"], pbo["cmp_only_leaked"]) == (4, 0)
      and close(pbo["mcnemar_exact_p"], 0.125, 1e-9), pbo)
check("consecutive rung O -> C present", "O → C" in blk["paired_consecutive"])
check("exact McNemar: no discordant pairs gives 1.0; 5 vs 5 gives 1.0; 6 vs 0 gives 2/64",
      at.exact_mcnemar(0, 0) == 1.0 and at.exact_mcnemar(5, 5) == 1.0 and close(at.exact_mcnemar(6, 0), 2 / 64, 1e-12))
check("no warnings on consistent duplicate A copies", not any("WARNING" in x for x in rep["checks"]), rep["checks"])

# a second pass in which A's seed 2 has a DIFFERENT outcome under the paired schedule -> warning
rows2 = trial("a", 2, leaked=(), t_issued=200.0)
f2 = write(rows2)
rep2 = at.build_report([("A", [f1, f2]), ("C", [f1])])
check("inconsistent A copies under the paired schedule are reported",
      any("different leaked layers" in x for x in rep2["checks"]), rep2["checks"])

# a seed excluded in one file but valid in another is recovered, not counted as excluded;
# a seed excluded in every file is counted once
rows4 = []
for s in range(1, 4):
    rows4 += trial("a", s)
rows4 += trial("a", 4, extra={"poller_error": "[poller still alive]"})
rows4 += trial("a", 5, extra={"poller_error": "[poller still alive]"})
rows5 = []
for s in range(1, 5):
    rows5 += trial("a", s, t_issued=50.0 + s)          # seed 4 valid here
rows5 += trial("a", 5, extra={"poller_error": "[x]"}, t_issued=99.0)
rep4 = at.build_report([("A", [write(rows4), write(rows5)])])
b4 = rep4["by_p"]["0.3"]
check("multi-file A: seed 4 recovered from the second file, seed 5 excluded once",
      b4["configs"]["A"]["n"] == 4 and b4["excluded"]["A"] == {"poller_error": 1}
      and b4["excluded_seeds"]["A"] == [(5, "poller_error")], (b4["configs"]["A"]["n"], b4["excluded"]))
check("leaked_seeds and per-trial list exported",
      c["A"]["leaked_seeds"] == [1, 2, 3, 4] and len(c["A"]["trials"]) == 6 and c["A"]["trials"][0]["seed"] == 1)

# stream schedule anywhere -> no paired block
rows3 = [r for r in rows if r["scenario"] != "c"]
for s in range(1, 7):
    rows3 += trial("c", s, sched="stream", extra={"bridge_status": {"contained": True, "missingLayers": []}})
rep3 = at.build_report([("A", [write(rows3)]), ("C", [write(rows3)])])
check("no paired block when C ran under the stream schedule", "paired" not in rep3["by_p"]["0.3"])

# Newcombe sanity: symmetric inputs give a symmetric interval around zero
d, lo, hi = at.newcombe_diff(5, 20, 5, 20)
check("Newcombe: equal proportions -> difference 0, symmetric interval", d == 0 and close(-lo, hi, 1e-12))
d, lo, hi = at.newcombe_diff(0, 20, 20, 20)
check("Newcombe: extreme proportions stay inside [-1, 1]", lo >= -1 and hi <= 1 and d == -1)

# markdown renders without error and names the reference
md = at.render_markdown(rep)
check("markdown renders with reference marked", "(reference)" in md and "Matched pairs" in md)

failed = [c for c in CHECKS if not c[1]]
print()
if failed:
    raise SystemExit(f"FAIL: {len(failed)} of {len(CHECKS)} checks failed")
print(f"ALL {len(CHECKS)} ABLATION-TABLE CHECKS PASSED")
