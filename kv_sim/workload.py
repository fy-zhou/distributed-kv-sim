"""Reproducible synthetic prefix-sharing workloads with real prefix-hash identities."""
import random
from .core import Request
from .prefix import prefix_hashes


def make_workload(count=200, families=8, block_size=16,
                  shared_blocks=32, unique_blocks=8, arrival_ms=12.0,
                  seed=42, skew=1.2, namespace="qwen3-0.6b"):
    """Zipf-like hot prefixes, variable-sized suffix, Poisson arrivals.

    Every request uses an exact token-id sequence. Sharing is in complete
    prefix blocks, not superficial equal text chunks later in a sequence.
    """
    if count <= 0 or families <= 0 or shared_blocks <= 0:
        raise ValueError("positive count/families/shared_blocks required")
    if arrival_ms < 0 or unique_blocks < 0:
        raise ValueError("invalid arrival or suffix")
    rng = random.Random(seed)
    fam_prefix = [[1000 + f * 10000 + b * block_size + j
                   for b in range(shared_blocks)
                   for j in range(block_size)] for f in range(families)]
    weights = [1 / (rank+1)**skew for rank in range(families)]
    requests = []
    t = 0.0
    for i in range(count):
        if arrival_ms:
            t += rng.expovariate(1 / arrival_ms)
        family = rng.choices(range(families), weights=weights, k=1)[0]
        suffix_blocks = rng.randint(max(0, unique_blocks//2),
                                    max(0, unique_blocks*2))
        suffix = [10000000 + i*100000 + j
                  for j in range(suffix_blocks * block_size)]
        tokens = fam_prefix[family] + suffix
        requests.append(Request.from_tokens(
            str(i), t, tokens, block_size, namespace))
    # Expose meaningful prefix blocks to prefill each node's cache.
    seed_prefixes = [prefix_hashes(p, block_size, namespace)
                     for p in fam_prefix]
    return requests, seed_prefixes


def make_initial_caches(specs, prefixes, seed=42, occupancy=1.0):
    """Fill with a mix of real hot prefixes and unique valid cold blocks.

    Distribute families across nodes, with deterministic distractor KV blocks.
    Initial occupancy is a fraction of each node's capacity. A small node
    may retain only a suffix of inserted prefixes under LRU; this is allowed.
    """
    if not (0 <= occupancy <= 1):
        raise ValueError("occupancy must lie in [0,1]")
    result = {}
    for ix, spec in enumerate(specs):
        quota = int(round(spec.cache_capacity_blocks * occupancy))
        keys = []
        for family in range(len(prefixes)):
            if family % len(specs) == ix:
                keys.extend(prefixes[family])
        # Cold blocks are distinct, deterministic and use explicit
        # warmup identities; do not count fake occupancy without cache keys.
        cold_ix = 0
        while len(keys) < quota:
            keys.append("cold:%d:%d:%d" % (seed, spec.node_id, cold_ix))
            cold_ix += 1
        result[spec.node_id] = keys[:quota]
    return result
