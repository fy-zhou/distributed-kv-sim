"""Deterministic fluid transfer engine with endpoint/network contention.

Each active flow shares the source NIC, destination NIC and the shared fabric.
Rates are recalculated at every start/finish. Transfers carry byte counts,
not tensor data. Endpoint division is a simple equal-share conservative model,
with an optional max-min allocator over the same three resource types.
"""
from dataclasses import dataclass
import math


@dataclass
class Flow:
    flow_id: int
    src: int
    dst: int
    remaining_bytes: float
    rate_bytes_per_ms: float = 0.0
    initial_bytes: float = 0.0


SHARING_MODELS = ('equal_share', 'max_min')


class FluidNetwork:
    def __init__(self, node_bandwidth_bytes_per_ms, fabric_bytes_per_ms,
                 sharing='equal_share'):
        self.nic = dict(node_bandwidth_bytes_per_ms)
        self.fabric = float(fabric_bytes_per_ms)
        if self.fabric <= 0 or any(v <= 0 for v in self.nic.values()):
            raise ValueError("network capacities must be positive")
        if sharing not in SHARING_MODELS:
            raise ValueError("invalid network sharing model")
        if sharing == 'max_min' and not all(
                math.isfinite(rate) for rate in (self.fabric, *self.nic.values())):
            raise ValueError("max-min sharing requires finite capacities")
        self.sharing = sharing
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

    def _max_min_rates(self, endpoints):
        """Progressive filling across full-duplex endpoint and fabric budgets."""
        fabric = ('fabric', None)
        budgets = {fabric: self.fabric}
        resources = {}
        for flow_id, (src, dst) in endpoints.items():
            send, receive = ('send', src), ('receive', dst)
            budgets[send] = self.nic[src]
            budgets[receive] = self.nic[dst]
            resources[flow_id] = (send, receive, fabric)
        rates = dict.fromkeys(endpoints, 0.)
        while resources:
            counts = {}
            for flow_resources in resources.values():
                for resource in flow_resources:
                    counts[resource] = counts.get(resource, 0) + 1
            tightest = min(counts, key=lambda resource: budgets[resource]/counts[resource])
            step = budgets[tightest]/counts[tightest]
            for resource, count in counts.items():
                budgets[resource] = max(0., budgets[resource]-count*step)
            for flow_id in list(resources):
                rates[flow_id] += step
                if tightest in resources[flow_id]:
                    del resources[flow_id]
        return rates

    def _max_min_prospective(self, src, dst):
        endpoints = {f.flow_id: (f.src, f.dst) for f in self.flows.values()}
        endpoints[None] = (src, dst)
        return self._max_min_rates(endpoints)

    def recalculate(self):
        if self.sharing == 'max_min':
            rates = self._max_min_rates({f.flow_id: (f.src, f.dst)
                                         for f in self.flows.values()})
            for flow_id, rate in rates.items():
                self.flows[flow_id].rate_bytes_per_ms = rate
            return
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
        """Immediate allocation if one flow were added now.

        Send and receive NIC budgets are independent (full duplex). This
        predicts the current state, not future flow arrivals/completions.
        """
        if self.sharing == 'max_min':
            return self._max_min_prospective(src, dst)[None]
        senders = sum(f.src == src for f in self.flows.values()) + 1
        receivers = sum(f.dst == dst for f in self.flows.values()) + 1
        return min(self.nic[src] / senders, self.nic[dst] / receivers,
                   self.fabric / (len(self.flows) + 1))

    def externality_ms(self, src, dst):
        """Instantaneous-rate approximation to aggregate extra flow duration."""
        if self.sharing == 'max_min':
            rates = self._max_min_prospective(src, dst)
            return max(0., sum(f.remaining_bytes *
                (1/rates[f.flow_id] - 1/f.rate_bytes_per_ms) for f in self.flows.values()))

        senders, receivers = {src: 1}, {dst: 1}
        for f in self.flows.values():
            senders[f.src] = senders.get(f.src, 0) + 1
            receivers[f.dst] = receivers.get(f.dst, 0) + 1
        fabric_share = self.fabric / (len(self.flows) + 1)
        added = 0.
        for f in self.flows.values():
            rate = min(self.nic[f.src] / senders[f.src],
                       self.nic[f.dst] / receivers[f.dst], fabric_share)
            added += f.remaining_bytes * (1/rate - 1/f.rate_bytes_per_ms)
        return max(0., added)

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
