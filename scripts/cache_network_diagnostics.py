#!/usr/bin/env python3
"""Factorial diagnostic: separate NIC vs fabric bottlenecks and cache cliffs.

Same request trace and seeded caches for each paired policy within a scenario.
Simulated network and compute; not physical NIC or GPU measurements.
"""
import argparse
import csv
import itertools
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kv_sim.core import Config, NodeSpec, Simulator
from kv_sim.workload.hotspot import make_hotspot_workload, make_hotspot_initial_caches


def write(path, rows):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def experiment(specs, requests, seeds, hot, fabric, weight, policy):
    sim = Simulator(specs, Config(
        policy=policy, fabric_gib_s=fabric,
        network_externality_weight=weight))
    for node, blocks in seeds.items():
        sim.seed_cache(node, blocks)
    sim.track_prefix(hot)
    sim.run(requests)
    summary = sim.summary()
    by_node = {i: [r for r in sim.cache_trace if r["node"] == i]
               for i in sim.nodes}
    source_trajectory = by_node[0]
    return sim, {
        "policy": policy,
        "p95_ms": summary["p95_ttft_ms"],
        "p50_ms": summary["p50_ttft_ms"],
        "traffic_gib": summary["network_gib"],
        "total_evictions": summary["evictions"],
        "source_hot_final_blocks": source_trajectory[-1][
            "hot_prefix_resident_blocks"],
        "source_hot_mean_blocks": statistics.mean(
            r["hot_prefix_resident_blocks"] for r in source_trajectory),
        "source_evictions": sim.nodes[0].cache.evictions,
        "total_queue_ms": sum(r["queue_ms"] for r in sim.rows),
        "total_network_ms": sum(r["network_ms"] for r in sim.rows),
        "total_h2d_ms": sum(r["h2d_ms"] for r in sim.rows),
        "total_compute_ms": sum(r["compute_ms"] for r in sim.rows),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seeds", nargs="+", type=int, default=[41,42,43,44,45])
    p.add_argument("--nics", nargs="+", type=float, default=[1,2,4,8])
    p.add_argument("--fabrics", nargs="+", type=float, default=[1,2,4,8,16])
    p.add_argument("--cache-blocks", nargs="+", type=int, default=[256,512])
    p.add_argument("--bursts", nargs="+", type=int, default=[1,8])
    p.add_argument("--lambdas", nargs="+", type=float, default=[0,1])
    p.add_argument("--requests", type=int, default=240)
    p.add_argument("--shared-blocks", type=int, default=256)
    p.add_argument("--nodes", type=int, default=4)
    p.add_argument("--burst-gap-ms", type=float, default=2.)
    p.add_argument("--out", type=pathlib.Path,
                   default=pathlib.Path("results/cache-network-diagnostics"))
    args=p.parse_args()
    if args.nodes < 2 or args.requests < 1:
        p.error("nodes >= 2 and requests >=1 required")
    if min(args.cache_blocks) < args.shared_blocks:
        p.error("source cache capacity below hot prefix")
    if min(args.nics+args.fabrics) <= 0:
        p.error("bandwidths must be positive")
    if min(args.lambdas) < 0:
        p.error("lambda must be >= 0")
    args.out.mkdir(parents=True, exist_ok=True)
    records = []
    source_traces = []
    configs = itertools.product(args.seeds,args.nics,args.fabrics,
                                args.cache_blocks,args.bursts)
    for index,(seed,nic,fabric,capacity,burst) in enumerate(configs,1):
        specs=[NodeSpec(i,capacity,nic) for i in range(args.nodes)]
        req,hot=make_hotspot_workload(
            count=args.requests,shared_blocks=args.shared_blocks,
            burst_width=burst,burst_gap_ms=args.burst_gap_ms,seed=seed)
        cold=make_hotspot_initial_caches(specs,hot,seed=seed)
        greedy,gs=experiment(specs,req,cold,hot,fabric,0,"greedy")
        for weight in args.lambdas:
            aware,bs=experiment(specs,req,cold,hot,fabric,weight,
                                "bandwidth_aware")
            left={x["request_id"]: x["node"] for x in greedy.rows}
            right={x["request_id"]: x["node"] for x in aware.rows}
            changed=sum(left[k] != right[k] for k in left)
            if weight == 0 and (changed or
                    abs(gs["p95_ms"]-bs["p95_ms"]) > 1e-7):
                raise AssertionError("lambda=0 differs from Greedy")
            records.append(dict(seed=seed,nic_gib_s=nic,fabric_gib_s=fabric,
                cache_blocks=capacity,burst_width=burst,weight=weight,
                changed_placements=changed,
                p95_improvement_pct=100*(1-bs["p95_ms"]/gs["p95_ms"]),
                traffic_reduction_pct=100*(1-bs["traffic_gib"]/gs["traffic_gib"])
                if gs["traffic_gib"] else 0,
                **{"greedy_"+k:v for k,v in gs.items() if k!="policy"},
                **{"aware_"+k:v for k,v in bs.items() if k!="policy"}))
            # Preserve one worst-ish and one favorable trace, not every event
            # across the full matrix; record only head and tail of source.
            if index == 1:
                for policy,sim in (("greedy",greedy),
                                   ("bandwidth_aware",aware)):
                    for item in sim.cache_trace:
                        if item["node"] == 0:
                            source_traces.append(dict(
                                policy=policy,weight=weight,**item))
        if index % 20 == 0:
            print("Completed",index,"scenarios",flush=True)
    write(args.out/"diagnostics.csv",records)
    if source_traces:
        write(args.out/"source_cache_trace_example.csv",source_traces)
    (args.out/"config.json").write_text(json.dumps(
        dict(vars(args),out=str(args.out),
             warning="Synthetic simulation. Cache tracing is observational."),
        indent=2))
    print("Rows:",len(records),"Saved:",args.out.resolve())


if __name__=="__main__":
    main()
