"""Local FIFO/cache forecasts using only arrived, assigned work.

Other nodes' cache contents stay frozen. Queued transfers use isolated link
rates; active work uses the engine's remaining-work estimate. Existing outgoing
pins are conservatively retained throughout the local forecast. This is a
local rollout approximation, not a cluster event replay or oracle.
"""
from collections import OrderedDict
import math
from ..predictor import ComputePredictor, H2DPredictor, TransferPredictor
from .shadow_network import active_remaining
from ..cache import BlockCache, admitted_blocks
from .reuse import minimum_service

POLICIES = ('shadow_frozen', 'shadow_cache', 'shadow_bounded',
            'shadow_fast', 'shadow_network', 'shadow_fast_lru', 'shadow_cluster',
            'shadow_cluster_pruned', 'shadow_cluster_fast', 'shadow_cluster_lru',
            'shadow_cluster_indexed', 'shadow_cluster_gated', 'shadow_cluster_finite', 'shadow_cluster_events',
            'shadow_cold_queue')


class _LocalView:
    def __init__(self, sim, target, cache):
        self.sim, self.target, self.cache = sim, target, cache

    def __getattr__(self, name):
        return getattr(self.sim, name)

    def _hit(self, req, node_id):
        if node_id == self.target:
            return self.cache.prefix_len(req.hashes)
        return self.sim._hit(req, node_id)


def _service(view, req, target, isolated):
    local, source, hit = minimum_service(view, req, target, isolated=isolated)
    remote = hit-local if source is not None else 0
    payload = remote*view.cfg.block_size*view.cfg.kv_bytes_per_token
    network_ms = 0.
    if remote:
        rate = (min(view.network.nic[source], view.network.nic[target], view.network.fabric)
                if isolated else view.network.prospective_rate(source, target))
        network_ms = view.transfer_predictor.predict_ms(payload, rate)
    h2d_ms, compute_ms = view._h2d_time(hit), view._compute_time(req, hit)
    return dict(node=target, local_blocks=local, remote_blocks=remote,
                total_hit_blocks=hit, source=source, payload_bytes=payload,
                competing_flows=0 if isolated else sum(
                    f.src == source or f.dst == target for f in view.network.flows.values()) if remote else 0,
                active_network_flows=len(view.network.flows),
                network_ms=network_ms, h2d_ms=h2d_ms, compute_ms=compute_ms,
                service_ms=network_ms+h2d_ms+compute_ms)


def complete_cache(cache, request, remote, local, admission):
    """Exact ordered contents for a private forecast; discard unused counters.

    With no pins, sequential admission's final LRU order is untouched old
    keys followed by the admitted keys in last-touch order, capped at capacity.
    Every consumed local key is included in full-request admission, so its
    preceding touch cannot change this final order. Pinned cases and empty
    admissions use the original implementation. This never touches live caches.
    """
    keys = admitted_blocks(request.hashes, remote, admission, capacity=cache.capacity)
    if cache.pins or not keys:
        cache.touch(request.hashes[:local])
        cache.admit(keys)
        return
    unique = list(dict.fromkeys(reversed(keys)))[::-1]
    incoming = set(unique)
    order = [key for key in cache.blocks if key not in incoming] + unique
    cache.blocks = OrderedDict.fromkeys(order[-cache.capacity:])


def complete_unpinned_lru(cache, request, remote, local, admission):
    """Update the existing ordered dictionary with constant-time victims."""
    keys = admitted_blocks(request.hashes, remote, admission, capacity=cache.capacity)
    if cache.pins or not keys:
        complete_cache(cache, request, remote, local, admission)
        return
    blocks = cache.blocks
    for key in keys:
        if key in blocks:
            blocks.move_to_end(key)
        else:
            if len(blocks) == cache.capacity:
                blocks.popitem(last=False)
            blocks[key] = None


def _cold_cost(sim, req):
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


def _cold_forecast(sim, req, node, horizon, waiting, memo):
    def cost(request):
        key = (request.length_tokens, len(request.hashes))
        if key not in memo:
            memo[key] = _cold_cost(sim, request)
        return memo[key]

    compute = cost(req)
    if compute is None:
        return None
    for pending in node.queue:
        service = cost(pending)
        if service is None:
            return None
        waiting += service
    jobs = len(node.queue) + int(node.active is not None)
    return dict(node=node.spec.node_id, local_blocks=0, remote_blocks=0,
                total_hit_blocks=0, source=None, payload_bytes=0,
                competing_flows=0, active_network_flows=len(sim.network.flows),
                network_ms=0., h2d_ms=0., compute_ms=compute, queue_ms=waiting,
                estimated_ms=waiting+compute, rollout_jobs=jobs,
                cache_updates=jobs if horizon is None else min(jobs, horizon))


def forecast(sim, req, node_id, horizon=None, *, cold_memo=None, remaining=None,
             cache_update=complete_cache):
    """Predict completion after known FIFO work; never mutate live state.

    horizon limits cache updates, not accounted queue work. Tail jobs are
    still charged service on the last forecast cache, with no further updates.
    None means full local FIFO; zero is the frozen-cache ablation.
    """
    node = sim.nodes[node_id]
    waiting = sim._remaining_active_ms(node) if remaining is None else remaining
    if cold_memo is not None:
        prediction = _cold_forecast(sim, req, node, horizon, waiting, cold_memo)
        if prediction is not None:
            return prediction
    cache = BlockCache(node.cache.capacity, node.cache.admission)
    cache.blocks = node.cache.blocks.copy()
    cache.pins = node.cache.pins.copy()
    view = _LocalView(sim, node_id, cache)
    updates = 0

    def complete(request, remote, local):
        nonlocal updates
        if horizon is None or updates < horizon:
            cache_update(cache, request, remote, local, sim.cfg.admission)
            updates += 1

    if node.active is not None:
        active = node.active
        complete(active.req, active.remote_blocks, active.local_hit)
    for pending in node.queue:
        plan = _service(view, pending, node_id, isolated=True)
        waiting += plan['service_ms']
        complete(pending, plan['remote_blocks'], plan['local_blocks'])
    plan = _service(view, req, node_id, isolated=waiting > 0.)
    plan.update(queue_ms=waiting, estimated_ms=waiting+plan.pop('service_ms'),
                rollout_jobs=len(node.queue)+int(node.active is not None),
                cache_updates=updates)
    return plan


def horizon(sim):
    if sim.cfg.policy == 'shadow_frozen':
        return 0
    if sim.cfg.policy == 'shadow_bounded':
        return sim.cfg.shadow_cache_updates
    return None


def select(sim, req):
    predictions = forecasts(sim, req, sim.nodes)
    # Exposed for observation after select; never used across later decisions.
    sim.shadow_last_forecast = (req.request_id, predictions)
    return min(predictions, key=lambda i: (predictions[i]['estimated_ms'], i))


def cold_queue_forecasts(sim, req, nodes):
    """Charge queued work as cold prefill, price only the incoming service."""
    cold_costs = {}
    predictions = {}
    for node_id in nodes:
        node = sim.nodes[node_id]
        waiting = sim._remaining_active_ms(node)
        for pending in node.queue:
            length = pending.length_tokens
            if length not in cold_costs:
                cold_costs[length] = sim._compute_time(pending, 0)
            waiting += cold_costs[length]
        plan = _service(sim, req, node_id, isolated=waiting > 0.)
        plan.update(queue_ms=waiting, estimated_ms=waiting+plan.pop('service_ms'),
                    rollout_jobs=len(node.queue)+int(node.active is not None),
                    cache_updates=0)
        predictions[node_id] = plan
    return predictions


def forecasts(sim, req, nodes):
    policy = sim.cfg.policy
    if policy == 'shadow_cold_queue':
        return cold_queue_forecasts(sim, req, nodes)
    if policy.startswith('shadow_cluster'):
        from .shadow_cluster import (_ForecastCache, _ForecastGatedCache, _ForecastIndexedCache,
                                     cold_forecasts, forecasts as cluster_forecasts)
        if policy in ('shadow_cluster_fast', 'shadow_cluster_lru', 'shadow_cluster_indexed',
                      'shadow_cluster_gated', 'shadow_cluster_finite', 'shadow_cluster_events'):
            cold = cold_forecasts(sim, req, nodes)
            if cold is not None:
                return cold
        prune = policy != 'shadow_cluster' and all(
            coefficient >= 0 for coefficient in (
                sim.cfg.gpu_fixed_ms, sim.cfg.gpu_ms_per_new_token,
                sim.cfg.gpu_ms_per_attention_pair, sim.cfg.h2d_fixed_ms,
                sim.cfg.h2d_ms_per_gib, sim.cfg.remote_setup_ms))
        cache_type = {'shadow_cluster_lru': _ForecastCache,
                      'shadow_cluster_indexed': _ForecastIndexedCache,
                      'shadow_cluster_gated': _ForecastGatedCache,
                      'shadow_cluster_finite': _ForecastCache,
                      'shadow_cluster_events': _ForecastCache}.get(policy, BlockCache)
        return cluster_forecasts(sim, req, nodes, prune=prune, cache_type=cache_type,
                                 finite_service=policy in ('shadow_cluster_finite', 'shadow_cluster_events'))
    memo = {} if policy in ('shadow_fast', 'shadow_network', 'shadow_fast_lru') else None
    update = complete_unpinned_lru if policy == 'shadow_fast_lru' else complete_cache
    remaining = active_remaining(sim) if policy == 'shadow_network' else {}
    return {node: forecast(sim, req, node, horizon(sim), cold_memo=memo,
                           remaining=remaining.get(node), cache_update=update)
            for node in nodes}
