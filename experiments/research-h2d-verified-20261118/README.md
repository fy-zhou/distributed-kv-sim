# Bounded H2D and CPU-affinity experiments

Physical single-host RTX4090 measurements, distinct from simulator results.
Every pilot retained; the first exceeds its declared tensor-memory bound (192 vs 128 MiB). Corrected and all four ABBA affinity runs pass the measured 128 MiB tensor bound.
CPU 16 is on GPU0-local NUMA 0; CPU 80 on NUMA 1. 64 MiB single-copy throughput: near 22.41/22.28 GiB/s, far 10.18/10.24 GiB/s. Two-stream far-socket throughput is variable (10.46/18.92 GiB/s).
Two process replicates per affinity, ordered ABBA; shared host, GPU1 occupied. Affinity is controlled, physical page placement is not independently verified. No KV scatter, prefill, inter-node NIC measurement, or simulator default replacement. CUDA event makespan includes launch gaps. All 720 raw samples and environment records remain outside Git and are hashed in manifest.json.
