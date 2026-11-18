"""Policy dispatch. Strategies receive simulator + request, return node ID."""
from . import round_robin, shortest_queue, max_hit, greedy, bandwidth_aware, cost_aware

POLICIES = {
    "round_robin": round_robin.select,
    "shortest_queue": shortest_queue.select,
    "max_hit": max_hit.select,
    "greedy": greedy.select,
    "bandwidth_aware": bandwidth_aware.select,
    "cost_aware": cost_aware.select,
    "round_robin_cost": round_robin.select,
}


def choose_node(sim, req):
    try:
        fn = POLICIES[sim.cfg.policy]
    except KeyError:
        raise ValueError("invalid policy: " + str(sim.cfg.policy))
    return fn(sim, req)

from .reuse import choose_reuse
