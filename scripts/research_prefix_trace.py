#!/usr/bin/env python3
"""Trace the traffic direction change using already-consumed admission seeds."""
import argparse
import json
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_sim import Config
from scripts.research_common import archive, assert_invariants, digest, new_directory, provenance, write_csv
from scripts.research_shadow import MeasuredSimulator, inputs, trial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    out = new_directory(args.out)
    seed = 1601
    rows = []
    for gap in (2., 1000.):
        name = 'gap_%d_ms' % gap
        scenario = dict(name=name, gap=gap, capacity=256, compute_scale=8.)
        prepared = inputs(scenario, seed, 120)
        params, specs, requests, hot, initial = prepared
        for admission in ('always', 'prefix_fit'):
            sim = MeasuredSimulator(specs, Config(policy='round_robin_cost', admission=admission,
                fabric_gib_s=params['fabric'], gpu_ms_per_new_token=.003*params['compute_scale'],
                gpu_ms_per_attention_pair=1.8e-6*params['compute_scale']), hot=hot, audit=False)
            for node, keys in initial.items():
                sim.seed_cache(node, keys)
            sim.track_prefix(hot)
            sim.run(requests)
            assert_invariants(sim)
            _, untracked = trial(scenario, seed, admission, 'round_robin_cost', prepared=prepared)
            assert digest(sim.rows) == untracked['outcomes_hash']
            states = {(r['request_id'], r['node']): r for r in sim.cache_trace}
            outcomes = []
            for row in sim.rows:
                target = states[row['request_id'], row['node']]
                source = states[row['request_id'], 0]
                n_hot_replicas = sum(states[row['request_id'], node]['hot_prefix_resident_blocks'] == len(hot)
                                     for node in sim.nodes)
                outcomes.append(dict(**row, target_hot_blocks=target['hot_prefix_resident_blocks'],
                    initial_source_hot_blocks=source['hot_prefix_resident_blocks'],
                    hot_replicas=n_hot_replicas))
            write_csv(out/(name+'-'+admission+'.csv'), outcomes)
            flows = Counter(r['source'] for r in outcomes if r['remote_hit_tokens'])
            rows.append(dict(gap_ms=gap, seed=seed, admission=admission, network_gib=sim.summary()['network_gib'],
                p95_ms=sim.summary()['p95_ttft_ms'], remote_requests=sum(flows.values()),
                remote_flows_by_source=json.dumps(flows, sort_keys=True),
                local_hit_requests=sum(bool(r['local_hit_tokens']) for r in outcomes),
                complete_hot_replica_snapshots=sum(bool(r['hot_replicas']) for r in outcomes),
                new_replica_snapshots=sum(r['hot_replicas'] > bool(r['initial_source_hot_blocks'] == len(hot))
                                          for r in outcomes)))
    write_csv(out/'trace_summary.csv', rows)
    (out/'config.json').write_text(json.dumps(provenance(dict(
        seed=seed, gaps_ms=[2, 1000], policy='round_robin_cost', requests=120,
        snapshot='Every job completion after admission. Instrumented outcomes match ordinary simulator.',
        interpretation='Post-hoc diagnosis on the first consumed offered-load seed. '
                       'Mechanism evidence from one paired trace, no tuning or independent performance claim.')),
        indent=2)+'\n')
    archive(out, args.archive, ['trace_summary.csv'],
            '# Admission trace across two offered loads\n\n'
            'The same first consumed test seed is used for both gaps. '
            'Raw per-request cache residency and timing remain outside Git.')
    print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
