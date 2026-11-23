#!/usr/bin/env python3
"""Isolate the cost of filtering unread arrivals from a cluster snapshot."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_sim import Config, NodeSpec, Request, Simulator
from kv_sim.engine.events import EVENT_ARRIVAL
from kv_sim.scheduler.shadow import forecasts
from scripts.research_common import archive, new_directory, provenance, write_csv

SIZES = (0, 120, 1000, 10000, 100000)
POLICIES = ('shadow_cluster_finite', 'shadow_cluster_events')


def snapshot(policy, arrivals, req):
    sim = Simulator([NodeSpec(i, 48, 4.) for i in range(4)],
                    Config(policy=policy, gpu_ms_per_new_token=1., fabric_gib_s=4.))
    sim.seed_cache(2, req.hashes)
    for i in (0, 1, 2):
        sim.nodes[i].queue.extend((req, req))
        sim._start(i)
    for i in range(arrivals):
        sim._schedule(10000.+i, EVENT_ARRIVAL, req)
    return sim


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    out = new_directory(args.out)
    req = Request.from_tokens('q', 0., list(range(512)))
    rows = []
    for size in SIZES:
        snapshots = {p: snapshot(p, size, req) for p in POLICIES}
        for repeat in range(-10, 200):
            scores, durations = {}, {}
            for policy in POLICIES[::1 if repeat%2 == 0 else -1]:
                sim = snapshots[policy]
                start = time.perf_counter_ns()
                scores[policy] = forecasts(sim, req, sim.nodes)
                durations[policy] = (time.perf_counter_ns()-start)/1000
            assert scores[POLICIES[0]] == scores[POLICIES[1]]
            if repeat >= 0:
                a, b = [durations[p] for p in POLICIES]
                rows.append(dict(unread_arrivals=size, repeat=repeat,
                                 original_us=a, indexed_us=b, paired_ratio=a/b))
    write_csv(out/'samples.csv', rows)
    summary = []
    for size in SIZES:
        paired = [r for r in rows if r['unread_arrivals'] == size]
        a, b, ratio = (statistics.median(r[k] for r in paired) for k in
                       ('original_us', 'indexed_us', 'paired_ratio'))
        summary.append(dict(unread_arrivals=size, paired_samples=len(paired),
                            original_median_us=a, indexed_median_us=b, median_paired_ratio=ratio))
    write_csv(out/'summary.csv', summary)
    (out/'config.json').write_text(json.dumps(provenance(dict(
        heap_sizes=SIZES, policies=POLICIES, repeats=200, warmups=10,
        order='AB/BA paired within snapshot',
        interpretation='Snapshot cost only. Arrivals are identical placeholders never dispatched. '
                       'Build time, full workload latency and physical serving are outside this microbenchmark.')),
        indent=2)+'\n')
    archive(out, args.archive, ['summary.csv'],
            '# Assigned event index microbenchmark\n\n'
            'All candidate forecasts match exactly. This measures call wall time on a fixed snapshot '
            'while varying only the number of unread arrivals in the heap. Full timing samples stay on the server.')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
