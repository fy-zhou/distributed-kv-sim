#!/usr/bin/env python3
"""Build small GitHub-friendly case study from server-side replay traces.

The output preserves top request discrepancies and all candidate-node scores
for those requests, not full traces. Keeps it inspectable and <1 MiB.
"""
import argparse
import csv
import hashlib
import json
import pathlib


def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for piece in iter(lambda:f.read(65536),b""):
            h.update(piece)
    return h.hexdigest()


def read(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write(path, rows):
    if not rows:
        raise ValueError("empty rows")
    with path.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def archive(source, destination):
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("archive already exists and is not empty")
    names=["regression","improvement"]
    config=[]
    for case in names:
        path=source/case
        for file in ("summary.json","sample_requests.csv","sample_decisions.csv",
                     "paired_requests.csv","greedy_decisions.csv",
                     "aware_decisions.csv","greedy_cache.csv","aware_cache.csv"):
            if not (path/file).is_file():
                raise ValueError("missing "+str(path/file))
        config.append(json.loads((path/"summary.json").read_text()))
    destination.mkdir(parents=True,exist_ok=True)
    report=["# Hotspot request-level diagnostic case study","",
            "**Synthetic discrete-event simulation. Post-hoc selected extremes; "
            "not representative and not proof of causality.**","",
            "| Case | Seed | NIC | Fabric | Cache | Burst | Changed placements | P95 change |",
            "|:--|--:|--:|--:|--:|--:|--:|--:|"]
    hashes={}
    for name,row in zip(names,config):
        report.append("| %s | %d | %g | %g | %d | %d | %d/%d | %+.2f%% |" %
            (name,row["seed"],row["nic_gib_s"],row["fabric_gib_s"],
             row["cache_blocks"],row["burst_width"],row["placement_changes"],
             row["request_count"],row["p95_improvement_pct"]))
        for label in ("sample_requests","sample_decisions"):
            original=source/name/(label+".csv")
            dest=destination/(name+"_"+label+".csv")
            data=read(original)
            write(dest,data)
        for key in ("paired_requests.csv","greedy_decisions.csv",
                    "aware_decisions.csv","greedy_cache.csv","aware_cache.csv"):
            p=source/name/key
            hashes[name+"/"+key]=digest(p)
    report.extend(["","## Queue/network/compute decomposition","",
        "| Case | Policy | Mean queue ms | Mean network ms | Mean H2D ms | Mean compute ms | Source hot prefix (mean blocks) |",
        "|:--|:--|--:|--:|--:|--:|--:|"])
    for name,row in zip(names,config):
        for policy,short in (("Greedy","greedy"),("Bandwidth-aware","aware")):
            report.append("| %s | %s | %.2f | %.2f | %.2f | %.2f | %.1f |" %
                (name,policy,row[short+"_mean_queue_ms"],
                 row[short+"_mean_network_ms"],
                 row[short+"_mean_h2d_ms"],
                 row[short+"_mean_compute_ms"],
                 row[short+"_source_hot_mean_blocks"]))
    report.extend(["","## Inspectable examples","",
        "- Regression: [requests](regression_sample_requests.csv) and "
        "[per-candidate scheduler scores](regression_sample_decisions.csv)",
        "- Improvement: [requests](improvement_sample_requests.csv) and "
        "[per-candidate scheduler scores](improvement_sample_decisions.csv)",
        "",
        "Samples include requests with largest absolute TTFT differences and "
        "the earliest changed placements. Because policies evolve different "
        "cache and queue states after divergence, their decision scores are "
        "not evaluated on identical runtime states.",
        "",
        "## Limitations","",
        "Selection is based on the already observed extreme outcomes. "
        "Queue, network, compute and cache trajectories are descriptive; "
        "do not infer a causal mechanism without additional controlled ablations. "
        "All times are model-generated, not actual GPU/NIC measurements.",
        ""])
    (destination/"README.md").write_text("\n".join(report))
    (destination/"case_summaries.json").write_text(
        json.dumps(config,indent=2)+"\n")
    (destination/"manifest.json").write_text(json.dumps({
        "synthetic":True,
        "source_trace_sha256":hashes,
        "cases":[r["case"] for r in config],
        "note":"Full request and decision traces are kept server-side; "
               "GitHub holds small selected samples only.",
    },indent=2)+"\n")
    size=sum(p.stat().st_size for p in destination.iterdir() if p.is_file())
    if size>1024*1024:
        raise RuntimeError("archive too large: %d bytes" % size)
    print("Created:",destination,"%.1f KiB" % (size/1024))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",type=pathlib.Path,required=True)
    p.add_argument("--destination",type=pathlib.Path,required=True)
    a=p.parse_args()
    archive(a.source,a.destination)


if __name__=="__main__":
    main()
