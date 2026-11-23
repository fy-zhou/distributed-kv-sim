"""Optional service-action planning, separate from node placement policies.

Legacy policies always take the longest available prefix. Cost-aware policies
compare recomputation, local reuse and each remote source using the same
service predictors as execution. No future cache states are assumed known.
"""


import math

from ..predictor import ComputePredictor, H2DPredictor, TransferPredictor


def certified_cold_cost(sim, req):
    """Return a cache-independent cost only if reuse strictly loses."""
    compute, h2d, transfer = sim.compute_predictor, sim.h2d_predictor, sim.transfer_predictor
    if (type(compute) is not ComputePredictor or type(h2d) is not H2DPredictor
            or type(transfer) is not TransferPredictor):
        return None
    coefficients = (compute.fixed_ms, compute.ms_per_new_token,
                    compute.ms_per_causal_pair, h2d.fixed_ms,
                    h2d.ms_per_gib, h2d.kv_bytes_per_token, transfer.setup_ms)
    if not all(math.isfinite(x) and x >= 0 for x in coefficients):
        return None
    if sim.cfg.kv_bytes_per_token <= 0 or sim.cfg.block_size <= 0:
        return None
    cold = sim._compute_time(req, 0)
    if not req.hashes:
        return cold
    # For positive hits the cost curve is concave. Check both endpoints.
    full = len(req.hashes)
    ends = [sim._h2d_time(h) + sim._compute_time(req, h) for h in (1, full)]
    # Leave numerical near-ties to the unmodified planner, which prefers local reuse.
    margin = max(abs(cold), *map(abs, ends), 1.) * 1e-12
    if min(ends) > cold + margin:
        return cold
    return None


def minimum_service(sim, req, dest, isolated=False):
    local = sim._hit(req, dest)
    # Tuple semantics: consumed local blocks, source, total reused blocks.
    choices = [(0, None, 0)]
    if local:
        choices.append((local, None, local))
    for src in sim.nodes:
        if src == dest:
            continue
        hit = sim._hit(req, src)
        if hit > local:
            choices.append((local, src, hit))

    def score(choice):
        consumed_local, source, hit = choice
        remote_ms = 0.0
        if source is not None:
            payload = (hit-consumed_local) * sim.cfg.block_size * sim.cfg.kv_bytes_per_token
            rate = (min(sim.network.nic[source], sim.network.nic[dest], sim.network.fabric)
                    if isolated else sim.network.prospective_rate(source, dest))
            remote_ms = sim.transfer_predictor.predict_ms(payload, rate)
        service = remote_ms + sim._h2d_time(hit) + sim._compute_time(req, hit)
        # Stable ties: avoid transfers, then prefer existing local reuse.
        return service, source is not None, -hit, -1 if source is None else source

    return min(choices, key=score)


REUSE_PLANNERS = {'cost_aware': minimum_service,
                  'round_robin_cost': minimum_service,
                  'shadow_frozen': minimum_service,
                  'shadow_cache': minimum_service,
                  'shadow_bounded': minimum_service,
                  'shadow_fast': minimum_service,
                  'shadow_network': minimum_service,
                  'shadow_fast_lru': minimum_service,
                  'shadow_cluster': minimum_service,
                  'shadow_cluster_pruned': minimum_service,
                  'shadow_cluster_fast': minimum_service,
                  'shadow_cluster_lru': minimum_service,
                  'shadow_cluster_indexed': minimum_service,
                  'shadow_cluster_gated': minimum_service,
                  'shadow_cluster_finite': minimum_service,
                  'shadow_cluster_events': minimum_service,
                  'shadow_cold_queue': minimum_service}


def _execution_certificate(sim, req):
    cache = sim.reuse_certificates
    if cache is None:
        return certified_cold_cost(sim, req)
    compute, h2d, transfer = sim.compute_predictor, sim.h2d_predictor, sim.transfer_predictor
    if (type(compute) is not ComputePredictor or type(h2d) is not H2DPredictor
            or type(transfer) is not TransferPredictor):
        return None
    key = (compute, h2d, transfer, sim.cfg.block_size, sim.cfg.kv_bytes_per_token,
           req.length_tokens, len(req.hashes))
    if key not in cache:
        if len(cache) >= 4096:
            cache.clear()
        cache[key] = certified_cold_cost(sim, req)
    return cache[key]


def choose_reuse(sim, req, node_id, isolated=False):
    planner = REUSE_PLANNERS.get(sim.cfg.policy)
    if planner is None:
        return sim._best_source(req, node_id)
    if sim.cfg.certified_reuse_shortcut and _execution_certificate(sim, req) is not None:
        return 0, None, 0
    return planner(sim, req, node_id, isolated=isolated)
