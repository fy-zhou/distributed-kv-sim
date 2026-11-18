#!/usr/bin/env python3
"""Generate an inspectable narrative from the frozen evaluation CSVs."""
import argparse
import csv
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
    p.add_argument('--evaluation',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    out=new_directory(args.out)
    rows=read(args.evaluation/'runs.csv')
    pairs=read(args.evaluation/'pairs.csv')
    cluster=read(args.evaluation/'seed_cluster_summary.csv')
    lines=['# Cost-aware evaluation: service choice helps; placement remains unresolved','',
        'All TTFT and network results are synthetic. Evaluation seeds 101–105 were disjoint from development seeds 11/12 and historical seeds 41–45. The method and 14 scenarios were frozen before evaluation; no parameter or lambda selection used evaluation results.','',
        '980 runs compare seven policies under 14 scenarios and two admissions. Each run has 120 requests; the 140 condition/seed pairs reuse identical requests and initial caches across policies. Configurations sharing a seed are correlated.','',
        '## Complete paired P95 outcomes','',
        'Positive improvement means lower candidate P95. Values average paired percentages with equal scenario/admission weights; they are not percent changes of pooled mean latencies. Bootstrap endpoints resample whole seeds (5000 samples). With only five seed clusters, they are descriptive conditional uncertainty, not evidence of hardware generalization.','',
        '| Baseline | Improve / neutral / regress | Mean improvement % | Seed bootstrap 2.5–97.5% |',
        '|---|---:|---:|---:|']
    for r in cluster:
        lines.append('| %s | %s / %s / %s | %+.3f | [%+.3f, %+.3f] |'%(r['baseline'],r['improve'],r['neutral'],r['regress'],float(r['mean_p95_improvement_pct']),float(r['seed_cluster_bootstrap_p025']),float(r['seed_cluster_bootstrap_p975'])))
    lines+=['','## Absolute metrics: reference, always admission','',
        'Means of cell metrics across five evaluation seeds. These rows refer to one common workload regime; full absolute metrics for every scenario/admission are in compact_runs.csv. Time units are ms.','',
        '| Policy | P50 | P95 | P99 | Mean | GiB | Remote requests | Queue | Network | H2D | Compute |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    fields=('p50_ttft_ms','p95_ttft_ms','p99_ttft_ms','mean_ttft_ms','network_gib','remote_requests','mean_queue_ms','mean_network_ms','mean_h2d_ms','mean_compute_ms')
    for policy in dict.fromkeys(r['policy']for r in rows):
        group=[r for r in rows if r['scenario']=='reference' and r['admission']=='always' and r['policy']==policy]
        values=[statistics.mean(float(r[k])for r in group)for k in fields]
        lines.append('| '+policy+' | '+' | '.join('%.3f'%v for v in values)+' |')
    lines+=['','## Failures and action-space attribution','',
        'Candidate cost_aware uses Greedy placement plus a planner choosing cold recompute, local reuse or each remote source. All legacy baselines retain mandatory longest-prefix reuse. round_robin_cost uses the same planner with Round Robin placement, exposing the service-action contribution.','',
        'Against action-matched Round Robin, 120/140 pairs are exactly neutral; only long_prefix and compute_heavy differ. Thus large legacy-baseline gains mostly reflect avoiding expensive reuse under the current illustrative compute/H2D ratio, not a proven placement advantage.','',
        'Worst regressions against the action-matched baseline:','',
        '| Scenario | Seed | Admission | Control P95 | Candidate P95 | Improvement % |',
        '|---|---:|---|---:|---:|---:|']
    failures=sorted([r for r in pairs if r['baseline']=='round_robin_cost' and r['outcome']=='regress'],key=lambda r:float(r['p95_improvement_pct']))
    for r in failures[:6]:
        lines.append('| %s | %s | %s | %.3f | %.3f | %+.3f |'%(r['scenario'],r['seed'],r['admission'],float(r['baseline_p95_ms']),float(r['candidate_p95_ms']),float(r['p95_improvement_pct'])))
    lines+=['',
        'In compute_heavy seed 102 / always, the candidate assigns 72/120 requests to node 0, versus 30 per node with round_robin_cost. Mean queue time increases 1164.271 → 1378.499 ms; mean H2D and compute are equal (29.007 and 32.906 ms), while network time decreases 16.420 → 12.769 ms. P95 nonetheless increases 2239.768 → 2923.569 ms (+30.53%). Fewer bytes do not establish better tail latency.','',
        'In long_prefix seed 105 / always, candidate node loads are 35/29/28/28 versus 30 each. Both transfer zero bytes; P95 is 2191.975 versus 2106.647 ms (4.05% regression). Current-cache FIFO forecasts cannot predict all future replica creation and local reuse. These diagnostics suggest a queue/cache forecast hypothesis; they do not uniquely prove its cause.','',
        '## Scheduler decision wall time','',
        'Means over 140 condition-level mean decision times, in microseconds, measured on the shared CPU host. These are implementation overhead, not simulated TTFT or controlled production benchmarks. Round Robin placement timing excludes its later execution-time reuse planning; that cost is not zero.','',
        '| Policy | Mean placement decision us |','|---|---:|']
    overhead=[]
    for policy in dict.fromkeys(r['policy']for r in rows):
        rr=[r for r in rows if r['policy']==policy]
        value=statistics.mean(float(r['decision_mean_us'])for r in rr)
        lines.append('| %s | %.3f |'%(policy,value));overhead.append(dict(policy=policy,mean_decision_us=value))
    lines+=['',
        'Candidate Greedy placement repeatedly scans queued work and can cost roughly 1.3 ms per placement at this scale. This overhead and its lack of action-matched P95 improvement argue against presenting it as a finished scheduler.','',
        '## Limits and next falsifiable experiment','',
        'The compute curve is illustrative; the H2D fit comes from contended, layered KV loading. No pure GPU-prefill calibration, GPU batching/decode model, multi-node NIC measurement, general fair network solver or production trace validation is available. The new bounded H2D experiment is a distinct contiguous-copy layout and does not update defaults.','',
        'Next: compare current-cache FIFO forecasts against a shadow cache/queue rollout on development seeds and controlled compute-bound traces, while keeping service actions matched. Evaluate any new placement method on fresh seeds (reserve 201–205); 101–105 are now consumed evaluation data and must not be reused as untouched holdout. Hardware prefill calibration should isolate loading/scatter from actual compute before interpreting absolute speedups.','']
    (out/'ANALYSIS.md').write_text('\n'.join(lines))
    write_csv(out/'decision_overhead.csv',overhead)
    (out/'config.json').write_text(json.dumps(provenance(dict(evaluation=str(args.evaluation))),indent=2)+'\n')
    print(out/'ANALYSIS.md')


if __name__=='__main__':main()
