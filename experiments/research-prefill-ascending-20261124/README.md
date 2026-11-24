# Bounded RTX 4090 measurement

Local Qwen3-0.6B BF16 decoder forward with prebuilt GPU-resident prefixes; the CUDA event interval includes launch gaps. The archived correctness CSV compares the final position to the uncached decoder. No LM head, NIC or KV transfer in this interval.

Source blob `scripts/measure_prefill_bounded.py` SHA-256 `991868458bc6f1b76a2c5186f53630e33ed500ec312d00c2c4338cb5e57d7241`.

