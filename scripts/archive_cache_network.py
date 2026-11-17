#!/usr/bin/env python3
"""Compactly archive paired NIC/fabric/cache diagnostics to a Git folder."""
import argparse
import csv
import hashlib
import json
import pathlib
import statistics


KEEP = ("seed","nic_gib_s","fabric_gib_s","cache_blocks","burst_width",
        "weight","p95_improvement_pct","traffic_reduction_pct",
        "changed_placements","greedy_p95_ms","aware_p95_ms",
        "greedy_source_hot_final_blocks","aware_source_hot_final_blocks",
        "greedy_source_hot_mean_blocks","aware_source_hot_mean_blocks",
        "greedy_source_evictions","aware_source_evictions")


def read(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(65536),b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",type=pathlib.Path,required=True)
    p.add_argument("--destination",type=pathlib.Path,required=True)
    args=p.parse_args()
    if args.destination.exists() and any(args.destination.iterdir()):
        p.error("destination is not empty")
    raw=args.source/"diagnostics.csv"
    config=args.source/"config.json"
    if not raw.is_file() or not config.is_file():
        p.error("missing diagnostics.csv or config.json")
    rows=read(raw)
    if not rows:
        p.error("empty diagnostics")
    if any(k not in rows[0] for k in KEEP):
        p.error("diagnostics.csv missing columns")
    args.destination.mkdir(parents=True,exist_ok=True)
    with (args.destination/"compact.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=KEEP)
        w.writeheader()
        for row in rows:
            w.writerow({k:row[k] for k in KEEP})
    with config.open() as f:
        cfg=json.load(f)
    cfg.pop("out",None)
    manifest={
        "simulation_only":True,
        "parameter_config":cfg,
        "source_sha256":{"diagnostics.csv":sha(raw),"config.json":sha(config)},
        "comparison_rows":len(rows),
    }
    (args.destination/"manifest.json").write_text(
        json.dumps(manifest,indent=2)+"\n")
    nonzero=[r for r in rows if float(r["weight"])>0]
    beneficial=sum(float(r["p95_improvement_pct"])>1e-7 for r in nonzero)
    neutral=sum(abs(float(r["p95_improvement_pct"]))<=1e-7 for r in nonzero)
    harmful=len(nonzero)-beneficial-neutral
    report=[
        "# NIC × Fabric × Cache: diagnostic archive","",
        "**Synthetic simulation only.** No hardware-level latency claims.","",
        "Paired experiments: %d; nonzero externality weight: %d." %
        (len(rows),len(nonzero)),
        "P95 beneficial / neutral / harmful: %d / %d / %d." %
        (beneficial,neutral,harmful),
        "",
        "| Cache blocks | Comparisons | Mean P95 improvement | Mean source hot-prefix retention delta |",
        "|---:|---:|---:|---:|",
    ]
    for capacity in sorted({int(r["cache_blocks"]) for r in nonzero}):
        selected=[r for r in nonzero if int(r["cache_blocks"])==capacity]
        gain=statistics.mean(float(r["p95_improvement_pct"]) for r in selected)
        retention=statistics.mean(
            float(r["aware_source_hot_mean_blocks"])-
            float(r["greedy_source_hot_mean_blocks"]) for r in selected)
        report.append("| %d | %d | %.2f%% | %.2f blocks |"%
                      (capacity,len(selected),gain,retention))
    report+=["","[Inspect compact CSV](compact.csv)",
             " | [Manifest and source hashes](manifest.json)","",
             "Configuration columns are paired on identical seed and workload. "
             "Retained source-prefix blocks are observational metrics, not "
             "proof of causal mechanisms."]
    (args.destination/"README.md").write_text("\n".join(report)+"\n")
    size=sum(p.stat().st_size for p in args.destination.iterdir())
    if size>1024*1024:
        raise RuntimeError("archive exceeds 1 MiB limit")
    print("Archive %s (%0.1f KiB), beneficial/neutral/harmful=%d/%d/%d"%
          (args.destination,size/1024,beneficial,neutral,harmful))


if __name__=="__main__":
    main()
