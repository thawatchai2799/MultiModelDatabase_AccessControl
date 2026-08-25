#!/usr/bin/env python3
"""Figures for the paper, built straight from the result JSONL files.

Separate from analyze.py's diagnostic plots on purpose: those exist to show
whether a run went well, these exist to make an argument in print, and the
two want different framing, labels and captions.

Every figure is written twice -- PDF for submission (vector text stays sharp
and most venues prefer it) and PNG at 600 dpi for reading and drafts -- at
the target column widths, so nothing has to be rescaled in a word processor.

Run from mldb/:  python3 docs/make_paper_figures.py [--results results] [--out docs/figures]
"""
import argparse
import json
import statistics as st
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COL_W, FULL_W = 3.5, 7.16          # two-column layout: one column, full text block
FIG_DPI = 600

# Colour-blind-safe and distinguishable in greyscale print.
C_A, C_C, C_ALT = "#0072B2", "#D55E00", "#009E73"


def style():
    plt.rcParams.update({
        "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 8.5,
        "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
        "lines.linewidth": 1.3, "lines.markersize": 4.5,
        "axes.grid": True, "grid.alpha": 0.25, "grid.linewidth": 0.5,
        "axes.spines.top": False, "axes.spines.right": False,
        "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
        "pdf.fonttype": 42, "ps.fonttype": 42,     # TrueType, not Type 3 (usuE requires it)
    })


def save(fig, out_dir, stem):
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{stem}.pdf")
    fig.savefig(out_dir / f"{stem}.png", dpi=FIG_DPI)
    plt.close(fig)
    print(f"  wrote {stem}.pdf and {stem}.png")


class MissingResults(Exception):
    pass


def load(results, name):
    path = Path(results) / f"{name}.jsonl"
    if not path.exists():
        # Raised, not sys.exit: a figure whose inputs are absent should be
        # skipped with a message, not take the remaining figures down with it.
        raise MissingResults(str(path))
    return [json.loads(l) for l in path.open() if l.strip()]


def drift_windows(rows, scenario_prefix="c"):
    """One drift window per trial: the max over the three layers. Trials with
    a timeout are excluded and counted, exactly as analyze.py does -- a
    timed-out window is a lower bound, not a value."""
    trials, timeouts, invalid = {}, set(), set()
    for r in rows:
        if not r["scenario"].startswith(scenario_prefix) or r["layer"] is None:
            continue
        key = (r["scenario"], r["scale"], r["seed"], r["resource_id"], r["principal_id"], r["t_issued"])
        # Same exclusions as analyze.py's window statistics. A trial whose
        # record was not readable before the revoke, or whose poller failed,
        # carries no information about the revoke; without this the figures
        # and the tables would be built on different trial sets.
        if r["extra"].get("retrievable_before_revoke") is False or r["extra"].get("poller_error"):
            invalid.add(key)
        if r["confirmed_contained"] is False:
            timeouts.add(key)
        trials.setdefault(key, []).append(r["leak_window_s"])
    good = [max(v) for k, v in trials.items()
            if k not in timeouts and k not in invalid and len(v) == 3]
    # An excluded trial may also carry confirmed_contained=False, so it lands
    # in BOTH sets; counting it as a timeout would report a trial that was
    # discarded as though it had leaked. analyze.py excludes first and counts
    # second, and the figures must do the same or they will disagree with the
    # tables beside them.
    return good, len(timeouts - invalid)


def median_ci(values):
    """Median with a seeded bootstrap 95% interval, matching analyze.py."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return float("nan"), (float("nan"), float("nan"))
    if arr.size < 2:
        return float(arr[0]), (float(arr[0]), float(arr[0]))
    rng = np.random.default_rng(12345)
    boots = np.median(arr[rng.integers(0, arr.size, size=(2000, arr.size))], axis=1)
    return float(np.median(arr)), (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)))


def timing_medians(rows):
    """Bridge-reported breakdown, deduplicated per trial."""
    seen, out = set(), {"anchor": [], "store": [], "prop": [], "total": []}
    for r in rows:
        if r["scenario"] != "c":
            continue
        key = (r["seed"], r["t_issued"])
        if key in seen:
            continue
        seen.add(key)
        br = r["extra"].get("bridge_response") or {}
        t = br.get("timing") or {}
        props = br.get("propagation") or []
        if not t:
            continue
        out["anchor"].append(t.get("anchorEventMs", 0) / 1000)
        out["total"].append(t.get("totalMs", 0) / 1000)
        out["store"].append(max((p.get("storeMs", 0) for p in props), default=0) / 1000)
        out["prop"].append(max((p.get("anchorPropMs") or 0 for p in props), default=0) / 1000)
    return {k: (st.median(v) if v else 0.0) for k, v in out.items()}


# ---------------------------------------------------------------------------
# Fig. 3 -- Leak Window against orderer BatchTimeout
# ---------------------------------------------------------------------------
def fig_batch_timeout(results, out_dir):
    points = [(0.5, "c_bt500ms"), (2.0, "c_v29"), (5.0, "c_bt5s")]
    xs, med, lo, hi = [], [], [], []
    for bt, name in points:
        w, _ = drift_windows(load(results, name))
        m, (a, b) = median_ci(w)
        xs.append(bt); med.append(m); lo.append(m - a); hi.append(b - m)

    slope, intercept = np.polyfit(xs, med, 1)
    fig, ax = plt.subplots(figsize=(COL_W, 2.5))
    grid = np.linspace(0, 5.4, 50)
    ax.plot(grid, slope * grid + intercept, "--", color="0.45", linewidth=1.0,
            label=f"fit: {slope:.2f}·T + {intercept:.2f} s")
    ax.errorbar(xs, med, yerr=[lo, hi], fmt="o", color=C_C, capsize=2.5,
                markeredgecolor="white", markeredgewidth=0.4, label="measured")
    ax.set_xlabel("orderer BatchTimeout, T (s)")
    ax.set_ylabel("Leak Window (s)")
    ax.set_xlim(0, 5.4); ax.set_ylim(0, 6.2)
    ax.legend(frameon=False, loc="upper left")
    save(fig, out_dir, "fig3_batchtimeout")
    print(f"    slope={slope:.3f}  intercept={intercept:.3f}s  points={[round(m,3) for m in med]}")
    return slope, intercept


# ---------------------------------------------------------------------------
# Fig. 4 -- synchronous vs asynchronous propagation anchoring
# ---------------------------------------------------------------------------
def fig_sync_async(results, out_dir):
    cfg = [("500 ms", "c_bt500ms_async", "c_bt500ms"), ("5 s", "c_bt5s_async", "c_bt5s_sync")]
    labels, win_s, win_a, lat_s, lat_a, conf_s, conf_a = [], [], [], [], [], [], []
    for label, async_f, sync_f in cfg:
        rs, ra = load(results, sync_f), load(results, async_f)
        ws, _ = drift_windows(rs); wa, _ = drift_windows(ra)
        labels.append(label)
        win_s.append(st.median(ws)); win_a.append(st.median(wa))
        lat_s.append(timing_medians(rs)["total"]); lat_a.append(timing_medians(ra)["total"])
        # how often the ledger could confirm propagation at reply time
        conf_s.append(_confirmed(rs)); conf_a.append(_confirmed(ra))

    x = np.arange(len(labels)); w = 0.2
    fig, ax = plt.subplots(figsize=(COL_W, 2.7))
    ax.bar(x - 1.5 * w, win_s, w, label="window, sync", color=C_A)
    ax.bar(x - 0.5 * w, win_a, w, label="window, async", color=C_A, alpha=0.45, hatch="///")
    bs = ax.bar(x + 0.5 * w, lat_s, w, label="latency, sync", color=C_C)
    ba = ax.bar(x + 1.5 * w, lat_a, w, label="latency, async", color=C_C, alpha=0.45, hatch="///")
    # The point of this figure is not that async is faster -- it is what the
    # speed costs. Put that on the bars rather than only in the caption.
    top = max(lat_s) * 1.55
    for bars, conf in ((bs, conf_s), (ba, conf_a)):
        for b, c in zip(bars, conf):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + top * 0.015,
                    c, ha="center", va="bottom", fontsize=6.2, rotation=90)
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_xlabel("orderer BatchTimeout")
    ax.set_ylabel("seconds")
    ax.legend(frameon=False, ncol=2, fontsize=6.2, loc="upper left")
    ax.set_ylim(0, top)
    ax.text(0.5, 0.845, "labels: trials in which the ledger could confirm\npropagation when the caller was answered",
            transform=ax.transAxes, ha="center", va="top", fontsize=5.8, color="0.3")
    save(fig, out_dir, "fig4_sync_vs_async")
    for i, l in enumerate(labels):
        print(f"    {l:>6}: window {win_s[i]:.3f}/{win_a[i]:.3f}  latency {lat_s[i]:.3f}/{lat_a[i]:.3f}  "
              f"ledger-confirmed sync {conf_s[i]}  async {conf_a[i]}")


def _confirmed(rows):
    """Trials whose /status said 'contained' at the moment the poller read it."""
    seen, yes, total = set(), 0, 0
    for r in rows:
        if r["scenario"] != "c" or r["layer"] != "vector":
            continue
        key = (r["seed"], r["t_issued"])
        if key in seen:
            continue
        seen.add(key); total += 1
        if (r["extra"].get("bridge_status") or {}).get("contained") is True:
            yes += 1
    return f"{yes}/{total}"


# ---------------------------------------------------------------------------
# Fig. 5 -- drift window against scale
# ---------------------------------------------------------------------------
def fig_scale(results, out_dir, c_1k_file):
    scales = [1000, 10000, 100000]
    a_files = ["ac_healthy", "ac_10k", "ac_100k"]
    c_files = [c_1k_file, "ac_10k", "ac_100k"]

    def series(files, prefix):
        med, lo, hi = [], [], []
        for f in files:
            w, _ = drift_windows(load(results, f), prefix)
            m, (a, b) = median_ci(w)
            med.append(m); lo.append(m - a); hi.append(b - m)
        return med, lo, hi

    am, al, ah = series(a_files, "a")
    cm, cl, ch = series(c_files, "c")

    fig, ax = plt.subplots(figsize=(COL_W, 2.5))
    ax.errorbar(scales, cm, yerr=[cl, ch], fmt="s-", color=C_C, capsize=2.5,
                markeredgecolor="white", markeredgewidth=0.4, label="C (bridge)")
    ax.errorbar(scales, am, yerr=[al, ah], fmt="o-", color=C_A, capsize=2.5,
                markeredgecolor="white", markeredgewidth=0.4, label="A (uncoordinated, 10 ms polling)")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("records seeded")
    ax.set_ylabel("Leak Window (s)")
    ax.set_xticks(scales); ax.set_xticklabels(["1K", "10K", "100K"])
    # The two series occupy the top and the bottom of the panel and never
    # meet, so the clear band is between them -- not at the middle right,
    # where the legend used to cross A's error bar at 100K, and not at the
    # bottom left, where A's lower whisker at 1K reaches down. The headroom
    # above C is opened up and the legend placed there, clear of both.
    ax.set_ylim(top=max(cm) * 6.0)
    ax.legend(frameon=False, loc="upper center", fontsize=6.4,
              handlelength=1.8, ncol=1, borderaxespad=0.3)
    save(fig, out_dir, "fig5_scale")
    print(f"    A: {[round(v,3) for v in am]}   C: {[round(v,3) for v in cm]}  (C@1K from {c_1k_file})")


# ---------------------------------------------------------------------------
# Fig. 6 -- the six B mechanisms
# ---------------------------------------------------------------------------
def fig_b_mechanisms(results, out_dir):
    rows = load(results, "b_10k")
    groups, timeouts = {}, {}
    for r in rows:
        sc = r["scenario"]
        if sc == "b6":
            continue
        key = sc.upper()
        if sc == "b3":
            key = "B3\nstrict" if not r["extra"].get("relaxed_order") else "B3\nrelaxed"
        if r["confirmed_contained"] is False:
            timeouts[key] = timeouts.get(key, 0) + 1
            continue
        if r["leak_window_s"] is not None:
            groups.setdefault(key, []).append(r["leak_window_s"])

    order = ["B1", "B2", "B3\nstrict", "B3\nrelaxed", "B4", "B5"]
    vals = [st.median(groups[k]) if groups.get(k) else None for k in order]
    is_timeout = [(k in timeouts and not groups.get(k)) for k in order]

    # A window of zero means "closed before the first check could see it",
    # i.e. below the 10 ms poll resolution -- it is the FASTEST case, not a
    # missing one. An earlier version drew those bars at the timeout height,
    # which made the two quickest mechanisms look like the two worst.
    FLOOR, TIMEOUT_H = 0.004, 30.0
    heights, colors, notes = [], [], []
    for v, t in zip(vals, is_timeout):
        if t:
            heights.append(TIMEOUT_H); colors.append("0.55"); notes.append("\u226530 s")
        elif v is None or v < 0.01:
            heights.append(FLOOR); colors.append(C_ALT); notes.append("\u2264 10 ms")
        else:
            heights.append(v); colors.append(C_ALT); notes.append(f"{v:.2f} s" if v >= 1 else f"{v*1000:.0f} ms")

    fig, ax = plt.subplots(figsize=(COL_W, 2.6))
    bars = ax.bar(range(len(order)), heights, color=colors, width=0.62,
                  hatch=["///" if t else "" for t in is_timeout], edgecolor="white", linewidth=0.4)
    ax.set_yscale("log")
    ax.set_ylim(FLOOR * 0.7, 90)
    for b, n in zip(bars, notes):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() * 1.15, n,
                ha="center", va="bottom", fontsize=6.2)
    ax.axhline(0.01, color="0.45", linestyle=":", linewidth=0.9)
    # The label used to sit at the right-hand end of the line, on top of B4's
    # bar and hard against the figure edge, in a grey light enough to read as
    # part of the gridline. It now sits in the clear gap between B1 and B2,
    # dark enough to read, on an opaque patch so the dotted line does not
    # run through the letters.
    # There is no clear space inside the axes: the bars are wide enough that
    # every gap between them is narrower than the words. Putting the label
    # over a bar in pale grey, as an earlier version did, made it read as part
    # of the gridline. It goes in the margin instead, level with the line it
    # names, which is where a reader looks for an axis annotation anyway.
    ax.annotate("poll resolution", xy=(len(order) - 0.4, 0.01),
                xytext=(6, 0), textcoords="offset points",
                fontsize=6.0, color="0.25", ha="left", va="center",
                annotation_clip=False)
    ax.set_xticks(range(len(order))); ax.set_xticklabels(order)
    ax.set_ylabel("Leak Window (s)")
    # save() writes with the figure's own bounding box rather than a tight
    # one, so anything drawn outside the axes is clipped unless room is
    # reserved for it here.
    fig.subplots_adjust(right=0.78)
    save(fig, out_dir, "fig6_b_mechanisms")
    print(f"    medians: {[None if v is None else round(v,4) for v in vals]}  timeouts: {timeouts}")


# ---------------------------------------------------------------------------
# Fig. 7 -- the ablation: what retry contributes and what the ledger adds.
# Three bars per panel rather than two, because the interesting comparison is
# not bridge-vs-nothing but A / retry-only / retry-plus-ledger.
# ---------------------------------------------------------------------------
def fig_ablation(results, out_dir):
    """Four bars, not two: A / retry only / retry + log table / retry + ledger.
    The question the figure answers is what each added mechanism buys and
    what it costs, so every step of the ladder has to be on the axis."""
    # Prefer the 20-seed grid; fall back to the original five-seed files.
    try:
        on, off = load(results, "faulty20_ledger"), load(results, "faulty20_none")
        logt = load(results, "faulty20_log")
        try:
            noretry = load(results, "faulty20_none-noretry")
        except MissingResults:
            noretry = None
        nseed = 20
    except MissingResults:
        on, off = load(results, "ablation_ledger_on"), load(results, "ablation_ledger_off")
        try:
            logt = load(results, "ablation_logtable")
        except MissingResults:
            logt = None
        nseed = 5

    def a_side(rows):
        w, t = drift_windows(rows, "a")
        lat = st.median([r["extra"]["revoke_latency_s"] for r in rows
                         if r["scenario"].startswith("a") and r["layer"] == "relational"])
        return (st.median(w) if w else 0.0), t, lat, len(w) + t

    def c_side(rows):
        w, t = drift_windows(rows, "c")
        return (st.median(w) if w else 0.0), t, timing_medians(rows)["total"], len(w) + t

    # Short tick labels; the legend below the panels spells each one out.
    # The middle column is the whole bridge minus its record: retry AND
    # concurrent propagation AND the per-record lock. Labelling it "retry"
    # would suggest retry was isolated on its own, which it was not.
    cols = [("A", C_A, a_side(on), "n/a", "A: sequential, no retry, no record")]
    if noretry is not None:
        # Concurrency on its own, so that it can be told apart from retry.
        cols.append(("+conc", "#6E7B8B", c_side(noretry), _confirmed(noretry),
                     "+conc: concurrent propagation, still no retry"))
    cols.append(("+retry", C_ALT, c_side(off), _confirmed(off), "+retry: bounded retry added, no record"))
    if logt is not None:
        cols.append(("+log", "#7A5195", c_side(logt), _confirmed(logt), "+log: record in an append-only table"))
    cols.append(("+ledger", C_C, c_side(on), _confirmed(on), "+ledger: record on Hyperledger Fabric"))

    labels = [c[0] for c in cols]; colors = [c[1] for c in cols]
    unbounded = [c[2][1] for c in cols]
    windows = [c[2][0] for c in cols]
    # valid trials = those that closed plus those that timed out; excluded
    # trials appear in neither, so the denominator shrinks with them.
    denom = [c[2][3] for c in cols]
    lats = [c[2][2] for c in cols]
    confirmed = [c[3] for c in cols]
    conf_n = [int(x.split("/")[0]) if x[0].isdigit() else 0 for x in confirmed]   # "n/a" -> 0
    x = np.arange(len(cols))

    # Three panels need the full text-block width; this figure was never one
    # of the four narrow ones. A blanket find-replace done while reverting an
    # unrelated change (v45 -> v46, restoring four OTHER figures from full
    # width back to column width) matched this figsize by coincidence and
    # shrank it too, which crammed three panels into one column's width and
    # produced the overlapping titles and the panel-c ylabel printed
    # diagonally across panel b.
    fig, axes = plt.subplots(1, 3, figsize=(FULL_W, 2.9))
    fig.subplots_adjust(wspace=0.52, bottom=0.34)

    ax = axes[0]
    ax.bar(x, unbounded, color=colors, width=0.62)
    for i, v in enumerate(unbounded):
        ax.text(i, v + 0.45, f"{v}/{denom[i]}", ha="center", fontsize=6.8)
    ax.set_ylim(0, max(unbounded) * 1.35 + 1); ax.set_yticks(range(0, nseed + 1, max(1, nseed // 5)))
    ax.set_ylabel("trials leaking\nwithout bound", fontsize=7)
    ax.set_title("(a) containment", fontsize=8)

    ax = axes[1]
    ax.bar(x - 0.20, windows, 0.38, color=colors, label="window")
    ax.bar(x + 0.20, lats, 0.38, color=colors, alpha=0.45, hatch="///", label="latency")
    ax.set_ylabel("seconds")
    ax.set_title("(b) window and latency", fontsize=8)
    ax.legend(frameon=False, fontsize=6.2, loc="upper left")
    ax.set_ylim(0, max(lats) * 1.3)

    ax = axes[2]
    ax.bar(x, conf_n, color=colors, width=0.62)
    # Each label goes above its own bar. A fixed height put the two 19/20
    # labels at the foot of two tall bars, where they overlapped the bars and
    # each other and ran together as "19/2019/20".
    for i, (v, h) in enumerate(zip(confirmed, conf_n)):
        ax.text(i, h + nseed * 0.035, v, ha="center", va="bottom", fontsize=6.5)
    ax.set_ylim(0, nseed * 1.30); ax.set_yticks(range(0, nseed + 1, max(1, nseed // 5)))
    ax.set_ylabel("trials the record\ncould confirm", fontsize=7)
    ax.set_title("(c) attribution", fontsize=8)

    for ax in axes:
        ax.set_xticks(x)
        # Five ticks in one column-pair width collide at 6.8 pt; rotating
        # slightly is cheaper than shortening the labels into initials.
        ax.set_xticklabels(labels, fontsize=6.0, rotation=20, ha="right")
        ax.tick_params(axis="x", pad=1)
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color=c[1], label=c[4]) for c in cols],
               loc="lower center", ncol=2, frameon=False, fontsize=6.4,
               bbox_to_anchor=(0.5, 0.0))
    save(fig, out_dir, "fig7_ablation")
    for lab, col, (w, t, l, nv), cf, _ in cols:
        print(f"    {lab:<10} unbounded {t}/{nv}  window {w:.3f}  latency {l:.3f}  confirmed {cf}")


def main():
    global FIG_DPI
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="docs/figures")
    ap.add_argument("--fig-dpi", type=int, default=FIG_DPI)
    ap.add_argument("--c-1k", default="c_v29",
                    help="which file provides the C point at 1K in Fig. 5. Default c_v29: it was "
                         "measured with the same chaincode as the 10K and 100K points, whereas "
                         "ac_healthy predates the key-layout fix")
    args = ap.parse_args()
    FIG_DPI = args.fig_dpi
    style()
    out = Path(args.out)
    jobs = [
        ("Fig. 3 -- Leak Window vs BatchTimeout", lambda: fig_batch_timeout(args.results, out)),
        ("Fig. 4 -- sync vs async anchoring",     lambda: fig_sync_async(args.results, out)),
        ("Fig. 5 -- Leak Window vs scale",        lambda: fig_scale(args.results, out, args.c_1k)),
        ("Fig. 6 -- Scenario B mechanisms",       lambda: fig_b_mechanisms(args.results, out)),
        ("Fig. 7 -- ledger ablation",             lambda: fig_ablation(args.results, out)),
    ]
    skipped = []
    for title, fn in jobs:
        print(title)
        try:
            fn()
        except MissingResults as e:
            print(f"  SKIPPED: missing {e}")
            skipped.append(title)
    if skipped:
        print(f"\n{len(skipped)} figure(s) skipped for missing inputs: " + "; ".join(skipped))


if __name__ == "__main__":
    main()
