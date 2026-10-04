#!/usr/bin/env python3
"""The ablation table (paper Table VII and Figure 6) computed from result
files, with the statistics the first submission's reviewers asked for.

Reads one or more result files, one configuration per label, and reports
per configuration (and per injection probability, when several are given):

  - trials leaking without bound (a layer never confirmed closed within the
    poll timeout), as k of n with a Wilson 95% interval;
  - drift window (max over the three layers) median with a bootstrap 95%
    interval, over the trials that closed;
  - revoke latency median;
  - the self-report split where the configuration has one -- the bridge's
    /status verdict for C, the outbox's own bookkeeping for O -- against the
    poller's ground truth: false containment, false non-containment, and
    whether a reported failure named the layer that actually leaked
    (attribution);
  - the difference in the unbounded-leak proportion against a reference
    configuration (A unless told otherwise) with a Newcombe 95% interval
    (from the two Wilson intervals; Wald misbehaves near 0 and 1);
  - and, when every configuration was run under the PAIRED fault schedule
    with the same seeds, scale and p, a matched-pairs comparison: the number
    of seeds on which the two configurations disagree, in each direction,
    and an exact McNemar test (two-sided binomial on the discordant pairs).
    Under the stream schedule the trials are not paired and this block is
    omitted rather than computed on a false premise.

Usage:
  python3 experiments/ablation_table.py \\
      A=results/paired_p0.30_none.jsonl \\
      O=results/paired_p0.30_none.jsonl \\
      "C no retry=results/paired_p0.30_none-noretry.jsonl" \\
      "C retry=results/paired_p0.30_none.jsonl" \\
      "C log=results/paired_p0.30_log.jsonl" \\
      "C ledger=results/paired_p0.30_ledger.jsonl" \\
      --out analysis/ablation_p0.30

Each label maps to a scenario inside its file: a label starting with "A"
reads Scenario A records, "O" reads Scenario O, anything else reads
Scenario C. A and O run in every pass of the driver, so they may be read
from any pass's file; under the paired schedule their outcomes must be
identical across passes and this script checks that they are.

Exclusions follow analyze.py exactly: a trial whose record was not
retrievable before the revoke, whose poller died, or with fewer than three
layer records carries no evidence and is excluded; a trial reporting a
window far beyond the poll timeout is physically impossible for the poller
(a host stall -- Section VIII-C of the paper; poll.py marks it in
poller_error) and is excluded as anomalous.
Excluded trials are counted and printed; they are never in a denominator.
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from experiments.analyze import wilson_interval, LAYERS  # noqa: E402

BOOTSTRAP_ITERS = 2000
BOOTSTRAP_SEED = 12345
POLL_TIMEOUT_S = 30.0


# ---------------------------------------------------------------------------
# Loading and per-trial reduction
# ---------------------------------------------------------------------------

def load(path):
    rows = []
    skipped = 0
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                skipped += 1
    return rows, skipped


def scenario_for_label(label):
    l = label.strip().upper()
    if l.startswith("A"):
        return "a"
    if l.startswith("O"):
        return "o"
    return "c"


def trials_for(rows, scenario, timeout_s=POLL_TIMEOUT_S):
    """Reduce layer records to one dict per trial. Returns (trials, excluded)
    where excluded is {reason: count}."""
    by = defaultdict(dict)
    errors = 0
    for r in rows:
        if r["scenario"] != scenario:
            continue
        if "error" in (r.get("extra") or {}) and r.get("layer") is None:
            errors += 1
            continue
        if r["layer"] not in LAYERS:
            continue
        key = (r["scale"], r["seed"], r["resource_id"], r["principal_id"], r["t_issued"])
        by[key][r["layer"]] = r
    trials, excluded = [], []      # excluded: (scale, fault_p, seed, reason)
    for _ in range(errors):
        excluded.append((None, None, None, "trial_error"))
    for key, layers in by.items():
        some = next(iter(layers.values()))
        ex_key = (key[0], some["extra"].get("fault_p"), key[1])
        if set(layers) != set(LAYERS):
            excluded.append(ex_key + ("incomplete",))
            continue
        if any(l["extra"].get("retrievable_before_revoke") is False for l in layers.values()):
            excluded.append(ex_key + ("invalid_setup",))
            continue
        if any(l["extra"].get("poller_error") for l in layers.values()):
            excluded.append(ex_key + ("poller_error",))
            continue
        windows = [l["leak_window_s"] for l in layers.values()]
        # A timed-out layer records a window of about timeout_s (it is a
        # lower bound, and confirmed_contained is False). A window far
        # beyond it is the host-stall anomaly, which poll.py already marks
        # in poller_error -- and that record was excluded above. This is
        # only a belt-and-braces check for files written by an older poller.
        if any(w is not None and w > 1.5 * timeout_s for w in windows):
            excluded.append(ex_key + ("anomalous_window",))
            continue
        first = layers[LAYERS[0]]
        e = first["extra"]
        leaked_layers = sorted(l for l, rec in layers.items() if rec["confirmed_contained"] is not True)
        t = {
            "scale": key[0], "seed": key[1],
            "fault_p": e.get("fault_p"), "fault_schedule": e.get("fault_schedule"),
            "regime": e.get("regime"),
            "leaked_without_bound": bool(leaked_layers),
            "leaked_layers": leaked_layers,
            "drift_window_s": (max(windows) if not leaked_layers and all(w is not None for w in windows) else None),
            # caller-observed, comparable across A, O and C
            "revoke_latency_s": e.get("revoke_latency_s"),
            # bridge-reported server-side total (C only): what Table VII's
            # latency column used in the first submission
            "bridge_total_s": _bridge_total_s(first) if scenario == "c" else None,
            "audit_mode": e.get("audit_mode"), "retry_attempts": e.get("retry_attempts"),
            "self_report": None,
        }
        if scenario == "c":
            st = None
            for l in reversed(LAYERS):
                cand = layers[l]["extra"].get("bridge_status")
                if isinstance(cand, dict) and "contained" in cand:
                    st = cand
                    break
            resp = first["extra"].get("bridge_response") or {}
            t["self_report"] = _self_report(st, leaked_layers, missing_key="missingLayers")
            t["rejected_fail_closed"] = isinstance(resp, dict) and "error" in resp
        elif scenario == "o":
            st = first["extra"].get("outbox_status")
            t["self_report"] = _self_report(st, leaked_layers, missing_key="missingLayers")
        trials.append(t)
    trials.sort(key=lambda t: (t["scale"], t["fault_p"] or 0, t["seed"]))
    return trials, excluded


def _bridge_total_s(rec):
    t = ((rec["extra"].get("bridge_response") or {}).get("timing") or {}).get("totalMs")
    return None if t is None else t / 1000.0


def _self_report(status, leaked_layers, missing_key):
    if not isinstance(status, dict) or "contained" not in status:
        return {"available": False}
    claimed = bool(status["contained"])
    truth = not leaked_layers
    missing = sorted(status.get(missing_key) or [])
    if claimed and truth:
        cell = "true_containment"
    elif claimed and not truth:
        cell = "false_containment"
    elif not claimed and truth:
        cell = "false_non_containment"
    else:
        cell = "true_non_containment"
    # Attribution: a reported non-containment names exactly the layers that
    # actually stayed open. Only meaningful in the true_non_containment cell.
    attributed = (cell == "true_non_containment" and missing == leaked_layers)
    return {"available": True, "cell": cell, "claimed_missing": missing, "attributed": attributed}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def median_ci(values):
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    if arr.size == 0:
        return None, (None, None)
    if arr.size < 2:
        return float(arr[0]), (float(arr[0]), float(arr[0]))
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    boots = np.median(arr[rng.integers(0, arr.size, size=(BOOTSTRAP_ITERS, arr.size))], axis=1)
    return float(np.median(arr)), (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)))


def newcombe_diff(k1, n1, k2, n2, z=1.96):
    """Newcombe (1998) method 10: interval for p1 - p2 from the two Wilson
    intervals. Returns (diff, lo, hi)."""
    if n1 == 0 or n2 == 0:
        return None, None, None
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson_interval(k1, n1, z)
    l2, u2 = wilson_interval(k2, n2, z)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return d, lo, hi


def exact_mcnemar(b, c):
    """Two-sided exact McNemar: binomial test of b against b + c at 0.5.
    b = pairs where the first configuration leaked and the second did not;
    c = the reverse. Returns the p-value (1.0 when there are no discordant
    pairs, which is 'no evidence of a difference', not 'no difference')."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_block(t_ref, t_cmp):
    """Matched-pairs comparison of leaked_without_bound between two
    configurations on the seeds they share (same scale and p)."""
    ref = {(t["scale"], t["fault_p"], t["seed"]): t["leaked_without_bound"] for t in t_ref}
    cmp_ = {(t["scale"], t["fault_p"], t["seed"]): t["leaked_without_bound"] for t in t_cmp}
    shared = sorted(set(ref) & set(cmp_))
    if not shared:
        return None
    both = sum(1 for k in shared if ref[k] and cmp_[k])
    neither = sum(1 for k in shared if not ref[k] and not cmp_[k])
    b = sum(1 for k in shared if ref[k] and not cmp_[k])   # reference leaked, comparison did not
    c = sum(1 for k in shared if not ref[k] and cmp_[k])   # comparison leaked, reference did not
    n = len(shared)
    diff = (b - c) / n
    # Wald interval for a paired difference of proportions; adequate for a
    # descriptive report at these sizes, and labelled as such in the output.
    se = math.sqrt(max(b + c - (b - c) ** 2 / n, 0.0)) / n
    return {"n_pairs": n, "both_leaked": both, "neither_leaked": neither,
            "ref_only_leaked": b, "cmp_only_leaked": c,
            "paired_diff": diff, "paired_diff_wald_ci95": [diff - 1.96 * se, diff + 1.96 * se],
            "mcnemar_exact_p": exact_mcnemar(b, c),
            "seeds_ref_only": [k[2] for k in shared if ref[k] and not cmp_[k]],
            "seeds_cmp_only": [k[2] for k in shared if not ref[k] and cmp_[k]]}


# ---------------------------------------------------------------------------
# Per-configuration summary
# ---------------------------------------------------------------------------

def summarise(trials):
    n = len(trials)
    k = sum(t["leaked_without_bound"] for t in trials)
    lo, hi = wilson_interval(k, n) if n else (None, None)
    med, (mlo, mhi) = median_ci([t["drift_window_s"] for t in trials if not t["leaked_without_bound"]])
    lat, (llo, lhi) = median_ci([t["revoke_latency_s"] for t in trials])
    btot, _ = median_ci([t["bridge_total_s"] for t in trials])
    out = {"n": n, "leaked_without_bound": k, "rate": (k / n if n else None), "wilson_ci95": [lo, hi],
           "n_closed": n - k, "drift_window_median_s": med, "drift_window_ci95": [mlo, mhi],
           "revoke_latency_median_s": lat, "revoke_latency_ci95": [llo, lhi],
           "bridge_total_median_s": btot,
           "layers_leaked": dict(sorted(_count([l for t in trials for l in t["leaked_layers"]]).items()))}
    reps = [t["self_report"] for t in trials if t["self_report"] and t["self_report"].get("available")]
    if reps:
        cells = _count([r["cell"] for r in reps])
        tnc = [r for r in reps if r["cell"] == "true_non_containment"]
        out["self_report"] = {"n": len(reps), "cells": dict(sorted(cells.items())),
                              "false_containment": cells.get("false_containment", 0),
                              "false_non_containment": cells.get("false_non_containment", 0),
                              "attributed": sum(r["attributed"] for r in tnc), "attributable": len(tnc)}
    out["leaked_seeds"] = sorted(t["seed"] for t in trials if t["leaked_without_bound"])
    # Compact per-trial record, so a downstream document can name seeds and
    # windows without re-reading the result files.
    out["trials"] = [{"seed": t["seed"], "leaked_layers": t["leaked_layers"],
                      "drift_window_s": t["drift_window_s"], "revoke_latency_s": t["revoke_latency_s"]}
                     for t in sorted(trials, key=lambda t: t["seed"])]
    rej = sum(1 for t in trials if t.get("rejected_fail_closed"))
    if rej:
        out["rejected_fail_closed"] = rej
    sched = sorted({t["fault_schedule"] for t in trials if t["fault_schedule"]})
    out["fault_schedule"] = sched
    out["fault_p"] = sorted({t["fault_p"] for t in trials if t["fault_p"] is not None})
    out["scale"] = sorted({t["scale"] for t in trials})
    am = sorted({str(t["audit_mode"]) for t in trials if t["audit_mode"]})
    ra = sorted({t["retry_attempts"] for t in trials if t["retry_attempts"] is not None})
    if am:
        out["audit_mode"] = am
    if ra:
        out["retry_attempts"] = ra
    return out


def _count(items):
    d = defaultdict(int)
    for i in items:
        d[i] += 1
    return d


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _f(x, nd=3):
    return "—" if x is None else f"{x:.{nd}f}"


def _p(x):
    return "< 0.001" if x < 0.001 else f"{x:.3f}"


def _ci(ci, nd=2):
    return "—" if not ci or ci[0] is None else f"[{ci[0]:.{nd}f}, {ci[1]:.{nd}f}]"


def render_markdown(report):
    md = ["# Ablation table", ""]
    for p, block in report["by_p"].items():
        md.append(f"## Injection probability p = {p}")
        md.append("")
        md.append("| configuration | n | leaking without bound | Wilson 95% | drift window, closed trials (median, 95% CI) | "
                  "revoke latency, caller-observed (median; bridge-reported total) | self-report: false cont. / false non-cont. / attributed | reference − this (Newcombe 95%) |")
        md.append("|---|---|---|---|---|---|---|---|")
        for label, s in block["configs"].items():
            sr = s.get("self_report")
            srs = "—" if not sr else (f"{sr['false_containment']} / {sr['false_non_containment']} / "
                                      f"{sr['attributed']} of {sr['attributable']}")
            d = block["diff_vs_reference"].get(label)
            ds = "—" if not d or d["diff"] is None else f"{d['diff']:+.2f} [{d['lo']:+.2f}, {d['hi']:+.2f}]"
            if label == block["reference"]:
                ds = "(reference)"
            md.append(f"| {label} | {s['n']} | {s['leaked_without_bound']} of {s['n']} | {_ci(s['wilson_ci95'])} | "
                      f"{_f(s['drift_window_median_s'])} s {_ci(s['drift_window_ci95'], 3)} | "
                      f"{_f(s['revoke_latency_median_s'])} s"
                      + (f" ({_f(s['bridge_total_median_s'])} s)" if s.get('bridge_total_median_s') is not None else "")
                      + f" | {srs} | {ds} |")
        md.append("")
        if block.get("paired"):
            md.append(f"Matched pairs (paired fault schedule, {block['paired_n']} shared seeds), each row against the reference "
                      f"'{block['reference']}': seeds where only the reference leaked / only this configuration leaked, "
                      f"paired difference (Wald 95%), exact McNemar p.")
            md.append("")
            md.append("| configuration | ref only | this only | both | neither | paired Δ | Wald 95% | McNemar p |")
            md.append("|---|---|---|---|---|---|---|---|")
            for label, pb in block["paired"].items():
                md.append(f"| {label} | {pb['ref_only_leaked']} | {pb['cmp_only_leaked']} | {pb['both_leaked']} | "
                          f"{pb['neither_leaked']} | {pb['paired_diff']:+.2f} | {_ci(pb['paired_diff_wald_ci95'])} | "
                          f"{_p(pb['mcnemar_exact_p'])} |")
            md.append("")
            if block.get("paired_consecutive"):
                md.append("Consecutive rungs of the ladder (each against the previous row):")
                md.append("")
                md.append("| previous → this | prev only | this only | paired Δ | McNemar p |")
                md.append("|---|---|---|---|---|")
                for pair, pb in block["paired_consecutive"].items():
                    md.append(f"| {pair} | {pb['ref_only_leaked']} | {pb['cmp_only_leaked']} | "
                              f"{pb['paired_diff']:+.2f} | {_p(pb['mcnemar_exact_p'])} |")
                md.append("")
        else:
            md.append("_No matched-pairs block: the configurations were not all run under the paired fault "
                      "schedule on the same seeds, so per-seed outcomes are not comparable._")
            md.append("")
        if block["excluded"]:
            md.append("Excluded trials (never in a denominator): " +
                      "; ".join(f"{label}: {ex}" for label, ex in block["excluded"].items() if ex))
            md.append("")
    if report["checks"]:
        md.append("## Checks")
        md.append("")
        for c in report["checks"]:
            md.append(f"- {c}")
        md.append("")
    return "\n".join(md)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_report(configs, reference=None, timeout_s=POLL_TIMEOUT_S):
    """configs: ordered list of (label, [paths]). Returns the report dict."""
    per_label = {}
    checks = []
    for label, paths in configs:
        scenario = scenario_for_label(label)
        trials, excluded = [], []
        for path in paths:
            rows, skipped = load(path)
            if skipped:
                checks.append(f"{path}: {skipped} unparseable line(s) skipped")
            t, ex = trials_for(rows, scenario, timeout_s)
            trials.extend(t)
            excluded.extend(ex)
        if not trials:
            checks.append(f"WARNING: '{label}' -> scenario '{scenario}' has no valid trials in {paths}")
        per_label[label] = {"scenario": scenario, "trials": trials, "excluded": excluded}

    # A and O appear in several passes' files: under the paired schedule the
    # SAME seeds must have produced the SAME outcomes. Report a violation
    # rather than silently averaging different runs.
    for label, d in per_label.items():
        seen = {}
        for t in d["trials"]:
            key = (t["scale"], t["fault_p"], t["seed"])
            if key in seen and t["fault_schedule"] == "paired" and seen[key] != t["leaked_layers"]:
                checks.append(f"WARNING: '{label}' seed {key} has different leaked layers across files under the "
                              f"paired schedule: {seen[key]} vs {t['leaked_layers']} -- the passes are not comparable")
            seen.setdefault(key, t["leaked_layers"])

    labels = [l for l, _ in configs]
    reference = reference or labels[0]
    if reference not in per_label:
        raise SystemExit(f"reference '{reference}' is not one of the configurations")

    all_p = sorted({t["fault_p"] for d in per_label.values() for t in d["trials"]}, key=lambda x: (x is None, x))
    by_p = {}
    for p in all_p:
        block = {"reference": reference, "configs": {}, "diff_vs_reference": {}, "excluded": {},
                 "excluded_seeds": {}}
        sub = {label: _dedupe([t for t in d["trials"] if t["fault_p"] == p]) for label, d in per_label.items()}
        for label in labels:
            block["configs"][label] = summarise(sub[label])
            # A seed is excluded only if NO file holds a valid trial for it:
            # when A and O are read from every pass, a seed whose poller
            # died in one pass and completed in another is recovered, not
            # lost. Trial-level errors (no seed recorded) are always counted.
            valid = {t["seed"] for t in sub[label]}
            counts, seeds = defaultdict(int), set()
            for scale, fp, seed, reason in per_label[label]["excluded"]:
                if reason == "trial_error":
                    counts[reason] += 1
                    continue
                if fp != p or seed in valid:
                    continue
                seeds.add((seed, reason))      # one seed excluded in two files is one exclusion
            for _, reason in seeds:
                counts[reason] += 1
            block["excluded"][label] = dict(counts)
            block["excluded_seeds"][label] = sorted(seeds)
        ref = block["configs"][reference]
        for label in labels:
            s = block["configs"][label]
            d, lo, hi = newcombe_diff(ref["leaked_without_bound"], ref["n"], s["leaked_without_bound"], s["n"])
            block["diff_vs_reference"][label] = {"diff": d, "lo": lo, "hi": hi,
                                                 "meaning": "reference rate minus this configuration's rate"}
        paired_ok = all(sub[l] and all(t["fault_schedule"] == "paired" for t in sub[l]) for l in labels)
        if paired_ok:
            block["paired"] = {}
            for label in labels:
                if label == reference:
                    continue
                pb = paired_block(sub[reference], sub[label])
                if pb:
                    block["paired"][label] = pb
            block["paired_n"] = min((pb["n_pairs"] for pb in block["paired"].values()), default=0)
            block["paired_consecutive"] = {}
            for prev, cur in zip(labels, labels[1:]):
                pb = paired_block(sub[prev], sub[cur])
                if pb:
                    block["paired_consecutive"][f"{prev} → {cur}"] = pb
        by_p[str(p)] = block
    return {"configs": [(l, [str(p) for p in ps]) for l, ps in configs], "reference": reference,
            "by_p": by_p, "checks": checks}


def _dedupe(trials):
    """One trial per (scale, p, seed): A and O are read from every pass's
    file, and their identical copies must not be counted several times."""
    seen, out = set(), []
    for t in trials:
        key = (t["scale"], t["fault_p"], t["seed"])
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("configs", nargs="+", help='"label=path[,path...]" in ladder order')
    ap.add_argument("--reference", default=None, help="label to difference against (default: the first)")
    ap.add_argument("--out", default=None, help="output stem: writes <out>.md and <out>.json")
    ap.add_argument("--timeout-s", type=float, default=POLL_TIMEOUT_S,
                    help="poll timeout the trials were run with; a window above it is anomalous")
    args = ap.parse_args()
    configs = []
    for c in args.configs:
        if "=" not in c:
            raise SystemExit(f"expected label=path, got {c!r}")
        label, paths = c.split("=", 1)
        configs.append((label.strip(), [Path(p.strip()) for p in paths.split(",")]))
    report = build_report(configs, args.reference, args.timeout_s)
    md = render_markdown(report)
    print(md)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # Append, never with_suffix: a stem such as "paired_p0.30" has a
        # ".30" that with_suffix would treat as an extension and replace.
        md_path = out.parent / (out.name + ".md")
        js_path = out.parent / (out.name + ".json")
        md_path.write_text(md)
        js_path.write_text(json.dumps(report, indent=2, default=str))
        print(f"wrote {md_path} and {js_path}")


if __name__ == "__main__":
    main()
