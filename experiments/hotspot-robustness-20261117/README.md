# Hotspot robustness experiment

**Source:** Synthetic discrete-event simulation; not measured GPU/NIC results.

## Coverage

- Paired policy comparisons: 1200
- Aggregated parameter combinations: 240
- Seed counts per aggregate: 5
- Nonzero-lambda comparisons: 1000

## P95 TTFT outcome by scenario

- Improvement: **352** (35.2%)
- No material difference (absolute delta <= 1e-7%): **0**
- Regression: **648**

## Dependence on externality weight

| Lambda | Comparisons | Mean P95 improvement (%) | Positive | Neutral | Negative |
|---:|---:|---:|---:|---:|---:|
| 0.25 | 200 | -5.425 | 66 | 0 | 134 |
| 0.5 | 200 | -1.326 | 63 | 0 | 137 |
| 1 | 200 | -0.269 | 70 | 0 | 130 |
| 2 | 200 | -4.289 | 74 | 0 | 126 |
| 4 | 200 | -0.660 | 79 | 0 | 121 |

All percent improvements use each scenario's Greedy baseline. Different lambda settings on the same seed are correlated, so the comparisons are not independent replicates.

## Inspectable files

- [aggregated_compact.csv](aggregated_compact.csv): grouped summary across seeds.
- [paired_compact.csv](paired_compact.csv): compact paired results for each seed.
- [manifest.json](manifest.json): source hashes, source-code commit, parameters.

Omitted intentionally: per-request execution traces and large raw result files.
Source hashes allow verifying the retained server-side raw results.
