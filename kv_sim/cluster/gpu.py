"""Simplified node-local GPU service: one active prefill at a time (v0.2).

GPU memory allocation, batching and decode require later calibration.
"""

def is_gpu_idle(node):
    return node.active is None


def remaining_gpu_service_ms(job, now):
    if job is None or job.expected_finish_ms is None:
        return 0.0
    return max(0.0, job.expected_finish_ms - now)
