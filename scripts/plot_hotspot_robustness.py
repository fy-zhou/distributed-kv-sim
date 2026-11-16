#!/usr/bin/env python3
"""Plot cross-seed hotspot robustness summaries (optional matplotlib).

Graphs show empirical seed-to-seed P10-P90 ranges, NOT confidence intervals.
All lines are simulated and are not real vLLM or NIC measurements.
"""
import argparse
import csv
import pathlib
import sys


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=pathlib.Path,
                   default=pathlib.Path("results/hotspot-robustness/aggregated.csv"))
    p.add_argument("--out", type=pathlib.Path, default=None)
    p.add_argument("--cache-blocks", type=int, default=512)
    p.add_argument("--burst-width", type=int, default=8)
    p.add_argument("--lambda-weight", type=float, default=1.)
    p.add_argument("--fabric-gib-s", type=float, default=2.)
    args = p.parse_args()
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise SystemExit("Install matplotlib in an isolated environment to plot; "
                         "CSV experiments do not require it.")
    rows = read(args.input)
    if not rows:
        raise SystemExit("No aggregated rows")
    out = args.out or args.input.parent / "figures"
    out.mkdir(parents=True, exist_ok=True)

    cases = [
        ("p95_improvement_by_bandwidth", "fabric_gib_s",
         "Fabric capacity (GiB/s)", "P95 TTFT improvement (%)",
         {"cache_blocks": args.cache_blocks,
          "burst_width": args.burst_width,
          "lambda": args.lambda_weight}),
        ("p95_improvement_by_burst", "burst_width",
         "Requests per burst", "P95 TTFT improvement (%)",
         {"cache_blocks": args.cache_blocks,
          "fabric_gib_s": args.fabric_gib_s,
          "lambda": args.lambda_weight}),
        ("placement_changes_by_weight", "lambda",
         "Externality weight (lambda)", "Placement changes (%)",
         {"cache_blocks": args.cache_blocks,
          "fabric_gib_s": args.fabric_gib_s,
          "burst_width": args.burst_width}),
    ]
    for filename, xcol, xlabel, ylabel, filters in cases:
        subset = [r for r in rows
                  if all(float(r[k]) == float(v)
                         for k, v in filters.items())]
        subset.sort(key=lambda r: float(r[xcol]))
        if not subset:
            print("Skipping empty plot:", filename, file=sys.stderr)
            continue
        x = [float(r[xcol]) for r in subset]
        if filename == "placement_changes_by_weight":
            y = [float(r["placement_change_mean_pct"]) for r in subset]
        else:
            y = [float(r["p95_improvement_mean_pct"]) for r in subset]
        fig, ax = plt.subplots(figsize=(6.5, 4.2))
        ax.plot(x, y, marker="o")
        if filename != "placement_changes_by_weight":
            low = [float(r["p95_improvement_p10_pct"]) for r in subset]
            high = [float(r["p95_improvement_p90_pct"]) for r in subset]
            ax.fill_between(x, low, high, alpha=.18,
                            label="Across-seed P10–P90 (not CI)")
            ax.axhline(0, linestyle="--", linewidth=1)
            ax.legend(frameon=False)
        ax.set(xlabel=xlabel, ylabel=ylabel,
               title="Synthetic multi-node KV scheduling")
        fig.tight_layout()
        fig.savefig(out / (filename + ".png"), dpi=180)
        fig.savefig(out / (filename + ".svg"))
        plt.close(fig)
        print("Saved:", out / (filename + ".png"))


if __name__ == "__main__":
    main()
