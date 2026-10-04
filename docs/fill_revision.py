#!/usr/bin/env python3
"""Fill docs/revision_v58_template.md from the ablation_table.py JSON reports.

    python3 docs/fill_revision.py --p30 analysis/paired_p0.30.json \\
        --p10 analysis/paired_p0.10.json --p05 analysis/paired_p0.05.json \\
        --out docs/revision_v58_filled.md

Every {{placeholder}} is resolved from the reports or computed here; the
script refuses to write the output while any placeholder is unresolved, and
prints every IF/ELSE branch it chose, with the value that decided it. No
number is typed: this is the rule that a hard-coded 190 GB once broke.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "docs" / "revision_v58_template.md"

CFG = {"A": "A", "O": "O", "Cnr": "C no retry", "Cr": "C retry", "Clog": "C log", "Cled": "C ledger"}


def f2(x):
    return "—" if x is None else f"{x:.2f}"


def f3(x):
    return "—" if x is None else f"{x:.3f}"


def pval(x):
    """Relation included, so the text reads 'p < 0.001' or 'p = 0.549'."""
    return "< 0.001" if x < 0.001 else f"= {x:.3f}"


def oxford(items):
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def signed(x):
    return "—" if x is None else f"{x:+.2f}"


def load_block(path):
    rep = json.loads(Path(path).read_text())
    ps = list(rep["by_p"])
    if len(ps) != 1:
        raise SystemExit(f"{path}: expected exactly one p in the report, found {ps}")
    return rep, rep["by_p"][ps[0]], ps[0]


def values_for(tag, block, rep):
    v = {}
    cfgs = block["configs"]
    for short, label in CFG.items():
        s = cfgs.get(label)
        if s is None:
            continue
        n, k = s["n"], s["leaked_without_bound"]
        sr = s.get("self_report") or {}
        v.update({
            f"{tag}.{short}.n": str(n), f"{tag}.{short}.k": str(k),
            f"{tag}.{short}.k_closed": str(n - k), f"{tag}.{short}.n_closed": str(n - k),
            f"{tag}.{short}.rate": f2(s["rate"]),
            f"{tag}.{short}.lo": f2(s["wilson_ci95"][0]), f"{tag}.{short}.hi": f2(s["wilson_ci95"][1]),
            f"{tag}.{short}.win": f3(s["drift_window_median_s"]),
            f"{tag}.{short}.winlo": f3(s["drift_window_ci95"][0]), f"{tag}.{short}.winhi": f3(s["drift_window_ci95"][1]),
            f"{tag}.{short}.lat": f3(s["revoke_latency_median_s"]),
            f"{tag}.{short}.btot": f3(s.get("bridge_total_median_s")),
            f"{tag}.{short}.sr_fc": str(sr.get("false_containment", "n/a")),
            f"{tag}.{short}.sr_fnc": str(sr.get("false_non_containment", "n/a")),
            f"{tag}.{short}.sr_att": str(sr.get("attributed", "n/a")),
            f"{tag}.{short}.sr_attable": str(sr.get("attributable", "n/a")),
        })
        d = block["diff_vs_reference"].get(label) or {}
        if d.get("diff") is not None:
            v[f"{tag}.diff.{short}"] = f"{signed(d['diff'])} [{signed(d['lo'])}, {signed(d['hi'])}]"
        pb = (block.get("paired") or {}).get(label)
        if pb:
            v.update({
                f"{tag}.pair.{short}.refonly": str(pb["ref_only_leaked"]),
                f"{tag}.pair.{short}.cmponly": str(pb["cmp_only_leaked"]),
                f"{tag}.pair.{short}.both": str(pb["both_leaked"]),
                f"{tag}.pair.{short}.neither": str(pb["neither_leaked"]),
                f"{tag}.pair.{short}.discordant": str(pb["ref_only_leaked"] + pb["cmp_only_leaked"]),
                f"{tag}.pair.{short}.n_pairs": str(pb["n_pairs"]),
                f"{tag}.pair.{short}.pdiff": signed(pb["paired_diff"]),
                f"{tag}.pair.{short}.p": pval(pb['mcnemar_exact_p']),
            })
    # consecutive rungs
    inv = {l: s for s, l in CFG.items()}
    for pair, pb in (block.get("paired_consecutive") or {}).items():
        a, b = [x.strip() for x in pair.split("→")]
        if a not in inv or b not in inv:
            print(f"  (rung {pair!r} uses a label the template has no short name for; skipped)")
            continue
        sa, sb = inv[a], inv[b]
        key = f"{tag}.rung.{sa}-{sb}"
        v.update({f"{key}.refonly": str(pb["ref_only_leaked"]), f"{key}.cmponly": str(pb["cmp_only_leaked"]),
                  f"{key}.pdiff": signed(pb["paired_diff"]), f"{key}.p": pval(pb['mcnemar_exact_p'])})
        # Newcombe for the rung, from the two configs' counts
        from experiments.ablation_table import newcombe_diff  # noqa: E402
        ca, cb = cfgs[a], cfgs[b]
        d, lo, hi = newcombe_diff(ca["leaked_without_bound"], ca["n"], cb["leaked_without_bound"], cb["n"])
        v[f"{tag}.diff.rung.{sa}-{sb}"] = f"[{signed(lo)}, {signed(hi)}]"
    # layers leaked in A, as prose
    missing = [l for l in CFG.values() if l not in cfgs]
    if missing:
        raise SystemExit(f"{tag}: the report lacks configuration(s) {missing}; the template needs all six "
                         f"({list(CFG.values())}). Run ablation_table.py with every rung, in that order.")
    la = cfgs["A"]["layers_leaked"]
    v[f"{tag}.A.layers"] = ", ".join(f"{l} in {c}" for l, c in sorted(la.items(), key=lambda x: -x[1])) or "none"
    return v


def main():
    sys.path.insert(0, str(ROOT))
    ap = argparse.ArgumentParser()
    ap.add_argument("--p30", required=True)
    ap.add_argument("--p10")
    ap.add_argument("--p05")
    ap.add_argument("--template", default=str(TEMPLATE))
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-missing", action="store_true",
                    help="write the output with unresolved placeholders left in place (for a partial campaign)")
    args = ap.parse_args()

    v = {}
    reports = {}
    for tag, path in (("p30", args.p30), ("p10", args.p10), ("p05", args.p05)):
        if not path:
            continue
        rep, block, p = load_block(path)
        reports[tag] = (rep, block)
        v.update(values_for(tag, block, rep))
    b30 = reports["p30"][1]["configs"]
    v["ledger_extra_s"] = f"{b30['C ledger']['revoke_latency_median_s'] - b30['C log']['revoke_latency_median_s']:.1f}"
    v["log_extra_s"] = f"{b30['C log']['revoke_latency_median_s'] - b30['C retry']['revoke_latency_median_s']:.2f}"
    anomalies = sum(ex.get("anomalous_window", 0) + ex.get("poller_error", 0)
                    for _, blk in reports.values() for ex in blk["excluded"].values())
    v["anomalies"] = str(anomalies)
    # Per-seed placeholders, from the per-trial list ablation_table.py exports.
    cr_leaked = b30["C retry"]["leaked_seeds"]
    v["p30.Cr.leaked_seeds"] = ", ".join(str(x) for x in cr_leaked) if cr_leaked else "none"
    o_win = {t["seed"]: t["drift_window_s"] for t in b30["O"]["trials"]}
    wins = [o_win.get(sd) for sd in cr_leaked]
    if cr_leaked and all(w is not None for w in wins):
        v["p30.O.win_leaked_seeds"] = " and ".join(f"{w:.2f}" for w in wins)
    elif cr_leaked:
        v["p30.O.win_leaked_seeds"] = "[[O did not close on every one of these seeds -- the IF branch is wrong; see ELSE]]"
    else:
        v["p30.O.win_leaked_seeds"] = "(no such seed)"
    ex = []
    for tag, (rep, blk) in reports.items():
        for label, seeds in blk.get("excluded_seeds", {}).items():
            for seed, reason in seeds:
                ex.append(f"{label} seed {seed} at p = {tag[1:].lstrip('0') and '0.' + tag[1:]} ({reason.replace('_', ' ')})")
    v["excluded_seeds"] = "; ".join(ex) if ex else "No trial was excluded."

    text = Path(args.template).read_text()

    # IF / ELSE branches
    def cond(expr):
        m = re.match(r"`([\w.]+) (==|>) (\d+)`", expr)
        if not m:
            raise SystemExit(f"cannot parse condition {expr!r}")
        key, op, rhs = m.groups()
        if key not in v:
            raise SystemExit(f"condition {expr!r} refers to {key}, which is not in the reports")
        try:
            lhs = float(v[key])
        except ValueError:
            raise SystemExit(f"condition {expr!r}: {key} is {v[key]!r}, not a number -- the template may only "
                             f"condition on integer fields (n, k, k_closed, pair.*, rung.*)")
        return (lhs == float(rhs)) if op == "==" else (lhs > float(rhs))

    chosen = []
    # Strict grammar: **IF** `cond`:  <body>  [**ELSE**:  <body>]  **ENDIF**
    # Each block is delimited by its own ENDIF, so one block can never swallow
    # the next (the bug a looser pattern had on the first dry run).
    block = re.compile(r"\*\*IF\*\* (`[^`]+`):\n\n(.*?)(?:\n\n\*\*ELSE\*\*:\n\n(.*?))?\n\n\*\*ENDIF\*\*", re.S)

    def choose(m):
        c = cond(m.group(1))
        body_if, body_else = m.group(2), m.group(3)
        if "**IF**" in body_if or (body_else and "**IF**" in body_else):
            raise SystemExit(f"nested or unterminated IF near {m.group(1)}")
        chosen.append(f"IF {m.group(1)} -> {'IF' if c else 'ELSE'} branch")
        if c:
            return body_if
        return body_else if body_else is not None else "(block dropped: condition false)"
    text = block.sub(choose, text)
    body = text.split("\n---\n", 1)[1] if "\n---\n" in text else text   # skip the legend above the first rule
    if "**IF**" in body or "**ENDIF**" in body:
        raise SystemExit("an IF block in the template did not match the grammar; nothing written")

    unresolved = set()

    def sub(m):
        key = m.group(1)
        if key in v:
            return v[key]
        if "<" in key or key == "...":
            return m.group(0)        # the grammar legend at the top of the template, not a placeholder
        unresolved.add(key)
        return m.group(0)
    text = re.sub(r"\{\{([^}]+)\}\}", sub, text)

    for c in chosen:
        print("  ", c)
    markers = re.findall(r"\[\[[^\]]+\]\]", text)
    for mk in markers:
        print("   NOTE:", mk)
    if unresolved and not args.allow_missing:
        print("UNRESOLVED placeholders -- output not written:")
        for k in sorted(unresolved):
            print("   ", k)
        sys.exit(1)
    Path(args.out).write_text(text)
    print(f"wrote {args.out}; {len(v)} values; {len(unresolved)} unresolved")


if __name__ == "__main__":
    main()
