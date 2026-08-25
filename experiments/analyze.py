#!/usr/bin/env python3
"""Analysis of results/*.jsonl produced by run_experiment.py.

Usage:
  python analyze.py results/run_*.jsonl --out analysis/
  python analyze.py results/run_a.jsonl results/run_b.jsonl --out analysis/ --no-figures

Reads one or more JSONL files (multiple files are simply concatenated, so a
grid run split across several invocations -- e.g. A/C on one day, B1-B6 on
another -- analyses as one dataset), and writes:

  analysis/summary.md        human-readable report (paste-ready tables)
  analysis/summary.json      every number in the report, machine-readable
  analysis/tables/*.csv      one CSV per table
  analysis/figures/*.png     only if matplotlib is installed (optional dep)

What it computes, and the reasoning behind each choice:

RQ1/RQ3 (Scenario A vs C) -- Leak Window per layer and, as the headline
  per-trial number, the *drift window*: max over the three layers of that
  trial, since what matters to an attacker is the slowest layer, not the
  average one. Median / IQR / p95 are the primary statistics (windows are
  bounded below by 0 and heavy-tailed by construction, so the mean is
  reported but not leaned on). Bootstrap 95% CIs on the median, seeded.

  Timeouts: a record with confirmed_contained == False hit the 30 s poll
  timeout, so its leak_window_s is a LOWER bound, not a measurement. These
  are counted separately (n_timeout) and excluded from the window
  statistics rather than being averaged in as if they were 30 s.

  Left-censoring: ground-truth polling is sequential per layer (PROGRESS.md
  decision 8). A layer whose first poll already returns "not retrievable"
  gets leak_window_s == 0, but the true window could be anywhere in
  [0, poll_start_offset_s] -- it closed before the harness started looking.
  This script reports how many zeros are censored this way and the bound
  they are censored at, so the paper can state it rather than quietly
  claiming sub-millisecond containment for a layer that was simply polled
  third. Old result files without poll_start_offset_s fall back to
  "unknown bound" and are still analysed.

RQ2 (Scenario B1-B6) -- B1/B2/B4/B5 are timed windows like A/C (one
  per trial, no layer). B3 is split strict vs relaxed_order (two records per
  trial). B1 and B5 are also reported relative to their configured
  interval, since the expected window under a periodic consumer is roughly
  U(0, interval) and the interesting question is whether the measurement
  matches that, not the absolute number. B6 is an occurrence RATE (leaked
  / calls) with a Wilson 95% interval, never a window in seconds -- see
  scenario_b.leak_via_pgbouncer_session.

False-containment split (Scenario C only, PROGRESS.md decision 5) -- per
  trial, compares the bridge's own claim (extra.bridge_status.contained,
  from Fabric IsContained) against ground truth (all three layers
  confirmed contained). Four cells, reported as four separate numbers that
  must never be summed into one "accuracy":
    true containment        bridge: contained,     truth: contained
    FALSE CONTAINMENT       bridge: contained,     truth: NOT contained   <- the metric's main purpose
    false non-containment   bridge: not contained, truth: contained       <- decision-5 edge case
    true non-containment    bridge: not contained, truth: NOT contained
  False non-containment is further split into "anchor-of-propagation
  failed after a successful store write" (the decision-5 mechanism: some
  propagation entry has ok=true, anchored=false) versus "other" (e.g. the
  /status call itself errored), because only the first is the documented
  under-estimate direction.
  If revoke_latency_s is present, a second, time-resolved form is also
  computed: trials where the bridge returned fullyPropagated=true yet the
  ground-truth poller still observed the data retrievable AFTER the bridge
  had returned (leak_window_s > revoke_latency_s on any layer). This is the
  stricter notion -- the bridge said "done" before it actually was.

Overhead -- revoke_latency_s (A vs C, per scale), i.e. what the bridge's
  Fabric anchoring + coordinated propagation costs the caller on the
  revoke path, versus three uncoordinated direct writes. Only available
  from result files written after the field was added; older files
  produce "not recorded" rather than a fabricated zero.

Scalability -- drift window vs scale (1K/10K/100K) for A and C, and
  revoke latency vs scale, as tables and (optionally) line plots.

Trial-level errors (extra.error, written by run_experiment's per-trial
  catch) are listed verbatim, never silently dropped.
"""
import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except ImportError:  # optional dependency
    HAVE_MPL = False

LAYERS = ("relational", "nosql", "vector")


def ac_labels(records):
    """Scenario A/C group labels present in the data, e.g. ["a", "a@faulty",
    "c", "c@faulty"]. A/C records carry extra.regime (decision 11); the
    healthy regime keeps the bare label so older files read unchanged."""
    return sorted({r["scenario"] for r in records if r["scenario"][0] in ("a", "c") and r["scenario"][:2] != "b"})


def relabel_regimes(records):
    """Returns a copy of the records with A/C scenario labels suffixed by
    their non-healthy regime ("a@faulty", "a@async", "c@faulty")."""
    out = []
    for r in records:
        if r["scenario"] in ("a", "c"):
            regime = r["extra"].get("regime") or "healthy"
            if regime != "healthy":
                r = dict(r, scenario=f'{r["scenario"]}@{regime}')
        out.append(r)
    return out


def is_c(label):
    return label.startswith("c")


def pretty(label):
    base, _, regime = label.partition("@")
    return base.upper() + (f"[{regime}]" if regime else "")
TIMED_B = ("b1", "b2", "b3", "b4", "b5")
BOOTSTRAP_ITERS = 2000
BOOTSTRAP_SEED = 12345


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_results(paths):
    """Returns (records, errors, skipped_lines). A truncated final line (the
    one thing JSONL can lose on a crash) is skipped and counted, not fatal."""
    records, errors, skipped = [], [], 0
    for p in paths:
        with open(p) as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    print(f"  [load] {p}:{lineno}: unparseable line skipped", file=sys.stderr)
                    continue
                rec.setdefault("extra", {})
                rec["_source"] = str(p)
                if "error" in rec["extra"]:
                    errors.append(rec)
                else:
                    records.append(rec)
    return records, errors, skipped


def trial_key(rec):
    """One trial = one revoke. (scenario, scale, seed, resource, principal)
    alone is NOT unique: pick_trial_subject is deterministic per (scale,
    seed), so the same grid run twice and concatenated would collide. The
    revoke's t_issued (one monotonic timestamp shared by the three layer
    records of an A/C trial, distinct for every other revoke -- including
    b3's strict vs relaxed pair and each b6 repeat) disambiguates."""
    return (rec["scenario"], rec["scale"], rec["seed"], rec["resource_id"], rec["principal_id"], rec["t_issued"])


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def bootstrap_ci(values, stat=np.median, iters=BOOTSTRAP_ITERS, seed=BOOTSTRAP_SEED):
    arr = np.asarray(values, dtype=float)
    if arr.size < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(iters, arr.size))
    boots = stat(arr[idx], axis=1)
    return (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)))


def describe(values):
    """Summary dict for a list of window/latency values (seconds)."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return {"n": 0}
    lo, hi = bootstrap_ci(arr)
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "std": float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        "min": float(arr.min()),
        "p25": float(np.percentile(arr, 25)),
        "median": float(np.median(arr)),
        "p75": float(np.percentile(arr, 75)),
        "p95": float(np.percentile(arr, 95)),
        "max": float(arr.max()),
        "median_ci95": [lo, hi],
    }


def wilson_interval(k, n, z=1.96):
    """Wilson score interval for a binomial proportion -- behaves sensibly
    at k=0 or k=n, where the normal approximation gives a zero-width CI."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


# ---------------------------------------------------------------------------
# Window partitioning shared by A/C and timed-B
# ---------------------------------------------------------------------------

def partition_windows(recs):
    """Splits records into measured windows, timeouts, and censored zeros.
    Returns dict with lists; a record is in exactly one of the first two."""
    measured, timeouts, censored_zero = [], [], []
    for r in recs:
        if r["extra"].get("retrievable_before_revoke") is False or r["extra"].get("poller_error"):
            continue   # not evidence; counted separately by setup_problems()
        if r.get("confirmed_contained") is False:
            timeouts.append(r)
            continue
        if r.get("leak_window_s") is None:
            continue
        measured.append(r)
        if r["leak_window_s"] == 0.0:
            censored_zero.append(r)
    return {"measured": measured, "timeouts": timeouts, "censored_zero": censored_zero}


def censor_bound(rec):
    """Offset (s after revoke-issued) of the first check that could have seen
    the layer closed. New result files: first_check_after_issue_s (concurrent
    pre-revoke polling, ~one poll interval). Older files: poll_start_offset_s
    (post-revoke sequential polling, the whole revoke duration). None if
    neither was recorded."""
    e = rec["extra"]
    if e.get("first_check_after_issue_s") is not None:
        return e["first_check_after_issue_s"]
    return e.get("poll_start_offset_s")


def resolution_summary(recs):
    """Worst measurement resolution in a group: the largest observed gap
    between consecutive post-issue checks. On an idle host this is the poll
    interval; on a stalled VM it can be far larger, and a window is only
    known to within it."""
    gaps = [r["extra"]["max_gap_after_issue_s"] for r in recs
            if r["extra"].get("max_gap_after_issue_s") is not None]
    n_err = sum(r["extra"].get("check_errors") or 0 for r in recs)
    widths = [r["extra"]["leak_window_upper_s"] - r["leak_window_s"]
              for r in recs
              if r["extra"].get("leak_window_upper_s") is not None and r.get("leak_window_s") is not None]
    intervals = sorted({r["extra"]["poll_interval_s"] for r in recs if r["extra"].get("poll_interval_s")})
    if not gaps:
        return None
    return {"max_gap_s": max(gaps), "median_gap_s": float(np.median(gaps)), "n": len(gaps),
            "check_errors": n_err, "poll_interval_s": intervals,
            "median_bracket_width_s": float(np.median(widths)) if widths else None,
            "max_bracket_width_s": max(widths) if widths else None}


def setup_problems(recs):
    """Records whose measurement is not evidence: the layer was not
    retrievable before the revoke (nothing to revoke) or the poller died."""
    not_live = [r for r in recs if r["extra"].get("retrievable_before_revoke") is False]
    errored = [r for r in recs if r["extra"].get("poller_error")]
    return {"n_not_retrievable_before_revoke": len(not_live), "n_poller_error": len(errored)}


def censoring_summary(censored_zero, n_measured):
    bounds = [censor_bound(r) for r in censored_zero if censor_bound(r) is not None]
    return {
        "n_zero": len(censored_zero),
        "n_measured": n_measured,
        "frac_zero": (len(censored_zero) / n_measured) if n_measured else float("nan"),
        "bound_known_for": len(bounds),
        "max_censor_bound_s": max(bounds) if bounds else None,
        "median_censor_bound_s": float(np.median(bounds)) if bounds else None,
    }


# ---------------------------------------------------------------------------
# RQ1 / RQ3: Scenario A vs C
# ---------------------------------------------------------------------------

def analyse_ac(records):
    out = {}
    for scen in ac_labels(records):
        recs = [r for r in records if r["scenario"] == scen and r["layer"] in LAYERS]
        if not recs:
            continue
        scen_out = {"per_layer": {}, "drift": {}}
        scales = sorted({r["scale"] for r in recs})

        # Per layer x scale. For C, trials the bridge rejected fail-closed
        # are excluded here too (nothing was revoked; see drift below) and
        # counted, so per-layer and drift tables agree on what "n" means.
        rejected = []
        kept = recs
        if is_c(scen):
            rejected = [r for r in recs if bridge_outcome(r["extra"].get("bridge_response")) == "rejected"]
            kept = [r for r in recs if bridge_outcome(r["extra"].get("bridge_response")) != "rejected"]
        for scale in scales:
            for layer in LAYERS:
                sub = [r for r in kept if r["scale"] == scale and r["layer"] == layer]
                if not sub:
                    if is_c(scen) and any(r["scale"] == scale and r["layer"] == layer for r in rejected):
                        d = {"n": 0, "n_timeout": 0,
                             "n_revoke_rejected": sum(1 for r in rejected if r["scale"] == scale and r["layer"] == layer)}
                        scen_out["per_layer"][f"{scale}|{layer}"] = d
                    continue
                part = partition_windows(sub)
                d = describe([r["leak_window_s"] for r in part["measured"]])
                d["n_timeout"] = len(part["timeouts"])
                d.update(setup_problems(sub))
                d["resolution"] = resolution_summary(sub)
                if is_c(scen):
                    d["n_revoke_rejected"] = sum(1 for r in rejected if r["scale"] == scale and r["layer"] == layer)
                d["censoring"] = censoring_summary(part["censored_zero"], len(part["measured"]))
                scen_out["per_layer"][f"{scale}|{layer}"] = d

        # Drift window = max over layers, per trial. A trial with any layer
        # timed out is a timeout trial (its drift window is >= 30 s, unknown).
        by_trial = defaultdict(dict)
        for r in recs:
            by_trial[trial_key(r)][r["layer"]] = r
        for scale in scales:
            drift, n_timeout, n_incomplete, n_rejected, n_invalid = [], 0, 0, 0, 0
            for key, layers in by_trial.items():
                if key[1] != scale:
                    continue
                if set(layers) != set(LAYERS):
                    n_incomplete += 1  # a crash mid-trial left <3 layer records
                    continue
                if is_c(scen) and bridge_outcome(next(iter(layers.values()))["extra"].get("bridge_response")) == "rejected":
                    # fail-closed refusal (decision 5): nothing was revoked,
                    # so the poller necessarily times out -- but that is a
                    # refusal, not a slow containment. Kept out of both the
                    # window stats and the timeout count.
                    n_rejected += 1
                    continue
                if any(l["extra"].get("retrievable_before_revoke") is False or l["extra"].get("poller_error")
                       for l in layers.values()):
                    n_invalid += 1   # setup problem on some layer -> not evidence
                    continue
                if any(l["confirmed_contained"] is False for l in layers.values()):
                    n_timeout += 1
                    continue
                drift.append(max(l["leak_window_s"] for l in layers.values()))
            d = describe(drift)
            d["n_timeout"] = n_timeout
            d["n_incomplete_trials"] = n_incomplete
            d["n_invalid_setup_trials"] = n_invalid
            d["batch_timeout"] = sorted({r["extra"].get("fabric_batch_timeout")
                                         for r in recs if r["scale"] == scale
                                         and r["extra"].get("fabric_batch_timeout")})
            # Regime "faulty" bookkeeping: how many complete trials actually
            # had a failure injected (A: any layer skipped; C: the bridge
            # reported >= 1 injected attempt), so the paper can state the
            # realised rate next to the configured p.
            n_injected = 0
            for key, layers in by_trial.items():
                if key[1] != scale or set(layers) != set(LAYERS):
                    continue
                e = next(iter(layers.values()))["extra"]
                if e.get("injected_failed_layers") or (e.get("bridge_fault_stats") or {}).get("injected", 0) > 0:
                    n_injected += 1
            if "@faulty" in scen:
                d["n_trials_with_injected_fault"] = n_injected
                d["fault_p"] = sorted({next(iter(l.values()))["extra"].get("fault_p") for l in by_trial.values()
                                       if next(iter(l.values()))["extra"].get("fault_p") is not None})
            if is_c(scen):
                d["n_revoke_rejected"] = n_rejected
            scen_out["drift"][str(scale)] = d
        out[scen] = scen_out
    return out


# ---------------------------------------------------------------------------
# RQ2: Scenario B
# ---------------------------------------------------------------------------

def analyse_b(records):
    out = {}
    for scen in TIMED_B:
        recs = [r for r in records if r["scenario"] == scen]
        if not recs:
            continue
        if scen == "b3":
            groups = {
                "strict": [r for r in recs if not r["extra"].get("relaxed_order")],
                "relaxed_order": [r for r in recs if r["extra"].get("relaxed_order")],
            }
        else:
            groups = {"all": recs}
        scen_out = {}
        for gname, grecs in groups.items():
            if not grecs:
                continue
            part = partition_windows(grecs)
            d = describe([r["leak_window_s"] for r in part["measured"]])
            d["n_timeout"] = len(part["timeouts"])
            d.update(setup_problems(grecs))
            d["resolution"] = resolution_summary(grecs)
            d["censoring"] = censoring_summary(part["censored_zero"], len(part["measured"]))
            # B1 / B5: relate to configured interval
            interval_key = {"b1": "refresh_interval_s", "b5": "consumer_poll_interval_s"}.get(scen)
            if interval_key:
                intervals = sorted({r["extra"].get(interval_key) for r in grecs if r["extra"].get(interval_key) is not None})
                d["configured_interval_s"] = intervals
                if len(intervals) == 1 and d.get("n", 0) > 0:
                    iv = intervals[0]
                    d["median_over_interval"] = d["median"] / iv
                    d["expected_median_if_uniform"] = iv / 2
                    d["n_exceeding_interval"] = sum(1 for r in part["measured"] if r["leak_window_s"] > iv)
            scen_out[gname] = d
        out[scen] = scen_out

    # B6: occurrence rate
    b6 = [r for r in records if r["scenario"] == "b6"]
    if b6:
        per = {}
        for (scale, seed) in sorted({(r["scale"], r["seed"]) for r in b6}):
            sub = [r for r in b6 if r["scale"] == scale and r["seed"] == seed]
            k = sum(1 for r in sub if r["extra"].get("leaked") is True)
            n = len(sub)
            lo, hi = wilson_interval(k, n)
            per[f"{scale}|{seed}"] = {"leaked": k, "calls": n, "rate": k / n, "wilson_ci95": [lo, hi]}
        k = sum(1 for r in b6 if r["extra"].get("leaked") is True)
        n = len(b6)
        lo, hi = wilson_interval(k, n)
        out["b6"] = {"per_scale_seed": per,
                     "pooled": {"leaked": k, "calls": n, "rate": k / n, "wilson_ci95": [lo, hi]}}
    return out


# ---------------------------------------------------------------------------
# False-containment split (Scenario C, decision 5)
# ---------------------------------------------------------------------------

def bridge_outcome(resp):
    """'rejected' if the bridge refused the revoke before touching any store
    (fail-closed per decision 5: Fabric anchor failed -> 502, or the HTTP
    call itself failed -> scenario_c records {"error": ...}); 'partial' if
    it returned 207 (some layer still failed after the 3-attempt retry of
    decision 2); 'full' if fullyPropagated; 'unknown' otherwise."""
    if not isinstance(resp, dict):
        return "unknown"   # not recorded (e.g. an incomplete trial) -- NOT a refusal
    if "error" in resp:
        return "rejected"
    fp = resp.get("fullyPropagated")
    if fp is True:
        return "full"
    if fp is False:
        return "partial"
    return "unknown"


def analyse_false_containment(records):
    """One split per C label (c, c@faulty)."""
    out = {}
    for label in [l for l in ac_labels(records) if is_c(l)]:
        fc = _analyse_false_containment_one([r for r in records if r["scenario"] == label])
        if fc:
            out[label] = fc
    return out or None


def _analyse_false_containment_one(records):
    recs = [r for r in records if r["layer"] in LAYERS]
    if not recs:
        return None
    by_trial = defaultdict(dict)
    for r in recs:
        by_trial[trial_key(r)][r["layer"]] = r

    cells = {"true_containment": 0, "false_containment": 0,
             "false_non_containment_anchor_failed": 0, "false_non_containment_anchor_pending": 0,
             "false_non_containment_other": 0, "invalid_setup_trials": 0,
             "true_non_containment": 0, "revoke_rejected_fail_closed": 0,
             "bridge_status_unavailable": 0, "incomplete_trials": 0}
    n_partial_propagation = 0
    false_containment_trials, fnc_trials = [], []
    # time-resolved form
    time_resolved = {"available": False,
                     "n_fully_propagated_claims": 0, "n_post_claim_leaks": 0, "post_claim_leak_trials": [],
                     # Lower-bound caveat: a layer whose poll started only
                     # AFTER the bridge returned and whose first check was
                     # already "not retrievable" (window 0) could have leaked
                     # in [revoke_latency_s, poll_start_offset_s] unseen.
                     "n_claims_with_blind_interval": 0, "max_blind_interval_s": 0.0}

    for key, layers in by_trial.items():
        if set(layers) != set(LAYERS):
            cells["incomplete_trials"] += 1
            continue
        # A trial whose record was not retrievable BEFORE the revoke proves
        # nothing about the revoke: every layer is trivially "contained" and
        # the ledger, which anchored the event regardless, agrees -- so the
        # trial would score as a true containment on no evidence at all.
        # Window statistics already exclude these; this classification did
        # not, which let a mis-seeded run manufacture containment successes.
        if any(l["extra"].get("retrievable_before_revoke") is False for l in layers.values()):
            cells["invalid_setup_trials"] += 1
            continue
        # Bridge claim: the status is fetched after each layer's poll, so
        # the last layer's is the most settled view the bridge had by the
        # end of the trial. If that particular /status call errored, fall
        # back to the latest earlier one that did return a verdict rather
        # than discarding the trial.
        last = layers[LAYERS[-1]]
        status = None
        for layer in reversed(LAYERS):
            cand = layers[layer]["extra"].get("bridge_status")
            if isinstance(cand, dict) and "contained" in cand:
                status = cand
                break
        if status is None:
            status = last["extra"].get("bridge_status")
        resp = last["extra"].get("bridge_response") or {}
        truth_contained = all(l["confirmed_contained"] is True for l in layers.values())

        outcome = bridge_outcome(resp)
        if outcome == "rejected":
            # The bridge never claimed anything -- it refused (decision 5,
            # fail-closed). Counting this as "true non-containment" would
            # credit the bridge for a refusal; counting it as a containment
            # failure would blame it for one. It is its own number.
            cells["revoke_rejected_fail_closed"] += 1
            continue
        if outcome == "partial":
            n_partial_propagation += 1

        if not isinstance(status, dict) or "contained" not in status:
            cells["bridge_status_unavailable"] += 1
            bridge_contained = None
        else:
            bridge_contained = bool(status["contained"])

        if bridge_contained is True and truth_contained:
            cells["true_containment"] += 1
        elif bridge_contained is True and not truth_contained:
            cells["false_containment"] += 1
            false_containment_trials.append(_trial_desc(key, layers, status))
        elif bridge_contained is False and truth_contained:
            props = resp.get("propagation") or []
            anchor_failed = any(p.get("ok") is True and p.get("anchored") is False for p in props)
            # In async mode the ledger legitimately lags: the stores are
            # closed but the propagation records have not committed yet.
            # That is a different finding from an anchoring FAILURE and is
            # counted separately -- conflating them would make async mode
            # look unsafe when it is merely late.
            anchor_st = last["extra"].get("event_anchor_status") or {}
            anchor_pending = bool(anchor_st.get("pending"))
            if anchor_failed:
                cells["false_non_containment_anchor_failed"] += 1
            elif anchor_pending:
                cells["false_non_containment_anchor_pending"] += 1
            else:
                cells["false_non_containment_other"] += 1
            fnc_trials.append(_trial_desc(key, layers, status, anchor_failed=anchor_failed))
        elif bridge_contained is False and not truth_contained:
            cells["true_non_containment"] += 1

        # Time-resolved: bridge said fullyPropagated, yet data observed after it returned
        lat = last["extra"].get("revoke_latency_s")
        if lat is not None:
            time_resolved["available"] = True
            if resp.get("fullyPropagated") is True:
                time_resolved["n_fully_propagated_claims"] += 1
                leaking = [l["layer"] for l in layers.values()
                           if l["leak_window_s"] is not None and l["leak_window_s"] > lat]
                blind = [censor_bound(l) - lat for l in layers.values()
                         if l["leak_window_s"] == 0.0 and censor_bound(l) is not None
                         and censor_bound(l) > lat]
                if blind:
                    time_resolved["n_claims_with_blind_interval"] += 1
                    time_resolved["max_blind_interval_s"] = max(time_resolved["max_blind_interval_s"], max(blind))
                if leaking:
                    time_resolved["n_post_claim_leaks"] += 1
                    time_resolved["post_claim_leak_trials"].append(
                        {"trial": list(key), "revoke_latency_s": lat, "layers": leaking,
                         "windows": {l: layers[l]["leak_window_s"] for l in leaking}})

    # invalid_setup_trials and incomplete_trials are deliberately NOT part of
    # the denominator: they are excluded observations, not outcomes.
    n_classified = sum(cells[k] for k in ("true_containment", "false_containment",
                                          "false_non_containment_anchor_failed",
                                          "false_non_containment_anchor_pending",
                                          "false_non_containment_other", "true_non_containment"))
    rates = {}
    if n_classified:
        for k in ("false_containment", "false_non_containment_anchor_failed",
                  "false_non_containment_anchor_pending", "false_non_containment_other"):
            lo, hi = wilson_interval(cells[k], n_classified)
            rates[k] = {"rate": cells[k] / n_classified, "wilson_ci95": [lo, hi]}
    return {"cells": cells, "n_classified": n_classified, "rates": rates,
            "n_partial_propagation": n_partial_propagation,
            "false_containment_trials": false_containment_trials,
            "false_non_containment_trials": fnc_trials,
            "time_resolved": time_resolved}


def _trial_desc(key, layers, status, **kw):
    d = {"scenario": key[0], "scale": key[1], "seed": key[2], "resource_id": key[3], "principal_id": key[4],
         "layer_windows": {l: layers[l]["leak_window_s"] for l in LAYERS},
         "layer_confirmed": {l: layers[l]["confirmed_contained"] for l in LAYERS},
         "bridge_status": status}
    d.update(kw)
    return d


# ---------------------------------------------------------------------------
# Overhead (revoke latency) and scalability
# ---------------------------------------------------------------------------

def bridge_timing_summary(records):
    """Scenario C only: split the caller-observed revoke latency into the
    fail-closed event anchoring (decision 5 -- paid BEFORE any store is
    touched, so it is inside the Leak Window) and the propagation phase
    (store writes, which close the leak, plus the anchoring that follows
    them). Without this split a large total is misread as the cost of
    containment."""
    out = {}
    for label in [l for l in ac_labels(records) if is_c(l)]:
        seen, rows = set(), []
        for r in records:
            if r["scenario"] != label:
                continue
            k = trial_key(r)
            if k in seen:
                continue
            seen.add(k)
            t = ((r["extra"].get("bridge_response") or {}).get("timing")) or {}
            if not t:
                continue
            props = (r["extra"].get("bridge_response") or {}).get("propagation") or []
            rows.append({
                "anchor_event_s": t.get("anchorEventMs", 0) / 1000.0,
                "propagate_s": t.get("propagateMs", 0) / 1000.0,
                "total_s": t.get("totalMs", 0) / 1000.0,
                "max_store_s": max((p.get("storeMs", 0) for p in props), default=0) / 1000.0,
                "max_anchor_prop_s": max((p.get("anchorPropMs", 0) or 0 for p in props), default=0) / 1000.0,
                "anchor_prop_attempts": max((p.get("anchorPropAttempts", 0) or 0 for p in props), default=0),
            })
        if rows:
            out[label] = {k: describe([r[k] for r in rows]) for k in rows[0]}
            out[label]["n_trials"] = len(rows)
            bts = sorted({r["extra"].get("fabric_batch_timeout") for r in records
                          if r["scenario"] == label and r["extra"].get("fabric_batch_timeout")})
            out[label]["batch_timeout"] = bts
            out[label]["anchor_mode"] = sorted({r["extra"].get("anchor_mode") for r in records
                                                if r["scenario"] == label and r["extra"].get("anchor_mode")})
            # None when the field predates this flag; True/False once recorded.
            out[label]["audit_mode"] = sorted({r["extra"].get("audit_mode") for r in records
                                               if r["scenario"] == label and r["extra"].get("audit_mode")})
            # 3 by default; 1 in the ablation column that measures concurrent
            # propagation without the bounded retry. Without this the
            # no-retry run is indistinguishable from an ordinary audit=none
            # run in every table the pipeline produces.
            out[label]["retry_attempts"] = sorted(
                {r["extra"].get("retry_attempts") for r in records
                 if r["scenario"] == label and r["extra"].get("retry_attempts") is not None},
                key=str)
            out[label]["fabric_enabled"] = sorted(
                {r["extra"].get("fabric_enabled") for r in records
                 if r["scenario"] == label and r["extra"].get("fabric_enabled") is not None},
                key=str)
    return out or None


def analyse_overhead(records):
    out = {}
    for scen in tuple(ac_labels(records)) + TIMED_B:
        recs = [r for r in records if r["scenario"] == scen and r["extra"].get("revoke_latency_s") is not None]
        if not recs:
            continue
        # A/C write one record per layer with the same latency -> dedupe per trial
        seen, vals = set(), defaultdict(list)
        for r in recs:
            k = trial_key(r)
            if k in seen:
                continue
            seen.add(k)
            vals[r["scale"]].append(r["extra"]["revoke_latency_s"])
        out[scen] = {str(scale): describe(v) for scale, v in sorted(vals.items())}
    # C-over-A ratio of medians, per scale, PAIRED BY REGIME: healthy C is
    # compared with healthy A, faulty with faulty. Comparing across regimes
    # would attribute the injection's cost to the bridge.
    ratios = {}
    for a_label in [l for l in out if l.startswith("a")]:
        regime = a_label.partition("@")[2]
        c_label = f"c@{regime}" if regime else "c"
        if c_label not in out:
            continue
        r = {}
        for scale in out[a_label]:
            if scale in out[c_label] and out[a_label][scale].get("n") and out[c_label][scale].get("n"):
                ma, mc = out[a_label][scale]["median"], out[c_label][scale]["median"]
                r[scale] = {"median_a_s": ma, "median_c_s": mc,
                            "c_minus_a_s": mc - ma, "c_over_a": (mc / ma) if ma > 0 else None}
        if r:
            ratios[f"{c_label}_vs_{a_label}"] = r
    if ratios:
        out["c_vs_a"] = ratios
    n_without = sum(1 for r in records if r["scenario"] in tuple(ac_labels(records)) + TIMED_B
                    and r["extra"].get("revoke_latency_s") is None)
    out["_n_records_without_latency"] = n_without
    return out


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------

def _f(x, nd=4):
    if x is None:
        return "-"
    if isinstance(x, float):
        if math.isnan(x):
            return "-"
        return f"{x:.{nd}f}"
    return str(x)


def _stats_row(label, d):
    if not d or d.get("n", 0) == 0:
        return f"| {label} | 0 | - | - | - | - | - | - | {d.get('n_timeout', 0) if d else 0} |"
    ci = d["median_ci95"]
    return (f"| {label} | {d['n']} | {_f(d['median'])} | [{_f(ci[0])}, {_f(ci[1])}] | "
            f"{_f(d['p25'])}–{_f(d['p75'])} | {_f(d['p95'])} | {_f(d['max'])} | {_f(d['mean'])} | {d.get('n_timeout', 0)} |")


STATS_HEADER = ("| group | n | median (s) | median 95% CI | IQR | p95 | max | mean | timeouts |\n"
                "|---|---|---|---|---|---|---|---|---|")


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows([["" if isinstance(v, float) and math.isnan(v) else v for v in row] for row in rows])


def stats_csv_rows(label_cols, label_vals, d):
    if not d or d.get("n", 0) == 0:
        return list(label_vals) + [0] + [""] * 9 + [d.get("n_timeout", 0) if d else 0]
    return list(label_vals) + [d["n"], d["median"], d["median_ci95"][0], d["median_ci95"][1],
                               d["p25"], d["p75"], d["p95"], d["max"], d["mean"], d["std"], d.get("n_timeout", 0)]


STATS_CSV_COLS = ["n", "median_s", "median_ci_lo", "median_ci_hi", "p25", "p75", "p95", "max", "mean", "std", "n_timeout"]


def write_report(out_dir, summary, n_records, n_errors, n_skipped, errors):
    out_dir = Path(out_dir)
    tables = out_dir / "tables"
    md = []
    md.append("# Leak Window analysis\n")
    md.append(f"Records: {n_records} measured, {n_errors} trial-level errors, {n_skipped} unparseable lines skipped.\n")

    # ---- A vs C
    ac = summary["ac"]
    if ac:
        md.append("## RQ1 / RQ3 — Scenario A (uncoordinated polyglot) vs C (ledger-anchored bridge)\n")
        md.append("### Drift window per trial (max over the three layers)\n")
        md.append("Timeouts (poll limit hit on any layer, so the window is only a lower bound) are excluded from the statistics and counted separately.\n")
        bt_by_group = {f"{pretty(l)} @ {sc}": (d.get("batch_timeout") or [])
                       for l in ac if is_c(l) for sc, d in ac[l].get("drift", {}).items()}
        if any(bt_by_group.values()):
            md.append("Orderer BatchTimeout per Scenario C group: "
                      + ", ".join(f"{k}: {', '.join(v)}" for k, v in bt_by_group.items() if v) + "\n")
        if any(len(v) > 1 for v in bt_by_group.values()):
            md.append("**WARNING: a Scenario C group below mixes more than one BatchTimeout.** Its window "
                      "statistics are not comparable — C's Leak Window is dominated by block time, so a "
                      "sweep must be analysed one result file per value.\n")
        rej = {f"{pretty(l)} scale {s}": d.get("n_revoke_rejected", 0)
               for l in ac if is_c(l) for s, d in ac[l].get("drift", {}).items()}
        if any(rej.values()):
            md.append("Scenario C revokes rejected fail-closed by the bridge (excluded from both stats and timeouts): "
                      + ", ".join(f"{k}: {n}" for k, n in rej.items() if n) + "\n")
        fl = {f"{pretty(l)} @ {sc}": (d.get("n_trials_with_injected_fault"), d.get("fault_p"))
              for l in ac if "@faulty" in l for sc, d in ac[l].get("drift", {}).items()}
        for k, (n_inj, fp) in fl.items():
            md.append(f"- {k}: configured p={fp}; {n_inj} trial(s) had at least one injected store-write failure\n")
        if any("@" in l for l in ac):
            md.append("Regimes (decision 11): bare = healthy; [faulty] = each store-write attempt fails with probability p "
                      "(A: one attempt, no retry; C: bridge retries up to 3x); [async] = A's vector layer written by a batch "
                      "worker every T s (C has no async regime and is compared as healthy).\n")
        md.append(STATS_HEADER)
        rows = []
        for scen in ac:
            for scale, d in ac[scen].get("drift", {}).items():
                md.append(_stats_row(f"{pretty(scen)} @ {scale}", d))
                rows.append(stats_csv_rows(None, [scen, scale], d))
        write_csv(tables / "drift_window.csv", ["scenario", "scale"] + STATS_CSV_COLS, rows)
        md.append("")
        md.append("### Per-layer Leak Window\n")
        md.append(STATS_HEADER)
        rows = []
        for scen in ac:
            for key, d in ac[scen].get("per_layer", {}).items():
                scale, layer = key.split("|")
                md.append(_stats_row(f"{pretty(scen)} @ {scale} / {layer}", d))
                rows.append(stats_csv_rows(None, [scen, scale, layer], d))
        write_csv(tables / "per_layer_window.csv", ["scenario", "scale", "layer"] + STATS_CSV_COLS, rows)
        md.append("")
        md.append("### Measurement resolution (largest gap between consecutive checks)\n")
        md.append("Every window is reported as its LOWER bound: the last instant the layer was proven still open. "
                  "The poller also records the first instant it was proven closed, so the true window lies in "
                  "[reported, reported + bracket]. The bracket columns below are that width; quote windows to it. "
                  "The gap columns are the interval between consecutive checks -- the poll interval on an idle host, "
                  "much larger when host/VM scheduling starves the poller.\n")
        md.append("| group | n | poll interval (ms) | median gap (ms) | max gap (ms) | median bracket (ms) | max bracket (ms) | failed checks |\n|---|---|---|---|---|---|---|---|")
        for scen in ac:
            for key, d in ac[scen].get("per_layer", {}).items():
                r = d.get("resolution")
                if r:
                    iv = ", ".join(f"{x * 1000:.0f}" for x in r.get("poll_interval_s") or []) or "-"
                    mb = r.get("median_bracket_width_s")
                    xb = r.get("max_bracket_width_s")
                    md.append(f"| {pretty(scen)} @ {key.replace('|', ' / ')} | {r['n']} | {iv} | "
                              f"{r['median_gap_s'] * 1000:.0f} | {r['max_gap_s'] * 1000:.0f} | "
                              f"{'-' if mb is None else f'{mb * 1000:.0f}'} | {'-' if xb is None else f'{xb * 1000:.0f}'} | "
                              f"{r.get('check_errors', 0)} |")
        md.append("")
        md.append("### Left-censoring of zero windows\n")
        md.append("A zero means the layer was already closed at the first check that could have seen it; the true window lies in [0, bound]. "
                  "With concurrent pre-revoke polling the bound is about one poll interval; with older post-revoke polling it was the whole revoke duration.\n")
        inv = {f"{pretty(scen)} @ {s}": d.get("n_invalid_setup_trials", 0)
               for scen in ac for s, d in ac[scen].get("drift", {}).items()}
        if any(inv.values()):
            md.append("Trials excluded as INVALID SETUP (a layer was not retrievable before the revoke, or a poller thread died): "
                      + ", ".join(f"{k}: {v}" for k, v in inv.items() if v) + "\n")
        md.append("| group | zeros / measured | bound known for | median bound (s) | max bound (s) |\n|---|---|---|---|---|")
        for scen in ac:
            for key, d in ac[scen].get("per_layer", {}).items():
                c = d.get("censoring")
                if not c:
                    continue
                md.append(f"| {pretty(scen)} @ {key.replace('|', ' / ')} | {c['n_zero']} / {c['n_measured']} | "
                          f"{c['bound_known_for']} | {_f(c['median_censor_bound_s'])} | {_f(c['max_censor_bound_s'])} |")
        md.append("")

    # ---- B
    b = summary["b"]
    if any(k in b for k in TIMED_B):
        md.append("## RQ2 — Scenario B (converged Postgres + pgvector + RLS), timed mechanisms\n")
        md.append(STATS_HEADER)
        rows = []
        for scen in TIMED_B:
            for g, d in b.get(scen, {}).items():
                label = scen.upper() + ("" if g == "all" else f" ({g})")
                md.append(_stats_row(label, d))
                rows.append(stats_csv_rows(None, [scen, g], d))
        inv_b = [(scen.upper() + ("" if g == "all" else f" ({g})"), d) for scen in TIMED_B for g, d in b.get(scen, {}).items()
                 if d.get("n_not_retrievable_before_revoke") or d.get("n_poller_error")]
        for label, d in inv_b:
            md.append(f"- {label}: {d.get('n_not_retrievable_before_revoke', 0)} record(s) not retrievable before revoke (invalid setup), "
                      f"{d.get('n_poller_error', 0)} poller error(s) -- excluded from the statistics above")
        write_csv(tables / "b_timed_windows.csv", ["scenario", "group"] + STATS_CSV_COLS, rows)
        md.append("")
        for scen in ("b1", "b5"):
            for g, d in b.get(scen, {}).items():
                if "configured_interval_s" in d:
                    md.append(f"- {scen.upper()}: configured interval {d['configured_interval_s']} s"
                              + (f"; median window / interval = {_f(d.get('median_over_interval'))} "
                                 f"(≈0.5 expected if revoke timing is uniform w.r.t. the cycle); "
                                 f"{d.get('n_exceeding_interval')} trial(s) exceeded one full interval"
                                 if "median_over_interval" in d else ""))
        md.append("")
    if "b6" in b:
        md.append("### B6 — pgbouncer session-variable leak (occurrence rate, not a window)\n")
        md.append("| scale | seed | leaked / calls | rate | Wilson 95% CI |\n|---|---|---|---|---|")
        rows = []
        for key, d in b["b6"]["per_scale_seed"].items():
            scale, seed = key.split("|")
            ci = d["wilson_ci95"]
            md.append(f"| {scale} | {seed} | {d['leaked']} / {d['calls']} | {_f(d['rate'], 3)} | [{_f(ci[0], 3)}, {_f(ci[1], 3)}] |")
            rows.append([scale, seed, d["leaked"], d["calls"], d["rate"], ci[0], ci[1]])
        p = b["b6"]["pooled"]
        ci = p["wilson_ci95"]
        md.append(f"| **pooled** | | {p['leaked']} / {p['calls']} | {_f(p['rate'], 3)} | [{_f(ci[0], 3)}, {_f(ci[1], 3)}] |")
        rows.append(["pooled", "", p["leaked"], p["calls"], p["rate"], ci[0], ci[1]])
        write_csv(tables / "b6_rate.csv", ["scale", "seed", "leaked", "calls", "rate", "ci_lo", "ci_hi"], rows)
        md.append("")

    # ---- False containment
    fcs = summary["false_containment"] or {}
    for fc_label, fc in fcs.items():
        md.append(f"## False-containment split ({pretty(fc_label)}, PROGRESS.md decision 5)\n")
        md.append("Bridge claim = Fabric `IsContained`; ground truth = independent poller confirmed all three layers closed.\n")
        c = fc["cells"]
        md.append("| cell | bridge says | ground truth | count |\n|---|---|---|---|")
        md.append(f"| true containment | contained | contained | {c['true_containment']} |")
        md.append(f"| **false containment** (over-claim — the metric's purpose) | contained | NOT contained | {c['false_containment']} |")
        md.append(f"| false non-containment, anchor-of-propagation failed (decision-5 under-estimate) | not contained | contained | {c['false_non_containment_anchor_failed']} |")
        md.append(f"| false non-containment, anchoring still in flight (async mode only) | not contained | contained | {c['false_non_containment_anchor_pending']} |")
        md.append(f"| false non-containment, other cause | not contained | contained | {c['false_non_containment_other']} |")
        md.append(f"| true non-containment | not contained | NOT contained | {c['true_non_containment']} |")
        md.append(f"| (revoke rejected fail-closed, decision 5 — excluded from the four cells) | — | — | {c['revoke_rejected_fail_closed']} |")
        md.append(f"| (bridge status unavailable) | — | — | {c['bridge_status_unavailable']} |")
        md.append(f"| (incomplete trials, <3 layer records) | — | — | {c['incomplete_trials']} |")
        md.append(f"| (invalid setup: record not retrievable before the revoke — excluded) | — | — | {c['invalid_setup_trials']} |")
        if c["invalid_setup_trials"]:
            md.append("\n**Invalid-setup trials are present.** The record was not retrievable before the revoke, "
                      "so the trial cannot demonstrate anything about it. The usual cause is running several "
                      "seeds against a store that was truncated and seeded for only one of them.")
        md.append("")
        if fc["rates"]:
            md.append(f"Rates over {fc['n_classified']} classified trials (Wilson 95% CI):\n")
            for k, v in fc["rates"].items():
                md.append(f"- {k}: {_f(v['rate'], 4)} [{_f(v['wilson_ci95'][0], 4)}, {_f(v['wilson_ci95'][1], 4)}]")
            md.append("")
        md.append(f"Partial propagation (bridge returned 207: a layer still failed after the 3-attempt retry of decision 2): "
                  f"{fc['n_partial_propagation']} trial(s).\n")
        tr = fc["time_resolved"]
        if tr["available"]:
            md.append(f"Time-resolved (stricter) form: of {tr['n_fully_propagated_claims']} trials where the bridge returned "
                      f"`fullyPropagated=true`, {tr['n_post_claim_leaks']} still had a layer observed retrievable by the "
                      f"ground-truth poller *after* the bridge had returned. This count is a LOWER bound: in "
                      f"{tr['n_claims_with_blind_interval']} of those trials at least one layer was polled only after "
                      f"the bridge returned and was already closed at its first check, leaving an unobserved interval "
                      f"of up to {_f(tr['max_blind_interval_s'])} s in which a post-claim leak could not have been seen.\n")
        else:
            md.append("Time-resolved form: not available (result file predates `revoke_latency_s`).\n")
        write_csv(tables / f"false_containment_cells_{fc_label.replace('@', '_')}.csv", ["cell", "count"], list(c.items()))

    # ---- Overhead
    ov = summary["overhead"]
    ov_ac = [k for k in ov if k[0] in ("a", "c") and not k.startswith("_") and k != "c_vs_a"]
    if ov_ac:
        md.append("## Overhead — revoke-call latency (caller-observed)\n")
        md.append(STATS_HEADER.replace(" | timeouts |", " |").replace("|---|---|---|---|---|---|---|---|---|", "|---|---|---|---|---|---|---|---|"))
        rows = []
        for scen in tuple(ov_ac) + TIMED_B:
            for scale, d in ov.get(scen, {}).items():
                md.append(_stats_row(f"{pretty(scen)} @ {scale}", d).rsplit("|", 2)[0] + "|")
                rows.append(stats_csv_rows(None, [scen, scale], d)[:-1])
        write_csv(tables / "revoke_latency.csv", ["scenario", "scale"] + STATS_CSV_COLS[:-1], rows)
        md.append("")
        if ov.get("c_vs_a"):
            md.append("| pair | scale | median A (s) | median C (s) | C − A (s) | C / A |\n|---|---|---|---|---|---|")
            for pair, per_scale in ov["c_vs_a"].items():
                c_label, _, a_label = pair.partition("_vs_")
                for scale, r in per_scale.items():
                    md.append(f"| {pretty(c_label)} vs {pretty(a_label)} | {scale} | {_f(r['median_a_s'])} | "
                              f"{_f(r['median_c_s'])} | {_f(r['c_minus_a_s'])} | {_f(r['c_over_a'], 2)} |")
            md.append("")
    elif ov.get("_n_records_without_latency") and not ov_ac:
        md.append("## Overhead\n\nNot recorded: these result files predate the `revoke_latency_s` field in run_experiment.py.\n")
    if ov.get("_n_records_without_latency") and ov_ac:
        md.append(f"Note: {ov['_n_records_without_latency']} record(s) lacked `revoke_latency_s` and were excluded from overhead.\n")

    # ---- Bridge latency breakdown
    bt = summary.get("bridge_timing") or {}
    if bt:
        md.append("### Where Scenario C's revoke latency goes\n")
        md.append("`anchor_event` is the fail-closed anchoring of the revoke event itself, before any store is touched "
                  "(decision 5) -- the Leak Window cannot be shorter than this. `max_store` is the slowest store write, "
                  "which is what actually closes the leak. `max_anchor_prop` is anchoring the propagation record, which "
                  "happens AFTER the data is already inaccessible and so costs latency, not exposure.\n")
        md.append("| group | audit | retry | BatchTimeout | anchoring | trials | anchor_event (s) | max_store (s) | max_anchor_prop (s) | anchor_prop attempts (max) | total (s) |\n|---|---|---|---|---|---|---|---|---|---|---|")
        for label, d in bt.items():
            btv = ", ".join(d.get("batch_timeout") or []) or "-"
            amv = ", ".join(d.get("anchor_mode") or []) or "-"
            am = d.get("audit_mode") or []
            if am:
                fev = ", ".join(am)          # ledger | log | none, recorded per trial
            else:
                fev = d.get("fabric_enabled") or []
                fev = "-" if not fev else ", ".join("on" if v else "OFF (ablation)" for v in fev)
            rav = ", ".join(str(x) for x in (d.get("retry_attempts") or [])) or "-"
            md.append(f"| {pretty(label)} | {fev} | {rav} | {btv} | {amv} | {d['n_trials']} | {_f(d['anchor_event_s']['median'])} | "
                      f"{_f(d['max_store_s']['median'])} | {_f(d['max_anchor_prop_s']['median'])} | "
                      f"{_f(d['anchor_prop_attempts']['max'], 0)} | {_f(d['total_s']['median'])} |")
        if any(len(d.get("batch_timeout") or []) > 1 or len(d.get("anchor_mode") or []) > 1
               or len(d.get("fabric_enabled") or []) > 1 or len(d.get("audit_mode") or []) > 1
               or len(d.get("retry_attempts") or []) > 1
               for d in bt.values()):
            md.append("\n**A group above mixes more than one BatchTimeout, anchoring mode, ledger setting or retry budget.** "
                      "Its medians are not comparable; separate the runs by result file.")
        def _is_none(d):
            am = d.get("audit_mode") or []
            if am:                                   # recorded: authoritative
                return "none" in am
            return False in (d.get("fabric_enabled") or [])   # legacy files only
        if any(1 in (d.get("retry_attempts") or []) for d in bt.values()):
            md.append("\nA group with retry=1 is the concurrency-without-retry ablation: the bridge "
                      "propagates to the three stores in parallel but makes a single attempt at each, "
                      "so containment reflects concurrency alone.")
        if any(_is_none(d) for d in bt.values()):
            md.append("\nA group with audit=none is the ablation: retry and concurrent propagation with no "
                      "record at all. It has no containment claim to audit, so every trial appears under "
                      "\"bridge status unavailable\" in the false-containment split \u2014 which is the finding, "
                      "not a measurement failure.")
        if any("log" in (d.get("audit_mode") or []) for d in bt.values()):
            md.append("\nA group with audit=log recorded the same events in an append-only PostgreSQL table "
                      "rather than on the ledger. It answers the same containment query at a fraction of the "
                      "cost; what it does not provide is tamper-evidence or an attested writer identity.")
        if any("async" in (d.get("anchor_mode") or []) for d in bt.values()):
            md.append("\nIn async mode `max_anchor_prop` is not part of the caller's wait: the reply is sent "
                      "as soon as the stores are written. A 200 then does NOT mean the propagation is on the "
                      "ledger, and anchors still in flight are lost if the bridge dies.")
        md.append("")

    # ---- Errors
    md.append("## Trial-level errors\n")
    if not errors:
        md.append("None.\n")
    else:
        for e in errors:
            md.append(f"- scenario={e['scenario']} scale={e['scale']} seed={e['seed']} ({e['wall_clock']}): `{e['extra']['error']}`")
        md.append("")

    (out_dir / "summary.md").write_text("\n".join(md))
    with open(out_dir / "summary.json", "w") as fh:
        json.dump(_sanitise(summary), fh, indent=2, allow_nan=False)


def _sanitise(o):
    """json.dump emits NaN as a bare `NaN` token (not valid JSON; its
    `default=` hook is never called for floats), so replace NaN with None
    explicitly, and unwrap numpy scalars, before serialising."""
    if isinstance(o, dict):
        return {k: _sanitise(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitise(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        o = o.item()
    if isinstance(o, float) and math.isnan(o):
        return None
    return o


# ---------------------------------------------------------------------------
# Figures (optional)
# ---------------------------------------------------------------------------

# The target layout is two-column: one column is about 3.5 in wide, the full text
# block about 7.16 in. Figures are emitted at those widths so nothing has to
# be rescaled in the manuscript (rescaling a raster is what makes axis labels
# fuzzy in submitted PDFs). Every figure is written twice: PDF for
# submission, because vector text stays sharp at any zoom and the file is
# small, and PNG at FIG_DPI for reading and for pasting into drafts.
COL_W, FULL_W, FIG_H = 3.5, 7.16, 2.6
FIG_DPI = 600


def _style_for_print():
    """Type sizes that stay legible at 3.5 in wide in a two-column layout.
    matplotlib's defaults are tuned for on-screen figures roughly twice that
    width, so at column width they come out too small to read in print."""
    plt.rcParams.update({
        "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 9,
        "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
        "lines.linewidth": 1.2, "lines.markersize": 4,
        # NOT constrained_layout: the plotting code below already calls
        # tight_layout(), and enabling both makes matplotlib warn and ignore
        # one of them.
        "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
        "pdf.fonttype": 42,   # embed TrueType rather than Type 3, as most venues require
        "ps.fonttype": 42,
    })


def _save(fig, fig_dir, stem):
    """PDF for submission, PNG at FIG_DPI for reading."""
    fig.savefig(fig_dir / f"{stem}.pdf")
    fig.savefig(fig_dir / f"{stem}.png", dpi=FIG_DPI)
    plt.close(fig)


def make_figures(out_dir, summary):
    if not HAVE_MPL:
        print("  [figures] matplotlib not installed -- skipping figures (pip install matplotlib)")
        return
    fig_dir = Path(out_dir) / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    _style_for_print()

    # Scalability: drift window median vs scale, A vs C
    ac = summary["ac"]
    if ac:
        fig, ax = plt.subplots(figsize=(COL_W, FIG_H))
        markers = "osd^v<>"
        for i, scen in enumerate(ac):
            pts = [(int(s), d) for s, d in ac[scen].get("drift", {}).items() if d.get("n")]
            if not pts:
                continue
            pts.sort()
            xs = [p[0] for p in pts]
            med = [p[1]["median"] for p in pts]
            lo = [p[1]["median"] - p[1]["median_ci95"][0] for p in pts]
            hi = [p[1]["median_ci95"][1] - p[1]["median"] for p in pts]
            ax.errorbar(xs, med, yerr=[lo, hi], marker=markers[i % len(markers)], capsize=3, label=pretty(scen))
        ax.set_xscale("log")
        ax.set_xlabel("resources seeded")
        ax.set_ylabel("drift window, median (s)")
        ax.set_title("Leak Window vs scale")
        ax.legend()
        fig.tight_layout()
        _save(fig, fig_dir, "scalability_drift_window")

        # Per-layer bars at each scale
        for scen in ac:
            pl = ac[scen].get("per_layer", {})
            if not pl:
                continue
            scales = sorted({int(k.split("|")[0]) for k in pl})
            fig, ax = plt.subplots(figsize=(COL_W, FIG_H))
            width = 0.25
            for i, layer in enumerate(LAYERS):
                ys = [pl.get(f"{s}|{layer}", {}).get("median", 0.0) for s in scales]
                ax.bar([x + (i - 1) * width for x in range(len(scales))], ys, width, label=layer)
            ax.set_xticks(range(len(scales)))
            ax.set_xticklabels([str(s) for s in scales])
            ax.set_xlabel("resources seeded")
            ax.set_ylabel("Leak Window, median (s)")
            ax.set_title(f"{pretty(scen)} per-layer")
            ax.legend()
            fig.tight_layout()
            _save(fig, fig_dir, f"per_layer_{scen.replace('@', '_')}")

    # Overhead vs scale
    ov = summary["overhead"]
    ov_ac = [k for k in ov if k[0] in ("a", "c") and not k.startswith("_") and k != "c_vs_a"]
    if ov_ac:
        fig, ax = plt.subplots(figsize=(COL_W, FIG_H))
        markers = "osd^v<>"
        for i, scen in enumerate(ov_ac):
            pts = sorted((int(s), d) for s, d in ov[scen].items() if d.get("n"))
            if pts:
                ax.errorbar([p[0] for p in pts], [p[1]["median"] for p in pts],
                            yerr=[[p[1]["median"] - p[1]["median_ci95"][0] for p in pts],
                                  [p[1]["median_ci95"][1] - p[1]["median"] for p in pts]],
                            marker=markers[i % len(markers)], capsize=3, label=pretty(scen))
        ax.set_xscale("log")
        ax.set_xlabel("resources seeded")
        ax.set_ylabel("revoke latency, median (s)")
        ax.set_title("Revoke-path overhead vs scale")
        ax.legend()
        fig.tight_layout()
        _save(fig, fig_dir, "overhead_vs_scale")

    # B timed mechanisms
    b = summary["b"]
    items = [(scen.upper() + ("" if g == "all" else f"\n{g}"), d)
             for scen in TIMED_B for g, d in b.get(scen, {}).items() if d.get("n")]
    if items:
        fig, ax = plt.subplots(figsize=(FULL_W, FIG_H))
        xs = range(len(items))
        ax.bar(xs, [d["median"] for _, d in items],
               yerr=[[d["median"] - d["median_ci95"][0] for _, d in items],
                     [d["median_ci95"][1] - d["median"] for _, d in items]], capsize=3)
        ax.set_xticks(list(xs))
        ax.set_xticklabels([lbl for lbl, _ in items])
        ax.set_ylabel("Leak Window, median (s)")
        ax.set_title("Scenario B timed mechanisms")
        fig.tight_layout()
        _save(fig, fig_dir, "b_timed_windows")


# ---------------------------------------------------------------------------

def analyse(records, errors):
    records = relabel_regimes(records)
    return {
        "ac": analyse_ac(records),
        "b": analyse_b(records),
        "false_containment": analyse_false_containment(records),
        "overhead": analyse_overhead(records),
        "bridge_timing": bridge_timing_summary(records),
        "n_records": len(records),
        "n_errors": len(errors),
    }


def main():
    global FIG_DPI
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="results JSONL file(s)")
    ap.add_argument("--out", default="analysis")
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--fig-dpi", type=int, default=FIG_DPI,
                    help="raster resolution for the PNG copies (the PDF copies are vector). "
                         "600 dpi is the usual requirement for raster art")
    args = ap.parse_args()

    FIG_DPI = args.fig_dpi
    records, errors, skipped = load_results(args.inputs)
    if not records and not errors:
        print("No records found.")
        sys.exit(1)
    summary = analyse(records, errors)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_report(out_dir, summary, len(records), len(errors), skipped, errors)
    if not args.no_figures:
        make_figures(out_dir, summary)
    print(f"Wrote {out_dir / 'summary.md'}, {out_dir / 'summary.json'}, tables/ and figures/ (if matplotlib).")


if __name__ == "__main__":
    main()
