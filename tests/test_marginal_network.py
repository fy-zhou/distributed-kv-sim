import unittest

from kv_sim import Config, NodeSpec, Request, Simulator
from kv_sim.engine.events import Job
from kv_sim.scheduler.shadow_network import known_flow_externality_ms


class MarginalNetworkTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulator([NodeSpec(i, 16, 8.) for i in range(4)],
            Config(policy='shadow_cluster_marginal', fabric_gib_s=2.,
                gpu_ms_per_new_token=.2, gpu_ms_per_attention_pair=0.,
                h2d_fixed_ms=0., h2d_ms_per_gib=0., network_externality_weight=.25))
        self.req = Request('next', 0., ('p',), 16)
        self.sim.seed_cache(0, self.req.hashes)

    def test_marginal_penalty_charges_only_after_setup_and_preserves_ttft_forecast(self):
        sim = self.sim
        size = 16*sim.cfg.kv_bytes_per_token
        flow = sim.network.start(0., 1, 2, size)
        sim.nodes[2].active = Job(self.req, 2, 0., 0., 0, 1, 1, 1, flow_id=flow)
        expected = size/sim.network.fabric-sim.cfg.remote_setup_ms
        self.assertAlmostEqual(known_flow_externality_ms(sim, 0, 3, size), expected)
        pred = sim._estimate_breakdown(self.req, 3)
        self.assertGreater(pred['remote_blocks'], 0)
        self.assertAlmostEqual(pred['known_flow_externality_ms'], expected)
        self.assertAlmostEqual(pred['selection_ms']-pred['estimated_ms'], .25*expected)
        self.assertEqual(sim.network.flows[flow].remaining_bytes, size)

    def test_completed_flow_before_setup_has_zero_penalty(self):
        sim = self.sim
        size = sim.network.fabric*sim.cfg.remote_setup_ms/2
        flow = sim.network.start(0., 1, 2, size)
        sim.nodes[2].active = Job(self.req, 2, 0., 0., 0, 1, 1, 1, flow_id=flow)
        self.assertEqual(known_flow_externality_ms(sim, 0, 3, 20*size), 0.)
