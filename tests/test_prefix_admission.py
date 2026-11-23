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

    def test_fit_and_skip_remote_compose_independently(self):
        from kv_sim.cache.admission import admitted_blocks
        blocks = ('a', 'b', 'c')
        self.assertEqual(admitted_blocks(blocks, 0, 'prefix_fit_no_remote', capacity=2),
                         ('a', 'b'))
        self.assertEqual(admitted_blocks(blocks, 1, 'prefix_fit_no_remote', capacity=2), ())
        self.assertEqual(admitted_blocks(blocks, 1, 'prefix_fit', capacity=2), ('a', 'b'))

    def test_joint_protocol_uses_resolved_capacity_for_defaulted_shapes(self):
        import csv
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from scripts import research_prefix_fit, research_rollout_efficiency

        scenario = dict(name='small', nodes=2, shared=4)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/'fresh'
            with patch.object(research_rollout_efficiency, 'STRESS', (scenario,)), \
                 patch.object(research_prefix_fit, 'JOINT_SEEDS', {'development': (190,)}), \
                 patch('sys.argv', ['runner', '--split', 'development', '--joint-stress',
                                    '--out', str(out)]):
                research_prefix_fit.main()
            with (out/'runs.csv').open(newline='') as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 6)
            self.assertTrue((out/'placement_pairs.csv').exists())
