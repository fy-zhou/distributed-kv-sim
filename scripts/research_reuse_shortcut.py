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

FLOOR_SEEDS = {'development': (3600, 3601), 'evaluation': (3701, 3702, 3703, 3704, 3705)}
MEMO_SEEDS = {'development': (2400, 2401), 'evaluation': (2301, 2302, 2303, 2304, 2305)}
SEEDS = {'development': (2300, 2301), 'evaluation': (2201, 2202, 2203, 2204, 2205)}
POLICIES = ('round_robin_cost', 'shadow_cluster_events')
SHAPES = ('reference', 'compute_heavy', 'short_prefix', 'long_prefix')
PARTIAL = dict(name='partial_prefix', kind='zipf', shared=256, capacity=160, compute_scale=1.35)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--memoized', action='store_true')
    modes.add_argument('--prefix-floor', action='store_true')
    args = parser.parse_args()
    seeds = FLOOR_SEEDS if args.prefix_floor else MEMO_SEEDS if args.memoized else SEEDS
    shapes = SHAPES + (PARTIAL['name'],) if args.prefix_floor else SHAPES
    scenarios = SCENARIOS + [PARTIAL]
    out = new_directory(args.out)
    count = 80
    rows, paired = [], []
    for scenario in scenarios:
        name = scenario['name']
        if name not in shapes:
            continue
        admissions = ('always', 'prefix_fit') if name == PARTIAL['name'] else ('always', 'no_remote')
        for seed in seeds[args.split]:
            prepared = inputs(scenario, seed, count)
            for admission in admissions:
                for policy in POLICIES:
                    original = None
                    for repeat in (0, 1):
                        values = {}
                        for enabled in (False, True)[::1 if not repeat else -1]:
                            _, row = trial(scenario, seed, admission, policy, count,
                                prepared=prepared, certified_reuse_shortcut=enabled or args.memoized or args.prefix_floor,
                                memoized_reuse_certificate=(enabled and args.memoized)
                                    or (args.prefix_floor and not enabled),
                                reuse_prefix_floor=args.prefix_floor and enabled)
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
    for name in shapes:
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
        seeds=seeds[args.split], memoized=args.memoized, prefix_floor=args.prefix_floor,
        policies=POLICIES, shapes=shapes, requests=count,
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
