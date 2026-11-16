"""Backwards-compatible imports. Use kv_sim.cache for new code."""
from .cache.prefix_hash import prefix_hashes
from .cache.block_store import BlockCache

__all__ = ["prefix_hashes", "BlockCache"]
