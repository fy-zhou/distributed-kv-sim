"""Deterministic network-hotspot workload for scheduling policy diagnostics.

Each request uses full, prefix-linked token hashes. The designated source
node holds a long shared prompt, while execution nodes begin with cold blocks.
Synthetic prefixes never imply measured behavior or production trace fidelity.
"""
import random

from ..engine.events import Request
from ..cache.prefix_hash import prefix_hashes


def make_hotspot_workload(count=240, block_size=16, shared_blocks=256,
                          suffix_blocks=4, burst_width=8,
                          burst_gap_ms=2.0, within_burst_ms=0.02,
                          seed=42, namespace="qwen3-0.6b", hot_probability=.85):
    """Burst arrivals with a shared long prefix and unique suffix per request.

    A small fraction of requests take one of several alternate prefixes.
    This lets local-admission choices affect future reuse without randomizing
    the hash identity of shared prefix blocks.
    """
    if min(count, block_size, shared_blocks, burst_width) <= 0:
        raise ValueError("count, block_size, shared_blocks and burst_width > 0")
    if suffix_blocks < 0 or burst_gap_ms < 0 or within_burst_ms < 0:
        raise ValueError("invalid suffix or timing parameter")
    if not 0 <= hot_probability <= 1:
        raise ValueError("hot_probability must lie in [0,1]")
    rng = random.Random(seed)
    hot = [500000 + j for j in range(block_size * shared_blocks)]
    alternate = [
        [800000 + f * 200000 + j for j in range(block_size * shared_blocks)]
        for f in range(2)
    ]
    requests = []
    for i in range(count):
        family = 0 if rng.random() < hot_probability else (1 + rng.randrange(2))
        prefix = hot if family == 0 else alternate[family - 1]
        suffix = [20000000 + i * 100000 + j
                  for j in range(suffix_blocks * block_size)]
        burst = i // burst_width
        within = i % burst_width
        arrival = burst * burst_gap_ms + within * within_burst_ms
        requests.append(Request.from_tokens(
            "hot-%06d" % i, arrival, prefix + suffix,
            block_size, namespace))
    return requests, prefix_hashes(hot, block_size, namespace)


def make_hotspot_initial_caches(specs, hot_hashes, source_node=0,
                                occupancy=1.0, seed=42):
    """Use meaningful hot hashes at one source; fill other slots with cold IDs.

    The simulator stores hashes only (no real KV tensor). A source's last
    hot block must remain cached for full-prefix matching.
    """
    if not 0 <= occupancy <= 1:
        raise ValueError("occupancy outside [0,1]")
    ids = {s.node_id for s in specs}
    if source_node not in ids:
        raise ValueError("invalid source node")
    result = {}
    for spec in specs:
        quota = int(round(spec.cache_capacity_blocks * occupancy))
        if spec.node_id == source_node:
            if quota < len(hot_hashes):
                raise ValueError("source cache smaller than hot prefix")
            values = list(hot_hashes)
        else:
            values = []
        cold_index = 0
        while len(values) < quota:
            values.append("cold:%d:%d:%d" % (
                seed, spec.node_id, cold_index))
            cold_index += 1
        result[spec.node_id] = values
    return result
