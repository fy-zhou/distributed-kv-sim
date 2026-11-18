"""Closed-form invariants and counterexamples from the research audit."""
import unittest
from kv_sim import Config, NodeSpec, Request, Simulator
from kv_sim.cache import BlockCache
from kv_sim.cluster.network import FluidNetwork


class CorrectnessAuditTests(unittest.TestCase):
    def test_candidate_rate_separates_send_and_receive_counts(self):
        net = FluidNetwork({i: 100. for i in range(4)}, 10000.)
        net.start(0, 0, 1, 1000)
        net.start(0, 2, 3, 1000)
        expected = net.prospective_rate(0, 3)
        fid = net.start(0, 0, 3, 1000)
        self.assertEqual(expected, 50.)
        self.assertEqual(net.flows[fid].rate_bytes_per_ms, expected)

    def test_estimate_uses_same_endpoint_counts_as_actual_network(self):
        sim = Simulator([NodeSpec(i, 8, 1.) for i in range(4)],
                        Config(block_size=1, fabric_gib_s=100.))
        req = Request.from_tokens('r', 0, [1], block_size=1)
        sim.seed_cache(0, req.hashes)
        sim.network.start(0, 0, 1, 10000000)
        sim.network.start(0, 2, 3, 10000000)
        estimate = sim._estimate_breakdown(req, 3)
        flow_id = sim.network.start(0, 0, 3, sim.cfg.kv_bytes_per_token)
        rate = sim.network.flows[flow_id].rate_bytes_per_ms
        self.assertAlmostEqual(estimate['network_ms'],
                               sim.cfg.remote_setup_ms + sim.cfg.kv_bytes_per_token / rate)

    def test_finish_guard_does_not_weaken_with_historical_traffic(self):
        net = FluidNetwork({0: 1e12, 1: 1e12}, 1e12)
        old = net.start(0, 0, 1, 1e12)
        net.finish(1, old)
        small = net.start(1, 0, 1, 1000)
        with self.assertRaises(RuntimeError):
            net.finish(1, small)
        self.assertIn(small, net.flows)
        net.finish(1 + 1e-9, small)

    def test_network_conserves_all_full_duplex_budgets(self):
        net = FluidNetwork({i: 120. for i in range(4)}, 140.)
        for src, dst in ((0, 1), (0, 2), (2, 1), (1, 0)):
            net.start(0, src, dst, 1000)
            rates = list(net.flows.values())
            self.assertLessEqual(sum(f.rate_bytes_per_ms for f in rates), net.fabric)
            for i in net.nic:
                self.assertLessEqual(sum(f.rate_bytes_per_ms for f in rates if f.src == i), net.nic[i])
                self.assertLessEqual(sum(f.rate_bytes_per_ms for f in rates if f.dst == i), net.nic[i])

    def test_oversized_admission_retains_suffix_but_loses_prefix_root(self):
        cache = BlockCache(4)
        cache.admit(('a', 'b', 'c', 'd', 'e'))
        self.assertEqual(tuple(cache.blocks), ('b', 'c', 'd', 'e'))
        self.assertEqual(cache.prefix_len(('a', 'b', 'c', 'd')), 0)
        cache = BlockCache(4)
        cache.admit(('a', 'b', 'c', 'd'))
        cache.pin(('a', 'b', 'c', 'd'))
        cache.admit(('a', 'b', 'c', 'd', 'e'))
        self.assertEqual(cache.prefix_len(('a', 'b', 'c', 'd')), 4)
        self.assertEqual(cache.rejections, 1)

    def test_stale_events_equal_time_completion_and_ttft_decomposition(self):
        reqs = [Request.from_tokens(str(i), 0, list(range(32))) for i in range(9)]
        sim = Simulator([NodeSpec(i, 16, .01) for i in range(4)],
                        Config(policy='round_robin', fabric_gib_s=.01))
        sim.seed_cache(3, reqs[0].hashes)
        rows = sim.run(reqs)
        self.assertEqual(len({r['request_id'] for r in rows}), 9)
        self.assertEqual(sim.network.flows, {})
        for node in sim.nodes.values():
            self.assertIsNone(node.active)
            self.assertFalse(node.queue)
            self.assertFalse(node.cache.pins)
        self.assertAlmostEqual(sim.network.total_bytes,
                               sum(r['remote_hit_tokens'] for r in rows) * sim.cfg.kv_bytes_per_token)
        for row in rows:
            self.assertAlmostEqual(row['ttft_ms'], sum(row[k] for k in
                                   ('queue_ms', 'network_ms', 'h2d_ms', 'compute_ms')))


if __name__ == '__main__':
    unittest.main()
