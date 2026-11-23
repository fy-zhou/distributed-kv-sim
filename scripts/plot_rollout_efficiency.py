#!/usr/bin/env python3
"""Plot paired rollout P95 outcomes alongside host planning costs."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import new_directory, provenance


def read(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = new_directory(args.out)
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt

    runs, pairs = [read(args.source/name) for name in ('runs.csv', 'pairs.csv')]
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 5.4), layout='constrained',
                                     gridspec_kw={'width_ratios': [1, 1.6]})
    policies = ['round_robin_cost', 'cost_aware', 'shadow_cache', 'shadow_fast', 'shadow_network']
    labels = ['RR-cost', 'Cost-aware', 'Full Shadow', 'Cold shortcut', 'Known flows']
    groups = [[r for r in runs if r['policy'] == p] for p in policies]
    placement = [statistics.mean(float(r['decision_mean_us']) for r in g)/1000 for g in groups]
    replanning = [statistics.mean(float(r['execution_plan_mean_us']) for r in g)/1000 for g in groups]
    left.barh(labels, placement, label='Placement', color='#21618c')
    left.barh(labels, replanning, left=placement, label='Execution planning', color='#e88b31')
    left.invert_yaxis()
    left.set_xlabel('Mean host time (ms/request)')
    left.legend(loc='lower right', frameon=False)
    left.set_title('Measured planning overhead')

    shapes = sorted({r['scenario'] for r in pairs})
    for index, (name, label, color) in enumerate([
            ('shadow_fast', 'Cold shortcut', '#21618c'),
            ('shadow_network', 'Known flows', '#bf5f24')]):
        offsets, positions, limits = [], [], []
        for number, shape in enumerate(shapes):
            values = [float(r['p95_improvement_pct']) for r in pairs
                      if r['candidate'] == name and r['baseline'] == 'round_robin_cost'
                      and r['scenario'] == shape]
            avg = statistics.mean(values)
            offsets.append(avg)
            positions.append(number + (index-.5)*.28)
            limits.append((avg-min(values), max(values)-avg))
        right.errorbar(offsets, positions, xerr=list(zip(*limits)), fmt='o', color=color,
                       markersize=4, capsize=2, label=label)
    right.axvline(0, color='.65', lw=.8)
    right.set_yticks(range(len(shapes)), [shape.replace('_', ' ') for shape in shapes])
    right.invert_yaxis()
    right.set_xlabel('Simulated P95 improvement vs RR-cost (%)')
    right.set_title('Means and observed ranges')
    right.legend(loc='lower right', frameon=False)
    fig.savefig(out/'efficiency.svg', metadata={'Date': None})
    fig.savefig(out/'efficiency.png', dpi=115)
    config = provenance(dict(matplotlib=matplotlib.__version__,
        input_sha256={name:hashlib.sha256((args.source/name).read_bytes()).hexdigest()
                      for name in ('runs.csv', 'pairs.csv')}))
    (out/'figure_manifest.json').write_text(json.dumps(config, indent=2)+'\n')


if __name__ == '__main__':
    main()
