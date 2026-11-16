"""Load token-ID traces from CSV for deterministic replay.

Required: request_id, arrival_ms, tokens (JSON list of integer token IDs).
No strings or raw text are hashed as if they were tokenizer outputs.
"""
import csv
import json
from ..engine.events import Request


def load_token_trace(path, block_size=16, namespace="qwen3-0.6b"):
    reqs = []
    with open(path, "r", newline="") as f:
        for row in csv.DictReader(f):
            tokens = json.loads(row["tokens"])
            if not isinstance(tokens, list):
                raise ValueError("tokens must be a JSON list of token IDs")
            reqs.append(Request.from_tokens(
                row["request_id"], float(row["arrival_ms"]), tokens,
                block_size=block_size, namespace=namespace))
    return reqs
