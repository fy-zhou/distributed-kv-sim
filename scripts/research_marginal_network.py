#!/usr/bin/env python3
"""Evaluate finite marginal network penalties with identical execution routes."""
import argparse
import gzip
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import archive, digest, new_directory, provenance, write_csv
from scripts.research_rollout_efficiency import STRESS
from scripts.research_shadow import inputs, summaries, trial

SEEDS = {'development': (3200, 3201), 'evaluation': (3301, 3302, 3303, 3304, 3305)}
SETTINGS = {'rr': ('round_robin_cost', 0.), 'routes': ('shadow_cluster_routes', 0.),
            'marginal_quarter': ('shadow_cluster_marginal', .25),
            'marginal_full': ('shadow_cluster_marginal', 1.)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    out = new_directory(args.out)
    config = provenance(dict(split=args.split, seeds=SEEDS[args.split], scenarios=STRESS,
        settings=SETTINGS, admission='always', requests=120, finite_execution_routes=True,
        penalty='Change in summed transfer completion times to currently known work; '
                'charge at predicted readiness of candidate node. Add nonnegative penalty to placement only.',
        limits='No future arrivals or queued requests’ future transfer starts enter the marginal probe. '
               'Does not choose a different KV route to reduce the penalty.'))
    (out/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    rows, pairs = [], []
    with gzip.GzipFile(str(out/'traces.jsonl.gz'), 'wb', mtime=0) as trace:
        for scenario in STRESS:
            for seed in SEEDS[args.split]:
                prepared = inputs(scenario, seed, 120)
                group = {}
                order = sorted(SETTINGS, key=lambda setting: digest([scenario['name'], seed, setting]))
                for setting in order:
                    policy, weight = SETTINGS[setting]
                    sim, row = trial(scenario, seed, 'always', policy, prepared=prepared,
                                     finite_execution_routes=True, network_externality_weight=weight)
                    penalties = [p.get('known_flow_externality_ms', 0.)
                                 for p in sim.selected_forecasts.values()]
                    row.update(setting=setting, weight=weight,
                        selected_positive_penalties=sum(p > 0. for p in penalties),
                        mean_selected_penalty_ms=statistics.mean(penalties),
                        assignment_hash=digest(sorted((r['request_id'], r['node']) for r in sim.rows)))
                    trace.write((json.dumps(dict(key=[scenario['name'], seed, setting],
                        requests=sim.rows, forecast=sim.selected_forecasts),
                        separators=(',', ':'))+'\n').encode())
                    rows.append(row)
                    group[setting] = row
                assert len({r['trace_hash'] for r in group.values()}) == 1
                assert len({r['initial_cache_hash'] for r in group.values()}) == 1
                for candidate in list(SETTINGS)[1:]:
                    for baseline in (('rr',) if candidate == 'routes' else ('rr', 'routes')):
                        c, b = group[candidate], group[baseline]
                        delta = 100*(1-c['p95_ttft_ms']/b['p95_ttft_ms'])
                        pairs.append(dict(scenario=scenario['name'], seed=seed, candidate=candidate,
                            baseline=baseline, candidate_p95_ms=c['p95_ttft_ms'], baseline_p95_ms=b['p95_ttft_ms'],
                            p95_improvement_pct=delta,
                            outcome='improve' if delta > 1e-7 else 'regress' if delta < -1e-7 else 'neutral',
                            candidate_gib=c['network_gib'], baseline_gib=b['network_gib'],
                            assignments_changed=c['assignment_hash'] != b['assignment_hash']))
            write_csv(out/'runs.csv', rows)
            print('Completed', args.split, scenario['name'], len(rows), flush=True)
    write_csv(out/'pairs.csv', pairs)
    write_csv(out/'summary.csv', summaries(pairs))
    if args.archive:
        archive(out, args.archive, ('runs.csv', 'pairs.csv', 'summary.csv'),
                '# Marginal delay from proposed remote KV routes\n\n'
                'Both weights are fixed sensitivities. All placement policies use finite service-route selection.')


if __name__ == '__main__':
    main()
