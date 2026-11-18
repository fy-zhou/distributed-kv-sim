#!/usr/bin/env python3
"""Controlled cache admission ablation, paired by workload and initial cache.

Contrasts always vs no_remote admission within each scheduler.  No_remote
avoids caching the entire completed request when its execution fetched remote
KV, rather than adding orphan suffixes. Simulation only; not real NIC/GPU.
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


def write_csv(path, rows):
    if not rows:
        raise ValueError("no rows")
    with path.open("w", newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_case(seed, nic, fabric, cache, burst, admission, policy,
             requests=240, shared_blocks=256, nodes=4, weight=1.0):
    specs=[NodeSpec(i,cache,nic) for i in range(nodes)]
    trace,hot=make_hotspot_workload(
        count=requests,shared_blocks=shared_blocks,burst_width=burst,
        seed=seed,burst_gap_ms=2.0)
    initial=make_hotspot_initial_caches(specs,hot,seed=seed)
    sim=Simulator(specs,Config(
        policy=policy,admission=admission,fabric_gib_s=fabric,
        network_externality_weight=weight))
    for i,hashes in initial.items():
        sim.seed_cache(i,hashes)
    sim.track_prefix(hot)
    sim.run(trace)
    summary=sim.summary()
    src=[r for r in sim.cache_trace if r["node"]==0]
    result=dict(seed=seed,nic_gib_s=nic,fabric_gib_s=fabric,
        cache_blocks=cache,burst_width=burst,admission=admission,policy=policy,
        weight=(weight if policy=="bandwidth_aware" else 0),
        p50_ms=summary["p50_ttft_ms"],p95_ms=summary["p95_ttft_ms"],
        p99_ms=summary["p99_ttft_ms"],
        traffic_gib=summary["network_gib"],evictions=summary["evictions"],
        source_evictions=sim.nodes[0].cache.evictions,
        source_hot_mean_blocks=statistics.mean(
            r["hot_prefix_resident_blocks"] for r in src),
        source_hot_final_blocks=src[-1]["hot_prefix_resident_blocks"],
        mean_queue_ms=statistics.mean(r["queue_ms"] for r in sim.rows),
        mean_network_ms=statistics.mean(r["network_ms"] for r in sim.rows),
        mean_h2d_ms=statistics.mean(r["h2d_ms"] for r in sim.rows),
        mean_compute_ms=statistics.mean(r["compute_ms"] for r in sim.rows),
        remote_request_count=sum(r["remote_hit_tokens"]>0 for r in sim.rows),
        hit_ratio=summary["hit_ratio"])
    return result


def make_pairs(rows):
    by={}
    for r in rows:
        key=(r["case"],r["seed"],r["admission"])
        by.setdefault(key,{})[r["policy"]]=r
    out=[]
    for (case,seed,admission), policies in sorted(by.items()):
        if set(policies)!={"greedy","bandwidth_aware"}:
            raise ValueError("missing paired policy")
        g,a=policies["greedy"],policies["bandwidth_aware"]
        out.append(dict(case=case,seed=seed,admission=admission,
            greedy_p95_ms=g["p95_ms"],aware_p95_ms=a["p95_ms"],
            p95_improvement_pct=100*(1-a["p95_ms"]/g["p95_ms"]),
            greedy_traffic_gib=g["traffic_gib"],
            aware_traffic_gib=a["traffic_gib"],
            greedy_source_hot_mean_blocks=g["source_hot_mean_blocks"],
            aware_source_hot_mean_blocks=a["source_hot_mean_blocks"],
            greedy_mean_queue_ms=g["mean_queue_ms"],
            aware_mean_queue_ms=a["mean_queue_ms"],
            greedy_mean_network_ms=g["mean_network_ms"],
            aware_mean_network_ms=a["mean_network_ms"]))
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",type=pathlib.Path,
                   default=pathlib.Path("experiments/cache-network-diagnostics-20261117"))
    p.add_argument("--seeds",type=int,nargs="+",default=[41,42,43,44,45])
    p.add_argument("--out",type=pathlib.Path,
                   default=pathlib.Path("results/cache-admission-ablation"))
    p.add_argument("--requests",type=int,default=240)
    p.add_argument("--shared-blocks",type=int,default=256)
    args=p.parse_args()
    if args.requests<=0 or args.shared_blocks<=0:
        p.error("requests/shared-blocks must be positive")
    with (args.archive/"manifest.json").open() as f:
        manifest=json.load(f)
    original={float(r["seed"]):r for r in []}
    with (args.archive/"compact.csv").open(newline="") as f:
        candidates=list(csv.DictReader(f))
    if not candidates:
        p.error("empty archived diagnostic")
    def select(stratum,better):
        eligible=[r for r in candidates if
                  float(r["weight"])==1.0 and
                  int(r["cache_blocks"])==stratum[0] and
                  int(r["burst_width"])==stratum[1]]
        return (max if better else min)(
            eligible,key=lambda r:float(r["p95_improvement_pct"]))
    worst=select((256,1),False)
    best=select((512,8),True)
    cases=[
      dict(case="regression",nic=float(worst["nic_gib_s"]),
           fabric=float(worst["fabric_gib_s"]),
           cache=int(worst["cache_blocks"]),burst=int(worst["burst_width"]),
           archived_seed=int(worst["seed"]),archived_greedy_p95=float(worst["greedy_p95_ms"]),
           archived_aware_p95=float(worst["aware_p95_ms"])),
      dict(case="improvement",nic=float(best["nic_gib_s"]),
           fabric=float(best["fabric_gib_s"]),
           cache=int(best["cache_blocks"]),burst=int(best["burst_width"]),
           archived_seed=int(best["seed"]),archived_greedy_p95=float(best["greedy_p95_ms"]),
           archived_aware_p95=float(best["aware_p95_ms"]))
    ]
    args.out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for c in cases:
        for seed,admission,policy in itertools.product(
                args.seeds,("always","no_remote"),
                ("greedy","bandwidth_aware")):
            result=run_case(seed,c["nic"],c["fabric"],c["cache"],
                            c["burst"],admission,policy,
                            args.requests,args.shared_blocks)
            result["case"]=c["case"]
            rows.append(result)
            if (admission=="always" and seed==c["archived_seed"]
                and args.requests==240 and args.shared_blocks==256):
                expected=(c["archived_greedy_p95"] if policy=="greedy"
                          else c["archived_aware_p95"])
                if abs(result["p95_ms"]-expected)>1e-6:
                    raise AssertionError("original archive replay changed: %s %s" %
                                         (c["case"],policy))
        print("Completed",c["case"],flush=True)
    pairs=make_pairs(rows)
    write_csv(args.out/"runs.csv",rows)
    write_csv(args.out/"pairs.csv",pairs)
    (args.out/"config.json").write_text(json.dumps({
        "cases":cases,"seeds":args.seeds,
        "requests":args.requests,"shared_blocks":args.shared_blocks,
        "original_archive":str(args.archive),
        "warning":"Synthetic, not a measured GPU/NIC experiment. "
                  "no_remote suppresses admission for remote-reuse requests."
    },indent=2)+"\n")
    for c in ("regression","improvement"):
        for admission in ("always","no_remote"):
            filtered=[x["p95_improvement_pct"] for x in pairs if
                      x["case"]==c and x["admission"]==admission]
            print("%s %s n=%d mean P95 improvement=%+.2f%% positive=%d" %
                (c,admission,len(filtered),statistics.mean(filtered),
                 sum(v>1e-7 for v in filtered)))
    print("Saved:",args.out.resolve())


if __name__=="__main__":
    main()
