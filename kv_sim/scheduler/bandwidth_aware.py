"""Bandwidth-aware placement with a marginal network-congestion penalty.

Greedy estimates the current request's service time; this policy additionally
estimates how introducing a candidate transfer delays existing active flows.
That estimate is a local fluid-network approximation, not counterfactual replay.
"""
def select(sim, req):
    return min(sim.nodes, key=lambda node_id: (
        sim._policy_cost(req, node_id), node_id))
