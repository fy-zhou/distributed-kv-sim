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

Development completed: 392 runs (14 scenarios × 2 seeds × 2 admissions × 7
policies), archived at `experiments/research-cost-development-20261118/`.
Candidate vs Greedy: 56/56 P95 improvements, mean paired +72.25%.
But versus action-matched Round Robin: 7 improvements, 5 regressions, 44
neutral; mean −0.20%. The apparent large legacy-baseline gains therefore
mostly reflect service-action choice, not established placement superiority.
This failure to improve consistently over an action-matched baseline is
retained. Candidate/protocol remains unchanged before evaluation seeds are run.
Do not interpret bootstrap endpoints from two development seeds as reliable
confidence intervals.

Calibration validation caught a measurement-script resource bug: a Python
loop variable retained one previous 64 MiB device tensor, making peak tensor
allocation 192 MiB rather than the declared 128 MiB. Copy readback and timing
completed, but the first run does not meet its stated allocation bound. Keep
that raw run unchanged at `research-20261118-h2d`; explicitly release the loop
reference, add a measured peak-allocation assertion, and rerun in a fresh
`research-20261118-h2d-corrected` directory. This is a measurement-script fix,
not a change to simulator coefficients or to the frozen scheduling candidate.

Corrected microbenchmark passes measured 128 MiB tensor-peak bound. However,
64 MiB one-stream transfer changes from 2.627 ms in the over-bound pilot to
5.200 ms in the corrected run; both raw observations are retained. This large
between-process variability precludes a new default calibration. Topology
shows GPU 0 attached to NUMA 0, GPU 1 to NUMA 1; unrestricted CPU affinity
allows first-touch allocation on either socket. A falsifiable follow-up uses
identical corrected copies with CPU 16 (NUMA 0) vs CPU 80 (NUMA 1), in an
ABBA sequence (near, far, far, near), without touching the occupied GPU 1.
Record topology and affinity for every fresh process. This is an auxiliary
hardware experiment, independent of the frozen simulator evaluation.

Externality microcase: exact fluid counterfactuals isolate the finite-flow
assumption. A 1-byte candidate sharing a 100 byte/ms source with a 1000-byte
incumbent delays it by 0.01 ms; the current externality predicts 10 ms
(1000×). Candidate sizes 10/100 bytes give 100×/10× overestimates; at
1000/10000 bytes the approximation is exact in this two-flow topology.
This tests an acknowledged approximation, not an undisclosed model change.
The first probe ran before its runner was committed; retain the pilot and
append exact runner provenance plus independently committed-code replay.

H2D ABBA results: all four processes pass 128 MiB tensor peak, copy readback,
and time limits (2.97–3.32 seconds of bounded measurement each). CPU 16
(GPU-local NUMA 0) one-stream 64 MiB throughput is 22.41/22.28 GiB/s;
CPU 80 (NUMA 1) is 10.18/10.24 GiB/s. Far-socket two-stream aggregate
throughput varies 10.46/18.92 GiB/s. CPU affinity is controlled; actual pinned
page placement is not independently observed. Two replicates per affinity
and shared-host operation prevent strong generalization. All six attempts
(720 measurements, including the initial memory-bound failure) are archived.

Post-evaluation diagnostic-only correction: optional candidate traces must
compute externality for the selected reuse action, rather than the legacy
mandatory-longest-prefix action. A recompute action now reports zero network
externality even if a source prefix exists. Neither new policy uses that
penalty for placement; evaluation has trace_decisions=False. Thus simulated
outcomes and the frozen candidate are unaffected. A focused test guards the
trace consistency; 49 tests pass. Full evaluation replay remains required.

Hardware archive metadata check: the initial summary exporter inherited a
generic simulator `synthetic: true` wrapper despite correct physical labels
in every nested run. Preserve that initial archive with a correction note;
fix the exporter to label physical measurements explicitly and produce a
separate verified re-export. No raw observations or historical archives change.

Iteration 3 result: 980 held-out runs completed, then all 980 independently
replayed. Every deterministic field matches, including full per-request
outcome hashes and final ordered-cache hashes. Only decision_mean_us and
decision_max_us (actual host wall time) are excluded. Evaluation code commit
`b3e64c2`; replay code commit `d7fc0e3`. The later `2666704` trace-only fix
is outside the placement/execution paths exercised in these runs. Final
49-test suite and a seven-policy CLI smoke pass on the current tree.

Held-out candidate versus Greedy: 140 improve / 0 neutral / 0 regress;
mean paired P95 +72.174%. Whole-seed bootstrap endpoints +70.610 to +73.686%
are descriptive with five clusters. Versus action-matched Round Robin:
7 improve / 120 neutral / 13 regress, mean −0.119%, endpoints −0.672 to
+0.433%. Thus the evidence supports a service-cost gate under this model,
not a superior placement scheduler. Candidate overhead averages 1301.574 us
per placement across condition-level means on the shared CPU host; the
Round Robin timer excludes its later execution-time reuse planning.

Failure: compute_heavy / seed 102 / always has P95 2923.569 ms versus
2239.768 ms for action-matched Round Robin (30.53% worse), despite fewer
network bytes. Candidate assigns 72/120 requests to node 0 versus 30 each;
mean queue rises 1164.271→1378.499 ms, while H2D/compute means are equal.
Long-prefix seed 105 regresses 4.05% with zero network traffic on both
sides. Keep these failures; do not tune on evaluation seeds 101–105.

Evidence: `experiments/research-cost-evaluation-20261118/ANALYSIS.md`,
`compact_runs.csv`, `pairs.csv`, `by_scenario.csv`, `verification.json` and
`evaluation.svg`. The mechanism SVG is in the mechanism archive; raw PNG
review previews and matplotlib environment stay outside Git. All new archives
are below 1 MiB (largest approximately 0.81 MiB).

### Completed checkpoints

| Milestone | Implementation commit | Evidence commit / directory | Verification |
|---|---|---|---|
| Correctness | 192c94a | 0da4649 / research-correctness-20261118 | 38 tests, 40 before/after runs identical |
| Mechanisms | 1a7f6af | 976c6cc / research-mechanisms-20261118 | 39 tests, 80 controlled runs |
| Service-cost candidate | 6b38df8, compact protocol 21a7416 | b3e64c2 development; research-cost-evaluation-20261118 | 392 development + 980 evaluation + 980 replay runs |
| Diagnostic trace correction | 2666704 | documented above | final 49 tests + seven-policy CLI smoke |
| Auxiliary measurements | 92f832e, memory fix d7fc0e3, affinity 1157f38 | research-h2d-verified-20261118 | 720 samples; all failures retained |
| Externality probe | 70f6771 | research-externality-replay-20261118 | exact committed-code CSV replay |

### Resume plan and limits

The first three requested iterations are complete. This is a resumable
checkpoint, not a finished research-quality serving system. Next highest
priority: improve placement under the *same* service-action space. Falsify
whether a shadow FIFO/cache rollout removes compute-bound source overload;
compare against action-matched Round Robin on development seeds, then fresh
held-out seeds 201–205. Profile and bound queue-estimation CPU overhead.
A finite-flow externality replay is another isolated candidate, but must
handle setup/queued starts and must not be advertised as future-state truth.

Hardware next: pin CPU affinity, independently inspect pinned page locality,
repeat transfer concurrency and actual KV scatter, then isolate real GPU
prefill kernels across reused lengths. Existing lab service-time fits include
loading and cannot be relabeled compute. Do not measure an inter-node network
using the two GPUs of this host. None of those future tasks is claimed done.
Input validation for arbitrary NaN/Inf configs, duplicate request IDs and
adversarial external traces remains incomplete; the audited synthetic traces
are finite, deterministic and have unique IDs.

Full results root: `/state/partition/fyzhou/results/distributed-kv-sim/`.
All directories beginning `research-20261118-` are immutable run attempts;
replay and corrected pilots use separate directories. The detached baseline
worktree is retained at `research-20261118-baseline-code` for exact resumption.
No historical experiment archive was modified. The user's original
`.gitignore` edit remains uncommitted and unchanged. No GPUs/models/system
packages were installed; plotting dependencies exist only in a new results-side
virtual environment. GPU 1 was never used by these measurements.

## Iteration 4 — shadow FIFO/cache placement (2026-11-24)

Resume verified local/GitHub checkpoint: `5d05886`; 49 tests pass, no old
research process running. Work branch `research/shadow-rollout` preserves the
prior branch. The original `.gitignore` change remains outside commits.

Question: does forecasting replicas created by already queued requests remove
source overload when service actions are held identical? Hypothesis: summing
queued costs against today's cache overprices cold destinations that will
soon create their own reusable prefixes. A local FIFO/cache rollout should
reduce that prediction error and improve placement in compute-heavy and
long-prefix regimes, but may fail when other nodes or pins evolve.

Three predeclared placement candidates use the exact same minimum_service
execution planner as cost_aware and round_robin_cost:
- shadow_frozen: frozen-cache control, isolated rates for delayed service;
- shadow_cache: roll active completion and every known FIFO job into a private
  target cache, accounting for touches, LRU, capacity and admission;
- shadow_bounded: same, but stop cache updates after eight completions. Every
  tail job still contributes service time on the last forecast cache.

No future arrival events or trace generator state are read. Other-node caches
are frozen; queued transfers use isolated link rates; active work uses the
existing remaining-work predictor. Outgoing pins are conservatively retained
throughout the forecast. These limitations make this an approximate local
rollout, not an exact full-cluster or oracle simulation. Real execution always
replans with minimum_service from current live state. No admission, network,
compute or legacy-policy semantics changed.

Tests establish a single-node closed-form forecast matching actual completion,
cache-root loss after oversized admission, no_remote semantics, no future-event
peek, no mutation of live caches/pins/jobs/network/events, and full accounting
of tail jobs after the bounded horizon. Common instrumentation is verified to
preserve per-request outcomes and count exactly one execution plan/request.
57 tests pass before development runs.

Protocol frozen in scripts/research_shadow.py: development seeds 21/22;
held-out 201–205. Reuse the prior 14 scenario definitions, 120 requests,
two admissions, all seven old policies plus three new candidates. Report all
variants, using round_robin_cost as primary placement comparator and frozen
rollout as cache-evolution ablation. No evaluation-seed tuning or variant
selection. Time both actual placement and every execution-time service plan
with the same instrumentation for all policies; observer forecasts/residuals
and I/O are excluded. Do not add measured CPU overhead to simulated TTFT.
Raw per-request outcomes and forecasts are streamed to compressed JSONL
outside Git; compact summaries retain every condition and negative outcome.
Whole-seed bootstrap preserves correlated scenario/admission pairs.

Timing protocol refinement before held-out evaluation: execute policies in
stable SHA256 order keyed by scenario/seed/admission/policy, independent of
workload RNG, to reduce systematic fixed-order timing bias. Initial development
attempt uses the original fixed order and remains preserved. This changes
only host execution order, not simulated inputs, algorithms or parameters.
Shared-server wall timings remain descriptive, not isolated CPU benchmarks.
A separate 196-run gate uses consumed seed 101 only on the seven old policies
and compares every prior deterministic metric/hash to the November 18 archive;
new candidates are not selected or tuned using that historical seed.

Development pilot complete: 560 runs. Full rollout versus action-matched Round
Robin: 12 improve, 44 neutral, zero regress, mean paired P95 +2.283%; bounded
rollout +1.986% with the same counts. Both still regress in two conditions
against cost_aware, and frozen-cache rollout regresses six times against RR.
These development failures are retained. 196 consumed-seed legacy comparisons
all match every historical deterministic metric and outcome/cache hash.

Fair planning cost in the initial shared-host development attempt averages
83.00 us RR-cost, 1624.78 us cost-aware, 11145.87 us full shadow, 7574.19 us
bounded shadow. Accurate local prediction is too expensive in this form.
A development-only cProfile of long_prefix/seed21/full shadow identifies
private cache completion as the dominant cost (3.159/3.941 seconds profiled
placement time); admit/touch repeatedly manipulate hundreds of blocks and
make 921456 LRU-victim calls. Raw profile retained outside Git. Profiler time
is instrumented and not a normal timing benchmark.

Before held-out evaluation, attempt a semantics-preserving optimization:
for unpinned private forecast caches, batch the final LRU order of completed
request admission. Pinned caches retain the exact original path. No live
cache, admission or service decision changes. Discarded private eviction
counters are not reported or used by the planner. Prove final ordered-cache
contents against sequential admission, then replay every development case
against pilot full-precision results. Do not optimize against 201–205.

Controlled private-LRU timing (500 interleaved AB/BA pairs/case; cache-copy
setup excluded) preserves a negative optimization result: hot admission median
127.189 us sequential versus 180.344 us batch (batch slower), cold replacement
493.458 versus 193.384 us (2.55× ratio), oversized admission 68.150 versus
50.160 us (1.36×). These are CPU metadata operations, not GPU/cache hardware
measurements. Do not claim universal speedup; a future semantics-preserving
hybrid could retain sequential touches for mostly resident requests.

Same-state development probe (compute-heavy seed 21, always): first differing
placement is hot-000007 at 0.14 ms. Node 0 has four assigned jobs, nodes 1–3
one each. Frozen forecast scores source node 0 at 200.756 ms and node 1 at
299.328 ms because it prices another remote fetch. Full rollout accounts for
the already active node-1 request's eventual admission and predicts local reuse,
189.803 ms, selecting node 1. Both forecasts use exactly the same causal
runtime snapshot; no future requests are read. Actual execution outcomes with
the extra probe match an ordinary full-rollout run exactly. This identifies
a decision mechanism, not exact global timing: known setup transfers and later
contention are still approximated by the remaining-work/isolated-link model.

Private-LRU optimization gate complete: all 560 development runs replay
exactly against the unoptimized pilot in every non-wall-time field, including
queue/TTFT prediction errors, every request outcome hash and final cache hash.
No scheduling scores or outcomes changed. Full precision verification is in
research-20261124-shadow-development-optimized/verification.json. Both pilot
and optimized timing attempts are preserved; order/load differed, so their
end-to-end wall-time difference is descriptive, not a controlled speedup.
59 tests pass. The model/candidate/horizon are frozen before any 201–205 run.
Next: execute the 1400 held-out cases (14×5×2×10) and independent full replay.

Development-only cost-bound diagnostic: 24/28 scenario/seed cells (12 of 14
shapes) have cold recomputation strictly cheaper than even full local reuse
for every request. Exhaustive enumeration verifies 611022 positive block-hit
points in those requests. Under the present nonnegative causal-pair and H2D
fixed coefficients, positive-hit service is concave in hit length; if its two
endpoints are above cold cost, no interior hit can win. Remote transfer adds
nonnegative cost. Thus many private-cache mutations cannot influence predicted
service cost in this matrix. This explains wasted forecast work and suggests
a provably guarded cache-rollout bypass in a future implementation; queue work
must still be accounted, particularly for variable-length Zipf requests.
No such fast path was added to the frozen candidates or current evaluation.
The bound is specific to the current illustrative curve, not an empirical GPU
property. Diagnostic CSV and provenance are appended to the diagnostics archive.

Held-out evaluation complete (replay still in progress): 1400 runs, untouched
seeds 201–205. Full rollout vs action-matched RR: 24 improve / 116 neutral /
0 regress; mean paired P95 +1.8843%, seed-bootstrap descriptive endpoints
[+1.3410,+2.4870]%. Bounded rollout: 20/116/4, mean +1.7635%, endpoints
[+1.2449,+2.3804]%. Frozen-cache control: 7/116/17, mean −0.5462%.
All three variants are reported, not selected on these results.

Relative to cost_aware, full rollout still regresses in two cases (18 improve,
120 neutral), mean +1.6348%. Strongest negative case: compute_heavy seed201,
no_remote, P95 3271.876 ms vs cost_aware 2932.538 ms (−11.5715% improvement).
This is not universal dominance. Full-rollout source load is 61/120 (vs 70),
but network mean grows 36.481→45.598 ms and mean queue grows
1614.925→1666.035 ms while mean H2D and compute are equal. More balanced
request counts alone are insufficient. Full rollout has 21 remote-action
forecast mismatches (cost_aware 19); hot-000059 predicts remote reuse and
1668.983 ms TTFT but actually recomputes and finishes at 2446.059 ms.
This exposes forecast uncertainty under admission/network interactions;
isolated-link and frozen-other-cache/pin assumptions remain hypotheses for
controlled follow-up, not a uniquely proven cause.

Across seeds, compute-heavy/always mean P95 improves RR 3038.068→full
2044.042 ms; largest-node share changes cost_aware .598→full .298. Long-prefix
mean P95 improves RR 2105.349→2083.978 ms; full queue forecast MAE is near
floating-point zero, versus cost_aware 144.262 ms. In compute-heavy/no_remote,
full queue MAE (189.198 ms) is worse than cost_aware (175.471 ms), preserving
a failure of the prediction hypothesis in that regime.

Fair mean total planning time (placement + actual execution service planning)
is RR-cost 70.940 us, cost_aware 1271.055 us, full shadow 7025.943 us,
bounded shadow 4674.566 us. This improves diagnostic validity over the prior
placement-only comparison, but exposes substantial implementation overhead.
No serving speedup including scheduler CPU saturation is established; CPU
planning latency is not part of this event model's simulated TTFT.

All per-condition results, uncertainty, absolute metrics and per-candidate
failures are in experiments/research-shadow-evaluation-20261124/ANALYSIS.md.
SVG outcome/timing figures were rendered and PNG previews visually reviewed;
only small SVGs are committed. Full raw traces and previews stay outside Git.

Physical-model completeness note exposed by future-replica scheduling: actual
_on_job_done admits newly computed KV to persistent CPU cache via zero-time
metadata insertion. D2H/materialization and spill backpressure are not modeled.
The rollout matches this existing abstraction exactly; it is not proof that
physical replicas become available at those times. Do not silently add spill
cost after evaluation or relabel this as an engine bug. A separately calibrated
spill model and repeated common baselines are required for physical validation.
The evaluated candidates have not changed. Detailed limitations are appended
as LIMITATIONS.md in the new evaluation archive, preserving generated findings.

### Iteration 4 verified checkpoint

All 1400 held-out runs independently replayed successfully. Every non-wall-time
field matches, including prediction residuals, full per-request outcome hashes
and final ordered-cache hashes. Only placement mean/max, execution-planning
mean and combined planning mean wall times are excluded. Full-precision pairs
and summary CSVs are byte-identical between evaluation and replay. Evaluation
source commit: 6269517; replay source commit: 0460dd2. No candidate/model code
changed between them; only diagnostics, reporting and documentation were added.
Verification and exact source/result hashes are retained in the evaluation
archive's verification.json.

Final checks: 59 unit/integration tests pass, compileall and git diff --check
pass, ten-policy CLI smoke completes, 196 legacy cases match authentic old
results, all 560 development optimization cases match, and all 1400 held-out
cases match independent replay. Thirty raw-file hashes across the four new
archives were checked. All new archives remain below 1 MiB; the largest is
about 0.62 MiB. Every historical archive present at 5d05886 is unchanged.
Original .gitignore edit remains uncommitted. No GPU or model execution and
no package installation was performed in this iteration.

| Checkpoint | Commit / evidence |
|---|---|
| Optional shadow planners + frozen protocol | 3731624 |
| Unoptimized development evidence, including CPU-cost failure | 1e034f4 |
| Private-cache batch equivalence, exhaustive LRU tests | 0393a74 |
| 560-case development equivalence before evaluation | 6269517 / research-shadow-development-optimized-20261124 |
| 196-case old-policy gate and same-state first divergence | b3401cb / research-shadow-diagnostics-20261124 |
| Fresh held-out comparisons, costs, figures, negative cases | 777d857 / research-shadow-evaluation-20261124 |
| Physical/model limitations | c6814c4 |
| Full replay proof | research-shadow-evaluation-20261124/verification.json |

The next research question is now narrower than the November 18 checkpoint:
can we retain the demonstrated conditional placement gain without paying
~7 ms/request planner cost or worsening no_remote's network-sensitive tails?
The CPU boundary is unresolved, not hidden by the simulated TTFT result.

Resume in research/shadow-rollout. Prioritize a provably guarded cache-update
bypass for recompute-dominated requests and/or incremental queue/cache forecast
maintenance with explicit cache/pin version invalidation. Preserve full queued
work and test exact score/outcome equivalence before judging runtime. The
current concavity-derived bound is valid only for this predictor family;
future measured non-concave curves need their own certified lower bounds.

Separately falsify whether accounting for already known setup transfers and
active-flow completion reduces the no_remote failures. It must not read future
arrivals, change minimum_service execution actions, or pretend frozen remote
caches/pins are exact. Compare against RR-cost, cost_aware and this full-shadow
checkpoint. Use development seeds for changes, then fresh holdouts 301–305;
201–205 are consumed evaluation data. All future physical claims require GPU
prefill, actual KV scatter and computed-KV materialization/D2H calibration plus
repeated common baselines. No such calibration is claimed in this checkpoint.

Immutable raw attempt directories are under
/state/partition/fyzhou/results/distributed-kv-sim/research-20261124-*, including
both development timing attempts, baseline gate, profile, paired microbench,
first-divergence probe, service bounds, evaluation, replay and final validation.

## Iterations 5–7 in progress (2026-11-24)

Branch `research/rollout-efficiency`, based on `3191b6b`. Three paired
hypotheses are frozen in `b98a4ab`. A certified cold-service shortcut skips
private cache work when every queued and incoming request is cache-independent.
It still accumulates every FIFO service term in the original order. The new
network variant adds an isolated replay of current flows and known setup
starts when predicting active-job completion. Other cache and queued-flow
assumptions stay as in full Shadow. The third experiment adds a serial ingress
controller before arrivals, using only development-measured placement plus
execution-planning rates at 0.25× and 1×. This pools work into one controller
as a sensitivity proxy. It does not model production threading or overlap.

Development seeds 31–32 and held-out seeds 301–305 use the same 14 workload
shapes, two admissions, five action-matched policies and 120 requests.
Development completed 280 cases with exact cold-shortcut outcome, final
cache and residual metric parity. Mean total host planning was 6.930 ms
for full Shadow, 2.013 ms for the shortcut and 2.081 ms for network replay.
Replay changed no P95 outcomes in the 56 development conditions.
All no-ingress held-out cells enforce exact shortcut equivalence and an
independent process replays every deterministic field.

## Iteration 8 in progress — calibration and materialization sensitivity

Existing 4090 matrix and configuration SHA-256 verified against the prior lab
archive. All 1200 verified measurements in 60 cells satisfied byte accounting
and computed-token checks. Leave-one-length-out MAE for fitted cold vLLM
prefill service was 37.95 ms, versus 141.98 ms for the illustrative predictor
on the six cold medians. H2D and paged scatter fit errors were 2.11 and
0.32 ms. Worker load wall error was 2.87 ms. Worker wall already contains
copy and scatter. Injected prefill service contains more than GPU compute.
Source runner `8011e55`, archive `research-calibration-audit-20261124`.
Both GPUs were occupied at inspection, so this reanalysis required no new GPU work.

A separate sensitivity runner compares the illustrative model, fitted cold
proxy, fitted worker-load proxy, and two assumed serial spill rates of
16 and 32 GiB/s. These rates are scenario assumptions. Spill delays node
release and visibility of newly produced full blocks after the first token.
The forecast sees remaining spill for known active work, while future queued
spill is not charged. Prefill and spill timings stay distinct in raw rows.
Evaluation will use fresh seeds 401–405 after development 41–42, with
three action-matched policies and both existing admission policies.

### Rollout efficiency held-out checkpoint

The ordinary 301–305 evaluation completed 700 runs. For all 140 paired
conditions, the cold shortcut matches full Shadow in every deterministic
field including outcomes, prediction residual aggregates and final ordered
cache. Host planning averages 6.833 ms/request for full Shadow and 1.972 ms
for the shortcut, a 3.47× ratio. RR-cost averages 0.066 ms. The shortcut
preserves full Shadow's placement outcomes, including regressions on six
conditions relative to RR-cost. Average paired P95 improvement is 1.935%,
with 22 improvements and 112 ties.

Known-transfer replay averages 2.009 ms/request. Against the shortcut,
it changes six of 140 P95 outcomes, all improvements, with a mean paired
improvement of 0.366% and a five-seed descriptive bootstrap range of
[0.046%, 0.737%]. Mean queue MAE is 7.614 ms versus 9.884 ms for the
shortcut. Against RR-cost it averages +2.205% with 22/112/6 outcomes.
The result is conditional on the chosen synthetic scenarios.
Full independent replay and the paired CPU ingress sensitivity are running.

The serial-spill development matrix completed 180 runs. Across 12 paired
conditions, its Shadow vs RR-cost average P95 improvements were 6.07%
(illustrative), 9.97% (cold proxy), 7.31% (worker-load proxy), and 6.29% /
7.68% (16 / 32 GiB/s serial spill). This narrower workload matrix is not
comparable to the aggregate across the 14 efficiency shapes.
The 401–405 holdout and replay are running.

The incremental private LRU development check completed 48 cases with
exact deterministic equivalence. In AB/BA wall-time pairs, median speed
ratios for incremental versus previous implementation were 0.91× for the
cold-shortcut reference, 1.22× for compute-heavy, and 1.47× for long-prefix.
The 501–505 evaluation is running. The previous policies remain controls
in these experiments. A separate 601–605 stress matrix covers sender NIC,
fabric, cache/pin and replica pressure to test known-transfer replay.
A 701–705 follow-up evaluates a full cluster rollout of already assigned
jobs. It copies only internal flow/job events and captures each candidate's
ready-time state. The eventual incoming transfer uses instantaneous shares.
This candidate uses the same service action chooser and does not insert
future arrivals into the fork. Source `955d224`.

The bounded physical copy CLI now accepts --direction d2h. The first
implementation contained a syntax error, fixed and compiled in `9591079`.
Both devices subsequently triggered the busy guard before CUDA setup.
No new D2H timings were collected.
