"""Choose node with the largest *local* valid prefix."""

def select(sim, req):
    return min(sim.nodes, key=lambda i: (-sim._hit(req, i), i))
