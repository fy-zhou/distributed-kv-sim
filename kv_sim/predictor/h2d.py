"""RTX4090 Qwen3-0.6B reference H2D fit, valid in measured size range."""
from dataclasses import dataclass

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
