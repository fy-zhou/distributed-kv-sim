#!/usr/bin/env python3
"""Same-runtime-state development probe of cache forecast's first placement effect."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kv_sim import Config
from kv_sim.scheduler.shadow import forecast
from scripts.research_shadow import inputs,MeasuredSimulator,trial
from scripts.research_common import new_directory,provenance,digest,assert_invariants


class Probe(MeasuredSimulator):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.first_difference=None

    def _choose(self,req):
        if self.first_difference is None:
            full={i:forecast(self,req,i)for i in self.nodes}
            frozen={i:forecast(self,req,i,horizon=0)for i in self.nodes}
            choose=lambda scores:min(scores,key=lambda i:(scores[i]['estimated_ms'],i))
            a,b=choose(full),choose(frozen)
            if a!=b:
                self.first_difference=dict(request_id=req.request_id,time_ms=self.now,
                    full_node=a,frozen_node=b,full=full,frozen=frozen,
                    runtime_nodes={i:dict(queued=len(n.queue),
                        active=None if n.active is None else n.active.req.request_id,
                        current_request_local_hit=self._hit(req,i),
                        pinned_blocks=len(n.cache.pins))for i,n in self.nodes.items()})
        return super()._choose(req)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scenario',choices=['compute_heavy','long_prefix'],required=True)
    p.add_argument('--seed',type=int,default=21)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    # Restrict this explanatory probe to declared development seeds.
    if args.seed not in (21,22):p.error('use development seed 21 or 22')
    scenario=(dict(name='compute_heavy',compute_scale=8.)if args.scenario=='compute_heavy'
              else dict(name='long_prefix',shared=512,capacity=1024))
    params,specs,reqs,hot,initial=inputs(scenario,args.seed,120)
    sim=Probe(specs,Config(policy='shadow_cache',fabric_gib_s=params['fabric'],
        gpu_ms_per_new_token=.003*params['compute_scale'],
        gpu_ms_per_attention_pair=1.8e-6*params['compute_scale']),hot=hot,audit=False)
    for i,keys in initial.items():sim.seed_cache(i,keys)
    sim.run(reqs);assert_invariants(sim)
    ordinary,_=trial(scenario,args.seed,'always','shadow_cache')
    assert sim.rows==ordinary.rows
    out=new_directory(args.out)
    (out/'first_difference.json').write_text(json.dumps(sim.first_difference,indent=2)+'\n')
    (out/'config.json').write_text(json.dumps(provenance(dict(scenario=scenario,seed=args.seed,
        admission='always',observed_outcomes_equal_to_plain=True,outcomes_hash=digest(sim.rows),
        interpretation='Frozen and full forecasts evaluated at the same causal runtime state along full rollout; first differing choice is a development diagnostic, not a held-out counterfactual execution.')),indent=2)+'\n')
    print(json.dumps(sim.first_difference,indent=2))


if __name__=='__main__':main()
