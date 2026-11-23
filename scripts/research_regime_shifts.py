#!/usr/bin/env python3
"""Evaluate admission under causal workload shifts with request-cohort metrics."""
import argparse
import gzip
import json
from pathlib import Path
import random
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_sim import NodeSpec, Request
from kv_sim.workload.hotspot import make_hotspot_initial_caches
from scripts.research_common import archive, digest, new_directory, provenance, write_csv
from scripts.research_evaluate import DEFAULTS
from scripts.research_shadow import summaries, trial

SEEDS = {'development': (4000, 4001), 'evaluation': (4101, 4102, 4103, 4104, 4105)}
POLICIES = ('round_robin_cost', 'cost_aware', 'shadow_cluster_finite')
ADMISSIONS = ('always', 'prefix_fit')
SCENARIOS = (
    dict(name='stationary_cliff', phases=(0, 0, 0, 0), gap=30., capacity=256),
    dict(name='rotating_cliff', phases=(0, 1, 2, 0), gap=30., capacity=256),
    dict(name='rotating_sparse_cliff', phases=(0, 1, 2, 0), gap=750., capacity=256),
    dict(name='rotating_roomy', phases=(0, 1, 2, 0), gap=30., capacity=512),
)
SEGMENT_REQUESTS = 30


def workload(scenario, seed):
    """Construct traces using the same randomness across popularity schedules."""
    params = {**DEFAULTS, **scenario, 'compute_scale': 16., 'burst': 6,
              'fabric': 4., 'nic': 4.}
    specs = [NodeSpec(i, params['capacity'], params['nic']) for i in range(params['nodes'])]
    rng = random.Random(seed)
    prefixes = [tuple(range(500000 + 200000*i, 500000 + 200000*i + 4096)) for i in range(3)]
    requests, families = [], {}
    for i, popular in enumerate(scenario['phases']):
        others = tuple(f for f in range(3) if f != popular)
        for within in range(SEGMENT_REQUESTS):
            # Always consume both draws so distinct conditions share the uniforms.
            sample, alternate = rng.random(), rng.randrange(len(others))
            family = popular if sample < .85 else others[alternate]
            idx = i*SEGMENT_REQUESTS + within
            arrival = idx//params['burst']*params['gap'] + idx%params['burst']*.02
            end = 20000000+idx*100000
            req = Request.from_tokens(str(idx), arrival, prefixes[family]+tuple(range(end, end+64)))
            requests.append(req)
            families[req.request_id] = family
    initial_prefix = Request.from_tokens('initial', 0., prefixes[0]).hashes
    initial = make_hotspot_initial_caches(specs, initial_prefix, seed=seed)
    return (params, specs, requests, initial_prefix, initial), families


def segments(scenario, seed, policy, admission, rows, families):
    for i, hot in enumerate(scenario['phases']):
        reqs = [r for r in rows if int(r['request_id'])//SEGMENT_REQUESTS == i]
        assert len(reqs) == SEGMENT_REQUESTS
        latencies = sorted(r['ttft_ms'] for r in reqs)
        hot_reqs = [r for r in reqs if families[r['request_id']] == hot]
        yield dict(scenario=scenario['name'], seed=seed, policy=policy, admission=admission,
                   segment=i, designated_hot_family=hot, requests=len(reqs),
                   hot_requests=len(hot_reqs), median_ttft_ms=statistics.median(latencies),
                   p95_ttft_ms=latencies[28],
                   mean_queue_ms=statistics.mean(r['queue_ms'] for r in reqs),
                   hot_local_hit_count=sum(r['local_hit_tokens'] > 0 for r in hot_reqs),
                   hot_remote_hit_count=sum(r['remote_hit_tokens'] > 0 for r in hot_reqs))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    out = new_directory(args.out)
    config = provenance(dict(split=args.split, seeds=SEEDS[args.split], scenarios=SCENARIOS,
        policies=POLICIES, admissions=ADMISSIONS, requests=4*SEGMENT_REQUESTS,
        fixed_parameters=dict(shared_blocks=256, suffix_blocks=4, nodes=4, burst=6,
                              compute_scale=16., nic=4., fabric=4., popularity=.85),
        segment='Request cohorts are keyed by arrival sequence, even if they overlap in service. '
                'P95 per cohort is nearest rank and exploratory with only 30 observations. '
                'Initial cache warms family 0 at node 0 in every condition. '
                'Popularity schedules are offered load, not scheduler information.'))
    (out/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    rows, pairs, cohorts = [], [], []
    with gzip.GzipFile(str(out/'traces.jsonl.gz'), 'wb', mtime=0) as trace:
        for scenario in SCENARIOS:
            for seed in SEEDS[args.split]:
                prepared, families = workload(scenario, seed)
                for policy in POLICIES:
                    group = {}
                    order = sorted(ADMISSIONS, key=lambda a: digest([scenario['name'], seed, policy, a]))
                    for admission in order:
                        sim, row = trial(scenario, seed, admission, policy, prepared=prepared)
                        cohorts.extend(segments(scenario, seed, policy, admission, sim.rows, families))
                        trace.write((json.dumps(dict(key=[scenario['name'], seed, policy, admission],
                            requests=sim.rows, families=families), separators=(',', ':'))+'\n').encode())
                        rows.append(row)
                        group[admission] = row
                    b, c = (group[a] for a in ADMISSIONS)
                    for field in ('trace_hash', 'initial_cache_hash'):
                        assert b[field] == c[field]
                    if scenario['capacity'] >= 260:
                        for field in ('outcomes_hash', 'final_cache_hash'):
                            assert b[field] == c[field]
                    delta = 100*(1-c['p95_ttft_ms']/b['p95_ttft_ms'])
                    pairs.append(dict(scenario=scenario['name'], seed=seed, policy=policy,
                        candidate='prefix_fit', baseline='always', candidate_p95_ms=c['p95_ttft_ms'],
                        baseline_p95_ms=b['p95_ttft_ms'], p95_improvement_pct=delta,
                        outcome='improve' if delta > 1e-7 else 'regress' if delta < -1e-7 else 'neutral',
                        candidate_gib=c['network_gib'], baseline_gib=b['network_gib']))
            write_csv(out/'runs.csv', rows)
            print('Completed', args.split, scenario['name'], len(rows), flush=True)
    write_csv(out/'pairs.csv', pairs)
    write_csv(out/'segments.csv', cohorts)
    write_csv(out/'summary.csv', [
        dict(policy=policy, **r) for policy in POLICIES
        for r in summaries([row for row in pairs if row['policy'] == policy])])
    if args.archive:
        archive(out, args.archive, ('runs.csv', 'pairs.csv', 'summary.csv', 'segments.csv'),
                '# Capacity fit with changing hot prefixes\n\n'
                'Paired admission changes across stationary, rotating and return-to-hot workloads. '
                'Segment cohorts are exploratory and share the same global queue.')


if __name__ == '__main__':
    main()
