import unittest

from kv_sim import Config, NodeSpec, Request
from scripts.research_common import assert_invariants
from scripts.research_spill_sensitivity import SpillSimulator


class SpillTests(unittest.TestCase):
    def test_first_token_precedes_spill_but_next_job_waits_for_admission(self):
        sim = SpillSimulator([NodeSpec(0, 16)], Config(policy='round_robin_cost',
            gpu_ms_per_new_token=1.), audit=False, spill_gib_s=16.)
        requests = [Request.from_tokens(str(i), 0, list(range(160))) for i in range(2)]
        first, second = sim.run(requests)
        expected_spill = 160*sim.cfg.kv_bytes_per_token/2**30/16*1000
        self.assertAlmostEqual(first['ttft_ms'], first['compute_ms'])
        self.assertAlmostEqual(first['spill_ms'], expected_spill)
        self.assertAlmostEqual(second['queue_ms'], first['ttft_ms']+expected_spill)
        self.assertEqual(second['local_hit_tokens'], 160)
        self.assertEqual(second['spill_bytes'], 0)
        assert_invariants(sim)
