"""Min-estimated-current-request completion time (not future-aware)."""

def select(sim, req):
    return min(sim.nodes, key=lambda i: (sim._estimate(req, i), i))
