"""Prefix-lineage hashing of fixed-size token blocks."""
import hashlib

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
