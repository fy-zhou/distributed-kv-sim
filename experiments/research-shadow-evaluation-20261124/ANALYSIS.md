# Shadow queue/cache rollout: action-matched placement evidence

All TTFT/traffic values are synthetic. Hardware-model coefficients and minimum_service execution actions are unchanged. Each policy uses identical request and initial-cache hashes per condition. No future arrivals are read.

Seeds: [201, 202, 203, 204, 205]; 1400 runs; 120 requests/run. All three variants were declared before held-out evaluation. The primary placement control is round_robin_cost, not a baseline with different reuse actions.

## All predeclared comparisons

Positive means reduced P95. Means average paired percentages over declared scenario/admission conditions. Bootstrap endpoints resample entire seeds, preserving all within-seed correlations; few seed clusters and multiple candidates make these descriptive conditional uncertainty, not a generalization or significance guarantee.

| Candidate | Baseline | Improve / neutral / regress | Mean P95 improvement % | Seed bootstrap 2.5–97.5% |
|---|---|---:|---:|---:|
| shadow_bounded | cost_aware | 18 / 120 / 2 | +1.532 | [+1.104, +2.071] |
| shadow_bounded | round_robin_cost | 20 / 116 / 4 | +1.763 | [+1.245, +2.380] |
| shadow_bounded | shadow_frozen | 20 / 120 / 0 | +2.108 | [+1.566, +2.877] |
| shadow_cache | cost_aware | 18 / 120 / 2 | +1.635 | [+1.136, +2.194] |
| shadow_cache | round_robin_cost | 24 / 116 / 0 | +1.884 | [+1.341, +2.487] |
| shadow_cache | shadow_frozen | 19 / 120 / 1 | +2.204 | [+1.637, +2.984] |
| shadow_frozen | cost_aware | 1 / 130 / 9 | -0.888 | [-1.518, -0.315] |
| shadow_frozen | round_robin_cost | 7 / 116 / 17 | -0.546 | [-1.060, -0.045] |

## Fair placement plus execution-planning overhead

All policies use the same timers: actual placement selection plus exactly one actual execution-time service plan per request. Observer forecasts/residuals and serialization are excluded. These shared-host CPU wall times are not simulated TTFT, isolated microbenchmarks, or added latency in the event model. Policy execution order is recorded in the run config.

| Policy | Placement us | Execution planning us | Total planning us/request | Queue forecast MAE ms | TTFT forecast MAE ms |
|---|---:|---:|---:|---:|---:|
| bandwidth_aware | 1180.049 | 32.169 | 1212.218 | 433.426 | 467.496 |
| greedy | 1101.467 | 33.183 | 1134.651 | 443.966 | 478.555 |
| shadow_cache | 6957.123 | 68.820 | 7025.943 | 10.151 | 11.229 |
| shadow_frozen | 1794.867 | 66.598 | 1861.465 | 35.266 | 38.058 |
| shortest_queue | 3.891 | 37.126 | 41.017 | 1545.697 | 1641.463 |
| round_robin_cost | 2.118 | 68.822 | 70.940 | 57.704 | 64.869 |
| round_robin | 2.118 | 37.768 | 39.886 | 1568.795 | 1664.923 |
| cost_aware | 1205.823 | 65.231 | 1271.055 | 25.277 | 28.209 |
| max_hit | 37.210 | 17.385 | 54.595 | 158.225 | 160.818 |
| shadow_bounded | 4607.270 | 67.295 | 4674.566 | 11.598 | 12.881 |

## Absolute P95 and traffic in informative regimes

Means across the seeds within each scenario/admission. Full P50/P95/P99/mean, timing decomposition, hits, traffic, eviction/residency and node-load metrics for every condition remain in compact_runs.csv (rounded) and raw runs.csv (full precision).

| Scenario | Admission | Policy | P95 ms | GiB | Queue MAE ms | Maximum node request share |
|---|---|---|---:|---:|---:|---:|
| compute_heavy | always | round_robin_cost | 3038.068 | 7.438 | 774.632 | 0.250 |
| compute_heavy | always | cost_aware | 3058.024 | 5.950 | 243.752 | 0.598 |
| compute_heavy | always | shadow_frozen | 3401.621 | 6.737 | 373.408 | 0.550 |
| compute_heavy | always | shadow_cache | 2044.042 | 3.500 | 95.031 | 0.298 |
| compute_heavy | always | shadow_bounded | 2158.485 | 4.638 | 104.010 | 0.318 |
| compute_heavy | no_remote | round_robin_cost | 3606.957 | 11.025 | 523.869 | 0.250 |
| compute_heavy | no_remote | cost_aware | 3069.325 | 7.525 | 175.471 | 0.598 |
| compute_heavy | no_remote | shadow_frozen | 3490.768 | 9.275 | 325.517 | 0.550 |
| compute_heavy | no_remote | shadow_cache | 2940.997 | 8.838 | 189.198 | 0.508 |
| compute_heavy | no_remote | shadow_bounded | 2874.162 | 8.750 | 183.099 | 0.515 |
| long_prefix | always | round_robin_cost | 2105.349 | 0.000 | 158.604 | 0.250 |
| long_prefix | always | cost_aware | 2177.589 | 0.000 | 144.262 | 0.298 |
| long_prefix | always | shadow_frozen | 2177.589 | 0.000 | 144.262 | 0.298 |
| long_prefix | always | shadow_cache | 2083.978 | 0.000 | 0.000 | 0.258 |
| long_prefix | always | shadow_bounded | 2098.572 | 0.000 | 18.820 | 0.268 |
| long_prefix | no_remote | round_robin_cost | 2105.349 | 0.000 | 158.604 | 0.250 |
| long_prefix | no_remote | cost_aware | 2177.589 | 0.000 | 144.262 | 0.298 |
| long_prefix | no_remote | shadow_frozen | 2177.589 | 0.000 | 144.262 | 0.298 |
| long_prefix | no_remote | shadow_cache | 2083.978 | 0.000 | 0.000 | 0.258 |
| long_prefix | no_remote | shadow_bounded | 2098.572 | 0.000 | 18.820 | 0.268 |

## Retained failures

Up to four worst regressions per candidate/comparator are selected post-hoc for diagnosis, not unbiased representative workloads. Every comparison remains in compact_pairs.csv.

| Candidate | Baseline | Scenario | Seed | Admission | Candidate P95 ms | Control P95 ms | Improvement % |
|---|---|---|---:|---|---:|---:|---:|
| shadow_cache | cost_aware | compute_heavy | 201 | no_remote | 3271.876 | 2932.538 | -11.571 |
| shadow_cache | cost_aware | compute_heavy | 205 | no_remote | 3477.966 | 3332.738 | -4.358 |
| shadow_bounded | round_robin_cost | long_prefix | 204 | always | 2118.084 | 2108.985 | -0.431 |
| shadow_bounded | round_robin_cost | long_prefix | 204 | no_remote | 2118.084 | 2108.985 | -0.431 |
| shadow_bounded | round_robin_cost | long_prefix | 205 | always | 2135.320 | 2133.652 | -0.078 |
| shadow_bounded | round_robin_cost | long_prefix | 205 | no_remote | 2135.320 | 2133.652 | -0.078 |
| shadow_bounded | cost_aware | compute_heavy | 205 | no_remote | 3477.966 | 3332.738 | -4.358 |
| shadow_bounded | cost_aware | compute_heavy | 201 | no_remote | 2937.699 | 2932.538 | -0.176 |
| shadow_frozen | round_robin_cost | compute_heavy | 205 | always | 4043.122 | 3021.394 | -33.816 |
| shadow_frozen | round_robin_cost | compute_heavy | 201 | always | 3244.962 | 2800.923 | -15.853 |
| shadow_frozen | round_robin_cost | compute_heavy | 202 | no_remote | 4377.033 | 3895.763 | -12.354 |
| shadow_frozen | round_robin_cost | compute_heavy | 202 | always | 3412.819 | 3215.220 | -6.146 |
| shadow_frozen | cost_aware | compute_heavy | 202 | no_remote | 4377.033 | 3000.337 | -45.885 |
| shadow_frozen | cost_aware | compute_heavy | 205 | always | 4043.122 | 3146.447 | -28.498 |
| shadow_frozen | cost_aware | compute_heavy | 205 | no_remote | 3856.831 | 3332.738 | -15.726 |
| shadow_frozen | cost_aware | compute_heavy | 203 | always | 3277.700 | 2963.730 | -10.594 |

## Approximation limits and interpretation

The rollout changes forecasts only. Real execution uses the shared planner and live caches. Forecasts roll only a target node, freeze other caches, assume isolated future transfer rates, and retain outgoing pins for the forecast lifetime. Full rollout does not make those assumptions exact; bounded rollout also freezes cache state after eight completions while still charging all tail work. Frozen-cache ablation isolates the effect of cache evolution within this shared forecast model.

An improved queue forecast does not alone establish improved tail latency or aggregate-system cost. Service latency is still illustrative, and CPU planning overhead is reported separately rather than charged to arrivals. No GPU-prefill, KV-scatter or multi-machine NIC calibration was added in this iteration. Future candidate changes must use fresh held-out seeds; this run consumes its declared evaluation seeds.
