#!/usr/bin/env python3
"""Compare execution-planner cold certificate under unchanged policy actions."""
import argparse
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import archive, new_directory, provenance, write_csv
from scripts.research_evaluate import SCENARIOS
from scripts.research_shadow import TIMING_FIELDS, inputs, trial

SEEDS = {'development': (2300, 2301), 'evaluation': (2201, 2202, 2203, 2204, 2205)}
POLICIES = ('round_robin_cost', 'shadow_cluster_events')
SHAPES = ('reference', 'compute_heavy', 'short_prefix', 'long_prefix')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    out = new_directory(args.out)
    count = 80
    rows, paired = [], []
    for scenario in SCENARIOS:
        name = scenario['name']
        if name not in SHAPES:
            continue
        for seed in SEEDS[args.split]:
            prepared = inputs(scenario, seed, count)
            for admission in ('always', 'no_remote'):
                for policy in POLICIES:
                    original = None
                    for repeat in (0, 1):
                        values = {}
                        for enabled in (False, True)[::1 if not repeat else -1]:
                            _, row = trial(scenario, seed, admission, policy, count,
                                prepared=prepared, certified_reuse_shortcut=enabled)
                            deterministic = {k:v for k,v in row.items() if k not in TIMING_FIELDS}
                            if original is None:
                                original = deterministic
                            assert deterministic == original, (name, seed, admission, policy)
                            values[enabled] = row
                            rows.append(dict(repeat=repeat, shortcut=enabled, **row))
                        a, b = values[False], values[True]
                        paired.append(dict(scenario=name, seed=seed, admission=admission,
                            policy=policy, repeat=repeat,
                            original_execution_us=a['execution_plan_mean_us'],
                            shortcut_execution_us=b['execution_plan_mean_us'],
                            execution_speed_ratio=a['execution_plan_mean_us']/b['execution_plan_mean_us'],
                            original_total_us=a['total_planning_mean_us'],
                            shortcut_total_us=b['total_planning_mean_us'],
                            total_speed_ratio=a['total_planning_mean_us']/b['total_planning_mean_us']))
        print(args.split, name, len(rows), flush=True)
    summary = []
    for name in SHAPES:
        for policy in POLICIES:
            group = [r for r in paired if (r['scenario'], r['policy']) == (name, policy)]
            summary.append(dict(scenario=name, policy=policy, pairs=len(group), **{
                'median_'+k: statistics.median(r[k] for r in group)
                for k in ('original_execution_us', 'shortcut_execution_us',
                          'execution_speed_ratio', 'original_total_us',
                          'shortcut_total_us', 'total_speed_ratio')}))
    write_csv(out/'runs.csv', rows)
    write_csv(out/'pairs.csv', paired)
    write_csv(out/'summary.csv', summary)
    (out/'config.json').write_text(json.dumps(provenance(dict(
        seeds=SEEDS[args.split], policies=POLICIES, shapes=SHAPES, requests=count,
        timing='AB/BA repeats within every paired workload, measured on the host.',
        interpretation='All deterministic results including outcome and ordered-cache hashes must match.'
                       ' Execution-planning and total CPU costs are reported separately.')),
        indent=2)+'\n')
    archive(out, args.archive, ['runs.csv', 'pairs.csv', 'summary.csv'],
            '# Optional certified execution shortcut\n\n'
            'All output hashes and forecast metrics agree within each pair. '
            'Timing measurements compare the same placement and execution actions.')


if __name__ == '__main__':
    main()
