# Continuous simulator research

All latency/traffic results below are synthetic model outputs. No physical
multi-node NIC or GPU performance validation is claimed.

## Iteration 1 — correctness audit (2026-11-18)

Question: are pathological traffic and admission equivalence artifacts of an
incorrect event/network model? Starting verified commit: `41d2f12` (32 tests).
Branch: `research/iterative-scheduler`; pre-existing `.gitignore` edit retained
outside research commits. All historical archives remain unchanged.

Confirmed bugs, separated from algorithm changes:

- Candidate transfer estimates divided both NICs by the union of send/receive
  competitors. Two disjoint incumbent flows plus a bridging candidate provide
  a counterexample (predicted 1/3 NIC vs actual 1/2 NIC). Use independent
  endpoint counts matching the existing fluid allocator.
- Flow completion tolerance scaled with all historical traffic; after 1 TB,
  an entirely unfinished 1000-byte flow could be accepted. Scale tolerance
  to that flow's initial size with a small absolute float allowance.

No scheduling algorithm or cache admission semantics changed in this iteration.
Six focused tests added; 38 tests pass. The network model explicitly uses
independent send/receive budgets (full duplex) and conservative equal shares;
unused bandwidth redistribution is not implemented.

Audit coverage and limits:

1. Events use `(time, insertion sequence)`; arrivals inserted first take
   precedence over completions at the same time. Deterministic, no zero-time
   cache visibility guarantee. Stale flow events use a global generation.
2. Remaining compute uses scheduled finish; transfer remaining uses current
   rate and includes setup. Queued work uses present caches and unshared
   transfer rates: an approximation, not an exact FIFO forecast.
3. Explicit setup precedes flow creation; pins cover setup and transfer.
   A node processes one entire request at a time. Endpoint/fabric conservation
   and equal-time flow completions covered by closed-form tests.
4. CPU cache is separate from active GPU KV; local blocks need no CPU pin
   after snapshot because active KV is abstracted. Remote blocks are pinned
   by reference counts, then unpinned at transfer completion. Remote reads
   do not update source LRU. Partial physical prefix residency is allowed;
   only contiguous prefix from root is reusable.
5. Oversized sequential LRU admission can remove a request's own prefix root.
   This is current cache policy behavior, not silently corrected here.
6. Externality assumes adding a transfer immediately and keeping all current
   rates for each incumbent's remaining lifetime. It ignores candidate size,
   queued start time, setup, future flows and early completions. It is an
   approximation and can overestimate finite-flow damage; not a physical
   marginal-cost measurement.
7. TTFT decomposes into queue + setup/transfer + H2D + compute. Token/byte
   conservation, final empty flows/queues/active jobs/pins tested.
8. Execution rechecks caches; decision-time hits and sources are forecasts.
   Arrival bursts/family draws use local seeded RNG. Percentiles interpolate
   at `(n-1)*p`; seed/config comparisons are correlated. Mean paired percent
   change differs from percent change of aggregate means.

Initial exact replay (seed 42, NIC=Fabric=4, capacity=256, burst=1):
Greedy P95 2204.5363852 ms, 3 remote requests, 1.3125 GiB;
Aware P95 6456.1511127 ms, 69 remote requests, 30.1875 GiB.
Both admissions produce these exact figures. Aware source has zero evictions
and 532 rejected block admissions; Greedy source has 21584 evictions and
36 rejections. This motivates instrumentation, not a causal claim alone.

Next: repeat 40 archived scheduler/admission runs before/after fixes, preserve
all changed outcomes, then trace actual admission effects and run controlled
capacity/pinning/transfer-cost interventions.

Iteration 1 result: all 40 original scheduler × admission × stratum × seed
runs replay exactly before and after the fixes; every retained per-policy
metric is identical. The archived failures are not explained by these two
bugs. Evidence: `experiments/research-correctness-20261118/`; raw before/after
CSVs under `research-20261118-audit-{baseline,corrected}` in the results root.
The old and corrected code ran in independent worktrees. The endpoint-count
counterexample remains covered by a focused test even though these two strata
do not exercise a behavior-changing instance.

## Iteration 2 — admission equivalence and controlled mechanisms

Hypothesis: oversized LRU admission makes remote destinations physically
retain blocks but lose the root, so always/no_remote can differ in contents
without differing in reusable prefix or execution outcomes. Persistent remote
pins can protect the original source; the externality heuristic may indirectly
keep assigning work to that expensive-but-hit-rich node.

Implementation: an observation-only simulator subclass records actual cache
admit return values, rejected blocks, before/after ordered cache fingerprints,
physical vs contiguous hot residency, pin reference counts, every candidate
score, queue histories, start-time plans and prediction residuals. An invariant
test checks identical instrumented and plain simulator rows, caches and summary.
No baseline cache behavior changes. 39 tests pass.

Protocol: seeds 41–45 (exploratory); 240 requests, 4 nodes, 256 shared + 4
suffix blocks, burst=1, NIC=Fabric=4 GiB/s. Paired admissions for both
schedulers, then separate interventions: capacity=260, permanent source hot
pinning, disallow remote reuse, NIC=16 only, Fabric=16 only, and force only
the first differing placement to the Greedy choice. Complete traces for seed
42, summary of every seed/control. These are mechanism interventions, not
candidate performance estimates. The pin intervention is removed after run
and checked for balanced transient reference counts.

Iteration 2 result: 80 runs completed. Seed 42 actual admission attempts differ
(17940 inserted remote-request blocks for Aware/always vs zero/no_remote), but
each remote destination finishes with zero contiguous request prefix. All
five seeds have identical execution outcomes across admissions, while some
cache contents differ. All 133 Aware source completions see the hot prefix
fully pinned, yielding 532 rejected suffix insertions and no source eviction.
Greedy's first unpinned completion at 355.060 ms evicts the first four hot
blocks. Exactly 69 × 0.4375 GiB explains Aware's entire 30.1875 GiB traffic.

Falsification: source protection alone makes Greedy P95 6697.88 ms (was
2204.54); disabling remote reuse gives both policies 1271.63 ms; force only
the first divergence gives Aware 3483.40 ms (was 6456.15). Capacity 260 hurts
Greedy (5239.01 ms); cache capacity benefits are not monotone under mandatory
reuse. NIC-only/Fabric-only increases are inconclusive because the unchanged
4 GiB/s constraint remains. Full findings and negative interventions:
`experiments/research-mechanisms-20261118/ANALYSIS.md`.

Modeled service costs expose the next hypothesis: for a 4160-token request,
cold compute 30.059 ms < local reuse 35.506 ms < isolated remote 145.031 ms.
This is a model observation, not hardware calibration. Greedy decision-time
TTFT bias −499.03 ms vs Aware +730.42 ms illustrates queue/cache uncertainty.

## Iteration 3 — explicit recompute/local/remote cost choice

Hypothesis: avoiding remote or local reuse when its full network + H2D +
prefill cost exceeds recomputation removes a central failure mode without
requiring a globally tuned externality weight.

Candidate interface: optional reuse planners, separate from placement;
`cost_aware` uses Greedy node placement with a minimum-service planner;
`round_robin_cost` uses exactly that planner with Round Robin placement.
Legacy policies still use longest-prefix reuse. The planner compares cold
recompute, maximum local reuse and each remote source's maximum prefix,
including endpoint/fabric sharing and setup/H2D/compute. It replans at job
start from actual cache state. Queued forecasts use isolated link rates,
matching the previous estimator's assumption. No oracle cache lookahead.
Comparing per-source endpoints is justified only under the present compute
curve (service between hit endpoints is concave); it is not a claim of optimal
partial reuse for arbitrary fitted models.

This changes both the service action space and node placement: gains over
legacy policies cannot be attributed to placement alone. Action-matched Round
Robin exposes this distinction. No new admission policy is introduced.

Protocol frozen in `scripts/research_evaluate.py`: development seeds 11/12,
evaluation 101–105; 14 declared scenarios × two admissions × seven policies,
120 requests each. Includes capacities 256/260/512/1024, 2/4/8 nodes,
asymmetric NIC/Fabric, burst/arrival rate, prefix lengths 64/256/512,
85%/50% popularity, flat/skewed Zipf traces, and 8× compute cost. Development
runs precede evaluation; no lambda or other parameter selection. Aggregate
uncertainty resamples entire seed clusters, preserving all correlated
scenario/admission outcomes; five clusters are too few for strong inference.
Per-request outcomes and final cache hashes must match independent replay;
wall-clock decision time is intentionally excluded from deterministic replay.
47 unit/integration tests pass before the first development run.

Auxiliary calibration preparation: existing lab fit provenance verified from
`cpu-kv-20261105-detailed/{configuration.json,figures/fits.json}`. The imported
1.684 ms + 71.210 ms/GiB coefficients match the lab's H2D CUDA-event fit
(54 cells, payload range 0.02734375–4.270751953125 GiB), measured on a
contended RTX4090 with pinned CPU buffers. Its separate prefill-service fit
includes CPU loading/scatter and host costs, so importing those coefficients
as pure GPU compute or subtracting medians would be unsound. Defaults stay
unchanged. No pure GPU-prefill calibration is claimed.

A bounded contiguous H2D microbenchmark is prepared for idle GPU 0; GPU 1 is
occupied and will not be used. Hard limits: 25 seconds after initialization,
128 MiB GPU tensors and 128 MiB pinned host tensors (plus CUDA context),
15 samples + 3 warmups for four sizes and one/two streams. Busy GPU detection
aborts, and readback validates copies. This measures a different transfer
layout than 28-layer KV loading and cannot directly replace that fit.
