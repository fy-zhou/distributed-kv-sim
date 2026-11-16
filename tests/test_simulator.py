import unittest
from kv_sim.prefix import prefix_hashes, BlockCache
from kv_sim.network import FluidNetwork
from kv_sim.core import Simulator, Config, NodeSpec, Request, run_policy
from kv_sim.workload import make_workload, make_initial_caches


class PrefixTests(unittest.TestCase):
    def test_hash_lineage_and_partial_block(self):
        a = prefix_hashes([1, 2, 3, 4, 5, 6], 2)
        b = prefix_hashes([1, 2, 9, 4, 5, 6], 2)
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])
        self.assertNotEqual(a[2], b[2])
        self.assertEqual(len(prefix_hashes([1, 2, 3], 2)), 1)

    def test_lru_pinning(self):
        c = BlockCache(2)
        c.admit(["a", "b"])
        c.touch(["a"])
        c.pin(["b"])
        c.admit(["c"])
        self.assertEqual(set(c.blocks), {"b", "c"})
        c.unpin(["b"])
        c.admit(["d"])
        self.assertEqual(set(c.blocks), {"c", "d"})
        self.assertEqual(c.evictions, 2)

    def test_cache_rejects_all_pinned(self):
        c = BlockCache(1)
        c.admit(["a"])
        c.pin(["a"])
        self.assertEqual(c.admit(["b"]), 0)
        self.assertEqual(c.rejections, 1)


class NetworkTests(unittest.TestCase):
    def test_shared_source_contention(self):
        n = FluidNetwork({0: 100., 1: 100., 2: 100.}, 1000.)
        x = n.start(0, 0, 1, 1000)
        self.assertEqual(n.flows[x].rate_bytes_per_ms, 100.)
        y = n.start(0, 0, 2, 1000)
        self.assertEqual(n.flows[x].rate_bytes_per_ms, 50.)
        self.assertEqual(n.flows[y].rate_bytes_per_ms, 50.)
        n.advance(10)
        self.assertAlmostEqual(n.flows[x].remaining_bytes, 500.)
        self.assertAlmostEqual(n.flows[y].remaining_bytes, 500.)
        self.assertAlmostEqual(n.next_finish(10)[0], 20.)

    def test_not_exceed_shared_fabric(self):
        n = FluidNetwork({0: 100, 1: 100, 2: 100, 3: 100}, 80)
        n.start(0, 0, 1, 1000)
        n.start(0, 2, 3, 1000)
        self.assertAlmostEqual(sum(f.rate_bytes_per_ms
                                   for f in n.flows.values()), 80)


class SimulatorTests(unittest.TestCase):
    def test_end_to_end(self):
        specs = [NodeSpec(0, 32, 1.), NodeSpec(1, 32, 1.)]
        reqs, prefixes = make_workload(
            count=25, families=2, block_size=16,
            shared_blocks=8, unique_blocks=2, arrival_ms=3)
        seeds = make_initial_caches(specs, prefixes)
        for policy in ("round_robin", "shortest_queue", "max_hit", "greedy"):
            sim = run_policy(specs, reqs, seeds, Config(
                policy=policy, fabric_gib_s=1.))
            self.assertEqual(len(sim.rows), 25)
            self.assertTrue(all(r["ttft_ms"] >= 0 for r in sim.rows))
            for r in sim.rows:
                self.assertEqual(
                    r["total_hit_tokens"] + r["recomputed_tokens"],
                    r["length_tokens"])
            self.assertEqual(len(sim.network.flows), 0)
            self.assertGreater(sim.summary()["throughput_req_s"], 0)

    def test_remote_network_load(self):
        specs = [NodeSpec(0, 8, .01), NodeSpec(1, 8, .01)]
        a = Request.from_tokens("a", 0, [1, 2, 3, 4], block_size=2)
        sim = Simulator(specs, Config(block_size=2,
                                     policy="round_robin",
                                     fabric_gib_s=.01))
        sim.seed_cache(1, a.hashes)
        # Round robin selects destination 0, must fetch from node 1.
        rows = sim.run([a])
        self.assertEqual(rows[0]["remote_hit_tokens"], 4)
        self.assertGreater(rows[0]["network_ms"], 0)
        self.assertAlmostEqual(
            sim.network.total_bytes, 4*sim.cfg.kv_bytes_per_token)
        self.assertEqual(sim.nodes[1].cache.pins, {})
        self.assertIn(a.hashes[0], sim.nodes[0].cache.blocks)


if __name__ == "__main__":
    unittest.main()
