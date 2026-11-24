#!/usr/bin/env python3
"""Render bounded RTX 4090 calibration summaries to an inspectable SVG."""
import argparse
import csv
from pathlib import Path


def rows(root, name):
    with (root/name/'summary.csv').open(newline='') as handle:
        return list(csv.DictReader(handle))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiments', type=Path, default=Path('experiments'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plt.rcParams.update({'svg.hashsalt': 'kv-sim-4090-20261124', 'svg.fonttype': 'none',
                         'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
    fig, (copy_ax, prefill_ax) = plt.subplots(1, 2, figsize=(10, 4), layout='constrained')
    for direction, name, color in [
            ('H2D', 'research-idle-copy-h2d-20261124', '#226494'),
            ('D2H', 'research-idle-copy-d2h-20261124', '#a55228')]:
        data = rows(args.experiments, name)
        for count, linestyle in [('1', '-'), ('2', '--')]:
            series = [r for r in data if r['streams'] == count]
            copy_ax.plot([int(r['payload_per_stream_bytes'])/2**20 for r in series],
                         [float(r['aggregate_gib_s']) for r in series], 'o', linestyle=linestyle,
                         color=color, label=f'{direction}, {count} stream(s)')
    copy_ax.set_xscale('log', base=2)
    copy_ax.set_xticks([.0625, 1, 16, 64], ['0.0625', '1', '16', '64'])
    copy_ax.set_xlabel('Payload per stream (MiB)')
    copy_ax.set_ylabel('Aggregate throughput (GiB/s)')
    copy_ax.set_title('Contiguous pinned-memory copies')
    copy_ax.legend(frameon=False, loc='upper left', fontsize=8)

    for name, label, marker, color in [
            ('research-prefill-ascending-20261124', 'Ascending hit fractions', 'o', '#226494'),
            ('research-prefill-descending-20261124', 'Descending hit fractions', 's', '#a55228')]:
        series = sorted((r for r in rows(args.experiments, name) if r['length'] == '4096'),
                        key=lambda r: int(r['hit_tokens']))
        prefill_ax.plot([int(r['hit_tokens']) for r in series],
                        [float(r['median_cuda_ms']) for r in series],
                        marker=marker, color=color, label=label)
    prefill_ax.set_xticks([0, 1024, 2048, 3584])
    prefill_ax.set_xlabel('Reused tokens out of 4096')
    prefill_ax.set_ylabel('Median CUDA event interval (ms)')
    prefill_ax.set_title('Qwen3-0.6B BF16 decoder')
    prefill_ax.legend(frameon=False, fontsize=8)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, metadata={'Date': None})
    plt.close(fig)


if __name__ == '__main__':
    main()
