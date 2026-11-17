#!/usr/bin/env python3
"""Replay two archived extreme hotspot cases and audit per-request divergences.

All results are discrete-event simulation. Extremes selected post-hoc for
diagnosis, not representative of typical performance or independent evidence.
Python 3.8+ and standard library only.
"""
import argparse
import csv
import json
import math
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kv_sim.core import Config, NodeSpec, Simulator
from kv_sim.workload.hotspot import make_hotspot_workload, make_hotspot_initial_caches


def read_rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def save(path, rows):
    if not rows:
        raise ValueError("empty rows: " + str(path))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def select_cases(rows):
    candidates = [r for r in rows if float(r["weight"]) == 1.0]
    reg = [r for r in candidates
           if int(r["cache_blocks"]) == 256 and int(r["burst_width"]) == 1]
    gain = [r for r in candidates
            if int(r["cache_blocks"]) == 512 and int(r["burst_width"]) == 8]
    if not reg or not gain:
        raise ValueError("archive lacks required diagnostic strata")
    worst = min(reg, key=lambda r: float(r["p95_improvement_pct"]))
    best = max(gain, key=lambda r: float(r["p95_improvement_pct"]))
    if float(worst["p95_improvement_pct"]) >= 0:
        raise ValueError("selected regression case has no regression")
    if float(best["p95_improvement_pct"]) <= 0:
        raise ValueError("selected improvement case has no improvement")
    return [("regression", worst), ("improvement", best)]


def simulate(params, policy, seed, nic, fabric, capacity, burst, weight):
    specs = [NodeSpec(i, capacity, nic) for i in range(int(params["nodes"]))]
    requests, hot = make_hotspot_workload(
        count=int(params["requests"]), shared_blocks=int(params["shared_blocks"]),
        burst_width=burst, burst_gap_ms=float(params["burst_gap_ms"]),
        seed=seed)
    caches = make_hotspot_initial_caches(specs, hot, seed=seed)
    sim = Simulator(specs, Config(
        policy=policy, fabric_gib_s=fabric,
        trace_decisions=True, network_externality_weight=weight))
    for node, blocks in caches.items():
        sim.seed_cache(node, blocks)
    sim.track_prefix(hot)
    sim.run(requests)
    return sim


def pair_requests(greedy, aware):
    left = {r["request_id"]: r for r in greedy.rows}
    right = {r["request_id"]: r for r in aware.rows}
    if left.keys() != right.keys():
        raise ValueError("request IDs do not match")
    differences = []
    for rid in greedy.arrival_order:
        a, b = left[rid], right[rid]
        differences.append({
            "request_id": rid, "arrival_ms": a["arrival_ms"],
            "greedy_node": a["node"], "aware_node": b["node"],
            "placement_changed": int(a["node"] != b["node"]),
            "greedy_ttft_ms": a["ttft_ms"], "aware_ttft_ms": b["ttft_ms"],
            "delta_ttft_ms": b["ttft_ms"] - a["ttft_ms"],
            "greedy_queue_ms": a["queue_ms"], "aware_queue_ms": b["queue_ms"],
            "greedy_network_ms": a["network_ms"], "aware_network_ms": b["network_ms"],
            "greedy_h2d_ms": a["h2d_ms"], "aware_h2d_ms": b["h2d_ms"],
            "greedy_compute_ms": a["compute_ms"], "aware_compute_ms": b["compute_ms"],
            "greedy_local_hit_tokens": a["local_hit_tokens"],
            "aware_local_hit_tokens": b["local_hit_tokens"],
            "greedy_remote_hit_tokens": a["remote_hit_tokens"],
            "aware_remote_hit_tokens": b["remote_hit_tokens"],
            "greedy_finished_ms": a["finished_ms"],
            "aware_finished_ms": b["finished_ms"],
        })
    return differences


def sample_candidate_decisions(simulators, selected_request_ids):
    """Preserve existing policy field from each simulator's decision record."""
    return [dict(row) for sim in simulators
            for row in sim.decisions
            if row["request_id"] in selected_request_ids]


def hot_at_end(sim):
    rows = [r for r in sim.cache_trace if r["node"] == 0]
    return dict(mean=statistics.mean(r["hot_prefix_resident_blocks"]
                                     for r in rows),
                final=rows[-1]["hot_prefix_resident_blocks"],
                source_evictions=sim.nodes[0].cache.evictions)


def verify_archive(greedy, aware, archived, tolerance=1e-6):
    for field, value in (("greedy_p95_ms", greedy.summary()["p95_ttft_ms"]),
                         ("aware_p95_ms", aware.summary()["p95_ttft_ms"])):
        expected = float(archived[field])
        if not math.isclose(value, expected, abs_tol=tolerance, rel_tol=0):
            raise AssertionError("%s mismatch: replay %.10f archive %.10f" %
                                 (field, value, expected))
    actual_changes = sum(
        a["node"] != b["node"] for a, b in zip(
            sorted(greedy.rows, key=lambda r:r["request_id"]),
            sorted(aware.rows, key=lambda r:r["request_id"])))
    if actual_changes != int(archived["changed_placements"]):
        raise AssertionError("placement count mismatch")
    for prefix, sim in (("greedy", greedy), ("aware", aware)):
        actual = hot_at_end(sim)
        for field, key in (("mean","source_hot_mean_blocks"),
                           ("final","source_hot_final_blocks"),
                           ("source_evictions","source_evictions")):
            expected=float(archived[prefix+"_"+key])
            if not math.isclose(actual[field],expected,abs_tol=tolerance,
                                rel_tol=0):
                raise AssertionError("residency mismatch %s.%s" %
                                     (prefix,field))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",type=pathlib.Path,
                   default=pathlib.Path("experiments/cache-network-diagnostics-20261117"))
    p.add_argument("--out",type=pathlib.Path,
                   default=pathlib.Path("results/request-case-study"))
    args=p.parse_args()
    rows=read_rows(args.archive/"compact.csv")
    with (args.archive/"manifest.json").open() as f:
        manifest=json.load(f)
    params=manifest["parameter_config"]
    args.out.mkdir(parents=True,exist_ok=True)
    brief=[]
    for name,record in select_cases(rows):
        seed=int(record["seed"])
        nic=float(record["nic_gib_s"])
        fabric=float(record["fabric_gib_s"])
        cache=int(record["cache_blocks"])
        burst=int(record["burst_width"])
        weight=float(record["weight"])
        greedy=simulate(params,"greedy",seed,nic,fabric,cache,burst,0)
        aware=simulate(params,"bandwidth_aware",seed,nic,fabric,cache,burst,weight)
        verify_archive(greedy,aware,record)
        paired=pair_requests(greedy,aware)
        folder=args.out/name
        folder.mkdir(parents=True,exist_ok=True)
        save(folder/"paired_requests.csv",paired)
        save(folder/"greedy_decisions.csv",greedy.decisions)
        save(folder/"aware_decisions.csv",aware.decisions)
        save(folder/"greedy_cache.csv",greedy.cache_trace)
        save(folder/"aware_cache.csv",aware.cache_trace)
        delta=sorted(paired,key=lambda r:abs(r["delta_ttft_ms"]),reverse=True)
        changed=[r for r in paired if r["placement_changed"]]
        # Sample the largest TTFT divergences plus the first changed placements
        # in arrival order. De-duplicate by request ID.
        sample={}
        for row in delta[:12]+changed[:12]:
            sample[row["request_id"]]=row
        selected=set(sample)
        compact_decisions=sample_candidate_decisions(
            (greedy, aware), selected)
        save(folder/"sample_requests.csv",list(sample.values()))
        save(folder/"sample_decisions.csv",compact_decisions)
        summary=dict(
            case=name,seed=seed,nic_gib_s=nic,fabric_gib_s=fabric,
            cache_blocks=cache,burst_width=burst,weight=weight,
            request_count=len(paired),
            placement_changes=len(changed),
            greedy_p95_ms=greedy.summary()["p95_ttft_ms"],
            aware_p95_ms=aware.summary()["p95_ttft_ms"],
            p95_improvement_pct=100*(1-aware.summary()["p95_ttft_ms"]/
                                      greedy.summary()["p95_ttft_ms"]),
            greedy_traffic_gib=greedy.summary()["network_gib"],
            aware_traffic_gib=aware.summary()["network_gib"],
            greedy_mean_queue_ms=statistics.mean(
                r["queue_ms"] for r in greedy.rows),
            aware_mean_queue_ms=statistics.mean(
                r["queue_ms"] for r in aware.rows),
            greedy_mean_network_ms=statistics.mean(
                r["network_ms"] for r in greedy.rows),
            aware_mean_network_ms=statistics.mean(
                r["network_ms"] for r in aware.rows),
            greedy_mean_h2d_ms=statistics.mean(
                r["h2d_ms"] for r in greedy.rows),
            aware_mean_h2d_ms=statistics.mean(
                r["h2d_ms"] for r in aware.rows),
            greedy_mean_compute_ms=statistics.mean(
                r["compute_ms"] for r in greedy.rows),
            aware_mean_compute_ms=statistics.mean(
                r["compute_ms"] for r in aware.rows),
            greedy_source_hot_mean_blocks=hot_at_end(greedy)["mean"],
            aware_source_hot_mean_blocks=hot_at_end(aware)["mean"],
            greedy_source_evictions=hot_at_end(greedy)["source_evictions"],
            aware_source_evictions=hot_at_end(aware)["source_evictions"])
        (folder/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
        brief.append(summary)
        print("%s: seed=%d cache=%d burst=%d P95 improvement=%+.2f%% "
              "changed=%d/%d (archive replay verified)" %
              (name,seed,cache,burst,summary["p95_improvement_pct"],
               len(changed),len(paired)),flush=True)
    with (args.out/"case_summaries.json").open("w") as f:
        json.dump(brief,f,indent=2)
    print("Saved:",args.out.resolve())
    print("NOTE: cases selected post-hoc; request-level correlations are not "
          "proof of causation.")


if __name__=="__main__":
    main()
