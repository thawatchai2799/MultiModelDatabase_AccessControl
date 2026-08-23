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
    fig, ax = blank((FULL_W, 2.7))
    ax.set_xlim(-0.01, 1.01); ax.set_ylim(-0.02, 1.02)

    box(ax, 0.00, 0.62, 0.135, 0.17, "revoking\nclient", INK, fontsize=7)
    box(ax, 0.205, 0.545, 0.285, 0.32,
        "Bridge\n\nfail-closed anchor\nconcurrent propagation\n3\u00d7 retry, per-record lock",
        BLUE, lw=1.4, fontsize=6.6)
    box(ax, 0.575, 0.60, 0.29, 0.21,
        "Hyperledger Fabric\nAccessLedger chaincode", ORANGE, lw=1.3, fontsize=7)

    names = [("PostgreSQL", "row + ACL array"), ("MongoDB", "document + acl"), ("Qdrant", "vector + payload")]
    sx, sw, sy, sh = [0.375, 0.590, 0.805], 0.180, 0.175, 0.20
    for x, (n, d) in zip(sx, names):
        box(ax, x, sy, sw, sh, f"{n}\n{d}", GREEN, fontsize=6.8)

    box(ax, 0.00, 0.155, 0.26, 0.29,
        "Ground-truth poller\n\none thread per store,\nits own client,\npolling before the revoke",
        GREY, lw=1.3, dashed=True, fill="#F7F7F7", fontsize=6.6)

    arrow(ax, (0.135, 0.705), (0.205, 0.705), INK, label="revoke", ly=0.078, fontsize=6.4)
    arrow(ax, (0.490, 0.755), (0.575, 0.720), ORANGE, lw=1.1)
    ax.text(0.505, 0.925, "1. anchor event before any store", ha="center", va="center",
            fontsize=6.4, color=ORANGE)

    arrow(ax, (0.348, 0.545), (0.440, 0.375), BLUE, lw=1.1)
    ax.text(0.168, 0.478, "2. propagate\nconcurrently (\u00d73)", ha="center", va="center",
            fontsize=6.3, color=BLUE, linespacing=1.3)

    arrow(ax, (0.700, 0.375), (0.700, 0.600), ORANGE, lw=1.0, dashed=True)
    ax.text(0.885, 0.500, "3. anchor propagation\nafter containment (\u00d73)", ha="center", va="center",
            fontsize=6.3, color=ORANGE, linespacing=1.3)

    # The observer reaches every store on its own path, routed below the row
    # so the three connections stay visibly separate rather than overlapping.
    BUS = 0.085
    ax.plot([0.130, 0.895], [BUS, BUS], linestyle=(0, (3, 2)), color=GREY, lw=0.9, zorder=1)
    arrow(ax, (0.130, 0.155), (0.130, BUS), GREY, style_="-", lw=0.9, dashed=True)
    for x in sx:
        arrow(ax, (x + sw / 2, BUS), (x + sw / 2, sy), GREY, style_="<|-|>", lw=0.9, dashed=True)
    ax.text(0.512, 0.012, "direct queries \u2014 never through the bridge or the ledger",
            fontsize=6.4, color=GREY, ha="center", style="italic")

    save(fig, out_dir, "fig_architecture")


# ---------------------------------------------------------------------------
# Key layout. Two panels, before and after, with the measured consequence
# printed under each -- the numbers are the argument, not the boxes.
# ---------------------------------------------------------------------------
def fig_key_layout(out_dir):
    fig, axes = plt.subplots(1, 2, figsize=(FULL_W, 2.5))
    for ax in axes:
        ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")

    RED = "#C1121F"
    layers = ["relational", "nosql", "vector"]
    tx = [0.02, 0.355, 0.69]        # transaction boxes
    tw = 0.29

    def caption(ax, lines, color):
        ax.text(0.5, 0.085, lines, ha="center", va="center", fontsize=6.9, color=INK,
                linespacing=1.5,
                bbox=dict(boxstyle="round,pad=0.32", fc="#F7F7F7", ec="0.8", lw=0.6))

    # ---- (a) before: one key, three writers
    ax = axes[0]
    ax.text(0.5, 0.965, "(a) one key per event", ha="center", fontsize=8, fontweight="bold", color=INK)
    for x, l in zip(tx, layers):
        box(ax, x, 0.755, tw, 0.145, f"RecordPropagation\n({l})", INK, fontsize=6.4)
        arrow(ax, (x + tw / 2, 0.755), (0.5, 0.615), RED, lw=1.0)
    box(ax, 0.235, 0.455, 0.53, 0.16, "state key:  eventId", RED, lw=1.5, fill="#FDECEC", fontsize=7)
    ax.text(0.5, 0.335, "three transactions read-modify-write\nthe same key in one block",
            ha="center", va="center", fontsize=6.6, color=RED, linespacing=1.4)
    caption(ax, "15 MVCC conflicts over 5 revokes\nretry budget exhausted (3 of 3)\nrevoke latency 9.47 s", RED)

    # ---- (b) after: one key each, disjoint
    ax = axes[1]
    ax.text(0.5, 0.965, "(b) one key per (event, layer)", ha="center", fontsize=8, fontweight="bold", color=INK)
    for x, l in zip(tx, layers):
        box(ax, x, 0.755, tw, 0.145, f"RecordPropagation\n({l})", INK, fontsize=6.4)
        arrow(ax, (x + tw / 2, 0.755), (x + tw / 2, 0.615), GREEN, lw=1.0)
        box(ax, x, 0.455, tw, 0.16, f"propIdx~\neventId~{l[:4]}", GREEN, fontsize=6.3, fill="#EBF7F2")
    ax.text(0.5, 0.335, "disjoint write sets; the event key is\nread, never written",
            ha="center", va="center", fontsize=6.6, color=GREEN, linespacing=1.4)
    caption(ax, "0 MVCC conflicts\nretry budget free (1 of 3)\nrevoke latency 5.00 s", GREEN)

    fig.subplots_adjust(wspace=0.10)
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
