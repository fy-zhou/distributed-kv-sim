"""Choose node with fewest queued or active requests."""

def select(sim, req):
    return min(sim.nodes, key=lambda i: (
        len(sim.nodes[i].queue) + int(sim.nodes[i].active is not None), i))
