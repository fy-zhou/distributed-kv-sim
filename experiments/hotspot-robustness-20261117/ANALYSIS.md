# Post-hoc diagnostic analysis: hotspot robustness (2026-11-17)

This analysis is derived from the checked-in
[paired_compact.csv](paired_compact.csv) and
[aggregated_compact.csv](aggregated_compact.csv) for the 240-request
**synthetic** discrete-event simulator. The base source is recorded by
[manifest.json](manifest.json). No hardware-level latency validation is
claimed.

## Overall result

Among 1,000 nonzero-lambda policy comparisons, bandwidth-aware improves
P95 TTFT in **352** cases and regresses in **648** cases (no exact
neutral cases at the 1e-7-percentage-point tolerance). Comparisons with
different weights and fabric settings reuse seed/traces and are **not
statistically independent observations**. Aggregate figures are
descriptive and must not be used as p-values or confidence intervals.

## Main effects (paired change, relative to Greedy)

| Condition | Comparisons | Positive | Negative | Mean P95 improvement |
|:--|--:|--:|--:|--:|
| Fabric 1 GiB/s | 200 | 130 | 70 | +10.55% |
| Fabric 2 GiB/s | 200 | 75 | 125 | -3.76% |
| Fabric 4 GiB/s | 200 | 47 | 153 | -6.90% |
| Cache 256 blocks | 500 | 90 | 410 | -10.02% |
| Cache 512 blocks | 500 | 262 | 238 | +5.23% |
| Burst width 1 | 250 | 86 | 164 | -16.80% |
| Burst width 4 | 250 | 95 | 155 | +3.32% |
| Burst width 8 | 250 | 98 | 152 | +4.75% |
| Burst width 16 | 250 | 73 | 177 | -0.85% |

### Important interactions

| Fabric | Cache blocks | Positive / 100 | Mean P95 improvement | Mean network-volume reduction |
|--:|--:|--:|--:|--:|
| 1 | 256 | 50 | +3.05% | -70.97% |
| 1 | 512 | 80 | +18.06% | +26.64% |
| 2 | 256 | 10 | -13.29% | -188.47% |
| 2 | 512 | 65 | +5.76% | +9.52% |
| 4 | 256 | 10 | -13.29% | -188.47% |
| 4 | 512 | 37 | -0.50% | +5.44% |
| 8 | 256 | 10 | -13.29% | -188.47% |
| 8 | 512 | 40 | +1.43% | +7.57% |

A **negative** network-volume reduction indicates that the bandwidth-aware
policy transferred **more** bytes than Greedy. Percentage reductions can be
less than -100% when Greedy's original volume is small.

The worst interaction occurs for **burst width 1 / cache 256**:
12/125 positive, 113/125 negative, mean P95 improvement **-36.61%**,
and mean network-volume reduction **-500.8%**. In contrast,
**burst width 8 / cache 512** has mean P95 improvement +8.97%,
63/125 positive; the mean alone conceals frequent regressions.

## Hypotheses to test, *not established causes*

1. **Cache capacity cliffs:** Source cache capacity 256 blocks equals the
   entire hot prefix of 256 blocks. With `admission=always`, completing
   requests can evict valuable prefix blocks and radically change routing.
   Instrument hot-prefix residency and evictions *per node*.
2. **Endpoint bottleneck:** NIC capacity is held at 2 GiB/s throughout the
   sweep. Multiple Fabric configurations above 2 GiB/s produce identical
   results for some scenarios, consistent with a NIC-bound regime.
   Sweep NIC and Fabric independently and log limiting resource.
3. **Future-state blind spot:** Externality penalty is a first-order
   instantaneous flow approximation. It does not predict future cache
   residency, admission/eviction, or future arrivals. More network
   externality avoidance can lead to excessive later remote transfers.
4. **Counterfactual identification:** Changed placement and reduced
   simulated P95 do not by themselves identify whether reduced network
   contention, queue differences, or cache evolution caused the gain.

## Recommended controlled follow-up

Keep the raw server trace, but archive compact condition-level diagnostics:
`(seed, fabric, NIC, cache, burst, lambda)`, P95, per-node source-hot-prefix
residency, per-node evictions, transfer bytes, and queue/network/H2D/prefill
components. Test independent NIC values 0.5/1/2/4/8 GiB/s against Fabric
1/2/4/8/16 GiB/s. Separate *cache pollution* from *network contention* by
running with admission and resident hot-prefix protection ablations.

If tuning lambda or designing a new conditional policy, use seeds outside
41-45 as held-out evaluation seeds rather than selecting the best lambda
from the same data.

## Example cases worth replaying

- Worst observed: seed 44, fabric 2 GiB/s, burst width 1,
  cache 256, lambda .25: Greedy P95 ~3999.36 ms vs aware ~8893.25 ms
  (**-122.37% improvement**).
- Positive observed: seed 43, fabric 2 GiB/s, burst width 8,
  cache 256, lambda .25: Greedy P95 ~9906.16 ms vs aware ~4469.64 ms
  (**+54.88% improvement**).

Both examples are exploratory post-hoc selections from the same grid, not
representative or validation workloads.
