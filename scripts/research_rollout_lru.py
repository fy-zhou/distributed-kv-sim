#!/usr/bin/env python3
"""Run the original and incremental private LRU in interleaved process order."""
import argparse
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import archive, new_directory, provenance, write_csv
from scripts.research_evaluate import SCENARIOS
from scripts.research_shadow import TIMING_FIELDS, inputs, trial

SEEDS = {'development': (51, 52), 'evaluation': (501, 502, 503, 504, 505)}
POLICIES = ('shadow_fast', 'shadow_fast_lru')
SHAPES = ('reference', 'compute_heavy', 'long_prefix')
LAZY_SEEDS = {'development': (3800, 3801), 'evaluation': (3901, 3902, 3903, 3904, 3905)}
STREAM_SEEDS = {'development': (2600, 2601), 'evaluation': (2501, 2502, 2503, 2504, 2505)}
EVENT_SEEDS = {'development': (2200, 2201), 'evaluation': (2101, 2102, 2103, 2104, 2105)}
GATE_SEEDS = {'development': (150, 151), 'evaluation': (1401, 1402, 1403, 1404, 1405)}
INDEX_SEEDS = {'development': (120, 121), 'evaluation': (1101, 1102, 1103, 1104, 1105)}
CLUSTER_LRU_SEEDS = {'development': (110, 111), 'evaluation': (1001, 1002, 1003, 1004, 1005)}
COLD_SEEDS = {'development': (91, 92), 'evaluation': (901, 902, 903, 904, 905)}
PRUNE_SEEDS = {'development': (81, 82), 'evaluation': (801, 802, 803, 804, 805)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    variants = parser.add_mutually_exclusive_group()
    variants.add_argument('--cluster-pruning', action='store_true')
    variants.add_argument('--cluster-cold', action='store_true')
    variants.add_argument('--cluster-lru', action='store_true')
    variants.add_argument('--cluster-indexed', action='store_true')
    variants.add_argument('--cluster-gated', action='store_true')
    variants.add_argument('--cluster-events', action='store_true')
    variants.add_argument('--cluster-event-stream', action='store_true')
    variants.add_argument('--cluster-lazy', action='store_true')
    args = parser.parse_args()
    scenarios, seeds, policies = SCENARIOS, SEEDS, POLICIES
    stress = args.cluster_pruning or args.cluster_lru or args.cluster_indexed or args.cluster_gated or args.cluster_events
    if stress:
        from scripts.research_rollout_efficiency import STRESS
        scenarios = STRESS
        seeds, policies = PRUNE_SEEDS, ('shadow_cluster', 'shadow_cluster_pruned')
    if args.cluster_lru:
        seeds, policies = CLUSTER_LRU_SEEDS, ('shadow_cluster_fast', 'shadow_cluster_lru')
    if args.cluster_indexed:
        seeds, policies = INDEX_SEEDS, ('shadow_cluster_lru', 'shadow_cluster_indexed')
    if args.cluster_gated:
        seeds, policies = GATE_SEEDS, ('shadow_cluster_lru', 'shadow_cluster_gated')
    if args.cluster_events:
        seeds, policies = EVENT_SEEDS, ('shadow_cluster_finite', 'shadow_cluster_events')
    if args.cluster_cold:
        seeds, policies = COLD_SEEDS, ('shadow_cluster_pruned', 'shadow_cluster_fast')
    if args.cluster_event_stream:
        stress = True
        seeds, policies = STREAM_SEEDS, ('shadow_cluster_finite', 'shadow_cluster_events')
        scenarios = [dict(name='stream_%d' % count, count=count,
                          shared=64, gap=1200., compute_scale=16., capacity=128)
                     for count in (128, 512, 2048)]
    if args.cluster_lazy:
        stress = True
        seeds, policies = LAZY_SEEDS, ('shadow_cluster_events', 'shadow_cluster_lazy')
        scenarios = [
            dict(name='sparse_cold', shared=64, capacity=128, burst=1, gap=1200.),
            dict(name='sparse_warm', shared=256, compute_scale=8., burst=1, gap=1200.),
            dict(name='sparse_warm_long', shared=512, capacity=1024, compute_scale=8.,
                 burst=1, gap=4000.),
            dict(name='burst_warm', compute_scale=16., burst=12, gap=2.),
        ]
    shapes = tuple(s['name'] for s in scenarios if stress or s['name'] in SHAPES)
    out = new_directory(args.out)
    (out/'config.json').write_text(json.dumps(provenance(dict(
        seeds=seeds[args.split], policies=policies, shapes=shapes,
        requests_by_shape={s['name']:s.get('count', 120) for s in scenarios if s['name'] in shapes},
        admissions=['always', 'no_remote'], repeats=2,
        scenario_parameters=[s for s in scenarios if s['name'] in shapes],
        order='AB then BA for each condition. Scheduler clock excludes setup of workload.',
        estimand='Mean total host CPU planning wall-time per request, paired by condition and repeat.')),
        indent=2)+'\n')
    rows, pairs = [], []
    for scenario in scenarios:
        if scenario['name'] not in shapes:
            continue
        count = scenario.get('count',120)
        for seed in seeds[args.split]:
            prepared = inputs(scenario, seed, count)
            for admission in ('always', 'no_remote'):
                original = None
                for repeat in (0, 1):
                    run = {}
                    for policy in policies[::1 if not repeat else -1]:
                        _, row = trial(scenario, seed, admission, policy, count, prepared=prepared)
                        deterministic = {k:v for k,v in row.items()
                                         if k not in TIMING_FIELDS and k != 'policy'}
                        if original is None:
                            original = deterministic
                        assert original == deterministic, (scenario['name'], seed, admission)
                        run[policy] = row['total_planning_mean_us']
                        rows.append(dict(repeat=repeat, **row))
                    a, b = [run[policy] for policy in policies]
                    pairs.append(dict(scenario=scenario['name'], seed=seed, admission=admission,
                        repeat=repeat, original_us=a, incremental_us=b, original_over_incremental=a/b))
        print(args.split, scenario['name'], len(rows), flush=True)
    write_csv(out/'runs.csv', rows)
    write_csv(out/'pairs.csv', pairs)
    write_csv(out/'summary.csv', [dict(scenario=name, pairs=len(group),
        median_original_us=statistics.median(r['original_us'] for r in group),
        median_incremental_us=statistics.median(r['incremental_us'] for r in group),
        median_paired_ratio=statistics.median(r['original_over_incremental'] for r in group))
        for name in shapes for group in [[r for r in pairs if r['scenario'] == name]]])
    archive(out, args.archive, ['runs.csv', 'pairs.csv', 'summary.csv'],
            '# Paired equivalent forecast timing\n\n'
            'Outcome, cache and forecast metrics must match the unoptimized comparator exactly. '
            'Wall-clock measurements on shared host, with paired AB/BA ordering.')


if __name__ == '__main__':
    main()
