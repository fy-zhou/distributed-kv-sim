#!/usr/bin/env python3
"""Preserve all H2D pilots and affinity runs, including the allocation failure."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.research_common import archive,new_directory,provenance,write_csv


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--archive',type=Path,required=True)
    args=p.parse_args()
    names=['research-20261118-h2d','research-20261118-h2d-corrected']+[
        'research-20261118-h2d-affinity-%d-cpu%d'%(i,cpu)for i,cpu in enumerate((16,80,80,16))]
    rows,sources=[],[]
    for name in names:
        root=args.root/name
        config=json.loads((root/'config.json').read_text())
        completion=json.loads((root/'completion.json').read_text())
        sources.append(dict(directory=str(root),config=config,completion=completion,
            sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest()for f in root.iterdir()if f.is_file()}))
        with (root/'summary.csv').open(newline='')as f:
            for row in csv.DictReader(f):
                rows.append(dict(run=name,cpu_affinity=json.dumps(config['config'].get('cpu_affinity','unrecorded')),
                    peak_tensor_bytes=completion['peak_tensor_bytes'],
                    declared_memory_bound_met=completion['peak_tensor_bytes']<=128*2**20,**row))
    out=new_directory(args.out)
    write_csv(out/'summary_all_attempts.csv',rows)
    metadata=provenance(dict(runs=sources,
        measurement='Physical single-host CUDA pinned H2D; not simulated and not a multi-node network.'))
    metadata['synthetic']=False
    metadata['note']='Physical single-host H2D measurements; no inter-node NIC measurement or simulator calibration replacement.'
    (out/'config.json').write_text(json.dumps(metadata,indent=2)+'\n')
    archive(out,args.archive,['summary_all_attempts.csv'],
        '# Bounded H2D and CPU-affinity experiments\n\n'
        'Physical single-host RTX4090 measurements, distinct from simulator results.\n'
        'Every pilot retained; the first exceeds its declared tensor-memory bound (192 vs 128 MiB). '
        'Corrected and all four ABBA affinity runs pass the measured 128 MiB tensor bound.\n'
        'CPU 16 is on GPU0-local NUMA 0; CPU 80 on NUMA 1. '
        '64 MiB single-copy throughput: near 22.41/22.28 GiB/s, far 10.18/10.24 GiB/s. '
        'Two-stream far-socket throughput is variable (10.46/18.92 GiB/s).\n'
        'Two process replicates per affinity, ordered ABBA; shared host, GPU1 occupied. '
        'Affinity is controlled, physical page placement is not independently verified. '
        'No KV scatter, prefill, inter-node NIC measurement, or simulator default replacement. '
        'CUDA event makespan includes launch gaps. All 720 raw samples and environment records '
        'remain outside Git and are hashed in manifest.json.')
    print('Archived',len(rows),'summary cells from',len(sources),'runs')


if __name__=='__main__':main()
