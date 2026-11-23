#!/usr/bin/env python3
"""Action-matched shadow placement evaluation with fair planner accounting."""
import argparse
import csv
from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kv_sim import Config,NodeSpec
from kv_sim.scheduler.shadow import POLICIES as SHADOW
from kv_sim.workload import make_workload,make_initial_caches
from kv_sim.workload.hotspot import make_hotspot_workload,make_hotspot_initial_caches
from scripts.research_evaluate import SCENARIOS,DEFAULTS,POLICIES as LEGACY
from scripts.research_common import (AuditSimulator,archive,assert_invariants,digest,
    metrics,new_directory,provenance,write_csv)

POLICIES=LEGACY+('shadow_frozen','shadow_cache','shadow_bounded')
SEEDS={'development':[21,22],'evaluation':[201,202,203,204,205]}
CANDIDATES=('shadow_cache','shadow_bounded','shadow_frozen')
BASELINES=('round_robin_cost','cost_aware','shadow_frozen')
TIMING_FIELDS=('decision_mean_us','decision_max_us','execution_plan_mean_us','total_planning_mean_us')


class MeasuredSimulator(AuditSimulator):
    """Same placement + execution-replanning timers for every policy.

    Observer forecasts/residual collection and serialization are outside both
    timers. Times are host wall time, never added to simulated TTFT.
    """
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.execution_plan_ns=[]
        self._in_execution=False
        self.selected_forecasts={}

    def _reuse_choice(self,req,node_id,isolated=False):
        if not self._in_execution:
            return super()._reuse_choice(req,node_id,isolated)
        start=time.perf_counter_ns()
        choice=super()._reuse_choice(req,node_id,isolated)
        self.execution_plan_ns.append(time.perf_counter_ns()-start)
        return choice

    def _start(self,node_id):
        self._in_execution=True
        try:
            super()._start(node_id)
        finally:
            self._in_execution=False

    def _choose(self,req):
        node=super()._choose(req)
        if self.cfg.policy in SHADOW:
            rid,predictions=self.shadow_last_forecast
            assert rid==req.request_id
            forecast=predictions[node]
        else:
            forecast=self._estimate_breakdown(req,node)
        self.selected_forecasts[req.request_id]=dict(forecast)
        return node

    def _on_job_done(self,node_id):
        job=self.nodes[node_id].active
        super()._on_job_done(node_id)
        row=self.rows[-1]
        pred=self.selected_forecasts[job.req.request_id]
        self.residuals.append(dict(request_id=job.req.request_id,node=node_id,
            predicted_queue_ms=pred['queue_ms'],actual_queue_ms=row['queue_ms'],
            predicted_ttft_ms=pred['estimated_ms'],actual_ttft_ms=row['ttft_ms'],
            predicted_remote_blocks=pred['remote_blocks'],actual_remote_blocks=job.remote_blocks,
            queue_error_ms=row['queue_ms']-pred['queue_ms'],
            ttft_error_ms=row['ttft_ms']-pred['estimated_ms']))


def inputs(scenario,seed,count):
    params=dict(DEFAULTS,**{k:v for k,v in scenario.items()if k!='name'})
    specs=[NodeSpec(i,params['capacity'],params['nic'])for i in range(params['nodes'])]
    if params['kind']=='hotspot':
        reqs,hot=make_hotspot_workload(count=count,seed=seed,shared_blocks=params['shared'],
            burst_width=params['burst'],burst_gap_ms=params['gap'],hot_probability=params['popularity'])
        initial=make_hotspot_initial_caches(specs,hot,seed=seed)
    else:
        reqs,prefixes=make_workload(count=count,seed=seed,families=8,
            shared_blocks=params['shared'],unique_blocks=4,arrival_ms=2.,skew=params['skew'])
        hot=prefixes[0];initial=make_initial_caches(specs,prefixes,seed=seed)
    return params,specs,reqs,hot,initial


def trial(scenario,seed,admission,policy,count=120,*,ingress_ms=0.):
    """Optional serial controller sensitivity model, in simulated milliseconds."""
    params,specs,reqs,hot,initial=inputs(scenario,seed,count)
    sim=MeasuredSimulator(specs,Config(policy=policy,admission=admission,
        fabric_gib_s=params['fabric'],gpu_ms_per_new_token=.003*params['compute_scale'],
        gpu_ms_per_attention_pair=1.8e-6*params['compute_scale'],shadow_cache_updates=8),hot=hot,audit=False)
    for node,keys in initial.items():sim.seed_cache(node,keys)
    dispatched = reqs
    if ingress_ms:
        available = 0.
        dispatched = []
        for req in sorted(reqs, key=lambda request: request.arrival_ms):
            available = max(available, req.arrival_ms) + ingress_ms
            dispatched.append(replace(req, arrival_ms=available))
    sim.run(dispatched);assert_invariants(sim)
    if ingress_ms:
        original = {r.request_id: r.arrival_ms for r in reqs}
        for row in sim.rows:
            delay = row['arrival_ms'] - original[row['request_id']]
            row['arrival_ms'] -= delay
            row['queue_ms'] += delay
            row['ttft_ms'] += delay
        assert_invariants(sim)
    assert len(sim.execution_plan_ns)==len(reqs)==len(sim.residuals)
    row=dict(scenario=scenario['name'],seed=seed,
        trace_hash=digest([r.__dict__ for r in reqs]),initial_cache_hash=digest(initial),
        outcomes_hash=digest(sim.rows),
        final_cache_hash=digest({i:list(n.cache.blocks)for i,n in sim.nodes.items()}),**metrics(sim))
    row.update(execution_plan_mean_us=statistics.mean(sim.execution_plan_ns)/1000,
        total_planning_mean_us=(sum(sim.execution_plan_ns)+sum(sim.decision_ns))/len(reqs)/1000,
        mean_abs_queue_error_ms=statistics.mean(abs(r['queue_error_ms'])for r in sim.residuals),
        mean_abs_ttft_error_ms=statistics.mean(abs(r['ttft_error_ms'])for r in sim.residuals),
        mean_ttft_bias_ms=statistics.mean(r['ttft_error_ms']for r in sim.residuals),
        remote_prediction_changes=sum(r['predicted_remote_blocks']!=r['actual_remote_blocks']for r in sim.residuals),
        max_node_load_fraction=max(sum(r['node']==i for r in sim.rows)for i in sim.nodes)/len(reqs))
    return sim,row


def comparisons(rows):
    groups={}
    for row in rows:
        key=(row['scenario'],int(row['seed']),row['admission'])
        group=groups.setdefault(key,{})
        if row['policy']in group:raise ValueError('duplicate run')
        group[row['policy']]=row
    pairs=[]
    for (scenario,seed,admission),group in sorted(groups.items()):
        if set(group)!=set(POLICIES):raise ValueError('incomplete policies')
        assert len({r['trace_hash']for r in group.values()})==1
        assert len({r['initial_cache_hash']for r in group.values()})==1
        for candidate in CANDIDATES:
            for baseline in BASELINES:
                if baseline==candidate:continue
                c,b=group[candidate],group[baseline]
                delta=100*(1-float(c['p95_ttft_ms'])/float(b['p95_ttft_ms']))
                pairs.append(dict(scenario=scenario,seed=seed,admission=admission,
                    candidate=candidate,baseline=baseline,
                    candidate_p95_ms=c['p95_ttft_ms'],baseline_p95_ms=b['p95_ttft_ms'],
                    p95_improvement_pct=delta,
                    outcome='improve'if delta>1e-7 else'regress'if delta< -1e-7 else'neutral'))
    return pairs


def summaries(pairs):
    groups={}
    for r in pairs:groups.setdefault((r['candidate'],r['baseline']),[]).append(r)
    out=[]
    for (candidate,baseline),rr in sorted(groups.items()):
        seeds=sorted({r['seed']for r in rr})
        means=[statistics.mean(r['p95_improvement_pct']for r in rr if r['seed']==seed)for seed in seeds]
        rng=random.Random(20261124)
        boot=sorted(statistics.mean(rng.choices(means,k=len(means)))for _ in range(5000))
        out.append(dict(candidate=candidate,baseline=baseline,conditions=len(rr),seed_clusters=len(seeds),
            mean_p95_improvement_pct=statistics.mean(means),
            seed_bootstrap_p025=boot[124],seed_bootstrap_p975=boot[4874],
            improve=sum(r['outcome']=='improve'for r in rr),
            neutral=sum(r['outcome']=='neutral'for r in rr),regress=sum(r['outcome']=='regress'for r in rr)))
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--split',choices=tuple(SEEDS),required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--requests',type=int,default=120)
    p.add_argument('--archive',type=Path)
    p.add_argument('--verify-against',type=Path)
    args=p.parse_args()
    if args.requests<=0:p.error('positive requests required')
    out=new_directory(args.out)
    config=provenance(dict(split=args.split,seeds=SEEDS[args.split],requests=args.requests,
        policies=POLICIES,scenarios=SCENARIOS,defaults=DEFAULTS,admissions=['always','no_remote'],
        bounded_cache_updates=8,primary_comparator='round_robin_cost',
        limitations='Other caches frozen; outgoing pins retained; queued links isolated; no future arrivals read.',
        timing='Same timers for actual placement and every execution-time service plan; observer work excluded. Not simulated TTFT.',
        candidate_selection='All three predeclared variants evaluated; no evaluation-seed tuning.',
        policy_execution_order='Stable SHA256 ordering by scenario, seed, admission, policy; independent of workload RNG.'))
    (out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    expected={}
    if args.verify_against:
        with args.verify_against.open(newline='')as f:
            for row in csv.DictReader(f):
                key=(row['scenario'],int(row['seed']),row['admission'],row['policy'])
                if key in expected:raise ValueError('duplicate expected row')
                expected[key]=row
        assert len(expected)==len(SCENARIOS)*len(SEEDS[args.split])*2*len(POLICIES)
    rows=[]
    with gzip.GzipFile(str(out/'traces.jsonl.gz'),'wb',mtime=0)as traces:
        for scenario in SCENARIOS:
            for seed in SEEDS[args.split]:
                for admission in ('always','no_remote'):
                    order=sorted(POLICIES,key=lambda policy:digest([scenario['name'],seed,admission,policy]))
                    for policy in order:
                        sim,row=trial(scenario,seed,admission,policy,args.requests)
                        key=(scenario['name'],seed,admission,policy)
                        if args.verify_against:
                            before=expected.pop(key)
                            for field in row:
                                if field in TIMING_FIELDS:continue
                                if str(row[field])!=before[field]:raise AssertionError((key,field,row[field],before[field]))
                        else:
                            traces.write((json.dumps(dict(key=key,requests=sim.rows,predictions=sim.residuals),separators=(',',':'))+'\n').encode())
                        rows.append(row)
            write_csv(out/'runs.csv',rows)
            print('Completed',args.split,scenario['name'],len(rows),flush=True)
    assert not expected
    pairs=comparisons(rows)
    write_csv(out/'pairs.csv',pairs);write_csv(out/'summary.csv',summaries(pairs))
    if args.verify_against:
        (out/'verification.json').write_text(json.dumps(dict(verified_runs=len(rows),ignored_fields=TIMING_FIELDS,
            reference=str(args.verify_against),reference_sha256=hashlib.sha256(args.verify_against.read_bytes()).hexdigest(),
            compared='Every remaining metric, per-request outcome hash and final ordered-cache hash.'),indent=2)+'\n')
    if args.archive:
        # Rounded derived compact table; complete precision/hashes remain in raw runs.csv.
        compact=[{k:round(v,6)if isinstance(v,float)else v for k,v in r.items()
                  if not k.endswith('_hash')and k not in ('node_service_ms','node_evictions','node_hot_final')}
                 for r in rows]
        write_csv(out/'compact_runs.csv',compact)
        compact_pairs=[{k:round(v,6)if isinstance(v,float)else v for k,v in r.items()}for r in pairs]
        write_csv(out/'compact_pairs.csv',compact_pairs)
        archive(out,args.archive,['compact_runs.csv','compact_pairs.csv','summary.csv'],
            '# Shadow FIFO/cache rollout placement\n\nSynthetic, unchanged compute/network model. '
            'All candidates and action-matched baselines share minimum_service execution actions. '
            'Full and bounded (8 cache updates) local rollouts plus frozen-cache ablation. '
            'No future arrival events accessed. Other caches frozen, queued links isolated, outgoing pins retained. '
            'Whole-seed bootstrap is descriptive with few seed clusters. '
            'All candidates and failures reported; no held-out parameter selection. '
            'Compact floats rounded to six decimals; raw full precision/hashes/traces remain server-side. '
            'Timings fairly sum placement plus execution replanning, exclude observer/I/O, and are not added to simulated TTFT.')
    print(json.dumps(summaries(pairs),indent=2))


if __name__=='__main__':main()
