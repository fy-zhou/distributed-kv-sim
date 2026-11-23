#!/usr/bin/env python3
"""Compare equivalent fast rollout and known-transfer replay on fresh seeds."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import archive, digest, new_directory, provenance, write_csv
from scripts.research_evaluate import SCENARIOS
from scripts.research_shadow import TIMING_FIELDS, summaries, trial

SEEDS = {'development': (31, 32), 'evaluation': (301, 302, 303, 304, 305)}
STRESS_SEEDS = {'development': (61, 62), 'evaluation': (601, 602, 603, 604, 605)}
STRESS = (
    dict(name='endpoint_pressure', compute_scale=16., nic=2., fabric=16., burst=12),
    dict(name='fabric_pressure', compute_scale=16., nic=16., fabric=2., burst=12),
    dict(name='pin_pressure', compute_scale=16., nic=4., fabric=4., capacity=256, burst=12),
    dict(name='replica_pressure', compute_scale=16., nic=4., fabric=4., nodes=8, burst=16),
)
POLICIES = ('round_robin_cost', 'cost_aware', 'shadow_cache',
            'shadow_fast', 'shadow_network')
INGRESS = {'round_robin_cost': .071, 'cost_aware': 1.271, 'shadow_cache': 7.026,
           'shadow_fast': 7.026, 'shadow_network': 7.026}


def key(row):
    return row['scenario'], int(row['seed']), row['admission'], row['policy'], row['ingress_scale']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--verify-against', type=Path)
    parser.add_argument('--ingress', type=Path, help='Use development timing CSV to calibrate sensitivity')
    parser.add_argument('--network-stress', action='store_true')

    args = parser.parse_args()
    if args.network_stress and args.ingress:
        parser.error('network stress and ingress are separate experiments')
    out = new_directory(args.out)
    scenarios = STRESS if args.network_stress else SCENARIOS
    seed_set = STRESS_SEEDS if args.network_stress else SEEDS
    policies = POLICIES
    scales = (0.,)
    rates = dict(INGRESS)
    if args.ingress:
        import statistics
        with args.ingress.open(newline='') as handle:
            development = list(csv.DictReader(handle))
        if {int(r['seed']) for r in development} != set(SEEDS['development']):
            parser.error('ingress rates require the declared development seeds')
        rates = {p: statistics.mean(float(r['total_planning_mean_us'])
                                     for r in development if r['policy'] == p) / 1000
                 for p in policies}
        scenarios = [s for s in scenarios if s['name'] in
                     ('reference', 'compute_heavy', 'long_prefix', 'slow_arrivals')]
        scales = (.25, 1.)
    (out/'config.json').write_text(json.dumps(provenance(dict(
        split=args.split, seeds=seed_set[args.split], scenarios=scenarios, policies=policies,
        requests=120, admissions=['always', 'no_remote'], ingress_rates_ms=rates,
        ingress_scales=scales, ingress_source=str(args.ingress) if args.ingress else None,
        ingress_source_sha256=hashlib.sha256(args.ingress.read_bytes()).hexdigest() if args.ingress else None,
        ingress_model='Serial FCFS pooled planning proxy before simulator arrival. '
                      'TTFT includes ingress delay. Forecast errors are measured after dispatch.',
        network_model='Known active and setup transfers only. Future queued transfers use isolated links.',
        planning_time='Placement plus execution replanning, measured on shared CPU host.')),
        indent=2)+'\n')
    expected = {}
    if args.verify_against:
        with args.verify_against.open(newline='') as handle:
            expected = {key(r)[:-1]+(float(r['ingress_scale']),): r for r in csv.DictReader(handle)}
    rows, pairs = [], []
    with gzip.GzipFile(str(out/'traces.jsonl.gz'), 'wb', mtime=0) as traces:
        for scenario in scenarios:
            for seed in seed_set[args.split]:
                for admission in ('always', 'no_remote'):
                    for scale in scales:
                        results = {}
                        order = sorted(policies, key=lambda p: digest([scenario['name'], seed, admission, p]))
                        for policy in order:
                            sim, row = trial(scenario, seed, admission, policy,
                                             ingress_ms=scale*rates[policy])
                            row.update(ingress_scale=scale, ingress_ms=scale*rates[policy])
                            results[policy] = row
                            rows.append(row)
                            if args.verify_against:
                                reference = expected.pop(key(row))
                                for name, value in row.items():
                                    if name not in TIMING_FIELDS:
                                        assert str(value) == reference[name], (key(row), name)
                            else:
                                traces.write((json.dumps(dict(key=key(row), requests=sim.rows,
                                    predictions=sim.residuals), separators=(',', ':'))+'\n').encode())
                        assert len({r['trace_hash'] for r in results.values()}) == 1
                        assert len({r['initial_cache_hash'] for r in results.values()}) == 1
                        if not scale:
                            a, b = results['shadow_cache'], results['shadow_fast']
                            assert all(a[k] == b[k] for k in a
                                       if k not in TIMING_FIELDS and k != 'policy')
                        for candidate in ('shadow_fast', 'shadow_network'):
                            for baseline in ('round_robin_cost', 'cost_aware', 'shadow_fast'):
                                if candidate == baseline:
                                    continue
                                a = results[candidate]['p95_ttft_ms']
                                b = results[baseline]['p95_ttft_ms']
                                delta = 100*(1-a/b)
                                outcome = 'improve' if delta > 1e-7 else 'regress' if delta < -1e-7 else 'neutral'
                                pairs.append(dict(scenario=scenario['name'], seed=seed, admission=admission,
                                    ingress_scale=scale, candidate=candidate, baseline=baseline,
                                    candidate_p95_ms=a, baseline_p95_ms=b, p95_improvement_pct=delta,
                                    outcome=outcome))
            write_csv(out/'runs.csv', rows)
            print(args.split, scenario['name'], len(rows), flush=True)
    assert not expected
    write_csv(out/'pairs.csv', pairs)
    summary = [dict(ingress_scale=scale, **x) for scale in scales
               for x in summaries([r for r in pairs if r['ingress_scale'] == scale])]
    write_csv(out/'summary.csv', summary)
    if args.verify_against:
        (out/'verification.json').write_text(json.dumps(dict(verified_runs=len(rows),
            ignored=TIMING_FIELDS, reference=str(args.verify_against),
            reference_sha256=hashlib.sha256(args.verify_against.read_bytes()).hexdigest()), indent=2)+'\n')
    if args.archive:
        write_csv(out/'compact_runs.csv', [{k: round(v, 6) if isinstance(v, float) else v
            for k, v in row.items() if not k.endswith('_hash')} for row in rows])
        archive(out, args.archive, ['compact_runs.csv', 'pairs.csv', 'summary.csv'],
                '# Shadow rollout efficiency and network forecast\n\n'
                'Synthetic TTFT. Each placement policy uses the same execution service planner. '
                'Whole-seed bootstrap is descriptive. Raw outcomes and predictions remain on the server. '
                'Ingress mode is a serial CPU sensitivity proxy calibrated on development only.')


if __name__ == '__main__':
    main()
