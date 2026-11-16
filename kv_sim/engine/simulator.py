"""Event-driven multi-node CPU KV reuse simulator.

One active request per node (v1). Prefill and local PCIe loading are
serialized in the default model. Remote transfer progresses concurrently
across nodes under a fluid shared-NIC/fabric capacity constraint.
"""
import heapq
from dataclasses import dataclass

from ..cache import BlockCache, admitted_blocks
from ..cluster.node import Node, NodeSpec
from ..cluster.network import FluidNetwork
from ..cluster.gpu import is_gpu_idle
from ..predictor import ComputePredictor, H2DPredictor, TransferPredictor
from ..scheduler import choose_node
from ..metrics.collector import summarize
from .events import (Request, Job, EVENT_ARRIVAL, EVENT_FLOW_START,
                     EVENT_FLOW_DONE, EVENT_JOB_DONE)
from .clock import SimulationClock

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
        self.compute_predictor = ComputePredictor(
            fixed_ms=self.cfg.gpu_fixed_ms,
            ms_per_new_token=self.cfg.gpu_ms_per_new_token,
            ms_per_causal_pair=self.cfg.gpu_ms_per_attention_pair)
        self.h2d_predictor = H2DPredictor(
            kv_bytes_per_token=self.cfg.kv_bytes_per_token,
            fixed_ms=self.cfg.h2d_fixed_ms,
            ms_per_gib=self.cfg.h2d_ms_per_gib)
        self.transfer_predictor = TransferPredictor(
            setup_ms=self.cfg.remote_setup_ms)
        self.network = FluidNetwork(
            {s.node_id: s.nic_gib_s * (2**30) / 1000 for s in specs},
            self.cfg.fabric_gib_s * (2**30) / 1000)
        self.events = []
        self.sequence = 0
        self.now = 0.0
        self.clock = SimulationClock()
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
        return self.compute_predictor.predict_ms(req.length_tokens, h)

    def _h2d_time(self, hit_blocks):
        return self.h2d_predictor.predict_ms(
            hit_blocks * self.cfg.block_size)

    def _remaining_active_ms(self, node):
        """Use scheduled compute completion, not a full restart estimate."""
        job = node.active
        if job is None:
            return 0.0
        if job.expected_finish_ms is not None:
            return max(0.0, job.expected_finish_ms - self.now)
        # Remote transfer is in setup or in progress; include remaining
        # transfer, then local H2D and prefill (no lookahead into contention).
        remote_remaining = 0.0
        if job.flow_id is not None:
            flow = self.network.flows[job.flow_id]
            remote_remaining = flow.remaining_bytes / flow.rate_bytes_per_ms
        elif job.remote_blocks:
            payload = (job.remote_blocks * self.cfg.block_size
                       * self.cfg.kv_bytes_per_token)
            rate = min(self.network.nic[job.source],
                       self.network.nic[job.target], self.network.fabric)
            setup_left = (max(0.0, job.transfer_start_ms - self.now)
                          if job.transfer_start_ms is not None else
                          self.cfg.remote_setup_ms)
            remote_remaining = setup_left + payload / rate
        return (remote_remaining + self._h2d_time(job.hit)
                + self._compute_time(job.req, job.hit))

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
            remote_ms = self.transfer_predictor.predict_ms(size, rate)
        own = remote_ms + self._h2d_time(n) + self._compute_time(req, n)
        waiting = self._remaining_active_ms(node)
        # Account for already queued jobs using their estimated *own*
        # costs, including remote fetch and H2D, without recursively
        # counting the same queue. Cache evolution remains unknown.
        for pending in node.queue:
            pending_local, pending_source, pending_n = self._best_source(
                pending, node_id)
            pending_remote = pending_n - pending_local if pending_source is not None else 0
            if pending_remote:
                payload = (pending_remote * self.cfg.block_size
                           * self.cfg.kv_bytes_per_token)
                rate = min(self.network.nic[pending_source],
                           self.network.nic[node_id], self.network.fabric)
                waiting += self.transfer_predictor.predict_ms(payload, rate)
            waiting += self._h2d_time(pending_n) + self._compute_time(
                pending, pending_n)
        return waiting + own

    def _choose(self, req):
        return choose_node(self, req)

    def _refresh_flow_event(self):
        self.epoch += 1
        next_flow = self.network.next_finish(self.now)
        if next_flow is not None:
            t, flow_id = next_flow
            self._schedule(t, EVENT_FLOW_DONE, flow_id, self.epoch)

    def _start(self, node_id):
        node = self.nodes[node_id]
        if not is_gpu_idle(node) or not node.queue:
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
            # Setup latency is an explicit event, not a post-hoc subtraction.
            job.transfer_start_ms = self.now + self.cfg.remote_setup_ms
            self._schedule(job.transfer_start_ms, EVENT_FLOW_START,
                           (node_id, source, bytes_to_copy))
        else:
            self._start_compute(job)

    def _start_compute(self, job):
        job.load_ms = self._h2d_time(job.hit)
        job.compute_ms = self._compute_time(job.req, job.hit)
        job.expected_finish_ms = self.now + job.load_ms + job.compute_ms
        self._schedule(job.expected_finish_ms, EVENT_JOB_DONE, job.target)

    def _on_flow_done(self, flow_id):
        job = next((node.active for node in self.nodes.values()
                    if node.active is not None
                    and node.active.flow_id == flow_id), None)
        if job is None:
            raise RuntimeError("orphaned transfer")
        flow = self.network.finish(self.now, flow_id)
        keys = job.req.hashes[job.local_hit:job.hit]
        self.nodes[flow.src].cache.unpin(keys)
        job.transfer_ms = self.now - job.start_ms
        job.flow_id = None
        self._start_compute(job)
        self._refresh_flow_event()

    def _on_job_done(self, node_id):
        node = self.nodes[node_id]
        job = node.active
        if job is None:
            raise RuntimeError("job completion without active job")
        req = job.req
        node.cache.admit(admitted_blocks(req.hashes, job.remote_blocks,
                                         self.cfg.admission))
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
            self._schedule(req.arrival_ms, EVENT_ARRIVAL, req)
        while self.events:
            now, _, kind, payload, event_epoch = heapq.heappop(self.events)
            if kind == EVENT_FLOW_DONE and event_epoch != self.epoch:
                continue
            self.network.advance(now)
            self.clock.advance(now)
            self.now = now
            if kind == EVENT_ARRIVAL:
                node_id = self._choose(payload)
                self.nodes[node_id].queue.append(payload)
                self.arrival_order.append(payload.request_id)
                self._start(node_id)
            elif kind == EVENT_FLOW_START:
                target, source, size_bytes = payload
                job = self.nodes[target].active
                if job is None or job.source != source or job.flow_id is not None:
                    raise RuntimeError("invalid transfer setup event")
                job.flow_id = self.network.start(self.now, source, target,
                                                 size_bytes)
                self._refresh_flow_event()
            elif kind == "flow_done":
                self._on_flow_done(payload)
            elif kind == EVENT_JOB_DONE:
                self._on_job_done(payload)
            else:
                raise RuntimeError("unknown event")
        if len(self.rows) != len(requests):
            raise RuntimeError("not all requests completed")
        if self.network.flows:
            raise RuntimeError("unfinished flows")
        return self.rows

    def summary(self):
        return summarize(self)


def run_policy(specs, requests, seeds=None, config=None):
    sim = Simulator(specs, config)
    if seeds:
        for node, blocks in seeds.items():
            sim.seed_cache(node, blocks)
    sim.run(requests)
    return sim
