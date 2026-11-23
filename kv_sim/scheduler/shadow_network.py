"""Forecast completion of active and already scheduled setup transfers."""
from dataclasses import replace

from ..cluster.network import FluidNetwork


def active_remaining(sim):
    """Replay known network work on a copy, without reading the event heap."""
    live = sim.network
    network = FluidNetwork(live.nic, live.fabric)
    network.last_update = sim.now
    network.next_id = live.next_id
    network.flows = {i: replace(flow) for i, flow in live.flows.items()}
    waiting, starts, owners = {}, [], {}
    for node_id, node in sim.nodes.items():
        job = node.active
        waiting[node_id] = sim._remaining_active_ms(node)
        if job is None or job.expected_finish_ms is not None:
            continue
        if job.flow_id is not None:
            owners[job.flow_id] = node_id
        elif job.remote_blocks:
            size = job.remote_blocks * sim.cfg.block_size * sim.cfg.kv_bytes_per_token
            starts.append((job.transfer_start_ms, node_id, job.source, size))
    starts.sort(reverse=True)
    now = sim.now
    while starts or network.flows:
        finish = network.next_finish(now)
        if starts and (finish is None or starts[-1][0] <= finish[0]):
            now, target, source, size = starts.pop()
            owners[network.start(now, source, target, size)] = target
        else:
            now, flow_id = finish
            network.finish(now, flow_id)
            if flow_id in owners:
                target = owners.pop(flow_id)
                job = sim.nodes[target].active
                waiting[target] = ((now - sim.now) + sim._h2d_time(job.hit)
                                   + sim._compute_time(job.req, job.hit))
    return waiting
