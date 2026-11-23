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

## Hotspot experiment: inspect scheduler decisions

Use the standalone experiment on branch `exp/hotspot-evaluation-v2`:

```bash
cd /state/partition/fyzhou/distributed-kv-sim
git -c http.proxy=http://127.0.0.1:17890 fetch origin
git switch --track origin/exp/hotspot-evaluation-v2
python3 -m unittest discover -s tests -v
python3 scripts/hotspot_experiment.py --nodes 4 --requests 240 --cache-blocks 512 --shared-blocks 256 --burst-width 8 --burst-gap-ms 2 --fabric-gib-s 2 --nic-gib-s 2 --out /state/partition/fyzhou/results/distributed-kv-sim/hotspot-v2
```

A fully deterministic synthetic workload generates many requests with the same
long shared prefix, unique suffixes, and bursty arrivals. All nodes start with
full CPU caches; node 0 initially stores the hot prefix and other nodes store
cold blocks. Models and network transmission are still *simulated*.

Files under `hotspot-v2`:
- `summary.csv`: per-policy P50/P95/P99, aggregate transfers and evictions
- `requests.csv`: per-request actual simulated service outcomes
- `decisions.csv`: **every candidate node**'s breakdown, externality, policy score, selected bit
- `placement_diff.csv`: Greedy vs bandwidth-aware selected nodes and whether they differ
- `config.json`: parameters used

An equal P95 does not imply equal placement. Compare `placement_diff.csv`.
Conversely, changed placement does not establish improved system performance.
Bandwith-aware externality is a first-order heuristic; it is not a network
counterfactual solver or a calibrated physical latency model.

Next validation: collect real network concurrency measurements, fit GPU
prefill-only compute, replay several seeds and workload regimes.

## Paired Hotspot Robustness Sweep (`exp/hotspot-robustness`)

This branch evaluates **stability, not just one favorable seed**. Every
`(seed, fabric, burst, cache)` scenario runs Greedy once and
Bandwidth-Aware once per `lambda` on the exact same requests and initial
LRU state. The simulator validates that `lambda=0` yields the same
per-request placement, hit counts and simulated timing as Greedy.

### Fast verification on 4090-02 (Python 3.8 compatible)

```bash
cd /state/partition/fyzhou/distributed-kv-sim
git -c http.proxy=http://127.0.0.1:17890 fetch origin
git switch --track origin/exp/hotspot-robustness
python3 -m unittest discover -s tests -v
python3 scripts/hotspot_robustness.py --requests 24 --shared-blocks 16 --cache-blocks 32 --seeds 41 42 --fabric-gib-s 1 4 --burst-widths 1 4 --lambdas 0 1 --out /state/partition/fyzhou/results/distributed-kv-sim/robust-smoke
```

### Full sensitivity matrix (CPU-only, larger sweep)

```bash
python3 scripts/hotspot_robustness.py \
  --seeds 41 42 43 44 45 \
  --fabric-gib-s 1 2 4 8 16 \
  --burst-widths 1 4 8 16 \
  --cache-blocks 256 512 \
  --lambdas 0 0.25 0.5 1 2 4 \
  --requests 240 --shared-blocks 256 \
  --out /state/partition/fyzhou/results/distributed-kv-sim/robust-full
```

Outputs:
- `runs.csv`: *paired-by-seed* Greedy and Bandwidth-Aware metrics; signed
  improvement (%) can be negative.
- `aggregated.csv`: per-configuration averages, median, empirical
  across-seed P10/P90, min/max, positive-seed count.
- `config.json`: full experimental parameters and simulation caveats.

Optional figures, if `matplotlib` is installed:

```bash
python3 scripts/plot_hotspot_robustness.py \
  --input /state/partition/fyzhou/results/distributed-kv-sim/robust-full/aggregated.csv \
  --cache-blocks 512 --burst-width 8 --lambda-weight 1 --fabric-gib-s 2
```

Figures: P95 improvement vs fabric capacity, P95 improvement vs burst width,
and placement changes vs lambda. Shading denotes observed P10-P90 across
seeds, **not** 95% confidence intervals.

### Interpretation / limitations

Do **not** pick an optimal lambda on the same seeds and then report its
performance as an unbiased hold-out result. Use separate tuning and
evaluation seeds. Report unsuccessful and negative-improvement regimes.
Hardware calibration and validation are still required. This model does
not implement physical NIC packet congestion, full GPU decode, or arbitrary
multi-hop topology.

## Commit lightweight experiment records to GitHub

Server-side full results normally live outside the repository, e.g.
`/state/partition/fyzhou/results/distributed-kv-sim/robust-full`.
Do not commit per-request traces or the full results tree. Instead, use
the standard-library-only archive exporter to create an inspectable,
compact, versioned record in `experiments/`.

```bash
cd /state/partition/fyzhou/distributed-kv-sim
python3 scripts/archive_experiment.py \
  --source /state/partition/fyzhou/results/distributed-kv-sim/robust-full \
  --destination experiments/hotspot-robustness-20261117
git status --short
git add experiments/hotspot-robustness-20261117
git commit -m "Record hotspot robustness sweep: 5 seeds, 1200 paired runs"
git -c http.proxy=http://127.0.0.1:17890 push origin exp/hotspot-robustness
```

The archive includes:
- `README.md` — methodology, positive / neutral / negative outcome counts
  for nonzero lambda, and breakdown by lambda.
- `paired_compact.csv` — 10 retained columns per run; per-seed results stay
  inspectable without pushing full request/decision traces.
- `aggregated_compact.csv` — reduced parameter-level summary across seeds.
- `manifest.json` — actual code commit, reproducibility parameters, hashes
  of original `runs.csv`, `aggregated.csv` and `config.json`.

The exporter refuses to exceed 1 MiB in total. It also refuses to overwrite
an already nonempty archive directory: create a new dated or named directory
for each new run. Git push requires the server's existing Git credentials.
Store raw data and credentials on the server, outside of Git.

Only commit figures when they are genuinely useful and small; SVG files
are usually more inspectable and lightweight than screenshots.

## Post-hoc robustness findings and cache/network diagnostic follow-up

The compact result archive is at
`experiments/hotspot-robustness-20261117/`. Its
`ANALYSIS.md` documents the observed 352 positive / 648 negative
nonzero-lambda comparisons, capacity cliff, and suspected endpoint NIC
saturation. These are synthetic results, **not** measured cluster speedups.

Branch `exp/cache-network-diagnostics` adds an opt-in, read-only cache
residency trace (`Simulator.track_prefix`) and a paired diagnostic runner
which varies **NIC capacity independently from Fabric capacity**.
It records P95, network traffic, queue/network/H2D/compute totals,
per-node source evictions, and the remaining contiguous hot prefix.

### Fast diagnostic smoke test

```bash
cd /state/partition/fyzhou/distributed-kv-sim
git -c http.proxy=http://127.0.0.1:17890 fetch origin
git switch --track origin/exp/cache-network-diagnostics
python3 -m unittest discover -s tests -v
python3 scripts/cache_network_diagnostics.py --seeds 41 42 --nics 1 2 --fabrics 1 4 --cache-blocks 256 512 --bursts 1 8 --lambdas 0 1 --requests 36 --shared-blocks 16 --out /state/partition/fyzhou/results/distributed-kv-sim/diagnostics-smoke
```

### Focused multi-seed diagnostic

```bash
python3 scripts/cache_network_diagnostics.py \
  --seeds 41 42 43 44 45 \
  --nics 1 2 4 8 \
  --fabrics 1 2 4 8 16 \
  --cache-blocks 256 512 \
  --bursts 1 8 --lambdas 0 1 \
  --requests 240 --shared-blocks 256 \
  --out /state/partition/fyzhou/results/distributed-kv-sim/diagnostics-full
```

Output `diagnostics.csv` contains condition-level paired results and
`source_cache_trace_example.csv` shows the source-node hot prefix trajectory
for one example scenario; store the complete output outside the repository.
The test source caches are full; cache 256 equals exactly the hot prefix.
No real GPU or network transfers are performed.

### Compact diagnostic archive for GitHub

After the diagnostic run finishes, retain only condition-level results and
source hashes (hard cap: 1 MiB):

```bash
python3 scripts/archive_cache_network.py \
  --source /state/partition/fyzhou/results/distributed-kv-sim/diagnostics-full \
  --destination experiments/cache-network-diagnostics-20261117
git add experiments/cache-network-diagnostics-20261117
git commit -m "Record paired NIC fabric cache diagnostics"
git -c http.proxy=http://127.0.0.1:17890 push origin exp/cache-network-diagnostics
```

The archive contains `README.md`, `compact.csv` and
`manifest.json`. Raw event-level traces remain on the server.

## Archived Request-Level Case Study (`exp/request-case-study`)

This branch replays **two post-hoc selected extremes from the archived
cache/network matrix** with an identical request trace and initial cache for
Greedy and Bandwidth-Aware, then checks that the replayed P95, placement
changes, source hot-prefix residency, and evictions match the archived
condition-level results. It does **not** claim independent validation.

- Regression case: seed 42, NIC 4 GiB/s, Fabric 4 GiB/s,
  cache 256, burst width 1, lambda 1.
- Improvement case: seed 45, NIC 2 GiB/s, Fabric 2 GiB/s,
  cache 512, burst width 8, lambda 1.

### Run and verify on the server

```bash
cd /state/partition/fyzhou/distributed-kv-sim
git -c http.proxy=http://127.0.0.1:17890 fetch origin
git switch --track origin/exp/request-case-study
python3 -m unittest discover -s tests -v
python3 scripts/request_case_study.py \
  --archive experiments/cache-network-diagnostics-20261117 \
  --out /state/partition/fyzhou/results/distributed-kv-sim/request-case-study
```

**Complete server-only traces** per case:
`paired_requests.csv`, `greedy_decisions.csv`,
`aware_decisions.csv`, `greedy_cache.csv`, `aware_cache.csv`.
Also writes candidate-score and request samples, and a `summary.json`.
A compact `case_summaries.json` is written to the run root.

### Compact GitHub archive (max 1 MiB)

```bash
python3 scripts/archive_request_case_study.py \
  --source /state/partition/fyzhou/results/distributed-kv-sim/request-case-study \
  --destination experiments/request-case-study-20261117
git add experiments/request-case-study-20261117
git commit -m "Archive paired hotspot request-level case study"
git -c http.proxy=http://127.0.0.1:17890 push origin exp/request-case-study
```

This stores selected request and all candidate-node decision records,
decomposition tables, parameters and SHA-256 hashes of raw traces.
Files that are too large stay on the server.

**Interpretation warning:** two policies diverge into different runtime states
after the first differing placement. Later candidate estimates cannot be
interpreted as paired counterfactual outcomes at an identical cache/queue
state. Establishing a causal mechanism requires controlled cache-admission,
pinning or network penalty ablations, with held-out seeds.

## Scheduler × Cache Admission Ablation

The archived request-level case study suggests that high network traffic and
queueing—not GPU prefill alone—accompany the severe P95 regression. This
branch tests a **controlled admission ablation**, not a new optimized policy.

Each trace and initial cache is reused across four runs:
`greedy/always`, `greedy/no_remote`,
`bandwidth_aware/always`, `bandwidth_aware/no_remote`.
The `no_remote` admission policy avoids caching a completed request's
blocks whenever remote KV blocks were fetched. It does **not** pin any hot
prefix, and the result is not automatically a better scheduler.

Original archive P95 is checked in the two selected case/seed combinations,
so a changed model or workload is detected.

### Run on 4090-02

```bash
cd /state/partition/fyzhou/distributed-kv-sim
git -c http.proxy=http://127.0.0.1:17890 fetch origin
git switch --track origin/exp/cache-admission-ablation
python3 -m unittest discover -s tests -v
python3 scripts/cache_admission_ablation.py \
  --seeds 41 42 43 44 45 \
  --archive experiments/cache-network-diagnostics-20261117 \
  --out /state/partition/fyzhou/results/distributed-kv-sim/cache-admission-ablation
```

Expected outputs: `runs.csv`, `pairs.csv`, `config.json`.
Both selected scenario strata are reused; the seed variation is reported
rather than cherry-picking the original extreme seed.

### Archive (under 1 MiB)

```bash
python3 scripts/archive_cache_admission.py \
  --source /state/partition/fyzhou/results/distributed-kv-sim/cache-admission-ablation \
  --destination experiments/cache-admission-ablation-20261118
git add experiments/cache-admission-ablation-20261118
git commit -m "Archive scheduler cache admission ablation"
git -c http.proxy=http://127.0.0.1:17890 push origin exp/cache-admission-ablation
```

The source and archive scripts require only Python's standard library.
The model remains synthetic; measured GPU/NIC calibration is pending.

## Continuous research checkpoint (2026-11-18)

Branch `research/iterative-scheduler` completes three initial research
iterations: correctness fixes, controlled admission/regression diagnostics,
and an explicit recompute/local/remote service-cost candidate. See the
[research log](docs/RESEARCH_LOG.md) for commit provenance and next experiments.

- [Correctness replay](experiments/research-correctness-20261118/): two bugs
  fixed; all 40 historical scheduler/admission outcomes remain identical.
- [Mechanism evidence](experiments/research-mechanisms-20261118/ANALYSIS.md):
  remote admission really changes cache contents, but oversized sequential
  admission destroys reusable prefix roots. Source pinning can preserve an
  expensive remote-fetch cycle.
- [Held-out evaluation](experiments/research-cost-evaluation-20261118/ANALYSIS.md):
  980 runs plus 980 deterministic replays. Cost-aware improves over Greedy
  in 140/140 paired conditions, but fails to consistently beat an
  action-matched Round Robin control. This is a service-cost improvement
  under illustrative compute, not evidence of a superior placement scheduler.
- [Physical H2D measurements](experiments/research-h2d-verified-20261118/):
  bounded single-host copies; CPU socket affinity matters. Distinct from
  layered KV loading and inter-node NIC performance; defaults stay unchanged.
- [Externality microcase](experiments/research-externality-replay-20261118/):
  finite-flow replay exposes the current approximation's size-blindness.

The optional policies `cost_aware` and `round_robin_cost` use a separate
reuse-action planner. All five legacy placement/service behaviors remain
available. Try them with the existing CLI:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/run.py --requests 120 --policy greedy bandwidth_aware cost_aware round_robin_cost --out /state/partition/fyzhou/results/distributed-kv-sim/new-smoke
```

Reproduce research runs into **fresh** output/archive directories:

```bash
python3 scripts/research_mechanisms.py --out /state/partition/fyzhou/results/distributed-kv-sim/new-mechanisms --archive experiments/new-mechanisms
python3 scripts/research_evaluate.py --split development --out /state/partition/fyzhou/results/distributed-kv-sim/new-development --archive experiments/new-development
python3 scripts/research_evaluate.py --split evaluation --out /state/partition/fyzhou/results/distributed-kv-sim/new-evaluation --archive experiments/new-evaluation
python3 scripts/research_evaluate.py --split evaluation --out /state/partition/fyzhou/results/distributed-kv-sim/new-replay --verify-against /state/partition/fyzhou/results/distributed-kv-sim/new-evaluation/runs.csv
```

Evaluation seeds 101–105 are now consumed evidence: future methods must use
fresh holdouts. Simulation/research runners use Python 3.8 and the standard
library. Optional plotting uses matplotlib; the bounded physical H2D script
requires an existing CUDA-enabled torch environment and an idle GPU.

## Shadow queue/cache placement (2026-11-24)

Branch `research/shadow-rollout` continues the placement experiment under the
**same execution service actions** as Cost-Aware and Round-Robin-Cost:

- `shadow_cache` rolls known FIFO completions into a private target cache.
- `shadow_bounded` limits cache updates to eight completions while still
  charging all remaining queued work.
- `shadow_frozen` is the frozen-cache forecast ablation.

No future arrivals are read. Other-node caches are frozen, future transfer
rates are isolated estimates, and existing outgoing pins are retained in
forecasts. All real requests replan with the unchanged `minimum_service`.

On fresh seeds 201–205, 14 declared shapes × two admissions × ten policies
produce 1,400 held-out runs. Versus action-matched Round Robin, full rollout
has 24 better / 116 neutral / 0 worse P95 conditions, mean paired +1.884%;
bounded rollout has 20 / 116 / 4, mean +1.763%. Full rollout still loses in
two conditions against Cost-Aware. These are conditional synthetic findings.

Fair CPU timing includes placement **and** execution replanning: full rollout
averages 7.03 ms/request, bounded 4.67 ms, Cost-Aware 1.27 ms and RR-Cost
0.071 ms on the shared host. Those CPU costs are not charged to simulated
TTFT. There is no demonstrated net serving speedup including scheduler CPU
saturation, nor physical validation of the model's zero-time CPU KV admission.

See [complete results and figures](experiments/research-shadow-evaluation-20261124/ANALYSIS.md),
[model limits](experiments/research-shadow-evaluation-20261124/LIMITATIONS.md),
[development diagnostics](experiments/research-shadow-diagnostics-20261124/),
and the [research log](docs/RESEARCH_LOG.md) for exact provenance and resume plans.
Run into fresh directories (Python 3.8 standard library):

```bash
python3 -m unittest discover -s tests -v
python3 scripts/research_shadow.py --split development --out /state/partition/fyzhou/results/distributed-kv-sim/new-shadow-development --archive experiments/new-shadow-development
python3 scripts/research_shadow.py --split evaluation --out /state/partition/fyzhou/results/distributed-kv-sim/new-shadow-evaluation --archive experiments/new-shadow-evaluation
python3 scripts/research_shadow.py --split evaluation --out /state/partition/fyzhou/results/distributed-kv-sim/new-shadow-replay --verify-against /state/partition/fyzhou/results/distributed-kv-sim/new-shadow-evaluation/runs.csv
```

Seeds 201–205 are now consumed evaluation data. Reserve fresh seeds 301–305
for the next method; do not re-label 101–105 or 201–205 as untouched holdouts.
