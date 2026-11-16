"""Regression tests for auditable hot-prefix workload experiments."""
import unittest
from kv_sim.core import Config, NodeSpec, Simulator, run_policy
from kv_sim.workload.hotspot import (
    make_hotspot_workload, make_hotspot_initial_caches)


class HotspotTests(unittest.TestCase):
    def test_initial_caches_are_full_and_source_has_prefix(self):
        specs = [NodeSpec(i, 32, 1.) for i in range(3)]
        requests, hot = make_hotspot_workload(
            count=20, shared_blocks=12, suffix_blocks=2, seed=7)
        seed = make_hotspot_initial_caches(specs, hot, occupancy=1.)
        self.assertTrue(all(len(v) == 32 for v in seed.values()))
        self.assertEqual(seed[0][:len(hot)], list(hot))
        self.assertFalse(any(h in seed[1] for h in hot))
        self.assertEqual(len(requests), 20)

    def test_deterministic_burst_arrivals(self):
        a, _ = make_hotspot_workload(
            count=16, shared_blocks=8, burst_width=4, seed=31)
        b, _ = make_hotspot_workload(
            count=16, shared_blocks=8, burst_width=4, seed=31)
        self.assertEqual(a, b)
        self.assertEqual(a[0].arrival_ms, 0.0)
        self.assertEqual(a[4].arrival_ms, 2.0)

    def test_decision_trace_has_exactly_one_winner(self):
        specs = [NodeSpec(i, 24, .1) for i in range(3)]
        reqs, hot = make_hotspot_workload(
            count=15, shared_blocks=8, burst_width=3, seed=4)
        seed = make_hotspot_initial_caches(specs, hot)
        for policy in ("greedy", "bandwidth_aware"):
            sim = run_policy(specs, reqs, seed, Config(
                policy=policy, fabric_gib_s=.1,
                trace_decisions=True))
            self.assertEqual(len(sim.decisions), 45)
            for req in reqs:
                rows = [r for r in sim.decisions
                        if r["request_id"] == req.request_id]
                self.assertEqual(sum(r["selected"] for r in rows), 1)
                for row in rows:
                    self.assertGreaterEqual(row["estimated_ms"], 0)
                    self.assertGreaterEqual(row["network_externality_ms"], 0)
            self.assertEqual(len(sim.rows), len(reqs))

    def test_externality_for_busy_source_nonnegative(self):
        specs = [NodeSpec(i, 24, .1) for i in range(3)]
        reqs, hot = make_hotspot_workload(
            count=3, shared_blocks=8, burst_width=3)
        sim = Simulator(specs, Config(
            policy="bandwidth_aware", fabric_gib_s=.1))
        sim.seed_cache(0, hot)
        sim.network.start(0, 0, 1, 1000000)
        sim.network.advance(0)
        # The third node can fetch a shared prefix from node 0,
        # competing with the already-running 0->1 transfer.
        self.assertGreaterEqual(sim._externality_ms(reqs[0], 2), 0.0)
        self.assertGreater(sim._externality_ms(reqs[0], 2), 0.0)


if __name__ == "__main__":
    unittest.main()
