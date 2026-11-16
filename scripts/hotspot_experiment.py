#!/usr/bin/env python3
"""Network-hotspot evaluation with per-candidate placement audit.

This is a synthetic simulator experiment, not validation of GPU/network latency.
All policies start from identical request/token traces and cache seeds.
"""
import argparse
import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kv_sim.core import Config, NodeSpec, run_policy
from kv_sim.workload.hotspot import (
    make_hotspot_workload, make_hotspot_initial_caches)


def save_rows(path, rows):
    if not rows:
        raise RuntimeError("no rows for " + str(path))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def selected_map(decisions):
    chosen = {}
    for d in decisions:
        if d["selected"]:
            rid = d["request_id"]
            if rid in chosen:
                raise RuntimeError("duplicate placement decision: " + rid)
            chosen[rid] = d
    return chosen


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nodes", type=int, default=4)
    p.add_argument("--requests", type=int, default=240)
    p.add_argument("--cache-blocks", type=int, default=512)
    p.add_argument("--shared-blocks", type=int, default=256)
    p.add_argument("--suffix-blocks", type=int, default=4)
    p.add_argument("--burst-width", type=int, default=8)
    p.add_argument("--burst-gap-ms", type=float, default=2.0)
    p.add_argument("--within-burst-ms", type=float, default=.02)
    p.add_argument("--fabric-gib-s", type=float, default=2.0)
    p.add_argument("--nic-gib-s", type=float, default=2.0)
    p.add_argument("--externality-weight", type=float, default=1.0)
    p.add_argument("--admission", choices=["always", "no_remote"],
                   default="always")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=pathlib.Path,
                   default=pathlib.Path("results/hotspot"))
    args = p.parse_args()
    if args.nodes < 2:
        p.error("at least two nodes required")
    args.out.mkdir(parents=True, exist_ok=True)
    specs = [NodeSpec(i, args.cache_blocks, args.nic_gib_s)
             for i in range(args.nodes)]
    requests, hot_hashes = make_hotspot_workload(
        count=args.requests, shared_blocks=args.shared_blocks,
        suffix_blocks=args.suffix_blocks, burst_width=args.burst_width,
        burst_gap_ms=args.burst_gap_ms,
        within_burst_ms=args.within_burst_ms, seed=args.seed)
    seeds = make_hotspot_initial_caches(specs, hot_hashes)
    decisions, request_rows, summaries, maps = [], [], [], {}
    for policy in ("greedy", "bandwidth_aware"):
        sim = run_policy(specs, requests, seeds, Config(
            policy=policy, admission=args.admission,
            fabric_gib_s=args.fabric_gib_s, trace_decisions=True,
            network_externality_weight=args.externality_weight))
        chosen = selected_map(sim.decisions)
        if len(chosen) != args.requests:
            raise RuntimeError("missing placement decisions")
        maps[policy] = chosen
        summaries.append(sim.summary())
        request_rows += [dict(policy=policy, **r) for r in sim.rows]
        decisions.extend(sim.decisions)
    paired = []
    for req in requests:
        a = maps["greedy"][req.request_id]
        b = maps["bandwidth_aware"][req.request_id]
        paired.append(dict(
            request_id=req.request_id, arrival_ms=req.arrival_ms,
            greedy_node=a["node"], bandwidth_aware_node=b["node"],
            changed=int(a["node"] != b["node"]),
            greedy_selected_estimated_ms=a["estimated_ms"],
            bandwidth_selected_estimated_ms=b["estimated_ms"],
            greedy_selected_externality_ms=a["network_externality_ms"],
            bandwidth_selected_externality_ms=b["network_externality_ms"],
            greedy_selected_active_flows=a["active_network_flows"],
            bandwidth_selected_active_flows=b["active_network_flows"]))
    changed = sum(x["changed"] for x in paired)
    save_rows(args.out / "summary.csv", summaries)
    save_rows(args.out / "requests.csv", request_rows)
    save_rows(args.out / "decisions.csv", decisions)
    save_rows(args.out / "placement_diff.csv", paired)
    with (args.out / "config.json").open("w") as f:
        json.dump(dict(vars(args), out=str(args.out)), f, indent=2)
    print("Requests:", args.requests)
    print("Changed placements:", changed, "(%.2f%%)" % (
        100 * changed / args.requests))
    for row in summaries:
        print("%-16s P50=%9.2f ms P95=%9.2f ms traffic=%7.3f GiB" %
              (row["policy"], row["p50_ttft_ms"],
               row["p95_ttft_ms"], row["network_gib"]))
    print("Results:", args.out.resolve())
    if not changed:
        print("NOTE: no placement difference; do not claim policy benefit.")
    print("CAUTION: illustrative compute model and configured network, not measured.")


if __name__ == "__main__":
    main()
