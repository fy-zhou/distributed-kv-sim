"""Round-robin request placement."""

def select(sim, req):
    ids = sorted(sim.nodes)
    node_id = ids[sim.rr % len(ids)]
    sim.rr += 1
    return node_id
