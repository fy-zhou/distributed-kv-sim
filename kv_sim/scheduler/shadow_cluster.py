"""Forecast node readiness by simulating all previously assigned work."""
from dataclasses import replace
import copy
import heapq

from ..cache import BlockCache
from ..cluster.node import Node
from ..engine.events import (EVENT_FLOW_DONE, EVENT_FLOW_START, EVENT_JOB_DONE)


def _fork(sim):
    from ..engine.simulator import Simulator

    shadow = Simulator([node.spec for node in sim.nodes.values()],
                       replace(sim.cfg, trace_decisions=False))
    shadow.now = sim.now
    shadow.clock.advance(sim.now)
    shadow.sequence = sim.sequence
    shadow.epoch = sim.epoch
    shadow.network = copy.deepcopy(sim.network)
    for node_id, live in sim.nodes.items():
        cache = BlockCache(live.cache.capacity, live.cache.admission)
        cache.blocks = live.cache.blocks.copy()
        cache.pins = live.cache.pins.copy()
        shadow.nodes[node_id] = Node(live.spec, cache, live.queue.copy(),
                                     replace(live.active) if live.active else None)
    # Copy only the simulator's internal event types. External arrivals never
    # dispatch or participate in the fork, even when their timestamp is now.
    shadow.events = [e for e in sim.events
                     if e[2] in (EVENT_FLOW_START, EVENT_FLOW_DONE, EVENT_JOB_DONE)]
    heapq.heapify(shadow.events)
    return shadow


def forecasts(sim, req, nodes):
    """Score service on the forecast state when each candidate becomes idle.

    A single fork drains the already assigned queues. The new request is
    evaluated when a node becomes idle. Its service estimate uses bandwidth
    shares at that time and does not advance the fork with the new transfer.
    """
    from .shadow import _service

    needed = set(nodes)
    predictions = {}
    shadow = _fork(sim)

    def ready(node_id):
        if node_id not in needed:
            return
        node = shadow.nodes[node_id]
        if node.active is not None or node.queue:
            return
        plan = _service(shadow, req, node_id, isolated=False)
        waiting = shadow.now - sim.now
        plan.update(queue_ms=waiting, estimated_ms=waiting+plan.pop('service_ms'),
                    rollout_jobs=len(sim.nodes[node_id].queue)+int(sim.nodes[node_id].active is not None))
        predictions[node_id] = plan
        needed.remove(node_id)

    for node_id in sorted(needed):
        ready(node_id)

    while needed and shadow.events:
        now, _, kind, payload, epoch = heapq.heappop(shadow.events)
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
    if needed:
        raise RuntimeError('assigned work did not drain in cluster forecast')
    return predictions
