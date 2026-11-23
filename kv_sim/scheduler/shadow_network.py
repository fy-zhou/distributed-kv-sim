"""Forecast known network completions without mutating the event heap."""
from dataclasses import replace

from ..cluster.network import FluidNetwork


def _known_completions(sim, proposed=None):
    live = sim.network
    network = FluidNetwork(live.nic, live.fabric)
    network.last_update = sim.now
    network.next_id = live.next_id
    network.flows = {i: replace(flow) for i, flow in live.flows.items()}
    starts, owners = [], {}
    for node_id, node in sim.nodes.items():
        job = node.active
        if job is None or job.expected_finish_ms is not None:
            continue
        if job.flow_id is not None:
            owners[job.flow_id] = node_id
        elif job.remote_blocks:
            size = job.remote_blocks * sim.cfg.block_size * sim.cfg.kv_bytes_per_token
            starts.append((job.transfer_start_ms, node_id, job.source, size))
    if proposed is not None:
        source, target, size = proposed
        starts.append((sim.now + sim.cfg.remote_setup_ms, target, source, size))
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
                yield owners.pop(flow_id), now - sim.now


def active_remaining(sim):
    """Replay known network work on a copy, without reading the event heap."""
    waiting = {node_id: sim._remaining_active_ms(node)
               for node_id, node in sim.nodes.items()}
    for target, transfer_ms in _known_completions(sim):
        job = sim.nodes[target].active
        waiting[target] = (transfer_ms + sim._h2d_time(job.hit)
                           + sim._compute_time(job.req, job.hit))
    return waiting


def proposed_transfer_ms(sim, source, target, size):
    """Return setup plus copy time against active and currently known starts."""
    if sim.nodes[target].active is not None:
        raise ValueError('proposed transfer requires an idle target')
    for owner, elapsed in _known_completions(sim, (source, target, size)):
        if owner == target:
            return elapsed
    raise RuntimeError('proposed transfer did not finish')


def transfer_cost(sim, target):
    """Memoize duration evaluations for alternate routes to one ready node."""
    costs = {}

    def cost(source, size):
        key = (source, size)
        if key not in costs:
            costs[key] = proposed_transfer_ms(sim, source, target, size)
        return costs[key]

    return cost
