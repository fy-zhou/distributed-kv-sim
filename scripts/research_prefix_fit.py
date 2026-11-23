#!/usr/bin/env python3
"""Pair prefix-fit admission against always and no-remote under identical inputs."""
import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import archive, new_directory, provenance, write_csv
from scripts.research_shadow import TIMING_FIELDS, inputs, summaries, trial

FACTORIAL_SEEDS = {'development': (160, 161), 'evaluation': (1501, 1502, 1503, 1504, 1505)}
SEEDS = {'development': (140, 141), 'evaluation': (1301, 1302, 1303, 1304, 1305)}
POLICIES = ('round_robin_cost', 'cost_aware', 'shadow_cluster_lru')
ADMISSIONS = ('always', 'no_remote', 'prefix_fit')
SCENARIOS = (
    dict(name='cold_cliff', capacity=256, burst=1),
    dict(name='reuse_cliff', capacity=256, compute_scale=8.),
    dict(name='request_fit', capacity=260, compute_scale=8.),
    dict(name='extra_capacity', capacity=512, compute_scale=8.),
    dict(name='mixed_prefixes', capacity=256, compute_scale=8., popularity=.5),
)


def key(row):
    return row['scenario'], int(row['seed']), row['policy'], row['admission']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--verify-against', type=Path)
    parser.add_argument('--factorial', action='store_true',
                        help='Cross clipping with suppressing remote admission')
    args = parser.parse_args()
    scenarios, seeds, admissions = SCENARIOS, SEEDS, ADMISSIONS
    comparisons = (('prefix_fit', 'always'), ('prefix_fit', 'no_remote'))
    if args.factorial:
        scenarios = [s for s in scenarios if s['name'] in ('reuse_cliff', 'mixed_prefixes', 'request_fit')]
        seeds = FACTORIAL_SEEDS
        admissions += ('prefix_fit_no_remote',)
        comparisons = (('prefix_fit', 'always'), ('prefix_fit_no_remote', 'no_remote'),
                       ('prefix_fit_no_remote', 'prefix_fit'), ('no_remote', 'always'))
    out = new_directory(args.out)
    (out/'config.json').write_text(json.dumps(provenance(dict(
        seeds=seeds[args.split], policies=POLICIES, admissions=admissions,
        scenarios=scenarios, comparisons=comparisons, requests=120,
        interpretation='Change cache admission only within each placement policy. '
                       'prefix_fit considers the first capacity full blocks. Existing pins override eviction.')),
        indent=2)+'\n')
    expected = {}
    if args.verify_against:
        with args.verify_against.open(newline='') as handle:
            expected = {key(r): r for r in csv.DictReader(handle)}
    rows, pairs = [], []
    for scenario in scenarios:
        for seed in seeds[args.split]:
            prepared = inputs(scenario, seed, 120)
            for policy in POLICIES:
                runs = {}
                for admission in admissions:
                    _, row = trial(scenario, seed, admission, policy, prepared=prepared)
                    rows.append(row)
                    runs[admission] = row
                    if args.verify_against:
                        original = expected.pop(key(row))
                        assert all(str(v) == original[k] for k, v in row.items()
                                   if k not in TIMING_FIELDS)
                assert len({r['trace_hash'] for r in runs.values()}) == 1
                assert len({r['initial_cache_hash'] for r in runs.values()}) == 1
                candidate = runs['prefix_fit']
                if scenario['capacity'] >= max(len(req.hashes) for req in prepared[2]):
                    assert all(runs['always'][k] == v for k, v in candidate.items()
                               if k not in TIMING_FIELDS and k != 'admission')
                    if args.factorial:
                        assert all(runs['no_remote'][k] == v
                                   for k, v in runs['prefix_fit_no_remote'].items()
                                   if k not in TIMING_FIELDS and k != 'admission')
                for candidate_name, baseline in comparisons:
                    candidate, control = runs[candidate_name], runs[baseline]
                    a, b = candidate['p95_ttft_ms'], control['p95_ttft_ms']
                    delta = 100*(1-a/b)
                    outcome = 'improve' if delta > 1e-7 else 'regress' if delta < -1e-7 else 'neutral'
                    pairs.append(dict(scenario=scenario['name'], seed=seed, policy=policy,
                        candidate=candidate_name, baseline=baseline, candidate_p95_ms=a,
                        baseline_p95_ms=b, p95_improvement_pct=delta, outcome=outcome,
                        candidate_traffic_gib=candidate['network_gib'],
                        baseline_traffic_gib=control['network_gib']))
        write_csv(out/'runs.csv', rows)
        print(args.split, scenario['name'], len(rows), flush=True)
    assert not expected
    write_csv(out/'pairs.csv', pairs)
    write_csv(out/'summary.csv', [
        dict(policy=policy, **r) for policy in POLICIES
        for r in summaries([p for p in pairs if p['policy'] == policy])])
    if args.archive:
        archive(out, args.archive, ['runs.csv', 'pairs.csv', 'summary.csv'],
                '# Prefix-fit admission experiment\n\n'
                'Synthetic evaluation. Read pairs within the same placement policy. '
                'P95, traffic and reusable hot-prefix metrics cover every declared case. '
                'Admission avoids inserting the over-capacity tail of one request, '
                'while other requests can still evict its prefix according to LRU.')


if __name__ == '__main__':
    main()
