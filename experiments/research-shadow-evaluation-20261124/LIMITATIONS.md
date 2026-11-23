# What this placement result does not establish

All policies share the unchanged illustrative compute and configured fluid
network. The new result isolates placement under the same execution-time
minimum_service action planner; it does not validate physical serving speed.

- The local rollout freezes other-node caches, uses isolated future transfer
  rates and conservatively retains existing outgoing pins. Known setup flows
  and subsequent queue/network/admission interactions are not jointly replayed.
- Under no_remote, complete rollout can worsen prediction and P95 compared to
  cost_aware (seed 201: 3271.876 vs 2932.538 ms, 11.57% regression). More
  balanced request counts do not imply lower workload-weighted tail latency.
- The existing engine admits completed requests to persistent CPU cache as an
  instantaneous metadata operation. Physical materialization of newly computed
  GPU KV into CPU storage, including D2H and any backpressure, is absent. The
  forecast correctly mirrors that abstraction; future-replica benefits may be
  optimistic for a physical implementation. No spill model was silently added.
- The reported CPU planner cost is measured separately. Scheduler CPU queueing,
  contention and latency are not charged to simulated TTFT. Full rollout's
  ~7.03 ms planning/request may itself be a bottleneck; no net serving speedup
  including this cost is established.
- Only five held-out seed clusters and 14 chosen synthetic shapes were tested.
  Descriptive seed-bootstrap endpoints do not imply broad or hardware-level
  significance. All three variants and their negative outcomes are retained.

Next measurements should distinguish GPU prefill, H2D, actual KV scatter,
computed-KV materialization/D2H and CPU planner execution. Future placement
methods must use fresh holdouts; seeds 201–205 are now consumed evidence.
