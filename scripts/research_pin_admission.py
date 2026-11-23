#!/usr/bin/env python3
"""Pair prefix-fit admission with a pin-aware prefix budget."""
import argparse
import gzip
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import archive, digest, new_directory, provenance, write_csv
from scripts.research_shadow import MeasuredSimulator, inputs, summaries, trial

SEEDS = {'development': (3400, 3401), 'evaluation': (3501, 3502, 3503, 3504, 3505)}
POLICIES = ('round_robin_cost', 'bandwidth_aware', 'shadow_cluster_finite')
ADMISSIONS = ('prefix_fit', 'prefix_fit_pins')
SCENARIOS = (
    dict(name='single_node_control', nodes=1, capacity=256, compute_scale=16.),
    dict(name='pin_cache_cliff', capacity=256, compute_scale=16., burst=12),
    dict(name='pin_endpoint_cliff', capacity=260, compute_scale=16., nic=2., fabric=16., burst=16),
    dict(name='pin_mixed_families', capacity=384, compute_scale=16., burst=12, popularity=.5),
)


class PinObserverSimulator(MeasuredSimulator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pin_admissions = []

    def _on_job_done(self, node_id):
        node = self.nodes[node_id]
        req = node.active.req
        pins = len(node.cache.pins)
        before = node.cache.prefix_len(req.hashes) if pins else None
        evictions, rejections = node.cache.evictions, node.cache.rejections
        super()._on_job_done(node_id)
        if pins:
            self.pin_admissions.append(dict(request_id=req.request_id, node=node_id, pins=pins,
                prefix_before=before, prefix_after=node.cache.prefix_len(req.hashes),
                evictions=node.cache.evictions-evictions,
                rejections=node.cache.rejections-rejections))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    out = new_directory(args.out)
    config = provenance(dict(split=args.split, seeds=SEEDS[args.split], scenarios=SCENARIOS,
        policies=POLICIES, admissions=ADMISSIONS, requests=120,
        hypothesis='Reserved outgoing blocks can otherwise displace a new contiguous prefix.',
        negative_control='No outgoing pins with one node; require exact outcome/cache equality.',
        limits='Applies to admission at job completion in the baseline visibility model. '
               'Does not model memory copying or transport latency for KV writeback.'))
    (out/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    rows, pairs = [], []
    with gzip.GzipFile(str(out/'pin_admissions.jsonl.gz'), 'wb', mtime=0) as trace:
        for scenario in SCENARIOS:
            for seed in SEEDS[args.split]:
                prepared = inputs(scenario, seed, 120)
                for policy in POLICIES:
                    cases = {}
                    order = sorted(ADMISSIONS, key=lambda a: digest([scenario['name'], seed, policy, a]))
                    for admission in order:
                        sim, row = trial(scenario, seed, admission, policy, prepared=prepared,
                                         simulator_factory=PinObserverSimulator)
                        observed = sim.pin_admissions
                        row.update(pin_admissions=len(observed),
                            pin_evictions=sum(r['evictions'] for r in observed),
                            pin_rejections=sum(r['rejections'] for r in observed),
                            pin_mean_prefix_after=statistics.mean(r['prefix_after']
                                                                  for r in observed) if observed else 0.)
                        trace.write((json.dumps(dict(key=[scenario['name'], seed, policy, admission],
                            admissions=observed), separators=(',', ':'))+'\n').encode())
                        rows.append(row)
                        cases[admission] = row
                    baseline, candidate = (cases[a] for a in ADMISSIONS)
                    for field in ('trace_hash', 'initial_cache_hash'):
                        assert candidate[field] == baseline[field]
                    if not (candidate['pin_admissions'] or baseline['pin_admissions']):
                        for field in ('outcomes_hash', 'final_cache_hash'):
                            assert candidate[field] == baseline[field], (scenario['name'], seed, policy, field)
                    a, b = candidate['p95_ttft_ms'], baseline['p95_ttft_ms']
                    delta = 100*(1-a/b)
                    pairs.append(dict(scenario=scenario['name'], seed=seed, policy=policy,
                        candidate=ADMISSIONS[1], baseline=ADMISSIONS[0], candidate_p95_ms=a, baseline_p95_ms=b,
                        p95_improvement_pct=delta,
                        outcome='improve' if delta > 1e-7 else 'regress' if delta < -1e-7 else 'neutral',
                        candidate_gib=candidate['network_gib'], baseline_gib=baseline['network_gib'],
                        candidate_pin_admissions=candidate['pin_admissions'],
                        baseline_pin_admissions=baseline['pin_admissions'],
                        candidate_pin_rejections=candidate['pin_rejections'],
                        baseline_pin_rejections=baseline['pin_rejections']))
            write_csv(out/'runs.csv', rows)
            print('Completed', args.split, scenario['name'], len(rows), flush=True)
    write_csv(out/'pairs.csv', pairs)
    write_csv(out/'summary.csv', [dict(policy=p, **r) for p in POLICIES
                                 for r in summaries([row for row in pairs if row['policy'] == p])])
    if args.archive:
        archive(out, args.archive, ('runs.csv', 'pairs.csv', 'summary.csv'),
                '# Pin-aware prefix admission\n\n'
                'Admission-only comparisons with the same initial caches and placement policy. '
                'Request-level pin observations are in the raw results directory.')


if __name__ == '__main__':
    main()
