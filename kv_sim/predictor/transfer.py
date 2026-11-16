"""Fluid-flow network transfer duration prediction."""
from dataclasses import dataclass

@dataclass(frozen=True)
class TransferPredictor:
    setup_ms: float = 0.15

    def predict_ms(self, payload_bytes, allocated_rate_bytes_per_ms):
        if payload_bytes < 0 or allocated_rate_bytes_per_ms <= 0:
            raise ValueError("invalid transfer")
        if not payload_bytes:
            return 0.0
        return self.setup_ms + payload_bytes / allocated_rate_bytes_per_ms
