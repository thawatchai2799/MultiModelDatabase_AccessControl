#!/usr/bin/env python3
"""The two schematic figures for the paper: the architecture (with the
ground-truth poller drawn outside the bridge) and the chaincode key layout
before and after the MVCC fix.

Drawn with matplotlib rather than a diagramming tool so that they come out
at exactly the target column widths, in the same fonts and at the same
type sizes as the data figures, and so that regenerating them is one command
rather than an editing session.

Run from mldb/:  python3 docs/make_schematic_figures.py
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

COL_W, FULL_W = 3.5, 7.16
FIG_DPI = 600

INK = "#222222"
BLUE = "#0072B2"     # bridge / control path
ORANGE = "#D55E00"   # ledger
GREEN = "#009E73"    # stores
GREY = "#6E6E6E"     # measurement apparatus


def style():
    plt.rcParams.update({
        "font.size": 7.5, "axes.labelsize": 7.5,
        "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
        "pdf.fonttype": 42, "ps.fonttype": 42,
    })


def save(fig, out_dir, stem):
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{stem}.pdf")
    fig.savefig(out_dir / f"{stem}.png", dpi=FIG_DPI)
    plt.close(fig)
    print(f"  wrote {stem}.pdf and {stem}.png")


def box(ax, x, y, w, h, text, color, *, fill="white", lw=1.1, fontsize=7.5,
        style_="round,pad=0.012,rounding_size=0.02", dashed=False, weight="normal"):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=style_, linewidth=lw,
        edgecolor=color, facecolor=fill, linestyle="--" if dashed else "-", zorder=2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fontsize, color=INK, zorder=3, linespacing=1.35, fontweight=weight)


def arrow(ax, p0, p1, color, *, label=None, style_="-|>", lw=1.0, dashed=False,
          rad=0.0, lx=0, ly=0, fontsize=6.5):
    ax.add_patch(FancyArrowPatch(
        p0, p1, arrowstyle=style_, mutation_scale=8, linewidth=lw, color=color,
        linestyle=(0, (3, 2)) if dashed else "solid",
        connectionstyle=f"arc3,rad={rad}", shrinkA=1.5, shrinkB=1.5, zorder=1))
    if label:
        ax.text((p0[0] + p1[0]) / 2 + lx, (p0[1] + p1[1]) / 2 + ly, label,
                ha="center", va="center", fontsize=fontsize, color=color, zorder=4,
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.92))


def blank(figsize):
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    return fig, ax


# ---------------------------------------------------------------------------
# Architecture. The point the drawing has to make is that the poller touches
# the stores directly and never the bridge or the ledger -- everything else
# is context for that.
# ---------------------------------------------------------------------------
def fig_architecture(out_dir):
    """Scenario C.

    Every arrow stops in clear space before the box it points at. Two things
    made that harder than it looks. Boxes are drawn at zorder 2 and arrows at
    zorder 1, so an arrowhead that reaches a box is painted over by the box's
    white fill and disappears. And FancyBboxPatch adds its pad outside the
    rectangle given to it, so a box whose left edge is nominally at x is drawn
    from x - PAD: an arrow ending at x already overlaps the border.

    Both are handled here rather than by raising the arrows above the boxes,
    which would draw arrowheads on top of borders instead of under them.
    """
    fig, ax = blank((FULL_W, 3.1))
    M = 0.035                       # clear margin on every side
    PAD = 0.012                     # FancyBboxPatch draws this far outside
    GAP = 0.008                     # visible air between an arrowhead and a box
    ax.set_xlim(-M, 1 + M); ax.set_ylim(-M, 1 + M)

    def left(x):    return x - PAD - GAP        # stop short of a box's left edge
    def right(x, w):  return x + w + PAD + GAP
    def top(y, h):  return y + h + PAD + GAP
    def under(y):   return y - PAD - GAP

    # --- row 1: client, bridge, ledger
    cx, cw, cy, ch = 0.005, 0.125, 0.605, 0.185
    bx, bw, by, bh = 0.245, 0.285, 0.515, 0.345
    lx, lw, ly, lh = 0.625, 0.280, 0.585, 0.225
    box(ax, cx, cy, cw, ch, "revoking\nclient", INK, fontsize=7)
    box(ax, bx, by, bw, bh,
        "Bridge\n\nfail-closed anchor\nconcurrent propagation\n3\u00d7 retry, per-record lock",
        BLUE, lw=1.4, fontsize=6.6)
    box(ax, lx, ly, lw, lh,
        "Hyperledger Fabric\nAccessLedger chaincode", ORANGE, lw=1.3, fontsize=7)

    # --- row 2: the three stores
    names = [("PostgreSQL", "row + ACL array"), ("MongoDB", "document + acl"),
             ("Qdrant", "vector + payload")]
    sx, sw, sy, sh = [0.392, 0.608, 0.824], 0.170, 0.170, 0.215
    for x, (n, d) in zip(sx, names):
        box(ax, x, sy, sw, sh, f"{n}\n{d}", GREEN, fontsize=6.8)

    box(ax, 0.005, 0.150, 0.265, 0.290,
        "Ground-truth poller\n\none thread per store,\nits own client,\npolling before the revoke",
        GREY, lw=1.3, dashed=True, fill="#F7F7F7", fontsize=6.6)

    # --- 1: client to bridge, bridge to ledger
    arrow(ax, (right(cx, cw), 0.697), (left(bx), 0.697), INK)
    ax.text((right(cx, cw) + left(bx)) / 2, 0.722, "revoke",
            ha="center", va="bottom", fontsize=6.4, color=INK)
    arrow(ax, (right(bx, bw), 0.715), (left(lx), 0.715), ORANGE, lw=1.1)
    ax.text(0.545, 0.945, "1. anchor event before any store", ha="center", va="center",
            fontsize=6.4, color=ORANGE)

    # --- 2: bridge down to the stores
    arrow(ax, (0.400, under(by)), (0.462, top(sy, sh)), BLUE, lw=1.1)
    ax.text(0.135, 0.500, "2. propagate\nconcurrently (\u00d73)", ha="center", va="center",
            fontsize=6.3, color=BLUE, linespacing=1.3)

    # --- 3: propagation records anchored after the stores have closed
    arrow(ax, (0.693, top(sy, sh)), (0.693, under(ly)), ORANGE, lw=1.0, dashed=True)
    ax.text(0.905, 0.480, "3. anchor propagation\nafter containment (\u00d73)",
            ha="center", va="center", fontsize=6.3, color=ORANGE, linespacing=1.3)

    # --- the observer reaches every store on its own path, routed below the row
    BUS = 0.048
    ax.plot([0.137, 0.912], [BUS, BUS], linestyle=(0, (3, 2)), color=GREY, lw=0.9, zorder=1)
    arrow(ax, (0.137, under(0.150)), (0.137, BUS), GREY, style_="-", lw=0.9, dashed=True)
    for x in sx:
        arrow(ax, (x + sw / 2, BUS), (x + sw / 2, under(sy)),
              GREY, style_="<|-|>", lw=0.9, dashed=True)
    ax.text(0.521, -0.010, "direct queries \u2014 never through the bridge or the ledger",
            fontsize=6.4, color=GREY, ha="center", va="top", style="italic")

    save(fig, out_dir, "fig_architecture")


# ---------------------------------------------------------------------------
# Key layout. Two panels, before and after, with the measured consequence
# printed under each -- the numbers are the argument, not the boxes.
# ---------------------------------------------------------------------------
def fig_key_layout(out_dir):
    """Chaincode key layout, before and after.

    Same three corrections as Figure 1. Boxes keep a margin from the panel
    edge instead of running off it; "RecordPropagation" is set small enough
    to sit inside its box rather than overflowing the border; and every arrow
    stops clear of the box it points at, allowing for the pad that
    FancyBboxPatch draws outside the rectangle it is given.

    The panel is also taller than before, because the three fact lines at the
    foot were being clipped by the figure edge.
    """
    fig, axes = plt.subplots(1, 2, figsize=(FULL_W, 2.9))
    M = 0.04                        # margin inside each panel
    PAD = 0.012                     # FancyBboxPatch draws this far outside
    GAP = 0.010                     # air between an arrowhead and a box
    for ax in axes:
        ax.set_xlim(-M, 1 + M); ax.set_ylim(-M, 1 + M); ax.axis("off")

    RED = "#C1121F"
    layers = ["relational", "nosql", "vector"]
    # "RecordPropagation" is the widest string in the figure and sets the box
    # width: the boxes are sized around it so the word sits inside its border
    # rather than running to the edge of it.
    tx = [0.005, 0.347, 0.689]
    tw = 0.306
    TY, TH = 0.760, 0.150           # transaction row
    KY, KH = 0.450, 0.165           # key row

    def caption(ax, lines):
        ax.text(0.5, 0.075, lines, ha="center", va="center", fontsize=6.9, color=INK,
                linespacing=1.5,
                bbox=dict(boxstyle="round,pad=0.32", fc="#F7F7F7", ec="0.8", lw=0.6))

    # ---- (a) before: one key, three writers
    ax = axes[0]
    ax.text(0.5, 0.985, "(a) one key per event", ha="center", va="top",
            fontsize=8, fontweight="bold", color=INK)
    for x, l in zip(tx, layers):
        box(ax, x, TY, tw, TH, f"RecordPropagation\n({l})", INK, fontsize=5.8)
        arrow(ax, (x + tw / 2, TY - PAD - GAP), (0.5, KY + KH + PAD + GAP), RED, lw=1.0)
    box(ax, 0.230, KY, 0.540, KH, "state key:  eventId", RED, lw=1.5,
        fill="#FDECEC", fontsize=7)
    ax.text(0.5, 0.330, "three transactions read-modify-write\nthe same key in one block",
            ha="center", va="center", fontsize=6.6, color=RED, linespacing=1.4)
    caption(ax, "15 MVCC conflicts over 5 revokes\nretry budget exhausted (3 of 3)\n"
                "revoke latency 9.47 s")

    # ---- (b) after: one key each, disjoint
    ax = axes[1]
    ax.text(0.5, 0.985, "(b) one key per (event, layer)", ha="center", va="top",
            fontsize=8, fontweight="bold", color=INK)
    for x, l in zip(tx, layers):
        box(ax, x, TY, tw, TH, f"RecordPropagation\n({l})", INK, fontsize=5.8)
        arrow(ax, (x + tw / 2, TY - PAD - GAP), (x + tw / 2, KY + KH + PAD + GAP),
              GREEN, lw=1.0)
        box(ax, x, KY, tw, KH, f"propIdx~\neventId~{l[:4]}", GREEN, fontsize=6.3,
            fill="#EBF7F2")
    ax.text(0.5, 0.330, "disjoint write sets; the event key is\nread, never written",
            ha="center", va="center", fontsize=6.6, color=GREEN, linespacing=1.4)
    caption(ax, "0 MVCC conflicts\nretry budget free (1 of 3)\nrevoke latency 5.00 s")

    fig.subplots_adjust(wspace=0.14)
    save(fig, out_dir, "fig_key_layout")


def main():
    global FIG_DPI
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/figures")
    ap.add_argument("--fig-dpi", type=int, default=FIG_DPI)
    args = ap.parse_args()
    FIG_DPI = args.fig_dpi
    style()
    out = Path(args.out)
    print("architecture")
    fig_architecture(out)
    print("chaincode key layout")
    fig_key_layout(out)


if __name__ == "__main__":
    main()
