"""Illustrative GPU compute prediction; NOT hardware calibrated."""
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
