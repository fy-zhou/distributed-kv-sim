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

HETEROGENEOUS_SEEDS = {'development': (200, 201),
                       'evaluation': (1901, 1902, 1903, 1904, 1905)}
HETEROGENEOUS = (
    dict(name='slow_source', nic_by_node=(1., 8., 8., 8.)),
    dict(name='fast_source', nic_by_node=(16., 2., 2., 2.)),
    dict(name='slow_destination', nic_by_node=(8., 8., 8., 1.)),
    dict(name='split_pairs', nic_by_node=(2., 2., 8., 8.)),
)
JOINT_SEEDS = {'development': (190, 191), 'evaluation': (1801, 1802, 1803, 1804, 1805)}
RATE_SEEDS = {'development': (170, 171), 'evaluation': (1601, 1602, 1603, 1604, 1605)}
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
    protocol = parser.add_mutually_exclusive_group()
    protocol.add_argument('--factorial', action='store_true',
                          help='Cross clipping with suppressing remote admission')
    protocol.add_argument('--rate-sweep', action='store_true',
                          help='Cross clipping with four fixed offered loads')
    protocol.add_argument('--joint-stress', action='store_true',
                          help='Cross finite-flow placement with capacity-fit admission')
    protocol.add_argument('--heterogeneous', action='store_true',
                          help='Cross both interventions with asymmetric NIC capacities')
    args = parser.parse_args()
    scenarios, seeds, admissions = SCENARIOS, SEEDS, ADMISSIONS
    policies = POLICIES
    comparisons = (('prefix_fit', 'always'), ('prefix_fit', 'no_remote'))
    if args.factorial:
        scenarios = [s for s in scenarios if s['name'] in ('reuse_cliff', 'mixed_prefixes', 'request_fit')]
        seeds = FACTORIAL_SEEDS
        admissions += ('prefix_fit_no_remote',)
        comparisons = (('prefix_fit', 'always'), ('prefix_fit_no_remote', 'no_remote'),
                       ('prefix_fit_no_remote', 'prefix_fit'), ('no_remote', 'always'))
    if args.rate_sweep:
        seeds = RATE_SEEDS
        policies = ('round_robin_cost', 'shadow_cluster_lru')
        scenarios = tuple(dict(name='gap_%d_ms' % gap, gap=float(gap), capacity=256,
                               compute_scale=8.) for gap in (2, 40, 250, 1000))
        admissions = ('always', 'prefix_fit')
        comparisons = (('prefix_fit', 'always'),)
    out = new_directory(args.out)
    if args.joint_stress or args.heterogeneous:
        from scripts.research_rollout_efficiency import STRESS
        scenarios, seeds = STRESS, JOINT_SEEDS
        if args.heterogeneous:
            seeds = HETEROGENEOUS_SEEDS
            scenarios = tuple(dict(shape, compute_scale=16., fabric=8., capacity=256,
                                   burst=12) for shape in HETEROGENEOUS)
        policies = ('round_robin_cost', 'shadow_cluster_lru', 'shadow_cluster_finite')
        admissions = ('always', 'prefix_fit')
        comparisons = (('prefix_fit', 'always'),)
    (out/'config.json').write_text(json.dumps(provenance(dict(
        seeds=seeds[args.split], policies=policies, admissions=admissions,
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
            for policy in policies:
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
                if prepared[0]['capacity'] >= max(len(req.hashes) for req in prepared[2]):
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
        dict(policy=policy, **r) for policy in policies
        for r in summaries([p for p in pairs if p['policy'] == policy])])
    archived = ['runs.csv', 'pairs.csv', 'summary.csv']
    if args.joint_stress or args.heterogeneous:
        matched = {}
        for row in rows:
            grouped = matched.setdefault((row['scenario'], row['seed'], row['admission']), {})
            grouped[row['policy']] = row
        placement_pairs = []
        for (scenario, seed, admission), group in matched.items():
            candidate = group['shadow_cluster_finite']
            for baseline in policies[:-1]:
                a, b = candidate['p95_ttft_ms'], group[baseline]['p95_ttft_ms']
                delta = 100*(1-a/b)
                label = 'improve' if delta > 1e-7 else 'regress' if delta < -1e-7 else 'neutral'
                placement_pairs.append(dict(scenario=scenario, seed=seed, admission=admission,
                    candidate=candidate['policy'], baseline=baseline, candidate_p95_ms=a,
                    baseline_p95_ms=b, p95_improvement_pct=delta, outcome=label))
        write_csv(out/'placement_pairs.csv', placement_pairs)
        write_csv(out/'placement_summary.csv', [dict(admission=admission, **r)
            for admission in admissions for r in summaries(
                [p for p in placement_pairs if p['admission'] == admission])])
        archived.extend(['placement_pairs.csv', 'placement_summary.csv'])
    if args.archive:
        archive(out, args.archive, archived,
                '# Prefix-fit admission experiment\n\n'
                'Synthetic evaluation. Read pairs within the same placement policy. '
                'P95, traffic and reusable hot-prefix metrics cover every declared case. '
                'Admission avoids inserting the over-capacity tail of one request, '
                'while other requests can still evict its prefix according to LRU.')


if __name__ == '__main__':
    main()
