# Physical cost model audit

Existing RTX 4090 measurements with verified SHA-256 and verified KV injection. Cold prefill uses the no-injection vLLM service phase as a proxy. Injected prefill service includes loading, so it cannot isolate GPU compute. H2D and GPU paged-cache scatter are per-layer CUDA event sums. Worker load is a wall-clock phase that already includes these operations. Fit validation holds out entire lengths. The data come from one system and support sensitivity experiments within this sampled range.
