from dataclasses import replace
import unittest
from unittest.mock import patch

from kv_sim import Config, NodeSpec, Request, Simulator
from kv_sim.scheduler.reuse import profitable_prefix_floor


class ReusePrefixFloorTests(unittest.TestCase):
    def test_absence_of_threshold_block_proves_recompute(self):
        sim = Simulator([NodeSpec(i, 8, 100.) for i in range(2)],
            Config(policy='round_robin_cost', reuse_prefix_floor=True,
                   gpu_ms_per_new_token=.1, gpu_ms_per_attention_pair=0.,
                   h2d_fixed_ms=5., h2d_ms_per_gib=0.))
        req = Request('x', 0., tuple('abcdef'), 96)
        self.assertEqual(profitable_prefix_floor(sim, req), 4)
        sim.seed_cache(0, ('a', 'b', 'c', 'f'))
        sim.seed_cache(1, ('a', 'b', 'c'))
        with patch.object(sim, '_hit', side_effect=AssertionError('must skip prefix scan')):
            self.assertEqual(sim._reuse_choice(req, 0), (0, None, 0))
        sim.seed_cache(0, ('d',))
        self.assertEqual(sim._reuse_choice(req, 0), (4, None, 4))
        self.assertEqual(sim._reuse_choice(req, 1), (3, 0, 4))
        sim.compute_predictor = replace(sim.compute_predictor, ms_per_new_token=.05)
        self.assertEqual(sim._reuse_choice(req, 0), (0, None, 0))

    def test_exact_tie_and_orphan_threshold_block_fall_back_to_exact_scan(self):
        sim = Simulator([NodeSpec(0, 8)],
            Config(policy='round_robin_cost', block_size=1, reuse_prefix_floor=True,
                   gpu_fixed_ms=0., gpu_ms_per_new_token=1., gpu_ms_per_attention_pair=0.,
                   h2d_fixed_ms=3., h2d_ms_per_gib=0.))
        req = Request('x', 0., tuple('abcd'), 4)
        self.assertEqual(profitable_prefix_floor(sim, req), 3)
        sim.seed_cache(0, ('b', 'c', 'd'))
        self.assertEqual(sim._reuse_choice(req, 0), (0, None, 0))
        sim.seed_cache(0, ('a',))
        self.assertEqual(sim._reuse_choice(Request('tie', 0., tuple('abc'), 4), 0),
                         (3, None, 3))
