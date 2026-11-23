#!/usr/bin/env python3
"""Development-only bound diagnostic for forecast work that cannot affect cost.

For the current nonnegative causal-pair coefficient, service for positive hit
length is concave in H: compute(L,H)+fixed_H2D+linear_H2D(H). If both endpoint
costs are >= cold compute, no local H can be cheaper. Remote reuse adds a
nonnegative transfer cost. This does not change any scheduler implementation.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kv_sim import Config,Simulator
from scripts.research_evaluate import SCENARIOS
from scripts.research_shadow import inputs
from scripts.research_common import new_directory,provenance,write_csv


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();out=new_directory(args.out)
    rows=[]
    for scenario in SCENARIOS:
        for seed in (21,22):
            params,specs,reqs,_,_=inputs(scenario,seed,120)
            sim=Simulator(specs,Config(gpu_ms_per_new_token=.003*params['compute_scale'],
                gpu_ms_per_attention_pair=1.8e-6*params['compute_scale']))
            assert sim.cfg.gpu_ms_per_attention_pair>=0 and sim.cfg.h2d_fixed_ms>=0 and sim.cfg.remote_setup_ms>=0
            dominated=checked=0
            for req in reqs:
                cold=sim._compute_time(req,0)
                full=sim._h2d_time(len(req.hashes))+sim._compute_time(req,len(req.hashes))
                if full>cold+1e-9:
                    dominated+=1
                    for hit in range(1,len(req.hashes)+1):
                        assert sim._h2d_time(hit)+sim._compute_time(req,hit)>=cold-1e-9
                        checked+=1
            rows.append(dict(scenario=scenario['name'],seed=seed,requests=len(reqs),
                recompute_globally_dominates_requests=dominated,validated_positive_hit_points=checked))
    write_csv(out/'bounds.csv',rows)
    (out/'config.json').write_text(json.dumps(provenance(dict(seeds=[21,22],requests=120,
        interpretation='Development input diagnostic only. Concavity depends on this compute curve; no fast path implemented and no evaluated candidate changed.')),
        indent=2)+'\n')
    print('All-request-dominated scenario/seed cells:',sum(r['recompute_globally_dominates_requests']==r['requests']for r in rows),'/',len(rows))
    print('Validated positive hit points:',sum(r['validated_positive_hit_points']for r in rows))


if __name__=='__main__':main()
