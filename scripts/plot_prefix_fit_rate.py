#!/usr/bin/env python3
"""Plot paired prefix admission performance over the declared offered loads."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import new_directory, provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pairs', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = new_directory(args.out)
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt

    with args.pairs.open(newline='') as handle:
        pairs = list(csv.DictReader(handle))
    gaps = (2, 40, 250, 1000)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), layout='constrained')
    for policy, color, label in [('round_robin_cost', '#22618c', 'RR-cost'),
                                 ('shadow_cluster_lru', '#a24b29', 'Cluster LRU')]:
        cells = [[r for r in pairs if r['scenario'] == 'gap_%d_ms' % gap
                  and r['policy'] == policy] for gap in gaps]
        for ax, field in zip(axes, ('p95_improvement_pct', 'traffic_delta_gib')):
            values = [[float(r[field]) if field in r else float(r['candidate_traffic_gib'])
                       - float(r['baseline_traffic_gib']) for r in cell] for cell in cells]
            means = [statistics.mean(v) for v in values]
            limits = [(mean-min(v), max(v)-mean) for mean, v in zip(means, values)]
            ax.errorbar(gaps, means, yerr=list(zip(*limits)), marker='o', color=color,
                        capsize=3, label=label)
    for ax in axes:
        ax.set_xscale('log')
        ax.set_xticks(gaps, list(map(str, gaps)))
        ax.axhline(0., color='.65', linewidth=.8)
        ax.set_xlabel('Burst gap (ms)')
    axes[0].set_ylabel('P95 improvement vs always (%)')
    axes[1].set_ylabel('Traffic change vs always (GiB)')
    axes[1].legend(frameon=False)
    fig.suptitle('Prefix-fit admission at the capacity cliff\nMeans and observed ranges across seeds, simulated')
    fig.savefig(out/'offered_load.svg', metadata={'Date': None})
    fig.savefig(out/'offered_load.png', dpi=115)
    meta = provenance(dict(input_pairs_sha256=hashlib.sha256(args.pairs.read_bytes()).hexdigest(),
                           matplotlib=matplotlib.__version__))
    (out/'figure_manifest.json').write_text(json.dumps(meta, indent=2)+'\n')


if __name__ == '__main__':
    main()
