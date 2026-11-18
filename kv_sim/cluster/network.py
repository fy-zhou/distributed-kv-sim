"""Deterministic fluid transfer engine with endpoint/network contention.

Each active flow shares the source NIC, destination NIC and the shared fabric.
Rates are recalculated at every start/finish. Transfers carry byte counts,
not tensor data. Endpoint division is a simple equal-share conservative model,
not a general max-min fair multi-hop network solver.
"""
from dataclasses import dataclass


@dataclass
class Flow:
    flow_id: int
    src: int
    dst: int
    remaining_bytes: float
    rate_bytes_per_ms: float = 0.0
    initial_bytes: float = 0.0


class FluidNetwork:
    def __init__(self, node_bandwidth_bytes_per_ms, fabric_bytes_per_ms):
        self.nic = dict(node_bandwidth_bytes_per_ms)
        self.fabric = float(fabric_bytes_per_ms)
        if self.fabric <= 0 or any(v <= 0 for v in self.nic.values()):
            raise ValueError("network capacities must be positive")
        self.flows = {}
        self.last_update = 0.0
        self.next_id = 0
        self.total_bytes = 0.0

    def advance(self, now):
        if now < self.last_update - 1e-8:
            raise ValueError("clock reversed")
        elapsed = max(0, now - self.last_update)
        for flow in self.flows.values():
            flow.remaining_bytes = max(
                0.0, flow.remaining_bytes - elapsed * flow.rate_bytes_per_ms)
        self.last_update = now

    def recalculate(self):
        src_counts = {}
        dst_counts = {}
        for f in self.flows.values():
            src_counts[f.src] = src_counts.get(f.src, 0) + 1
            dst_counts[f.dst] = dst_counts.get(f.dst, 0) + 1
        n = len(self.flows)
        for f in self.flows.values():
            f.rate_bytes_per_ms = min(
                self.nic[f.src] / src_counts[f.src],
                self.nic[f.dst] / dst_counts[f.dst],
                self.fabric / n,
            )

    def prospective_rate(self, src, dst):
        """Immediate equal-share rate if one flow were added now.

        Send and receive NIC budgets are independent (full duplex). This
        predicts the current state, not future flow arrivals/completions.
        """
        senders = sum(f.src == src for f in self.flows.values()) + 1
        receivers = sum(f.dst == dst for f in self.flows.values()) + 1
        return min(self.nic[src] / senders, self.nic[dst] / receivers,
                   self.fabric / (len(self.flows) + 1))

    def start(self, now, src, dst, size_bytes):
        if src == dst:
            raise ValueError("network transfer needs distinct nodes")
        if size_bytes <= 0:
            raise ValueError("transfer size must be positive")
        self.advance(now)
        self.next_id += 1
        flow = Flow(self.next_id, src, dst, float(size_bytes),
                    initial_bytes=float(size_bytes))
        self.flows[flow.flow_id] = flow
        self.total_bytes += size_bytes
        self.recalculate()
        return flow.flow_id

    def finish(self, now, flow_id):
        self.advance(now)
        f = self.flows[flow_id]
        if f.remaining_bytes > 1e-5 + 1e-12 * f.initial_bytes:
            raise RuntimeError("flow finished prematurely")
        del self.flows[flow_id]
        self.recalculate()
        return f

    def next_finish(self, now):
        if not self.flows:
            return None
        return min((now + f.remaining_bytes / f.rate_bytes_per_ms, f.flow_id)
                   for f in self.flows.values())
