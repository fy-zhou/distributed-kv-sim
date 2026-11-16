"""Experimental bandwidth-aware current-request cost policy.

The base estimate already accounts for current network flows. This variant
adds a configurable congestion surcharge, which should only be used as an
experimental heuristic; it is not a validated scheduling improvement.
"""

def select(sim, req, congestion_weight_ms=1.0):
    def cost(i):
        local, source, longest = sim._best_source(req, i)
        remote = (longest - local) if source is not None else 0
        competing = sum(1 for f in sim.network.flows.values()
                        if remote and (f.src == source or f.dst == i))
        return (sim._estimate(req, i)
                + congestion_weight_ms * competing * int(remote > 0), i)
    return min(sim.nodes, key=cost)
