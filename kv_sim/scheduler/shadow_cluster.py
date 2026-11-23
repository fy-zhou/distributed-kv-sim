"""Forecast node readiness by simulating all previously assigned work."""
from dataclasses import replace
import copy
import heapq

from .shadow_network import known_flow_externality_ms, proposed_transfer_ms

from ..cache import BlockCache
from ..cluster.node import Node
from ..engine.events import (EVENT_FLOW_DONE, EVENT_FLOW_START, EVENT_JOB_DONE)


class _ForecastCache(BlockCache):
    """Use constant-time eviction when the fork has no pinned victims."""

    def admit(self, keys, *, remote=False):
        if self.pins or remote:
            return super().admit(keys, remote=remote)
        inserted = 0
        for key in keys:
            if key in self.blocks:
                self.blocks.move_to_end(key)
            else:
                if len(self.blocks) >= self.capacity:
                    self.blocks.popitem(last=False)
                    self.evictions += 1
                self.blocks[key] = None
                inserted += 1
        return inserted


class _ForecastIndexedCache(_ForecastCache):
    """Maintain the order of unpinned victims during one private admission."""

    def admit(self, keys, *, remote=False):
        if not self.pins or (remote and self.admission in ('no_remote', 'prefix_fit_no_remote')):
            return super().admit(keys, remote=remote)
        from collections import OrderedDict

        victims = OrderedDict.fromkeys(key for key in self.blocks if key not in self.pins)
        inserted = 0
        for key in keys:
            if key in self.blocks:
                self.blocks.move_to_end(key)
                if key in victims:
                    victims.move_to_end(key)
                continue
            if len(self.blocks) >= self.capacity:
                if not victims:
                    self.rejections += 1
                    continue
                victim, _ = victims.popitem(last=False)
                del self.blocks[victim]
                self.evictions += 1
            self.blocks[key] = None
            victims[key] = None
            inserted += 1
        return inserted


class _ForecastGatedCache(_ForecastIndexedCache):
    """Build an index only when repeated head scans look more expensive."""

    def admit(self, keys, *, remote=False):
        if not self.pins or (remote and self.admission in ('no_remote', 'prefix_fit_no_remote')):
            return _ForecastCache.admit(self, keys, remote=remote)
        keys = tuple(keys)
        if not keys:
            return 0
        leading_pins = 0
        for key in self.blocks:
            if key not in self.pins:
                break
            leading_pins += 1
        # Scale by incoming misses. The guard affects runtime only.
        if leading_pins and leading_pins * len(keys) > len(self.blocks):
            misses = sum(key not in self.blocks for key in keys)
            if leading_pins * misses > len(self.blocks) + len(keys):
                return super().admit(keys, remote=remote)
        return _ForecastCache.admit(self, keys, remote=remote)


def _fork(sim, *, cache_type=BlockCache):
    from ..engine.simulator import Simulator

    shadow = Simulator([node.spec for node in sim.nodes.values()],
                       replace(sim.cfg, trace_decisions=False))
    shadow.now = sim.now
    shadow.clock.advance(sim.now)
    shadow.sequence = sim.sequence
    shadow.epoch = sim.epoch
    shadow.network = copy.deepcopy(sim.network)
    for node_id, live in sim.nodes.items():
        cache = cache_type(live.cache.capacity, live.cache.admission)
        cache.blocks = live.cache.blocks.copy()
        cache.pins = live.cache.pins.copy()
        shadow.nodes[node_id] = Node(live.spec, cache, live.queue.copy(),
                                     replace(live.active) if live.active else None)
    # Copy only the simulator's internal event types. External arrivals never
    # dispatch or participate in the fork, even when their timestamp is now.
    if sim.service_events is None:
        shadow.events = [event for event in sim.events if event[2]
                         in (EVENT_FLOW_START, EVENT_FLOW_DONE, EVENT_JOB_DONE)]
    else:
        shadow.events = list(sim.service_events.values())
    # The fork drains its copied event queue directly.
    shadow.service_events = None
    heapq.heapify(shadow.events)
    return shadow


def cold_forecasts(sim, req, nodes):
    """Predict exact ready times without a fork if every queued job is cold."""
    from .reuse import certified_cold_cost

    if sim.network.flows or any(
            node.active and node.active.expected_finish_ms is None
            for node in sim.nodes.values()):
        return None
    memo = {}

    def cost(request):
        key = (request.length_tokens, len(request.hashes))
        if key not in memo:
            memo[key] = certified_cold_cost(sim, request)
        return memo[key]

    compute = cost(req)
    if compute is None:
        return None
    predictions = {}
    for node_id in nodes:
        node = sim.nodes[node_id]
        finish = sim.now
        if node.active:
            finish = node.active.expected_finish_ms
            if finish is None:
                return None
        for pending in node.queue:
            service = cost(pending)
            if service is None:
                return None
            # Mirror absolute timestamp accumulation and tie-breaking in _start_compute.
            finish += service
        waiting = finish - sim.now
        predictions[node_id] = dict(node=node_id, local_blocks=0, remote_blocks=0,
            total_hit_blocks=0, source=None, payload_bytes=0, competing_flows=0,
            active_network_flows=0, network_ms=0., h2d_ms=0., compute_ms=compute,
            queue_ms=waiting, estimated_ms=waiting+compute,
            rollout_jobs=len(node.queue)+int(node.active is not None))
    return predictions


def forecasts(sim, req, nodes, *, prune=False, cache_type=BlockCache, finite_service=False,
              compare_finite_routes=False, externality_weight=0.):
    """Score service on the forecast state when each candidate becomes idle.

    A single fork drains the already assigned queues. The new request is
    evaluated when a node becomes idle. Its service estimate uses bandwidth
    shares at that time and does not advance the fork with the new transfer.
    """
    from .shadow import _service

    needed = set(nodes)
    predictions = {}
    shadow = _fork(sim, cache_type=cache_type)

    def ready(node_id):
        if node_id not in needed:
            return
        node = shadow.nodes[node_id]
        if node.active is not None or node.queue:
            return
        plan = _service(shadow, req, node_id, isolated=False,
                        compare_finite_routes=compare_finite_routes)
        if finite_service and plan['remote_blocks']:
            plan['network_ms'] = proposed_transfer_ms(
                shadow, plan['source'], node_id, plan['payload_bytes'])
            plan['service_ms'] = plan['network_ms'] + plan['h2d_ms'] + plan['compute_ms']
        waiting = shadow.now - sim.now
        plan.update(queue_ms=waiting, estimated_ms=waiting+plan.pop('service_ms'),
                    rollout_jobs=len(sim.nodes[node_id].queue)+int(sim.nodes[node_id].active is not None))
        if externality_weight:
            extra_delay = 0.
            if plan['remote_blocks']:
                extra_delay = known_flow_externality_ms(
                    shadow, plan['source'], node_id, plan['payload_bytes'])
            plan['known_flow_externality_ms'] = extra_delay
            plan['selection_ms'] = plan['estimated_ms'] + externality_weight*extra_delay
        predictions[node_id] = plan
        needed.remove(node_id)

    for node_id in sorted(needed):
        ready(node_id)
    def score(prediction):
        return prediction.get('selection_ms', prediction['estimated_ms'])

    best_finish = min(map(score, predictions.values()), default=float('inf'))

    while needed and shadow.events:
        now, _, kind, payload, epoch = heapq.heappop(shadow.events)
        if prune and predictions and now - sim.now > best_finish:
            # Waiting alone already exceeds the best known total service.
            # The strict comparison preserves tie-breaking at equal scores.
            return predictions
        if kind == EVENT_FLOW_DONE and epoch != shadow.epoch:
            continue
        shadow.network.advance(now)
        shadow.clock.advance(now)
        shadow.now = now
        if kind == EVENT_FLOW_START:
            target, source, size = payload
            job = shadow.nodes[target].active
            job.flow_id = shadow.network.start(now, source, target, size)
            shadow._refresh_flow_event()
        elif kind == EVENT_FLOW_DONE:
            shadow._on_flow_done(payload)
        elif kind == EVENT_JOB_DONE:
            shadow._on_job_done(payload)
            ready(payload)
            if payload in predictions:
                best_finish = min(best_finish, score(predictions[payload]))
    if needed:
        raise RuntimeError('assigned work did not drain in cluster forecast')
    return predictions
