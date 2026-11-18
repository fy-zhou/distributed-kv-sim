#!/usr/bin/env python3
"""Predeclared paired evaluation: fixed candidate, all baselines, no lambda tuning."""
import argparse
import csv
import json
from pathlib import Path
import random
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kv_sim import Config, NodeSpec
from kv_sim.workload import make_workload, make_initial_caches
from kv_sim.workload.hotspot import make_hotspot_workload, make_hotspot_initial_caches
from scripts.research_common import (AuditSimulator,archive,assert_invariants,digest,
    metrics,new_directory,provenance,write_csv)

POLICIES=('round_robin','shortest_queue','max_hit','greedy','bandwidth_aware',
          'cost_aware','round_robin_cost')
SEEDS={'development':[11,12], 'evaluation':[101,102,103,104,105]}
# One-factor interventions around reference, plus distinct Zipf workload shapes.
SCENARIOS=[dict(name='reference'),dict(name='cache_cliff',capacity=256),
    dict(name='cache_just_fit',capacity=260),dict(name='nic_bound',nic=1.,fabric=16.),
    dict(name='fabric_bound',nic=16.,fabric=1.),dict(name='two_nodes',nodes=2),
    dict(name='eight_nodes',nodes=8),dict(name='slow_arrivals',burst=1,gap=30.),
    dict(name='short_prefix',shared=64,capacity=128),
    dict(name='long_prefix',shared=512,capacity=1024),
    dict(name='balanced_popularity',popularity=.5),
    dict(name='compute_heavy',compute_scale=8.),
    dict(name='zipf_flat',kind='zipf',shared=64,skew=0.),
    dict(name='zipf_skewed',kind='zipf',shared=64,skew=2.)]
DEFAULTS=dict(nodes=4,capacity=512,nic=4.,fabric=4.,shared=256,burst=8,gap=2.,
              popularity=.85,compute_scale=1.,kind='hotspot',skew=1.2)


def trial(scenario,seed,admission,policy,count):
    params=dict(DEFAULTS,**{k:v for k,v in scenario.items() if k!='name'})
    specs=[NodeSpec(i,params['capacity'],params['nic'])for i in range(params['nodes'])]
    if params['kind']=='hotspot':
        reqs,hot=make_hotspot_workload(count=count,seed=seed,shared_blocks=params['shared'],
            burst_width=params['burst'],burst_gap_ms=params['gap'],hot_probability=params['popularity'])
        initial=make_hotspot_initial_caches(specs,hot,seed=seed)
    else:
        reqs,prefixes=make_workload(count=count,seed=seed,families=8,
            shared_blocks=params['shared'],unique_blocks=4,arrival_ms=2.,skew=params['skew'])
        initial=make_initial_caches(specs,prefixes,seed=seed)
        hot=prefixes[0]
    sim=AuditSimulator(specs,Config(policy=policy,admission=admission,
        fabric_gib_s=params['fabric'],gpu_ms_per_new_token=.003*params['compute_scale'],
        gpu_ms_per_attention_pair=1.8e-6*params['compute_scale']),hot=hot,audit=False)
    for i,keys in initial.items():sim.seed_cache(i,keys)
    sim.run(reqs)
    assert_invariants(sim)
    result=dict(scenario=scenario['name'],seed=seed,
        trace_hash=digest([r.__dict__ for r in reqs]),initial_cache_hash=digest(initial),
        outcomes_hash=digest(sim.rows),
        final_cache_hash=digest({i:list(n.cache.blocks) for i,n in sim.nodes.items()}),
        **metrics(sim))
    return sim,result


def pair_results(rows):
    groups={}
    for r in rows:
        key=(r['scenario'],int(r['seed']),r['admission'])
        if r['policy'] in groups.setdefault(key,{}):raise ValueError('duplicate policy')
        groups[key][r['policy']]=r
    paired=[]
    for (scenario,seed,admission),group in sorted(groups.items()):
        if set(group)!=set(POLICIES):raise ValueError('incomplete paired run')
        assert len({r['trace_hash']for r in group.values()})==1
        assert len({r['initial_cache_hash']for r in group.values()})==1
        c=group['cost_aware']
        for baseline in POLICIES:
            if baseline=='cost_aware':continue
            b=group[baseline]
            change=100*(1-float(c['p95_ttft_ms'])/float(b['p95_ttft_ms']))
            paired.append(dict(scenario=scenario,seed=seed,admission=admission,
                baseline=baseline,baseline_p95_ms=b['p95_ttft_ms'],candidate_p95_ms=c['p95_ttft_ms'],
                p95_improvement_pct=change,baseline_network_gib=b['network_gib'],
                candidate_network_gib=c['network_gib'],
                outcome='improve' if change>1e-7 else 'regress' if change < -1e-7 else 'neutral'))
    return paired


def summarize_pairs(pairs):
    groups={}
    for r in pairs:groups.setdefault((r['scenario'],r['admission'],r['baseline']),[]).append(r)
    summaries=[]
    for (scenario,admission,baseline),rr in sorted(groups.items()):
        values=[r['p95_improvement_pct']for r in rr]
        summaries.append(dict(scenario=scenario,admission=admission,baseline=baseline,seeds=len(rr),
            baseline_p95_mean_ms=statistics.mean(float(r['baseline_p95_ms'])for r in rr),
            candidate_p95_mean_ms=statistics.mean(float(r['candidate_p95_ms'])for r in rr),
            mean_p95_improvement_pct=statistics.mean(values),
            sd_p95_improvement_pct=statistics.stdev(values) if len(values)>1 else 0.,
            min_p95_improvement_pct=min(values),max_p95_improvement_pct=max(values),
            improve=sum(r['outcome']=='improve'for r in rr),
            regress=sum(r['outcome']=='regress'for r in rr),neutral=sum(r['outcome']=='neutral'for r in rr)))
    return summaries


def cluster_summary(pairs):
    """Resample whole seeds, retaining correlated scenarios/admissions together."""
    result=[]
    for baseline in POLICIES:
        if baseline=='cost_aware':continue
        rows=[r for r in pairs if r['baseline']==baseline]
        seeds=sorted({r['seed']for r in rows})
        means=[statistics.mean(r['p95_improvement_pct']for r in rows if r['seed']==s)for s in seeds]
        rng=random.Random(20261118)
        boot=sorted(statistics.mean(rng.choices(means,k=len(means)))for _ in range(5000))
        result.append(dict(baseline=baseline,seed_clusters=len(seeds),conditions=len(rows),
            mean_p95_improvement_pct=statistics.mean(means),
            seed_cluster_bootstrap_p025=boot[124],seed_cluster_bootstrap_p975=boot[4874],
            improve=sum(r['outcome']=='improve'for r in rows),
            regress=sum(r['outcome']=='regress'for r in rows),neutral=sum(r['outcome']=='neutral'for r in rows)))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--split',choices=tuple(SEEDS),required=True)
    p.add_argument('--requests',type=int,default=120)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--archive',type=Path)
    p.add_argument('--verify-against',type=Path)
    args=p.parse_args()
    if args.requests<=0:p.error('positive request count required')
    out=new_directory(args.out)
    params=dict(split=args.split,seeds=SEEDS[args.split],requests=args.requests,
        scenarios=SCENARIOS,defaults=DEFAULTS,policies=POLICIES,
        admissions=['always','no_remote'],network_externality_weight=1.,
        no_parameter_tuning=True,bootstrap='5000 whole-seed resamples; only 5 evaluation seed clusters; descriptive uncertainty conditional on chosen synthetic scenarios')
    (out/'config.json').write_text(json.dumps(provenance(params),indent=2)+'\n')
    expected={}
    if args.verify_against:
        with args.verify_against.open(newline='')as f:
            for row in csv.DictReader(f):expected[(row['scenario'],int(row['seed']),row['admission'],row['policy'])]=row
    rows=[]
    traces=new_directory(out/'requests')
    for scenario in SCENARIOS:
        for seed in SEEDS[args.split]:
            for admission in ('always','no_remote'):
                for policy in POLICIES:
                    sim,row=trial(scenario,seed,admission,policy,args.requests)
                    if expected:
                        before=expected.pop((scenario['name'],seed,admission,policy))
                        for key in row:
                            if key.startswith('decision_'):continue
                            if str(row[key])!=before[key]:
                                raise AssertionError('replay differs at %s: %r vs %r'%(key,row[key],before[key]))
                    else:
                        write_csv(traces/('%s-%d-%s-%s.csv'%(scenario['name'],seed,admission,policy)),sim.rows)
                    rows.append(row)
        write_csv(out/'runs.csv',rows)
        print('Completed',args.split,scenario['name'],len(rows),'runs',flush=True)
    if expected:raise AssertionError('missing replay runs')
    pairs=pair_results(rows)
    write_csv(out/'pairs.csv',pairs)
    write_csv(out/'by_scenario.csv',summarize_pairs(pairs))
    write_csv(out/'seed_cluster_summary.csv',cluster_summary(pairs))
    if args.verify_against:
        (out/'verification.json').write_text(json.dumps(dict(verified_runs=len(rows),
            ignored_fields=['decision_mean_us','decision_max_us'],
            compared='Every other field including per-request outcomes and final cache hashes.'),indent=2)+'\n')
    if args.archive:
        # Full per-request replay hashes remain in raw runs.csv and its manifest digest.
        write_csv(out/'compact_runs.csv', [{k:v for k,v in r.items() if not k.endswith('_hash')} for r in rows])
        archive(out,args.archive,['compact_runs.csv','pairs.csv','by_scenario.csv','seed_cluster_summary.csv'],
            '# Cost-aware service and scheduling experiment\n\n'
            '%d paired synthetic runs; split: %s; seeds: %s.\n'
            'All baselines use unchanged mandatory longest-prefix reuse. Candidate expands the service action space.\n'
            'Round-robin-cost uses the same action planner to isolate placement from service selection.\n'
            'No lambda optimization. All scenarios declared in config before evaluation.\n'
            'Whole-seed bootstrap preserves scenario correlation; only five evaluation clusters, '
            'so intervals are descriptive, conditional, and do not establish hardware generalization.\n'
            'Request traces stay outside Git; outcomes/cache hashes permit exact replay. '
            'Decision runtime is host wall time, not simulated latency.\n'%
            (len(rows),args.split,SEEDS[args.split]))
    print(json.dumps(cluster_summary(pairs),indent=2))


if __name__=='__main__':main()
