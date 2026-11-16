"""Event-driven multi-node CPU KV reuse simulator.

One active request per node (v1). Prefill and local PCIe loading are
serialized in the default model. Remote transfer progresses concurrently
across nodes under a fluid shared-NIC/fabric capacity constraint.
"""
import heapq
import math
import statistics
from collections import deque
from dataclasses import dataclass, field

from .prefix import BlockCache, prefix_hashes
from .network import FluidNetwork


@dataclass(frozen=True)
class Request:
    request_id: str
    arrival_ms: float
    hashes: tuple
    length_tokens: int

    @classmethod
    def from_tokens(cls, request_id, arrival_ms, tokens, block_size=16,
                    namespace="qwen3-0.6b"):
        return cls(str(request_id), float(arrival_ms),
                   prefix_hashes(tokens, block_size, namespace), len(tokens))


@dataclass(frozen=True)
class NodeSpec:
    node_id: int
    cache_capacity_blocks: int = 4096
    nic_gib_s: float = 12.0


@dataclass
class Config:
    block_size: int = 16
    kv_bytes_per_token: int = 114688  # Qwen3-0.6B BF16, 28x2x8x128x2
    # Simple calibrated-shape illustrative computation curve, NOT GPU profiling.
    gpu_fixed_ms: float = 2.0
    gpu_ms_per_new_token: float = 0.003
    gpu_ms_per_attention_pair: float = 1.8e-6
    # Measured single-host Qwen3-0.6B fit, 4090 pinned CPU memory.
    h2d_fixed_ms: float = 1.684
    h2d_ms_per_gib: float = 71.210
    remote_setup_ms: float = 0.15
    fabric_gib_s: float = 24.0
    policy: str = "greedy"
    admission: str = "always"


@dataclass
class Node:
    spec: NodeSpec
    cache: BlockCache
    queue: deque = field(default_factory=deque)
    active: object = None


@dataclass
class Job:
    req: Request
    target: int
    start_ms: float
    queue_ms: float
    local_hit: int
    hit: int
    source: object
    remote_blocks: int
    transfer_ms: float = 0.0
    load_ms: float = 0.0
    compute_ms: float = 0.0
    flow_id: object = None


class Simulator:
    def __init__(self, specs, config=None):
        self.cfg = config or Config()
        if not specs:
            raise ValueError("at least one node required")
        if len({s.node_id for s in specs}) != len(specs):
            raise ValueError("node ids must be unique")
        if self.cfg.admission not in ("always", "no_remote"):
            raise ValueError("invalid admission policy")
        self.nodes = {s.node_id: Node(
            s, BlockCache(s.cache_capacity_blocks, self.cfg.admission))
            for s in specs}
        self.network = FluidNetwork(
            {s.node_id: s.nic_gib_s * (2**30) / 1000 for s in specs},
            self.cfg.fabric_gib_s * (2**30) / 1000)
        self.events = []
        self.sequence = 0
        self.now = 0.0
        self.epoch = 0
        self.rr = 0
        self.rows = []
        self.arrival_order = []

    def seed_cache(self, node_id, blocks):
        """Initialize meaningful cache hashes; caller controls initial fill."""
        self.nodes[node_id].cache.admit(blocks)

    def _schedule(self, when, kind, payload=None, epoch=None):
        if when < self.now - 1e-7:
            raise RuntimeError("event in the past")
        self.sequence += 1
        heapq.heappush(
            self.events, (float(when), self.sequence, kind, payload, epoch))

    def _hit(self, req, node_id):
        return self.nodes[node_id].cache.prefix_len(req.hashes)

    def _best_source(self, req, dest):
        local = self._hit(req, dest)
        best, best_len = None, local
        for source in self.nodes:
            if source != dest:
                n = self._hit(req, source)
                if n > best_len:
                    best, best_len = source, n
        return local, best, best_len

    def _compute_time(self, req, hit_blocks):
        h = min(req.length_tokens, hit_blocks * self.cfg.block_size)
        m = req.length_tokens - h
        # Each newly computed token attends to all earlier cached tokens
        # and causally to its own part of the new sequence.
        causal_pairs = m * (2 * h + m + 1) / 2
        return (self.cfg.gpu_fixed_ms
                + self.cfg.gpu_ms_per_new_token * m
                + self.cfg.gpu_ms_per_attention_pair * causal_pairs)

    def _h2d_time(self, hit_blocks):
        if hit_blocks == 0:
            return 0.0
        gib = (hit_blocks * self.cfg.block_size
               * self.cfg.kv_bytes_per_token / 2**30)
        return self.cfg.h2d_fixed_ms + self.cfg.h2d_ms_per_gib * gib

    def _estimate(self, req, node_id):
        node = self.nodes[node_id]
        local, source, n = self._best_source(req, node_id)
        remote = n - local if source is not None else 0
        remote_ms = 0.0
        if remote:
            size = remote * self.cfg.block_size * self.cfg.kv_bytes_per_token
            active = sum(1 for f in self.network.flows.values()
                         if f.src == source or f.dst == node_id)
            rate = min(
                self.network.nic[source] / (active + 1),
                self.network.nic[node_id] / (active + 1),
                self.network.fabric / (len(self.network.flows) + 1))
            remote_ms = self.cfg.remote_setup_ms + size / rate
        own = remote_ms + self._h2d_time(n) + self._compute_time(req, n)
        waiting = 0.0
        if node.active is not None:
            # Deliberately simple predictive bound, not an oracle.
            waiting += self._h2d_time(node.active.hit) + self._compute_time(
                node.active.req, node.active.hit)
        for pending in node.queue:
            waiting += self._compute_time(pending, self._hit(pending, node_id))
        return waiting + own

    def _choose(self, req):
        ids = sorted(self.nodes)
        policy = self.cfg.policy
        if policy == "round_robin":
            chosen = ids[self.rr % len(ids)]
            self.rr += 1
            return chosen
        if policy == "shortest_queue":
            return min(ids, key=lambda i: (
                len(self.nodes[i].queue) +
                int(self.nodes[i].active is not None), i))
        if policy == "max_hit":
            return min(ids, key=lambda i: (-self._hit(req, i), i))
        if policy == "greedy":
            return min(ids, key=lambda i: (self._estimate(req, i), i))
        raise ValueError("invalid policy: " + policy)

    def _refresh_flow_event(self):
        self.epoch += 1
        next_flow = self.network.next_finish(self.now)
        if next_flow is not None:
            t, flow_id = next_flow
            self._schedule(t, "flow_done", flow_id, self.epoch)

    def _start(self, node_id):
        node = self.nodes[node_id]
        if node.active is not None or not node.queue:
            return
        req = node.queue.popleft()
        local, source, longest = self._best_source(req, node_id)
        local_keys = req.hashes[:local]
        node.cache.touch(local_keys)
        job = Job(req, node_id, self.now, self.now-req.arrival_ms,
                  local, longest, source, longest-local)
        node.active = job
        if job.remote_blocks:
            keys = req.hashes[local:longest]
            self.nodes[source].cache.pin(keys)
            bytes_to_copy = (job.remote_blocks * self.cfg.block_size
                             * self.cfg.kv_bytes_per_token)
            job.flow_id = self.network.start(self.now, source, node_id,
                                             bytes_to_copy)
            self._refresh_flow_event()
        else:
            self._start_compute(job)

    def _start_compute(self, job):
        job.load_ms = self._h2d_time(job.hit)
        job.compute_ms = self._compute_time(job.req, job.hit)
        self._schedule(
            self.now + job.load_ms + job.compute_ms, "job_done", job.target)

    def _on_flow_done(self, flow_id):
        job = next((node.active for node in self.nodes.values()
                    if node.active is not None
                    and node.active.flow_id == flow_id), None)
        if job is None:
            raise RuntimeError("orphaned transfer")
        flow = self.network.finish(self.now, flow_id)
        keys = job.req.hashes[job.local_hit:job.hit]
        self.nodes[flow.src].cache.unpin(keys)
        job.transfer_ms = self.now - job.start_ms - self.cfg.remote_setup_ms
        job.flow_id = None
        self._start_compute(job)
        self._refresh_flow_event()

    def _on_job_done(self, node_id):
        node = self.nodes[node_id]
        job = node.active
        if job is None:
            raise RuntimeError("job completion without active job")
        req = job.req
        if self.cfg.admission == "always":
            node.cache.admit(req.hashes)
        else:
            # No remote retention, but locally computed suffix may be stored.
            node.cache.admit(req.hashes[job.hit:])
        ttft = self.now - req.arrival_ms
        self.rows.append({
            "request_id": req.request_id,
            "arrival_ms": req.arrival_ms,
            "node": node_id,
            "source": "" if job.source is None else job.source,
            "length_tokens": req.length_tokens,
            "local_hit_tokens": job.local_hit * self.cfg.block_size,
            "remote_hit_tokens": job.remote_blocks * self.cfg.block_size,
            "total_hit_tokens": job.hit * self.cfg.block_size,
            "recomputed_tokens": req.length_tokens -
                                 job.hit * self.cfg.block_size,
            "queue_ms": job.queue_ms,
            "network_ms": job.transfer_ms,
            "h2d_ms": job.load_ms,
            "compute_ms": job.compute_ms,
            "ttft_ms": ttft,
            "finished_ms": self.now,
        })
        node.active = None
        self._start(node_id)

    def run(self, requests):
        if self.events or self.rows:
            raise RuntimeError("Simulator instance can be run only once")
        for req in requests:
            if req.length_tokens <= 0 or req.arrival_ms < 0:
                raise ValueError("invalid request")
            if req.length_tokens // self.cfg.block_size < len(req.hashes):
                raise ValueError("invalid request hashes")
            self._schedule(req.arrival_ms, "arrival", req)
        while self.events:
            now, _, kind, payload, event_epoch = heapq.heappop(self.events)
            if kind == "flow_done" and event_epoch != self.epoch:
                continue
            self.network.advance(now)
            self.now = now
            if kind == "arrival":
                node_id = self._choose(payload)
                self.nodes[node_id].queue.append(payload)
                self.arrival_order.append(payload.request_id)
                self._start(node_id)
            elif kind == "flow_done":
                self._on_flow_done(payload)
            elif kind == "job_done":
                self._on_job_done(payload)
            else:
                raise RuntimeError("unknown event")
        if len(self.rows) != len(requests):
            raise RuntimeError("not all requests completed")
        if self.network.flows:
            raise RuntimeError("unfinished flows")
        return self.rows

    def summary(self):
        if not self.rows:
            raise RuntimeError("run the simulator first")
        timings = sorted(row["ttft_ms"] for row in self.rows)
        def pct(p):
            pos = (len(timings) - 1) * p
            i = int(pos)
            return (timings[i] * (1-(pos-i)) +
                    timings[min(i+1,len(timings)-1)] * (pos-i))
        total_tokens = sum(r["length_tokens"] for r in self.rows)
        hits = sum(r["total_hit_tokens"] for r in self.rows)
        makespan = max(r["finished_ms"] for r in self.rows)
        return {
            "policy": self.cfg.policy,
            "admission": self.cfg.admission,
            "requests": len(self.rows),
            "mean_ttft_ms": statistics.mean(timings),
            "p50_ttft_ms": pct(.5),
            "p95_ttft_ms": pct(.95),
            "p99_ttft_ms": pct(.99),
            "makespan_ms": makespan,
            "throughput_req_s": len(self.rows)*1000/max(makespan,1e-9),
            "hit_ratio": hits/total_tokens,
            "network_gib": self.network.total_bytes/2**30,
            "evictions": sum(n.cache.evictions for n in self.nodes.values()),
            "rejections": sum(n.cache.rejections for n in self.nodes.values()),
        }


def run_policy(specs, requests, seeds=None, config=None):
    sim = Simulator(specs, config)
    if seeds:
        for node, blocks in seeds.items():
            sim.seed_cache(node, blocks)
    sim.run(requests)
    return sim
