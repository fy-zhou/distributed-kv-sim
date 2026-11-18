# Cost-aware service and scheduling experiment

980 paired synthetic runs; split: evaluation; seeds: [101, 102, 103, 104, 105].
All baselines use unchanged mandatory longest-prefix reuse. Candidate expands the service action space.
Round-robin-cost uses the same action planner to isolate placement from service selection.
No lambda optimization. All scenarios declared in config before evaluation.
Whole-seed bootstrap preserves scenario correlation; only five evaluation clusters, so intervals are descriptive, conditional, and do not establish hardware generalization.
Request traces stay outside Git; outcomes/cache hashes permit exact replay. Decision runtime is host wall time, not simulated latency.

