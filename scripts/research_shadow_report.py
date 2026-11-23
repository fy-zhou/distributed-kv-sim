#!/usr/bin/env python3
"""Synthesize every shadow variant, fair timing, forecast errors and failures."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.research_common import new_directory,provenance,write_csv


def read(path):
    with path.open(newline='')as f:return list(csv.DictReader(f))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--plot',action='store_true')
    args=p.parse_args()
    out=new_directory(args.out)
    rows,pairs,summary=[read(args.source/name)for name in ('runs.csv','pairs.csv','summary.csv')]
    config=json.loads((args.source/'config.json').read_text())
    lines=['# Shadow queue/cache rollout: action-matched placement evidence','',
        'All TTFT/traffic values are synthetic. Hardware-model coefficients and minimum_service execution actions are unchanged. Each policy uses identical request and initial-cache hashes per condition. No future arrivals are read.','',
        'Seeds: %s; %d runs; %d requests/run. All three variants were declared before held-out evaluation. The primary placement control is round_robin_cost, not a baseline with different reuse actions.'%(config['config']['seeds'],len(rows),config['config']['requests']),'',
        '## All predeclared comparisons','',
        'Positive means reduced P95. Means average paired percentages over declared scenario/admission conditions. Bootstrap endpoints resample entire seeds, preserving all within-seed correlations; few seed clusters and multiple candidates make these descriptive conditional uncertainty, not a generalization or significance guarantee.','',
        '| Candidate | Baseline | Improve / neutral / regress | Mean P95 improvement % | Seed bootstrap 2.5–97.5% |','|---|---|---:|---:|---:|']
    for r in summary:
        lines.append('| %s | %s | %s / %s / %s | %+.3f | [%+.3f, %+.3f] |'%(r['candidate'],r['baseline'],r['improve'],r['neutral'],r['regress'],float(r['mean_p95_improvement_pct']),float(r['seed_bootstrap_p025']),float(r['seed_bootstrap_p975'])))
    lines+=['','## Fair placement plus execution-planning overhead','',
        'All policies use the same timers: actual placement selection plus exactly one actual execution-time service plan per request. Observer forecasts/residuals and serialization are excluded. These shared-host CPU wall times are not simulated TTFT, isolated microbenchmarks, or added latency in the event model. Policy execution order is recorded in the run config.','',
        '| Policy | Placement us | Execution planning us | Total planning us/request | Queue forecast MAE ms | TTFT forecast MAE ms |','|---|---:|---:|---:|---:|---:|']
    policies=list(dict.fromkeys(r['policy']for r in rows))
    for policy in policies:
        group=[r for r in rows if r['policy']==policy]
        fields=('decision_mean_us','execution_plan_mean_us','total_planning_mean_us','mean_abs_queue_error_ms','mean_abs_ttft_error_ms')
        values=[statistics.mean(float(r[f])for r in group)for f in fields]
        lines.append('| '+policy+' | '+' | '.join('%.3f'%v for v in values)+' |')
    lines+=['','## Absolute P95 and traffic in informative regimes','',
        'Means across the seeds within each scenario/admission. Full P50/P95/P99/mean, timing decomposition, hits, traffic, eviction/residency and node-load metrics for every condition remain in compact_runs.csv (rounded) and raw runs.csv (full precision).','',
        '| Scenario | Admission | Policy | P95 ms | GiB | Queue MAE ms | Maximum node request share |','|---|---|---|---:|---:|---:|---:|']
    focused=('round_robin_cost','cost_aware','shadow_frozen','shadow_cache','shadow_bounded')
    for scenario in ('compute_heavy','long_prefix'):
        for admission in ('always','no_remote'):
            for policy in focused:
                group=[r for r in rows if r['scenario']==scenario and r['admission']==admission and r['policy']==policy]
                values=[statistics.mean(float(r[f])for r in group)for f in ('p95_ttft_ms','network_gib','mean_abs_queue_error_ms','max_node_load_fraction')]
                lines.append('| %s | %s | %s | %.3f | %.3f | %.3f | %.3f |'%((scenario,admission,policy)+tuple(values)))
    failures=sorted([r for r in pairs if r['baseline']=='round_robin_cost' and r['outcome']=='regress'],key=lambda r:float(r['p95_improvement_pct']))
    lines+=['','## Retained failures','',
        'Up to four worst regressions per candidate are selected post-hoc for diagnosis, not unbiased representative workloads. Every comparison remains in compact_pairs.csv.','',
        '| Candidate | Scenario | Seed | Admission | Candidate P95 ms | Control P95 ms | Improvement % |','|---|---|---:|---|---:|---:|---:|']
    retained=[]
    for candidate in ('shadow_cache','shadow_bounded','shadow_frozen'):
        retained.extend([r for r in failures if r['candidate']==candidate][:4])
    for r in retained:
        lines.append('| %s | %s | %s | %s | %.3f | %.3f | %+.3f |'%(r['candidate'],r['scenario'],r['seed'],r['admission'],float(r['candidate_p95_ms']),float(r['baseline_p95_ms']),float(r['p95_improvement_pct'])))
    if not failures:lines+=['','No regressions against the primary comparator in this particular matrix; this is not universal dominance.']
    lines+=['','## Approximation limits and interpretation','',
        'The rollout changes forecasts only. Real execution uses the shared planner and live caches. Forecasts roll only a target node, freeze other caches, assume isolated future transfer rates, and retain outgoing pins for the forecast lifetime. Full rollout does not make those assumptions exact; bounded rollout also freezes cache state after eight completions while still charging all tail work. Frozen-cache ablation isolates the effect of cache evolution within this shared forecast model.','',
        'An improved queue forecast does not alone establish improved tail latency or aggregate-system cost. Service latency is still illustrative, and CPU planning overhead is reported separately rather than charged to arrivals. No GPU-prefill, KV-scatter or multi-machine NIC calibration was added in this iteration. Future candidate changes must use fresh held-out seeds; this run consumes its declared evaluation seeds.','']
    (out/'ANALYSIS.md').write_text('\n'.join(lines))
    grouped={}
    for r in pairs:
        grouped.setdefault((r['candidate'],r['baseline'],r['scenario'],r['admission']),[]).append(r)
    variability=[]
    for key,rr in sorted(grouped.items()):
        values=[float(r['p95_improvement_pct'])for r in rr]
        variability.append(dict(candidate=key[0],baseline=key[1],scenario=key[2],admission=key[3],
            seeds=len(values),mean_improvement_pct=statistics.mean(values),
            sd_improvement_pct=statistics.stdev(values)if len(values)>1 else 0.,
            min_improvement_pct=min(values),max_improvement_pct=max(values),
            improve=sum(r['outcome']=='improve'for r in rr),
            neutral=sum(r['outcome']=='neutral'for r in rr),regress=sum(r['outcome']=='regress'for r in rr)))
    write_csv(out/'by_scenario.csv',variability)
    meta=provenance(dict(source=str(args.source),input_sha256={name:hashlib.sha256((args.source/name).read_bytes()).hexdigest()for name in ('runs.csv','pairs.csv','summary.csv','config.json')}))
    if args.plot:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        plt.rcParams.update({'font.size':9,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
        names=sorted({r['scenario']for r in pairs})
        fig,axs=plt.subplots(1,3,figsize=(12,6),sharey=True,layout='constrained')
        for ax,candidate,color in zip(axs,('shadow_cache','shadow_bounded','shadow_frozen'),('#2364aa','#138a72','#c05a32')):
            values=[[float(r['p95_improvement_pct'])for r in pairs if r['scenario']==name and r['candidate']==candidate and r['baseline']=='round_robin_cost']for name in names]
            means=[statistics.mean(v)for v in values]
            ax.errorbar(means,range(len(names)),xerr=[[m-min(v)for m,v in zip(means,values)],[max(v)-m for m,v in zip(means,values)]],fmt='o',color=color,capsize=2)
            ax.axvline(0,color='.6',lw=.7);ax.set_title(candidate)
            ax.set_xlabel('P95 improvement vs RR-cost (%)');ax.set_yticks(range(len(names)),names)
        axs[0].invert_yaxis()
        fig.suptitle('Synthetic held-out placement; identical service-action planner\nDots: mean; whiskers: observed min–max across seeds/admissions (not CI)',fontsize=10)
        fig.savefig(out/'placement.svg',metadata={'Date':None});fig.savefig(out/'placement.png',dpi=120);plt.close(fig)
        fig,ax=plt.subplots(figsize=(9,4),layout='constrained')
        selected=list(focused)
        placement=[statistics.mean(float(r['decision_mean_us'])for r in rows if r['policy']==policy)for policy in selected]
        execution=[statistics.mean(float(r['execution_plan_mean_us'])for r in rows if r['policy']==policy)for policy in selected]
        ax.bar(selected,placement,label='Placement');ax.bar(selected,execution,bottom=placement,label='Execution replanning')
        ax.set_ylabel('Mean host planning time (us/request)');ax.legend(frameon=False)
        ax.set_title('Shared-host CPU timings; observer and I/O excluded; not simulated TTFT')
        fig.savefig(out/'planning.svg',metadata={'Date':None});fig.savefig(out/'planning.png',dpi=120);plt.close(fig)
        meta['matplotlib']=matplotlib.__version__
    (out/'manifest.json').write_text(json.dumps(meta,indent=2)+'\n')
    print(out/'ANALYSIS.md')


if __name__=='__main__':main()
