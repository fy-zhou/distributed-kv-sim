# NIC × Fabric × Cache: diagnostic archive

**Synthetic simulation only.** No hardware-level latency claims.

Paired experiments: 800; nonzero externality weight: 400.
P95 beneficial / neutral / harmful: 172 / 4 / 224.

| Cache blocks | Comparisons | Mean P95 improvement | Mean source hot-prefix retention delta |
|---:|---:|---:|---:|
| 256 | 200 | -9.50% | 42.04 blocks |
| 512 | 200 | 5.52% | 2.29 blocks |

[Inspect compact CSV](compact.csv)
 | [Manifest and source hashes](manifest.json)

Configuration columns are paired on identical seed and workload. Retained source-prefix blocks are observational metrics, not proof of causal mechanisms.
