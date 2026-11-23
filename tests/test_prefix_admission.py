import unittest

from kv_sim import Config, NodeSpec, Request, Simulator
from kv_sim.scheduler.shadow import forecast


class PrefixAdmissionTests(unittest.TestCase):
    def test_capacity_fit_keeps_reusable_root_after_oversized_request(self):
        requests = [Request.from_tokens(str(i), 200*i, list(range(48))) for i in range(2)]
        legacy = Simulator([NodeSpec(0, 2)], Config(policy='round_robin_cost',
                           gpu_ms_per_new_token=1.))
        candidate = Simulator([NodeSpec(0, 2)], Config(policy='round_robin_cost',
                              gpu_ms_per_new_token=1., admission='prefix_fit'))
        self.assertEqual(legacy.run(requests)[1]['local_hit_tokens'], 0)
        self.assertEqual(candidate.run(requests)[1]['local_hit_tokens'], 32)
        self.assertTrue(candidate.nodes[0].cache.contains(requests[0].hashes[0]))

    def test_shadow_uses_same_capacity_fit_admission_for_active_request(self):
        req = Request.from_tokens('r', 0., list(range(48)))
        sim = Simulator([NodeSpec(0, 2)], Config(policy='shadow_cache',
                        gpu_ms_per_new_token=1., admission='prefix_fit'))
        sim.nodes[0].queue.append(req)
        sim._start(0)
        score = forecast(sim, req, 0)
        self.assertEqual(score['total_hit_blocks'], 2)
        self.assertFalse(sim.nodes[0].cache.blocks)
