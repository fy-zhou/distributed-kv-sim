import unittest

from kv_sim import Config, NodeSpec, Simulator
from kv_sim.engine.events import Job, Request
from kv_sim.scheduler.shadow import _service


class FiniteRoutesTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulator([NodeSpec(i, 8, 8.) for i in range(4)],
            Config(policy='shadow_cluster_routes', fabric_gib_s=2.,
                   gpu_ms_per_new_token=.075, gpu_ms_per_attention_pair=0.,
                   h2d_fixed_ms=0., h2d_ms_per_gib=0.))
        self.req = Request('next', 0., ('a',), 16)
        self.sim.seed_cache(0, self.req.hashes)

    def test_remote_beats_recompute_after_flow_drains_during_setup(self):
        sim = self.sim
        flow = sim.network.start(0., 1, 2, sim.network.fabric*.05)
        sim.nodes[2].active = Job(self.req, 2, 0., 0., 0, 1, 1, 1, flow_id=flow)
        self.assertEqual(sim._reuse_choice(self.req, 3), (0, None, 0))
        plan = _service(sim, self.req, 3, isolated=False, compare_finite_routes=True)
        self.assertEqual(plan['source'], 0)
        sim.cfg.finite_execution_routes = True
        self.assertEqual(sim._reuse_choice(self.req, 3), (0, 0, 1))
        self.assertEqual(sim.network.flows[flow].remaining_bytes, sim.network.fabric*.05)

    def test_pending_transfer_makes_recompute_cheaper(self):
        sim = self.sim
        sim.nodes[2].active = Job(self.req, 2, 0., 0., 0, 1, 1, 1, transfer_start_ms=.05)
        self.assertEqual(sim._reuse_choice(self.req, 3), (0, 0, 1))
        plan = _service(sim, self.req, 3, isolated=False, compare_finite_routes=True)
        self.assertIsNone(plan['source'])
        sim.cfg.finite_execution_routes = True
        self.assertEqual(sim._reuse_choice(self.req, 3), (0, None, 0))
        self.assertFalse(sim.network.flows)
