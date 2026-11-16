"""Independent performance predictors for single-request KV reuse simulation.

Only the H2D coefficients below come from the existing RTX 4090 single-node
experiment. Compute and network coefficients are illustrative defaults.
All times are milliseconds; do not interpret default simulated TTFT as real.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ComputePredictor:
    fixed_ms: float = 2.0
    ms_per_new_token: float = 0.003
    ms_per_causal_pair: float = 1.8e-6

    def predict_ms(self, input_tokens, hit_tokens):
        if input_tokens < 0 or not 0 <= hit_tokens <= input_tokens:
            raise ValueError("invalid token counts")
        remaining = input_tokens - hit_tokens
        pairs = remaining * (2 * hit_tokens + remaining + 1) / 2
        return (self.fixed_ms
                + self.ms_per_new_token * remaining
                + self.ms_per_causal_pair * pairs)


@dataclass(frozen=True)
class H2DPredictor:
    kv_bytes_per_token: int = 114688
    fixed_ms: float = 1.684
    ms_per_gib: float = 71.210

    def predict_ms(self, hit_tokens):
        if hit_tokens < 0:
            raise ValueError("hit_tokens must be nonnegative")
        if not hit_tokens:
            return 0.0
        gib = hit_tokens * self.kv_bytes_per_token / (2**30)
        return self.fixed_ms + self.ms_per_gib * gib


@dataclass(frozen=True)
class TransferPredictor:
    setup_ms: float = 0.15

    def predict_ms(self, payload_bytes, allocated_rate_bytes_per_ms):
        if payload_bytes < 0 or allocated_rate_bytes_per_ms <= 0:
            raise ValueError("invalid transfer")
        if not payload_bytes:
            return 0.0
        return self.setup_ms + payload_bytes / allocated_rate_bytes_per_ms
