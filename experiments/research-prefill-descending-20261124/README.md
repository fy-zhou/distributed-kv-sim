# Bounded RTX 4090 measurement

Local Qwen3-0.6B BF16 decoder forward with prebuilt GPU-resident prefixes; the CUDA event interval includes launch gaps. The archived correctness CSV compares the final position to the uncached decoder. No LM head, NIC or KV transfer in this interval.

Source blob `scripts/measure_prefill_bounded.py` SHA-256 `cd48998a6aba9baaf69b5f102e2107013af5805c91012fd90e230f10d74a303c`.

