#!/usr/bin/env python3
"""Cross finite-route placement and execution against action-matched controls."""
import argparse
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import archive, digest, new_directory, provenance, write_csv
from scripts.research_rollout_efficiency import STRESS
from scripts.research_shadow import inputs, summaries, trial

SEEDS = {'development': (3000, 3001), 'evaluation': (3101, 3102, 3103, 3104, 3105)}
POLICIES = ('round_robin_cost', 'shadow_cluster_finite', 'shadow_cluster_routes')
EXECUTION = ('instant', 'finite')
SCENARIOS = STRESS + (
    dict(name='threshold_endpoint', compute_scale=9.5, nic=2., fabric=16., burst=12),
    dict(name='threshold_fabric', compute_scale=12., nic=16., fabric=2., burst=12),
)


def compare(group, scenario, seed):
    assert len({r['trace_hash'] for r in group.values()}) == 1
    assert len({r['initial_cache_hash'] for r in group.values()}) == 1
    assert group['instant', POLICIES[0]]['assignment_hash'] == group['finite', POLICIES[0]]['assignment_hash']
    contrasts = []
    for model in EXECUTION:
        contrasts.extend([((model, policy), (model, POLICIES[0])) for policy in POLICIES[1:]])
        contrasts.append(((model, POLICIES[2]), (model, POLICIES[1])))
    contrasts.extend((('finite', policy), ('instant', policy)) for policy in POLICIES)
    for candidate, baseline in contrasts:
        cr, br = group[candidate], group[baseline]
        c, b = cr['p95_ttft_ms'], br['p95_ttft_ms']
        delta = 100*(1-c/b)
        yield dict(scenario=scenario, seed=seed, candidate='/'.join(candidate),
                   baseline='/'.join(baseline), candidate_p95_ms=c, baseline_p95_ms=b,
                   p95_improvement_pct=delta,
                   outcome='improve' if delta > 1e-7 else 'regress' if delta < -1e-7 else 'neutral',
                   candidate_gib=cr['network_gib'], baseline_gib=br['network_gib'],
                   service_actions_changed=cr['service_hash'] != br['service_hash'],
                   assignments_changed=cr['assignment_hash'] != br['assignment_hash'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    out = new_directory(args.out)
    config = provenance(dict(split=args.split, seeds=SEEDS[args.split], scenarios=SCENARIOS,
        policies=POLICIES, execution=EXECUTION, admission='always', requests=120,
        hypothesis='Considering alternatives after active and pending transfers progress may reverse the preferred route.',
        limitations='No future request or future queued-job network starts enter the finite service choice. '
                    'All policies retain direct recompute/local/one-source remote options.'))
    (out/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    rows, pairs = [], []
    with gzip.GzipFile(str(out/'traces.jsonl.gz'), 'wb', mtime=0) as trace:
        for scenario in SCENARIOS:
            for seed in SEEDS[args.split]:
                prepared = inputs(scenario, seed, 120)
                group = {}
                order = sorted(((m, p) for m in EXECUTION for p in POLICIES),
                               key=lambda key: digest([scenario['name'], seed, key]))
                for model, policy in order:
                    sim, row = trial(scenario, seed, 'always', policy, prepared=prepared,
                                     finite_execution_routes=model == 'finite')
                    row.update(execution_model=model,
                        assignment_hash=digest(sorted((r['request_id'], r['node']) for r in sim.rows)),
                        service_hash=digest(sorted((r['request_id'], r['source'], r['total_hit_tokens'],
                                                    r['remote_hit_tokens']) for r in sim.rows)))
                    trace.write((json.dumps(dict(key=[scenario['name'], seed, model, policy],
                        requests=sim.rows, forecasts=sim.residuals), separators=(',', ':'))+'\n').encode())
                    group[model, policy] = row
                    rows.append(row)
                pairs.extend(compare(group, scenario['name'], seed))
            write_csv(out/'runs.csv', rows)
            print('Completed', args.split, scenario['name'], len(rows), flush=True)
    write_csv(out/'pairs.csv', pairs)
    write_csv(out/'summary.csv', summaries(pairs))
    if args.archive:
        archive(out, args.archive, ('runs.csv', 'pairs.csv', 'summary.csv'),
                '# Finite KV route placement and execution\n\n'
                'The same placement policy uses either default or finite service-route selection at execution. '
                'Placement comparisons always hold execution semantics fixed.')


if __name__ == '__main__':
    main()
