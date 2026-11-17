#!/usr/bin/env python3
"""Archive compact, inspectable simulator results in the Git repository.

No requests, decision traces, host credentials or large raw traces are copied.
Only derived summary tables, a human-readable report, and reproducibility
parameters are retained. Python standard library only, >=3.8.
"""
import argparse
import csv
import datetime
import hashlib
import json
import pathlib
import statistics
import subprocess


RUN_FIELDS = [
    "seed", "fabric_gib_s", "burst_width", "cache_blocks", "lambda",
    "p95_improvement_pct", "network_reduction_pct", "placement_changes",
    "greedy_p95_ms", "aware_p95_ms",
]
AGG_FIELDS = [
    "fabric_gib_s", "burst_width", "cache_blocks", "lambda",
    "seed_count", "p95_improvement_mean_pct", "p95_improvement_min_pct",
    "p95_improvement_max_pct", "p95_positive_seeds",
    "network_reduction_mean_pct", "placement_change_mean_pct",
]
MAX_ARCHIVE_BYTES = 1024 * 1024


def read_csv(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_reduced(rows, fields, path):
    missing = [name for name in fields if name not in rows[0]]
    if missing:
        raise ValueError("CSV missing expected fields: " + ", ".join(missing))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row[name] for name in fields})


def number(x):
    return float(x)


def summarize(rows, summary):
    nonzero = [r for r in rows if number(r["lambda"]) != 0]
    improvements = [number(r["p95_improvement_pct"]) for r in nonzero]
    eps = 1e-7
    pos = sum(x > eps for x in improvements)
    neg = sum(x < -eps for x in improvements)
    zero = len(improvements) - pos - neg
    breakdown = []
    for weight in sorted({number(r["lambda"]) for r in nonzero}):
        xs = [number(r["p95_improvement_pct"]) for r in nonzero
              if number(r["lambda"]) == weight]
        breakdown.append("| %g | %d | %.3f | %d | %d | %d |" % (
            weight, len(xs), statistics.mean(xs),
            sum(x > eps for x in xs),
            sum(abs(x) <= eps for x in xs),
            sum(x < -eps for x in xs)))
    counts = sorted({int(float(r["seed_count"])) for r in summary})
    return pos, zero, neg, breakdown, counts


def git_head():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL,
            universal_newlines=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument("--destination", required=True, type=pathlib.Path)
    parser.add_argument("--max-kib", type=int, default=1024)
    args = parser.parse_args()
    if args.destination.exists() and any(args.destination.iterdir()):
        parser.error("destination already contains files; choose a new tag")
    files = {name: args.source / name for name in
             ("runs.csv", "aggregated.csv", "config.json")}
    for name, path in files.items():
        if not path.is_file():
            parser.error("source file not found: " + str(path))
    runs = read_csv(files["runs.csv"])
    agg = read_csv(files["aggregated.csv"])
    if not runs or not agg:
        parser.error("empty run/aggregate input")
    with files["config.json"].open() as f:
        cfg = json.load(f)
    args.destination.mkdir(parents=True, exist_ok=True)
    paired_path = args.destination / "paired_compact.csv"
    aggregate_path = args.destination / "aggregated_compact.csv"
    copy_reduced(runs, RUN_FIELDS, paired_path)
    copy_reduced(agg, AGG_FIELDS, aggregate_path)
    pos, zero, neg, breakdown, counts = summarize(runs, agg)
    manifest = {
        "kind": "synthetic_hotspot_robustness",
        "source_sha256": {name: digest(path) for name, path in files.items()},
        "git_code_commit": git_head(),
        "parameters": {
            key: val for key, val in cfg.items()
            if key not in ("out", "path", "source", "destination")},
        "rows": len(runs),
        "aggregate_rows": len(agg),
        "note": "Simulation uses illustrative GPU compute and fluid network. "
                "Not measured hardware results.",
    }
    (args.destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    report = [
        "# Hotspot robustness experiment",
        "",
        "**Source:** Synthetic discrete-event simulation; not measured GPU/NIC results.",
        "",
        "## Coverage",
        "",
        "- Paired policy comparisons: %d" % len(runs),
        "- Aggregated parameter combinations: %d" % len(agg),
        "- Seed counts per aggregate: %s" % ", ".join(map(str, counts)),
        "- Nonzero-lambda comparisons: %d" % (pos + zero + neg),
        "",
        "## P95 TTFT outcome by scenario",
        "",
        "- Improvement: **%d** (%.1f%%)" % (
            pos, 100*pos/max(1,pos+zero+neg)),
        "- No material difference (absolute delta <= 1e-7%%): **%d**" % zero,
        "- Regression: **%d**" % neg,
        "",
        "## Dependence on externality weight",
        "",
        "| Lambda | Comparisons | Mean P95 improvement (%) | Positive | Neutral | Negative |",
        "|---:|---:|---:|---:|---:|---:|",
    ] + breakdown + [
        "",
        "All percent improvements use each scenario's Greedy baseline. "
        "Different lambda settings on the same seed are correlated, so "
        "the comparisons are not independent replicates.",
        "",
        "## Inspectable files",
        "",
        "- [aggregated_compact.csv](aggregated_compact.csv): grouped summary across seeds.",
        "- [paired_compact.csv](paired_compact.csv): compact paired results for each seed.",
        "- [manifest.json](manifest.json): source hashes, source-code commit, parameters.",
        "",
        "Omitted intentionally: per-request execution traces and large raw result files.",
        "Source hashes allow verifying the retained server-side raw results.",
        "",
    ]
    (args.destination / "README.md").write_text("\n".join(report))
    size = sum(p.stat().st_size for p in args.destination.iterdir()
               if p.is_file())
    cap = min(MAX_ARCHIVE_BYTES, args.max_kib * 1024)
    if size > cap:
        raise RuntimeError("archive %d bytes exceeds configured limit %d"
                           % (size, cap))
    print("Archive: %s" % args.destination)
    print("Files: %d; total: %.1f KiB" % (
        len([p for p in args.destination.iterdir() if p.is_file()]),
        size / 1024))
    print("Positive/neutral/negative (nonzero lambda): %d/%d/%d" %
          (pos, zero, neg))
    print("Ready to stage and commit explicitly with git.")


if __name__ == "__main__":
    main()
