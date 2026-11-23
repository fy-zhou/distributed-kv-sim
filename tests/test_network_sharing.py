import unittest

from kv_sim import Config, NodeSpec, Simulator
from kv_sim.cluster.network import FluidNetwork
from kv_sim.scheduler.shadow_network import _known_completions
from kv_sim.engine.events import Job, Request


class MaxMinSharingTests(unittest.TestCase):
    def test_unused_fabric_share_is_redistributed(self):
        caps = {0: 1., 1: 8., 2: 8., 3: 8.}
        for model, expected in (('equal_share', 4.), ('max_min', 7.)):
            with self.subTest(model=model):
                network = FluidNetwork(caps, 8., sharing=model)
                slow = network.start(0., 0, 2, 10.)
                self.assertEqual(network.prospective_rate(1, 3), expected)
                fast = network.start(0., 1, 3, 28.)
                self.assertEqual(network.flows[slow].rate_bytes_per_ms, 1.)
                self.assertEqual(network.next_finish(0.), (28./expected, fast))
                network.finish(28./expected, fast)
                self.assertEqual(network.next_finish(28./expected), (10., slow))

    def test_endpoint_and_fabric_bounds_and_independent_duplex(self):
        network = FluidNetwork({0: 4., 1: 8., 2: 8., 3: 8.}, 11., sharing='max_min')
        ids = [network.start(0., src, dst, 40.) for src, dst in
               ((0, 1), (0, 2), (1, 0), (2, 3))]
        self.assertEqual([network.flows[i].rate_bytes_per_ms for i in ids], [2., 2., 3.5, 3.5])
        self.assertEqual(network.prospective_rate(3, 2), 7./3)
        self.assertGreater(network.externality_ms(3, 2), 0.)
        ids.append(network.start(0., 3, 2, 40.))
        self.assertAlmostEqual(sum(f.rate_bytes_per_ms for f in network.flows.values()), 11.)
        for nic, capacity in network.nic.items():
            for direction in ('src', 'dst'):
                self.assertLessEqual(sum(f.rate_bytes_per_ms for f in network.flows.values()
                                         if getattr(f, direction) == nic), capacity)

    def test_forecast_preserves_active_allocator(self):
        specs = [NodeSpec(i, 1, rate) for i, rate in enumerate((1., 8., 8., 8.))]
        sim = Simulator(specs, Config(network_sharing='max_min', fabric_gib_s=8.))
        b = sim.network.nic[0]
        flow = sim.network.start(0., 0, 2, 10*b)
        req = Request('ongoing', 0., ('a',), 16)
        sim.nodes[2].active = Job(req, 2, 0., 0., 0, 1, 0, 1, flow_id=flow)
        predicted = list(_known_completions(sim, (1, 3, 28*b)))
        self.assertEqual([target for target, _ in predicted], [3, 2])
        self.assertAlmostEqual(predicted[0][1], sim.cfg.remote_setup_ms+4.)
        self.assertAlmostEqual(predicted[1][1], 10.)
