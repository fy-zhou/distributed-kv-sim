#!/usr/bin/env python3
"""Generate controlled workload; run all policies; export raw and summary CSV."""
import argparse
import csv
import dataclasses
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kv_sim.core import Config, NodeSpec, run_policy
from kv_sim.workload import make_workload, make_initial_caches


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nodes", type=int, default=4)
    p.add_argument("--requests", type=int, default=200)
    p.add_argument("--cache-blocks", type=int, default=256)
    p.add_argument("--initial-occupancy", type=float, default=1.0)
    p.add_argument("--families", type=int, default=8)
    p.add_argument("--shared-blocks", type=int, default=32)
    p.add_argument("--unique-blocks", type=int, default=8)
    p.add_argument("--arrival-ms", type=float, default=12.0)
    p.add_argument("--nic-gib-s", type=float, default=12.0)
    p.add_argument("--fabric-gib-s", type=float, default=24.0)
    p.add_argument("--policy", nargs="+", default=[
        "round_robin", "shortest_queue", "max_hit", "greedy"])
    p.add_argument("--admission", choices=["always", "no_remote"],
                   default="always")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=pathlib.Path,
                   default=pathlib.Path("results/smoke"))
    a = p.parse_args()
    if a.nodes < 1 or a.requests < 1:
        p.error("nodes and requests must be positive")
    a.out.mkdir(parents=True, exist_ok=True)

    specs = [NodeSpec(i, a.cache_blocks, a.nic_gib_s)
             for i in range(a.nodes)]
    reqs, prefixes = make_workload(
        a.requests, a.families, shared_blocks=a.shared_blocks,
        unique_blocks=a.unique_blocks, arrival_ms=a.arrival_ms,
        seed=a.seed)
    seeds = make_initial_caches(
        specs, prefixes, a.seed, a.initial_occupancy)
    summary_rows, request_rows = [], []
    for policy in a.policy:
        cfg = Config(policy=policy, admission=a.admission,
                     fabric_gib_s=a.fabric_gib_s)
        sim = run_policy(specs, reqs, seeds, cfg)
        summary_rows.append(sim.summary())
        request_rows += [dict(policy=policy, admission=a.admission, **r)
                         for r in sim.rows]
        s = sim.summary()
        print("%-17s P50=%8.2f ms P95=%8.2f ms hit=%5.1f%% "
              "network=%6.3f GiB evictions=%d" %
              (policy, s["p50_ttft_ms"], s["p95_ttft_ms"],
               s["hit_ratio"]*100, s["network_gib"], s["evictions"]))
    write_csv(a.out / "summary.csv", summary_rows)
    write_csv(a.out / "requests.csv", request_rows)
    with (a.out / "configuration.json").open("w") as f:
        json.dump(dict(args=dict(vars(a), out=str(a.out)),
                       model_note="Compute curve is illustrative; not yet "
                       "benchmarked against real vLLM GPU execution.",
                       prefix_note="Per-16-token cryptographic chain hash.",
                       network_note="Equal-share endpoint and fabric fluid "
                       "model; no actual RPC or RDMA.",
                       sim_note="One active request per node, sequential "
                       "H2D and Prefill, no decode throughput model."),
                  f, indent=2)
    print("Results:", a.out.resolve())


if __name__ == "__main__":
    main()
