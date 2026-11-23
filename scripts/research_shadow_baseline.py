#!/usr/bin/env python3
"""Regression gate on consumed historical seeds; no new candidates evaluated."""
import argparse
import csv
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.research_evaluate import POLICIES,SCENARIOS
from scripts.research_shadow import trial
from scripts.research_common import new_directory,provenance


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    with args.reference.open(newline='')as f:
        rows=[r for r in csv.DictReader(f)if r['seed']=='101']
    expected={(r['scenario'],r['admission'],r['policy']):r for r in rows}
    assert len(rows)==len(expected)==196
    out=new_directory(args.out)
    (out/'config.json').write_text(json.dumps(provenance(dict(reference=str(args.reference),
        seed=101,note='Consumed seed: legacy regression check only, not independent candidate validation.')),indent=2)+'\n')
    checked=0
    for scenario in SCENARIOS:
        for admission in ('always','no_remote'):
            for policy in POLICIES:
                _,actual=trial(scenario,101,admission,policy,120)
                old=expected.pop((scenario['name'],admission,policy))
                for key in old:
                    if key.startswith('decision_'):continue
                    assert str(actual[key])==old[key],(scenario['name'],admission,policy,key,actual[key],old[key])
                checked+=1
        print('Legacy check',scenario['name'],checked,flush=True)
    assert not expected
    (out/'verification.json').write_text(json.dumps(dict(verified_runs=checked,
        compared='Every old field except wall-clock placement timing, including full request and final cache hashes.'),indent=2)+'\n')


if __name__=='__main__':main()
