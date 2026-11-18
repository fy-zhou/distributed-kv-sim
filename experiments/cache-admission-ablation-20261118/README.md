# Cache admission × scheduler controlled ablation

All results are synthetic discrete-event simulations. The two strata were selected post-hoc in the prior archive; seeds are paired across policy and admission. This is not an unbiased performance estimate.

The no_remote policy **does not cache a completed request** if it reused any remote blocks. It is not a hot-prefix pinning policy.

| Stratum | Admission | Seeds | Mean P95 improvement (%) | Positive seeds | Greedy P95 mean (ms) | Aware P95 mean (ms) |
|:--|:--|--:|--:|--:|--:|--:|
| improvement | always | 5 | +19.94 | 5 | 7748.92 | 6045.64 |
| improvement | no_remote | 5 | +28.56 | 5 | 18236.65 | 9895.11 |
| regression | always | 5 | -32.68 | 2 | 2431.51 | 3085.54 |
| regression | no_remote | 5 | -32.68 | 2 | 2431.51 | 3085.54 |

[Paired seed-level results](pairs.csv) | [Per-policy metrics](runs.csv) | [Manifest](manifest.json)

Do not interpret admission-induced performance changes as proof of the cause of observed scheduler regressions; they may involve cache state, queue lengths and routing interactions.
