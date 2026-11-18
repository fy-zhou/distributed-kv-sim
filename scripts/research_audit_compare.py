#!/usr/bin/env python3
"""Pair every archived admission run before/after a model correction."""
import argparse
import csv
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import archive, new_directory, provenance, write_csv


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--corrected',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--archive',type=Path,required=True)
    args=p.parse_args()
    def read(path):
        with (path/'runs.csv').open(newline='') as f:
            rows=list(csv.DictReader(f))
        key=lambda r:(r['case'],r['seed'],r['admission'],r['policy'])
        mapped={key(r):r for r in rows}
        assert len(mapped)==len(rows)==40
        return mapped
    a,b=read(args.baseline),read(args.corrected)
    assert a.keys()==b.keys()
    pairs=[]
    for key in sorted(a):
        x,y=a[key],b[key]
        row=dict(case=key[0],seed=int(key[1]),admission=key[2],policy=key[3])
        for metric in ('p95_ms','traffic_gib','remote_request_count','mean_queue_ms'):
            row['baseline_'+metric]=float(x[metric])
            row['corrected_'+metric]=float(y[metric])
        row['p95_change_pct']=100*(float(y['p95_ms'])/float(x['p95_ms'])-1)
        row['all_metrics_equal']=all(x[k]==y[k] for k in x)
        pairs.append(row)
    out=new_directory(args.out)
    write_csv(out/'paired_model_correction.csv',pairs)
    changed=[r for r in pairs if not r['all_metrics_equal']]
    info=dict(runs=len(pairs),changed_runs=len(changed),
              min_p95_change_pct=min(r['p95_change_pct'] for r in pairs),
              max_p95_change_pct=max(r['p95_change_pct'] for r in pairs))
    (out/'summary.json').write_text(json.dumps(info,indent=2)+'\n')
    (out/'config.json').write_text(json.dumps(provenance(dict(
        baseline_commit='41d2f12',corrected_model_commit='192c94a',
        baseline=str(args.baseline),corrected=str(args.corrected),
        baseline_config=json.loads((args.baseline/'config.json').read_text()),
        corrected_config=json.loads((args.corrected/'config.json').read_text()))),indent=2)+'\n')
    archive(out,args.archive,['paired_model_correction.csv','summary.json'],
        '# Correctness audit: model correction replay\n\n'
        '40 paired synthetic scheduler/admission runs on the two previously selected strata.\n'
        'Baseline 41d2f12 versus corrected model 192c94a. No algorithm change.\n'
        'Changed runs: %d; P95 change range %.6f%% to %.6f%%.\n'
        'This checks sensitivity to confirmed implementation bugs, not hardware fidelity.' %
        (len(changed),info['min_p95_change_pct'],info['max_p95_change_pct']))
    print(json.dumps(info,indent=2))


if __name__=='__main__': main()
