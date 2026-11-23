"""Local FIFO/cache forecasts using only arrived, assigned work.

Other nodes' cache contents stay frozen. Queued transfers use isolated link
rates; active work uses the engine's remaining-work estimate. Existing outgoing
pins are conservatively retained throughout the local forecast. This is a
local rollout approximation, not a cluster event replay or oracle.
"""
from collections import OrderedDict
from ..cache import BlockCache, admitted_blocks
from .reuse import minimum_service

POLICIES = ('shadow_frozen', 'shadow_cache', 'shadow_bounded')


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
    keys = admitted_blocks(request.hashes, remote, admission)
    if cache.pins or not keys:
        cache.touch(request.hashes[:local])
        cache.admit(keys)
        return
    unique = list(dict.fromkeys(reversed(keys)))[::-1]
    incoming = set(unique)
    order = [key for key in cache.blocks if key not in incoming] + unique
    cache.blocks = OrderedDict.fromkeys(order[-cache.capacity:])


def forecast(sim, req, node_id, horizon=None):
    """Predict completion after known FIFO work; never mutate live state.

    horizon limits cache updates, not accounted queue work. Tail jobs are
    still charged service on the last forecast cache, with no further updates.
    None means full local FIFO; zero is the frozen-cache ablation.
    """
    node = sim.nodes[node_id]
    cache = BlockCache(node.cache.capacity, node.cache.admission)
    cache.blocks = node.cache.blocks.copy()
    cache.pins = node.cache.pins.copy()
    view = _LocalView(sim, node_id, cache)
    waiting = sim._remaining_active_ms(node)
    updates = 0

    def complete(request, remote, local):
        nonlocal updates
        if horizon is None or updates < horizon:
            complete_cache(cache, request, remote, local, sim.cfg.admission)
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
    predictions = {i: forecast(sim, req, i, horizon(sim)) for i in sim.nodes}
    # Exposed for observation after select; never used across later decisions.
    sim.shadow_last_forecast = (req.request_id, predictions)
    return min(sim.nodes, key=lambda i: (predictions[i]['estimated_ms'], i))
