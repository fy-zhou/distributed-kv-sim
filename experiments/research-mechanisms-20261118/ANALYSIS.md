# What admission equivalence does and does not mean

80 synthetic runs, five exploratory seeds (41–45), unchanged corrected model
`192c94a`, mechanism implementation `1a7f6af`. No hardware inference.

For seed 42, actual completed-request rows and all candidate decisions are
identical between admissions under each scheduler. Caches are not always
identical: Bandwidth-Aware ends with a different node-2 cache. Every remote
completion has a different post-admission cache fingerprint (69 Aware, 3
Greedy). `always` inserts 17940/780 blocks respectively; `no_remote` inserts
zero. Every such completion ends with zero contiguous request-prefix blocks.
The 260-block sequential admission into 256 slots evicts the prefix root;
physical suffix occupancy does not imply reusable prefix occupancy. No
remote request count or admission-policy dispatch bug explains equivalence.
All five seeds have exactly equal request outcomes across admissions.

The first placement divergence is hot-000009 at 18 ms for seed 42. Runtime
states are identical before this choice. Greedy chooses node 3 at estimated
254.406 ms versus node 0 at 266.048 ms. Aware adds 99.525 ms externality to
node 3 and chooses node 0. Later score comparisons are on different states.

All 133 Aware source completions occur with all 256 hot blocks pinned. The
four suffix insertions are rejected each time (532 rejections, zero source
evictions). Remote destinations never create a reusable hot replica, and
69 requests each transfer 0.4375 GiB from that source: 30.1875 GiB total.
Greedy's first unpinned source completion, hot-000012 at 355.060 ms, admits
four suffix blocks and evicts the first four hot blocks. Its remote reuse
then collapses, permitting cheaper recomputation in the current model.

For the 4160-token request, modeled cold prefill is 30.059 ms; local hot
reuse costs 35.506 ms including H2D; isolated remote reuse over 4 GiB/s costs
145.031 ms. Longest-prefix reuse is therefore an expensive mandatory action
in the current baselines. This conclusion depends strongly on the illustrative
compute coefficients, not a demonstrated GPU property.

## Controlled changes (seed 42)

| Intervention | Greedy P95 ms | Aware P95 ms | Greedy / Aware GiB |
|---|---:|---:|---:|
| Reference | 2204.54 | 6456.15 | 1.3125 / 30.1875 |
| Capacity 260 | 5239.01 | 4083.02 | 21.875 / 18.8125 |
| Permanently protect source | 6697.88 | 6456.15 | 31.0625 / 30.1875 |
| Disable remote reuse | 1271.63 | 1271.63 | 0 / 0 |
| NIC 16 only | 2204.54 | 6456.15 | 1.3125 / 30.1875 |
| Fabric 16 only | 2204.54 | 6456.15 | 1.3125 / 30.1875 |
| Force only first divergence to Greedy node | 2204.54 | 3483.40 | 1.3125 / 7.875 |

A larger cache or preserving a prefix is not necessarily beneficial under
mandatory reuse. Forced-first-placement is evidence of path dependence; it
does not identify a unique complete causal chain. NIC-only and Fabric-only
increases leave the other 4 GiB/s constraint unchanged, so their null result
cannot distinguish the dominant one. Independent asymmetric regimes are
required next; do not conclude that the network is irrelevant.

Across five seeds, mean P95 (Greedy / Aware) is 2431.51 / 3085.54 ms for
reference, 6956.25 / 6690.14 ms with source pinning, 1269.56 / 1269.56 ms
without remote reuse, and 2431.51 / 2495.18 ms with the forced placement.
These are descriptive means over selected strata, not population estimates.

Decision-time TTFT prediction bias (actual minus predicted) in seed 42 is
−499.03 ms Greedy, +730.42 ms Aware; queue bias is −409.39 / +762.21 ms.
Greedy has 54 requests whose actual remote-block count differs from the
selected decision forecast; Aware has zero. Current-cache queue estimation
cannot forecast the source eviction and resulting switch to recomputation.
This is a known forecasting approximation; execution correctly rechecks state.

Next: introduce a separate cost-aware action planner that compares full
recompute, local reuse and each remote source. Compare it and an action-matched
Round Robin ablation with all five legacy policies on predeclared held-out
seeds and network/cache/compute regimes. Preserve every regression.
