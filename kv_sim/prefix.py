"""Prefix-safe fixed-token-block hashing and LRU cache storage."""
import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field


def prefix_hashes(tokens, block_size=16, namespace="model-default"):
    """Hash the *entire prefix lineage*, not independent token chunks.

    An incomplete trailing block is never cached. Namespace must include
    model/version/tokenizer/KV layout and relevant tenant isolation context.
    """
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    parent = hashlib.sha256(namespace.encode("utf8")).digest()
    result = []
    for start in range(0, len(tokens) - block_size + 1, block_size):
        chunk = tokens[start:start + block_size]
        if any(not isinstance(t, int) or t < 0 for t in chunk):
            raise ValueError("token ids must be nonnegative integers")
        payload = ",".join(map(str, chunk)).encode("ascii")
        parent = hashlib.sha256(parent + b":" + payload).digest()
        result.append(parent.hex())
    return tuple(result)


@dataclass
class BlockCache:
    capacity: int
    admission: str = "always"
    blocks: OrderedDict = field(default_factory=OrderedDict)
    pins: dict = field(default_factory=dict)
    evictions: int = 0
    rejections: int = 0

    def __post_init__(self):
        if self.capacity <= 0:
            raise ValueError("cache capacity must be > 0")
        if self.admission not in ("always", "no_remote"):
            raise ValueError("admission must be always or no_remote")

    def contains(self, key):
        return key in self.blocks

    def prefix_len(self, hashes):
        n = 0
        for key in hashes:
            if key not in self.blocks:
                break
            n += 1
        return n

    def touch(self, keys):
        for key in keys:
            if key in self.blocks:
                self.blocks.move_to_end(key)

    def pin(self, keys):
        for key in keys:
            if key not in self.blocks:
                raise KeyError("source block evicted before transfer starts")
            self.pins[key] = self.pins.get(key, 0) + 1

    def unpin(self, keys):
        for key in keys:
            count = self.pins.get(key, 0)
            if count <= 0:
                raise RuntimeError("cache pin underflow")
            if count == 1:
                del self.pins[key]
            else:
                self.pins[key] = count - 1

    def admit(self, keys, *, remote=False):
        """LRU-evict unpinned blocks. Reject if every occupant is pinned.

        Insert only after the current request finishes using KV; active request
        KV storage is separate from this persistent CPU-cache abstraction.
        """
        if remote and self.admission == "no_remote":
            return 0
        inserted = 0
        for key in keys:
            if key in self.blocks:
                self.blocks.move_to_end(key)
                continue
            if len(self.blocks) >= self.capacity:
                victim = next((k for k in self.blocks if k not in self.pins), None)
                if victim is None:
                    self.rejections += 1
                    continue
                del self.blocks[victim]
                self.evictions += 1
            self.blocks[key] = None
            inserted += 1
        return inserted
