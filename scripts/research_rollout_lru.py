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
from scripts.research_shadow import TIMING_FIELDS, trial

SEEDS = {'development': (51, 52), 'evaluation': (501, 502, 503, 504, 505)}
POLICIES = ('shadow_fast', 'shadow_fast_lru')
SHAPES = ('reference', 'compute_heavy', 'long_prefix')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    out = new_directory(args.out)
    (out/'config.json').write_text(json.dumps(provenance(dict(
        seeds=SEEDS[args.split], policies=POLICIES, shapes=SHAPES, requests=120,
        admissions=['always', 'no_remote'], repeats=2,
        order='AB then BA for each condition. Scheduler clock excludes setup of workload.',
        estimand='Mean total host CPU planning wall-time per request, paired by condition and repeat.')),
        indent=2)+'\n')
    rows, pairs = [], []
    for scenario in SCENARIOS:
        if scenario['name'] not in SHAPES:
            continue
        for seed in SEEDS[args.split]:
            for admission in ('always', 'no_remote'):
                original = None
                for repeat in (0, 1):
                    run = {}
                    for policy in POLICIES[::1 if not repeat else -1]:
                        _, row = trial(scenario, seed, admission, policy)
                        deterministic = {k:v for k,v in row.items()
                                         if k not in TIMING_FIELDS and k != 'policy'}
                        if original is None:
                            original = deterministic
                        assert original == deterministic, (scenario['name'], seed, admission)
                        run[policy] = row['total_planning_mean_us']
                        rows.append(dict(repeat=repeat, **row))
                    a, b = [run[policy] for policy in POLICIES]
                    pairs.append(dict(scenario=scenario['name'], seed=seed, admission=admission,
                        repeat=repeat, original_us=a, incremental_us=b, original_over_incremental=a/b))
        print(args.split, scenario['name'], len(rows), flush=True)
    write_csv(out/'runs.csv', rows)
    write_csv(out/'pairs.csv', pairs)
    write_csv(out/'summary.csv', [dict(scenario=name, pairs=len(group),
        median_original_us=statistics.median(r['original_us'] for r in group),
        median_incremental_us=statistics.median(r['incremental_us'] for r in group),
        median_paired_ratio=statistics.median(r['original_over_incremental'] for r in group))
        for name in SHAPES for group in [[r for r in pairs if r['scenario'] == name]]])
    archive(out, args.archive, ['runs.csv', 'pairs.csv', 'summary.csv'],
            '# Incremental private LRU timing\n\n'
            'Outcome, cache and forecast metrics must match the prior shortcut exactly. '
            'Wall-clock measurements on shared host, with paired AB/BA ordering.')


if __name__ == '__main__':
    main()
