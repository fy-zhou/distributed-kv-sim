# Cost-aware evaluation: service choice helps; placement remains unresolved

All TTFT and network results are synthetic. Evaluation seeds 101–105 were disjoint from development seeds 11/12 and historical seeds 41–45. The method and 14 scenarios were frozen before evaluation; no parameter or lambda selection used evaluation results.

980 runs compare seven policies under 14 scenarios and two admissions. Each run has 120 requests; the 140 condition/seed pairs reuse identical requests and initial caches across policies. Configurations sharing a seed are correlated.

## Complete paired P95 outcomes

Positive improvement means lower candidate P95. Values average paired percentages with equal scenario/admission weights; they are not percent changes of pooled mean latencies. Bootstrap endpoints resample whole seeds (5000 samples). With only five seed clusters, they are descriptive conditional uncertainty, not evidence of hardware generalization.

| Baseline | Improve / neutral / regress | Mean improvement % | Seed bootstrap 2.5–97.5% |
|---|---:|---:|---:|
| round_robin | 138 / 0 / 2 | +78.432 | [+76.402, +80.536] |
| shortest_queue | 138 / 0 / 2 | +78.356 | [+76.374, +80.505] |
| max_hit | 140 / 0 / 0 | +80.738 | [+80.656, +80.820] |
| greedy | 140 / 0 / 0 | +72.174 | [+70.610, +73.686] |
| bandwidth_aware | 140 / 0 / 0 | +71.138 | [+69.988, +72.255] |
| round_robin_cost | 7 / 120 / 13 | -0.119 | [-0.672, +0.433] |

## Absolute metrics: reference, always admission

Means of cell metrics across five evaluation seeds. These rows refer to one common workload regime; full absolute metrics for every scenario/admission are in compact_runs.csv. Time units are ms.

| Policy | P50 | P95 | P99 | Mean | GiB | Remote requests | Queue | Network | H2D | Compute |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| round_robin | 1174.223 | 2532.968 | 2766.979 | 1224.083 | 9.363 | 21.400 | 1147.002 | 42.002 | 30.266 | 4.813 |
| shortest_queue | 1174.223 | 2532.968 | 2766.979 | 1224.083 | 9.363 | 21.400 | 1147.002 | 42.002 | 30.266 | 4.813 |
| max_hit | 2053.423 | 3869.949 | 4032.380 | 2054.740 | 0.000 | 0.000 | 2020.559 | 0.000 | 24.848 | 9.333 |
| greedy | 1172.621 | 2325.521 | 2472.599 | 1214.662 | 8.312 | 19.000 | 1141.947 | 37.636 | 30.266 | 4.813 |
| bandwidth_aware | 1276.936 | 2443.795 | 2633.632 | 1271.019 | 8.750 | 20.000 | 1193.697 | 42.197 | 30.540 | 4.585 |
| cost_aware | 451.871 | 843.705 | 873.684 | 451.871 | 0.000 | 0.000 | 421.812 | 0.000 | 0.000 | 30.059 |
| round_robin_cost | 451.871 | 843.705 | 873.684 | 451.871 | 0.000 | 0.000 | 421.812 | 0.000 | 0.000 | 30.059 |

## Failures and action-space attribution

Candidate cost_aware uses Greedy placement plus a planner choosing cold recompute, local reuse or each remote source. All legacy baselines retain mandatory longest-prefix reuse. round_robin_cost uses the same planner with Round Robin placement, exposing the service-action contribution.

Against action-matched Round Robin, 120/140 pairs are exactly neutral; only long_prefix and compute_heavy differ. Thus large legacy-baseline gains mostly reflect avoiding expensive reuse under the current illustrative compute/H2D ratio, not a proven placement advantage.

Worst regressions against the action-matched baseline:

| Scenario | Seed | Admission | Control P95 | Candidate P95 | Improvement % |
|---|---:|---|---:|---:|---:|
| compute_heavy | 102 | always | 2239.768 | 2923.569 | -30.530 |
| compute_heavy | 104 | always | 2365.527 | 2989.285 | -26.369 |
| long_prefix | 105 | always | 2106.647 | 2191.975 | -4.050 |
| long_prefix | 105 | no_remote | 2106.647 | 2191.975 | -4.050 |
| long_prefix | 104 | always | 2086.743 | 2161.188 | -3.568 |
| long_prefix | 104 | no_remote | 2086.743 | 2161.188 | -3.568 |

In compute_heavy seed 102 / always, the candidate assigns 72/120 requests to node 0, versus 30 per node with round_robin_cost. Mean queue time increases 1164.271 → 1378.499 ms; mean H2D and compute are equal (29.007 and 32.906 ms), while network time decreases 16.420 → 12.769 ms. P95 nonetheless increases 2239.768 → 2923.569 ms (+30.53%). Fewer bytes do not establish better tail latency.

In long_prefix seed 105 / always, candidate node loads are 35/29/28/28 versus 30 each. Both transfer zero bytes; P95 is 2191.975 versus 2106.647 ms (4.05% regression). Current-cache FIFO forecasts cannot predict all future replica creation and local reuse. These diagnostics suggest a queue/cache forecast hypothesis; they do not uniquely prove its cause.

## Scheduler decision wall time

Means over 140 condition-level mean decision times, in microseconds, measured on the shared CPU host. These are implementation overhead, not simulated TTFT or controlled production benchmarks. Round Robin placement timing excludes its later execution-time reuse planning; that cost is not zero.

| Policy | Mean placement decision us |
|---|---:|
| round_robin | 1.268 |
| shortest_queue | 2.820 |
| max_hit | 28.635 |
| greedy | 1170.084 |
| bandwidth_aware | 1233.875 |
| cost_aware | 1301.574 |
| round_robin_cost | 1.328 |

Candidate Greedy placement repeatedly scans queued work and can cost roughly 1.3 ms per placement at this scale. This overhead and its lack of action-matched P95 improvement argue against presenting it as a finished scheduler.

## Limits and next falsifiable experiment

The compute curve is illustrative; the H2D fit comes from contended, layered KV loading. No pure GPU-prefill calibration, GPU batching/decode model, multi-node NIC measurement, general fair network solver or production trace validation is available. The new bounded H2D experiment is a distinct contiguous-copy layout and does not update defaults.

Next: compare current-cache FIFO forecasts against a shadow cache/queue rollout on development seeds and controlled compute-bound traces, while keeping service actions matched. Evaluate any new placement method on fresh seeds (reserve 201–205); 101–105 are now consumed evaluation data and must not be reused as untouched holdout. Hardware prefill calibration should isolate loading/scatter from actual compute before interpreting absolute speedups.
