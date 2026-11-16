#!/usr/bin/env python3
"""Reproducible paired robustness sweep for hotspot KV scheduling.

Runs each policy on *exactly the same* request trace and initial cache within
a scenario. Emits per-scenario, paired-by-seed, and across-seed CSV files.
Simulated times are synthetic, not physical NIC/GPU measurements.
"""
import argparse
import csv
import itertools
import json
import math
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from kv_sim.core import Config, NodeSpec, run_policy
from kv_sim.workload.hotspot import (
    make_hotspot_workload, make_hotspot_initial_caches)


def percentile(values, p):
    values = sorted(values)
    if not values:
        raise ValueError("empty series")
    at = (len(values) - 1) * p
    lo = int(at)
    frac = at - lo
    return values[lo] * (1.0 - frac) + values[min(lo + 1, len(values) - 1)] * frac


def write_csv(path, rows):
    if not rows:
        raise ValueError("empty output: " + str(path))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _chosen(sim):
    return {r["request_id"]: r["node"] for r in sim.rows}


def _assert_equivalent(baseline, candidate, atol=1e-7):
    """With lambda=0 bandwidth-aware must be exactly Greedy in v0.x."""
    b = {r["request_id"]: r for r in baseline.rows}
    c = {r["request_id"]: r for r in candidate.rows}
    if b.keys() != c.keys():
        raise AssertionError("lambda=0 changes request identities")
    for key, left in b.items():
        right = c[key]
        for field in ("node", "source", "local_hit_tokens",
                      "remote_hit_tokens", "total_hit_tokens",
                      "recomputed_tokens"):
            if left[field] != right[field]:
                raise AssertionError("lambda=0 changed %s on %s" % (field, key))
        for field in ("finished_ms", "ttft_ms", "network_ms"):
            if not math.isclose(left[field], right[field],
                                rel_tol=0.0, abs_tol=atol):
                raise AssertionError("lambda=0 changed %s on %s" % (field, key))


def paired_trial(specs, requests, initial, fabric_gib_s,
                 admission, lambdas):
    """Run a Greedy baseline once then one bandwidth-aware run per lambda."""
    greedy = run_policy(specs, requests, initial, Config(
        policy="greedy", admission=admission, fabric_gib_s=fabric_gib_s))
    base = greedy.summary()
    baseline_map = _chosen(greedy)
    rows = []
    for weight in lambdas:
        aware = run_policy(specs, requests, initial, Config(
            policy="bandwidth_aware", admission=admission,
            fabric_gib_s=fabric_gib_s,
            network_externality_weight=weight))
        if weight == 0.0:
            _assert_equivalent(greedy, aware)
        result = aware.summary()
        policy_map = _chosen(aware)
        changed = sum(baseline_map[key] != policy_map[key]
                      for key in baseline_map)
        assert len(policy_map) == len(baseline_map)
        rows.append({
            "lambda": weight,
            "greedy_p50_ms": base["p50_ttft_ms"],
            "aware_p50_ms": result["p50_ttft_ms"],
            "greedy_p95_ms": base["p95_ttft_ms"],
            "aware_p95_ms": result["p95_ttft_ms"],
            "greedy_p99_ms": base["p99_ttft_ms"],
            "aware_p99_ms": result["p99_ttft_ms"],
            "p50_improvement_pct": 100.0 * (
                1.0 - result["p50_ttft_ms"] / base["p50_ttft_ms"]),
            "p95_improvement_pct": 100.0 * (
                1.0 - result["p95_ttft_ms"] / base["p95_ttft_ms"]),
            "p99_improvement_pct": 100.0 * (
                1.0 - result["p99_ttft_ms"] / base["p99_ttft_ms"]),
            "greedy_network_gib": base["network_gib"],
            "aware_network_gib": result["network_gib"],
            "network_reduction_pct": (
                100.0 * (1.0 - result["network_gib"] /
                         base["network_gib"])
                if base["network_gib"] > 0 else 0.0),
            "greedy_hit_ratio": base["hit_ratio"],
            "aware_hit_ratio": result["hit_ratio"],
            "greedy_evictions": base["evictions"],
            "aware_evictions": result["evictions"],
            "placement_changes": changed,
            "placement_change_pct": 100.0 * changed / len(requests),
        })
    return rows


def aggregate_runs(records):
    """Descriptive across-seed estimates: no causal or significance claims."""
    groups = {}
    for row in records:
        key = (row["fabric_gib_s"], row["burst_width"],
               row["cache_blocks"], row["lambda"])
        groups.setdefault(key, []).append(row)
    output = []
    for (fabric, burst, cache, weight), rows in sorted(groups.items()):
        improvements = [r["p95_improvement_pct"] for r in rows]
        traffic = [r["network_reduction_pct"] for r in rows]
        placement = [r["placement_change_pct"] for r in rows]
        output.append({
            "fabric_gib_s": fabric,
            "burst_width": burst,
            "cache_blocks": cache,
            "lambda": weight,
            "seed_count": len(rows),
            "p95_improvement_mean_pct": statistics.mean(improvements),
            "p95_improvement_median_pct": statistics.median(improvements),
            "p95_improvement_min_pct": min(improvements),
            "p95_improvement_max_pct": max(improvements),
            "p95_improvement_p10_pct": percentile(improvements, .1),
            "p95_improvement_p90_pct": percentile(improvements, .9),
            "p95_positive_seeds": sum(x > 0 for x in improvements),
            "network_reduction_mean_pct": statistics.mean(traffic),
            "placement_change_mean_pct": statistics.mean(placement),
            "greedy_p95_mean_ms": statistics.mean(
                r["greedy_p95_ms"] for r in rows),
            "aware_p95_mean_ms": statistics.mean(
                r["aware_p95_ms"] for r in rows),
        })
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seeds", type=int, nargs="+", default=[41, 42, 43, 44, 45])
    p.add_argument("--fabric-gib-s", type=float, nargs="+",
                   default=[1.0, 2.0, 4.0, 8.0, 16.0])
    p.add_argument("--burst-widths", type=int, nargs="+", default=[1, 4, 8, 16])
    p.add_argument("--cache-blocks", type=int, nargs="+", default=[256, 512])
    p.add_argument("--lambdas", type=float, nargs="+",
                   default=[0.0, 0.25, 0.5, 1.0, 2.0, 4.0])
    p.add_argument("--nodes", type=int, default=4)
    p.add_argument("--requests", type=int, default=240)
    p.add_argument("--shared-blocks", type=int, default=256)
    p.add_argument("--suffix-blocks", type=int, default=4)
    p.add_argument("--burst-gap-ms", type=float, default=2.0)
    p.add_argument("--within-burst-ms", type=float, default=0.02)
    p.add_argument("--nic-gib-s", type=float, default=2.0)
    p.add_argument("--admission", choices=["always", "no_remote"],
                   default="always")
    p.add_argument("--out", type=pathlib.Path,
                   default=pathlib.Path("results/hotspot-robustness"))
    args = p.parse_args()
    if args.nodes < 2 or args.requests <= 0:
        p.error("nodes >= 2 and requests > 0 required")
    if args.shared_blocks <= 0 or min(args.cache_blocks) < args.shared_blocks:
        p.error("cache capacity must hold the hot prefix")
    if any(v <= 0 for v in args.fabric_gib_s + [args.nic_gib_s]):
        p.error("bandwidth must be positive")
    if any(w < 0 for w in args.lambdas) or 0.0 not in args.lambdas:
        p.error("lambda values must include 0 and be nonnegative")
    if len(set(args.seeds)) != len(args.seeds):
        p.error("duplicate seeds are not allowed")
    args.out.mkdir(parents=True, exist_ok=True)
    records = []
    total_scenarios = (len(args.seeds)*len(args.fabric_gib_s) *
                       len(args.burst_widths)*len(args.cache_blocks))
    finished = 0
    for seed, fabric, burst, capacity in itertools.product(
            args.seeds, args.fabric_gib_s,
            args.burst_widths, args.cache_blocks):
        specs = [NodeSpec(i, capacity, args.nic_gib_s)
                 for i in range(args.nodes)]
        requests, hashes = make_hotspot_workload(
            count=args.requests, shared_blocks=args.shared_blocks,
            suffix_blocks=args.suffix_blocks, burst_width=burst,
            burst_gap_ms=args.burst_gap_ms,
            within_burst_ms=args.within_burst_ms, seed=seed)
        caches = make_hotspot_initial_caches(
            specs, hashes, occupancy=1.0, seed=seed)
        for row in paired_trial(specs, requests, caches, fabric,
                                args.admission, args.lambdas):
            row.update(seed=seed, fabric_gib_s=fabric,
                       burst_width=burst, cache_blocks=capacity,
                       admission=args.admission,
                       requests=args.requests)
            records.append(row)
        finished += 1
        if finished % max(1, total_scenarios//10) == 0:
            print("Completed %d/%d scenarios" % (
                finished, total_scenarios), flush=True)
    aggregated = aggregate_runs(records)
    write_csv(args.out / "runs.csv", records)
    write_csv(args.out / "aggregated.csv", aggregated)
    with (args.out / "config.json").open("w") as f:
        json.dump(dict(vars(args), out=str(args.out),
                       runs=len(records), scenarios=total_scenarios,
                       note="Synthetic, uncalibrated GPU compute; configured "
                       "fluid network. P10/P90 are across-seed empirical "
                       "percentiles, NOT confidence intervals."),
                  f, indent=2)
    improved = sum(r["p95_improvement_pct"] > 0 for r in records
                   if r["lambda"] != 0.0)
    assessed = sum(r["lambda"] != 0.0 for r in records)
    print("Runs:", len(records), "Nonzero lambda improvements:",
          "%d/%d" % (improved, assessed))
    print("Saved:", args.out.resolve())


if __name__ == "__main__":
    main()
