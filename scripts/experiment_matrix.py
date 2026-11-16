#!/usr/bin/env python3
"""Run deterministic admission/bandwidth sweep; output experiment matrix CSV.

Runs against model profiles in scripts/run.py, not actual inference measurements.
"""
import argparse
import csv
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kv_sim.core import Config, NodeSpec, run_policy
from kv_sim.workload import make_workload, make_initial_caches


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="results/v02-matrix")
    p.add_argument("--requests", type=int, default=300)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--nodes", type=int, default=4)
    p.add_argument("--cache-blocks", type=int, default=256)
    p.add_argument("--nic-gib-s", type=float, default=12.0)
    p.add_argument("--bandwidths", nargs="+", type=float,
                   default=[1.0, 4.0, 12.0, 24.0])
    p.add_argument("--arrivals", nargs="+", type=float,
                   default=[2.0, 6.0, 12.0])
    args = p.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    specs = [NodeSpec(i, args.cache_blocks, args.nic_gib_s)
             for i in range(args.nodes)]
    records = []
    for arrival in args.arrivals:
        requests, prefixes = make_workload(
            count=args.requests, seed=args.seed, arrival_ms=arrival)
        seeds = make_initial_caches(specs, prefixes, args.seed, 1.0)
        for bw, policy, admission in itertools.product(
                args.bandwidths,
                ("round_robin", "shortest_queue", "max_hit", "greedy"),
                ("always", "no_remote")):
            cfg = Config(
                fabric_gib_s=bw, policy=policy, admission=admission)
            sim = run_policy(specs, requests, seeds, cfg)
            record = dict(arrival_ms=arrival, fabric_gib_s=bw,
                          **sim.summary())
            records.append(record)
            print("arrival=%5.1f fabric=%5.1f policy=%-15s admission=%-9s "
                  "p95=%8.2f traffic=%6.3f GiB" % (
                      arrival, bw, policy, admission,
                      record["p95_ttft_ms"], record["network_gib"]))
    with (out / "matrix.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print("Saved", out / "matrix.csv")


if __name__ == "__main__":
    main()
