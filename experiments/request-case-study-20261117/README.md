# Hotspot request-level diagnostic case study

**Synthetic discrete-event simulation. Post-hoc selected extremes; not representative and not proof of causality.**

| Case | Seed | NIC | Fabric | Cache | Burst | Changed placements | P95 change |
|:--|--:|--:|--:|--:|--:|--:|--:|
| regression | 42 | 4 | 4 | 256 | 1 | 146/240 | -192.86% |
| improvement | 45 | 2 | 2 | 512 | 8 | 94/240 | +39.57% |

## Queue/network/compute decomposition

| Case | Policy | Mean queue ms | Mean network ms | Mean H2D ms | Mean compute ms | Source hot prefix (mean blocks) |
|:--|:--|--:|--:|--:|--:|--:|
| regression | Greedy | 947.64 | 3.74 | 1.78 | 28.58 | 11.7 |
| regression | Bandwidth-aware | 2728.93 | 78.54 | 27.64 | 7.00 | 256.0 |
| improvement | Greedy | 4367.27 | 132.99 | 31.06 | 4.15 | 246.4 |
| improvement | Bandwidth-aware | 3123.41 | 66.93 | 30.38 | 4.72 | 256.0 |

## Inspectable examples

- Regression: [requests](regression_sample_requests.csv) and [per-candidate scheduler scores](regression_sample_decisions.csv)
- Improvement: [requests](improvement_sample_requests.csv) and [per-candidate scheduler scores](improvement_sample_decisions.csv)

Samples include requests with largest absolute TTFT differences and the earliest changed placements. Because policies evolve different cache and queue states after divergence, their decision scores are not evaluated on identical runtime states.

## Limitations

Selection is based on the already observed extreme outcomes. Queue, network, compute and cache trajectories are descriptive; do not infer a causal mechanism without additional controlled ablations. All times are model-generated, not actual GPU/NIC measurements.
