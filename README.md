# Distributed KV Simulator

A lightweight, deterministic **discrete-event simulator** for multi-node CPU-resident KV prefix reuse. Written in Python (standard library only). No GPUs, model weights, RDMA devices, root permissions or externally hosted services required to run the simulation.

**Status: prototype v0.2 candidate (on `feat/v02-predictor-correctness`).** This is a synthetic simulator, not measured cluster performance. Compute times are an illustrative workload-dependent model; 4090 H2D coefficients are imported from a separate single-node experiment, and network bandwidth is configured. Do not interpret output TTFT as actual vLLM measurements.

## Quick start (4090-02 or any Linux host)

```bash
cd /state/partition/fyzhou
git clone https://github.com/pe0-p1e/distributed-kv-sim.git
cd distributed-kv-sim

python3 -m unittest discover -s tests -v

python3 scripts/run.py --nodes 4 --requests 200 \
  --cache-blocks 256 --initial-occupancy 1.0 \
  --arrival-ms 12 --out results/smoke

cat results/smoke/summary.csv
```

No GPU is needed. If using the existing vLLM virtual environment, its Python interpreter also works.

## Parameter sweep

```bash
for arrival in 3 6 12 24; do
  for bw in 2 8 24; do
    python3 scripts/run.py --nodes 4 --requests 1000 \
      --cache-blocks 256 --families 8 --shared-blocks 32 \
      --initial-occupancy 1.0 --arrival-ms "$arrival" \
      --nic-gib-s "$bw" --fabric-gib-s "$bw" \
      --out "results/a${arrival}_b${bw}"
  done
done
```

Outputs: `summary.csv` (policy-level P50/P95/P99 TTFT, cache hit rate, bytes transferred, eviction counts and throughput), `requests.csv` (per-request times, placement, source and cached/recomputed tokens), and `configuration.json`.

## Semantics

- **Prefix hashing:** SHA256 chained across fixed 16-token blocks, including a configurable namespace; a matching internal chunk without matching preceding blocks is not reusable.
- **Initial caches:** deterministic 100%-occupied CPU block LRU caches seeded with actual family prefixes and cold dummy-block identities; they contain no tensor bytes.
- **KV state:** a persistent CPU block store per node. GPU-resident active inference KV is abstracted separately; nodes process one request at a time in v0.1.
- **Remote hits:** source must hold a longer contiguous prefix than target. Remote flow copies only the missing extension to the destination, then the full reused prefix undergoes local H2D to GPU. One source per request in v0.1.
- **Bandwidth sharing:** all active flows receive a conservative equal share of sending NIC, receiving NIC and common fabric bandwidth. Completion times change when transfers enter/leave; a stale-event generation guard prevents double completions.
- **Cache admission:** on request completion, persistent blocks may replace unpinned LRU entries. `always` retains fetched and computed blocks; `no_remote` retains only computed suffix. Source blocks are pinned during transfer.
- **Scheduling:** `round_robin`, `shortest_queue`, `max_hit` (local prefix), `greedy` (heuristic estimated current completion).
- **Timing:** remote transfer + local H2D + remaining Prefill are sequential *for each request* in v0.1. Distinct nodes and transfers progress concurrently. TTFT is arrival-to-end-of-Prefill plus a fixed compute overhead; there is not yet a full decode model.

## Performance model

For total length `L`, prefix hit `H` and recomputation `M = L - H`:

```text
GPU_compute_ms = gpu_fixed_ms + gpu_ms_per_new_token*M
               + gpu_ms_per_attention_pair * M*(2*H+M+1)/2
H2D_ms = (H > 0) * [1.684 + 71.210 * (H * kv_bytes_per_token / 2^30)]
Remote transfer = payload_bytes / dynamically shared link throughput
```

The default GPU compute coefficients are **illustrative, not calibrated**. Default H2D values correspond to the 2026-11 Qwen3-0.6B / RTX 4090 pinned-memory transfer regression (valid only over its measured payload range); see [single-node lab](https://github.com/pe0-p1e/vllm-prefix-lab/tree/exp/prefix-hit-matrix/docs/experiments/cpu-kv-20261105-detailed). The simulator can be used to test algorithm invariants but not to claim realistic speedups until its compute and network predictors have independent measured calibration.

## Roadmap and limitations

1. Validate FIFO/greedy/hit policies and fairness against closed-form microcases.
2. Fit ComputePredictor to true GPU Prefill-only timings; fit NIC/link contention to real network experiments.
3. Add chunked prefill, requests with arbitrary decode lengths and overlap, multi-source fetching, fairness policies and separate GPU/CPU cache capacities.
4. Implement eviction-aware scheduling, bandwidth-aware scheduling and limited-lookahead policies.
5. Use recorded trace replay and compare simulation error against real multi-node vLLM execution.

Conceptually informed by [Vidur](https://github.com/microsoft/vidur) (event simulation/performance profiling), [Preble](https://arxiv.org/abs/2407.00023) (prefix locality scheduling) and [Mooncake](https://arxiv.org/abs/2407.00079) (KV transfer/cache tiers). No upstream code has been copied.

## v0.2 candidate: predictor and correctness improvements

v0.2 introduces explicit `ComputePredictor`, `H2DPredictor` and
`TransferPredictor` classes in `kv_sim/predictor.py`. Their parameters are
CLI-configurable. The GPU compute default remains **illustrative** and must
be replaced with actual measurements before drawing hardware conclusions.

The greedy cost estimator now accounts for already elapsed time of the active
request, including actual transfer remaining bytes if a transfer is underway,
and includes fetch/H2D cost for queued requests. It is *still an approximation*:
future contention and cache evolution are unknown.

With `admission=no_remote`, requests that fetch a remote prefix retain none of
the remote-dependent KV blocks. This avoids creating unusable orphan suffix
entries; it is a deliberately conservative first policy, not optimal selective
admission.

### Test this branch on 4090-02

```bash
cd /state/partition/fyzhou/distributed-kv-sim
git -c http.proxy=http://127.0.0.1:17890 fetch origin
git switch --track origin/feat/v02-predictor-correctness
python3 -m unittest discover -s tests -v
python3 scripts/run.py --nodes 4 --requests 200 \
  --out /state/partition/fyzhou/results/distributed-kv-sim/v02-smoke
python3 scripts/experiment_matrix.py --requests 300 \
  --out /state/partition/fyzhou/results/distributed-kv-sim/v02-matrix
```

To restore the previous stable implementation: `git switch main`.

### Experimental design caveat

`experiment_matrix.py` is a **synthetic algorithm comparison**, not a
hardware validation. The network is a fluid contention model with configured
capacities, and GPU compute is a placeholder performance curve. Compare
relative behavior, correctness and pathological traces; do not report its
latency values as observed vLLM TTFT. For all published performance results,
perform independent physical GPU/NIC calibration and error analysis.

## Modular architecture refactor (branch: `refactor/modular-architecture`)

The original v0.2 behavior is being reorganized into separate packages while
retaining backwards-compatible `kv_sim.core`, `kv_sim.prefix` and
`kv_sim.network` imports. This branch is **not yet server-validated**.

```text
kv_sim/
  engine/    simulator.py, events.py, clock.py
  cluster/   node.py, gpu.py, network.py
  cache/     prefix_hash.py, block_store.py, lru.py, admission.py
  scheduler/ round_robin.py, shortest_queue.py, max_hit.py,
             greedy.py, bandwidth_aware.py
  predictor/ compute.py, h2d.py, transfer.py
  workload/  synthetic.py, trace.py
  metrics/   collector.py
```

- The **engine** now performs request and event orchestration, not the
  definitions of every subsystem.
- **Scheduler** functions choose placement; all four legacy strategies plus
  an **experimental** `bandwidth_aware` candidate are selectable.
- **Cache** separates cryptographic block IDs, capacity-constrained storage,
  LRU eviction, and admission decisions.
- **Cluster** holds node state, the fluid shared network, and a minimal
  one-request-at-a-time GPU resource abstraction.
- **Predictor** implementations are independent calibration targets.
- **Workload** supports synthetic prefix-locality traces and CSV replay with
  `tokens` containing JSON arrays of token IDs.
- **Metrics** computes per-policy request latency and cache/network totals.

### Verify on server using your own proxy

```bash
cd /state/partition/fyzhou/distributed-kv-sim
git -c http.proxy=http://127.0.0.1:17890 fetch origin
git switch --track origin/refactor/modular-architecture
python3 -m compileall -q kv_sim scripts tests
python3 -m unittest discover -s tests -v
python3 scripts/run.py --nodes 4 --requests 200 --out /state/partition/fyzhou/results/distributed-kv-sim/modular-smoke
python3 scripts/run.py --nodes 4 --requests 200 --policy bandwidth_aware --out /state/partition/fyzhou/results/distributed-kv-sim/modular-bandwidth-aware
```

Do not treat the experimental `bandwidth_aware` heuristic as an established
result or a performance improvement; compare it under identical traces.
