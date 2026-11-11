"""Turn results.csv into the slide graphs (graphs/*.png) and a summary table (graphs/summary.md)."""
import csv
import math
import os
from collections import defaultdict

import matplotlib.pyplot as plt

SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES = {"AODV": "#2a78d6", "AODV-DR": "#eb6834"}
LABEL = {"AODV": "Standard AODV", "AODV-DR": "AODV-DR (ours)"}

METRICS = [  # column, title, unit, higher-is-better
    ("sos_pdr", "SOS delivery ratio", "%", True),
    ("sos_delay_med_ms", "SOS end-to-end delay (median)", "ms", False),
    ("recovery_med_ms", "Route recovery time (median)", "ms", False),
    ("nrl", "Routing overhead", "control pkts per delivered pkt", False),
]

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
    "text.color": INK, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8,
    "font.size": 11, "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
})


def load():
    groups = defaultdict(list)
    with open("results.csv") as fh:
        for r in csv.DictReader(fh):
            groups[(r["experiment"], r["variant"], int(r["n_nodes"]), float(r["fail_frac"]))].append(r)
    return groups


def stats(rows, col):
    v = [float(r[col]) for r in rows if r[col] not in ("", "nan")]
    if not v:
        return float("nan"), 0.0
    m = sum(v) / len(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / max(len(v) - 1, 1))
    return m, sd / math.sqrt(len(v))


def sweep(groups, exp, xkey, xs, xlabel, title, fname):
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.2))
    for ax, (col, mtitle, unit, _) in zip(axes.flat, METRICS):
        ends = {}
        for v in ("AODV", "AODV-DR"):
            pts = []
            for x in xs:
                key = (exp, v, x, 0.1) if xkey == "n" else (exp, v, 40, x)
                pts.append(stats(groups[key], col))
            xv = [x * 100 if xkey == "f" else x for x in xs]
            m = [p[0] for p in pts]
            e = [p[1] for p in pts]
            ax.fill_between(xv, [a - b for a, b in zip(m, e)], [a + b for a, b in zip(m, e)],
                            color=SERIES[v], alpha=0.12, lw=0)
            ax.plot(xv, m, color=SERIES[v], lw=2, marker="o", ms=8, mec=SURFACE, mew=2, label=LABEL[v])
            ends[v] = (xv[-1], m[-1])
        hi = max(ends, key=lambda k: ends[k][1])
        for v, (x, y) in ends.items():   # nudge the two end labels apart so they never overlap
            ax.annotate(LABEL[v], (x, y), xytext=(8, 7 if v == hi else -7), textcoords="offset points",
                        va="center", fontsize=9, color=INK2)
        if col == "recovery_med_ms":
            ax.set_yscale("log")
            unit += " (log scale)"
        ax.set_title(mtitle)
        ax.set_ylabel(unit)
        ax.set_xlabel(xlabel)
        ax.set_xticks([x * 100 if xkey == "f" else x for x in xs])
        ax.set_xlim(right=ax.get_xlim()[1] + (ax.get_xlim()[1] - ax.get_xlim()[0]) * 0.28)
        if col == "sos_pdr":
            ax.set_ylim(top=100)
    h, l = axes.flat[0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper right", ncol=2, frameon=False)
    fig.suptitle(title, x=0.01, ha="left", fontsize=14, weight="bold")
    fig.text(0.01, 0.005, "Mean of 10 random runs; shaded band = standard error. 200 s, 1 km x 1 km, "
             "250 m radio range, walking speed 0.5-1.5 m/s.", fontsize=8.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.02, 1, 0.95))
    fig.savefig(os.path.join("graphs", fname), dpi=160)
    plt.close(fig)


def ablation(groups):
    order = ["AODV", "+priority only", "+battery-aware only", "+store-carry only", "AODV-DR"]
    names = ["Standard AODV", "+ SOS priority only", "+ battery-aware only", "+ store-and-carry only",
             "AODV-DR (all three)"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2), sharey=True)
    for ax, (col, mtitle, unit, _) in zip(axes, [METRICS[0], METRICS[1], METRICS[2]]):
        vals = [stats(groups[("ablation", v, 40, 0.1)], col) for v in order]
        y = list(range(len(order)))[::-1]
        ax.barh(y, [m for m, _ in vals], height=0.6, color=SERIES["AODV"], edgecolor=SURFACE, linewidth=2)
        for yy, (m, _) in zip(y, vals):
            ax.text(m, yy, f" {m:.1f}" if col == "sos_pdr" else f" {m:.0f}", va="center", fontsize=9.5, color=INK2)
        ax.set_title(mtitle)
        ax.set_xlabel(unit)
        ax.grid(axis="x", color=GRID)
        ax.grid(axis="y", visible=False)
        ax.set_xlim(right=max(m for m, _ in vals) * 1.18)
    axes[0].set_yticks(list(range(len(order)))[::-1], names)
    fig.suptitle("What each enhancement contributes (40 phones, 10 % failures)", x=0.01, ha="left",
                 fontsize=14, weight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(os.path.join("graphs", "fig3_ablation.png"), dpi=160)
    plt.close(fig)


def summary(groups):
    cols = [("sos_pdr", "SOS delivered %"), ("sos_delay_med_ms", "SOS delay median ms"),
            ("sos_delay_ms", "SOS delay mean ms"), ("recovery_med_ms", "Recovery median ms"),
            ("nrl", "Overhead (ctrl/delivered)"), ("all_pdr", "All traffic delivered %"),
            ("battery_deaths", "Phones dead (battery)")]
    lines = ["| Experiment | Protocol | Nodes | Failures | " + " | ".join(c[1] for c in cols) + " |",
             "|" + "---|" * (4 + len(cols))]
    for key in sorted(groups):
        exp, v, n, f = key
        vals = [stats(groups[key], c)[0] for c, _ in cols]
        lines.append(f"| {exp} | {v} | {n} | {f * 100:.0f} % | " +
                     " | ".join(f"{x:.2f}" if c == "nrl" else f"{x:.1f}" for (c, _), x in zip(cols, vals)) + " |")
    with open(os.path.join("graphs", "summary.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    os.makedirs("graphs", exist_ok=True)
    g = load()
    sweep(g, "nodes", "n", [20, 40, 60, 80], "Number of phones", "Effect of network size (10 % of relays fail)",
          "fig1_vs_nodes.png")
    sweep(g, "failures", "f", [0.0, 0.1, 0.2, 0.3], "Relays that lose power (%)", "Effect of node failures (40 phones)",
          "fig2_vs_failures.png")
    ablation(g)
    summary(g)
    print("wrote graphs/fig1_vs_nodes.png, fig2_vs_failures.png, fig3_ablation.png, summary.md")
