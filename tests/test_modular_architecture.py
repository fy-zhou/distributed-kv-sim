"""Regression tests for the modular package boundary (Python >= 3.8)."""
import csv
import json
import os
import tempfile
import unittest

from kv_sim import Request, Simulator, Config, NodeSpec
from kv_sim.cache import BlockCache, prefix_hashes, admitted_blocks
from kv_sim.cluster.network import FluidNetwork
from kv_sim.engine.clock import SimulationClock
from kv_sim.metrics.collector import summarize
from kv_sim.predictor import ComputePredictor, H2DPredictor, TransferPredictor
from kv_sim.scheduler import POLICIES
from kv_sim.workload import make_workload, make_initial_caches, load_token_trace


class ModularArchitectureTests(unittest.TestCase):
    def test_imports_and_policies(self):
        for policy in ("round_robin", "shortest_queue", "max_hit",
                       "greedy", "bandwidth_aware"):
            self.assertIn(policy, POLICIES)
        self.assertIsInstance(ComputePredictor(), ComputePredictor)
        self.assertIsInstance(H2DPredictor(), H2DPredictor)
        self.assertIsInstance(TransferPredictor(), TransferPredictor)

    def test_clock_monotonic(self):
        clock = SimulationClock()
        clock.advance(10.0)
        self.assertEqual(clock.now_ms, 10.0)
        with self.assertRaises(ValueError):
            clock.advance(9.0)

    def test_admission_is_explicit(self):
        hashes = ("a", "b", "c")
        self.assertEqual(admitted_blocks(hashes, 0, "no_remote"), hashes)
        self.assertEqual(admitted_blocks(hashes, 2, "no_remote"), ())
        self.assertEqual(admitted_blocks(hashes, 2, "always"), hashes)

    def test_csv_trace_replay(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "trace.csv")
            with open(p, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=[
                    "request_id", "arrival_ms", "tokens"])
                w.writeheader()
                w.writerow({"request_id": "r1", "arrival_ms": 0.5,
                            "tokens": json.dumps([1, 2, 3, 4])})
            reqs = load_token_trace(p, block_size=2)
            self.assertEqual(reqs[0].length_tokens, 4)
            self.assertEqual(len(reqs[0].hashes), 2)

    def test_all_policies_end_to_end(self):
        specs = [NodeSpec(i, 64, 1.) for i in range(3)]
        reqs, prefixes = make_workload(
            count=60, families=3, shared_blocks=8,
            unique_blocks=2, arrival_ms=2, seed=17)
        seed = make_initial_caches(specs, prefixes, seed=17)
        for policy in POLICIES:
            from kv_sim import run_policy
            sim = run_policy(specs, reqs, seed,
                             Config(policy=policy, fabric_gib_s=1.))
            self.assertEqual(len(sim.rows), 60)
            self.assertEqual(sim.summary()["requests"], 60)
            self.assertEqual(len(sim.network.flows), 0)
            self.assertIs(sim.nodes[0].cache.__class__, BlockCache)
            self.assertTrue(all(r["recomputed_tokens"] +
                                r["total_hit_tokens"] == r["length_tokens"]
                                for r in sim.rows))


if __name__ == "__main__":
    unittest.main()
