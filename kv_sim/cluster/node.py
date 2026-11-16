"""Node metadata and FIFO single-server node state."""
from collections import deque
from dataclasses import dataclass, field
from ..cache.block_store import BlockCache

@dataclass(frozen=True)
class NodeSpec:
    node_id: int
    cache_capacity_blocks: int = 4096
    nic_gib_s: float = 12.0


@dataclass
class Node:
    spec: NodeSpec
    cache: BlockCache
    queue: deque = field(default_factory=deque)
    active: object = None
