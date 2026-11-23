from copy import deepcopy
import unittest
from unittest.mock import patch

from kv_sim import Config, NodeSpec, Request, Simulator
from kv_sim.scheduler import shadow_cluster


class LazyRolloutTests(unittest.TestCase):
    def test_warm_idle_candidates_use_live_read_only_state(self):
        sim = Simulator([NodeSpec(i, 16) for i in range(3)],
            Config(policy='shadow_cluster_lazy', gpu_ms_per_new_token=1.))
        req = Request.from_tokens('next', 0., list(range(160)))
        sim.seed_cache(1, req.hashes)
        before = deepcopy((sim.nodes, sim.events, sim.network.__dict__))
        expected = shadow_cluster.forecasts(sim, req, sim.nodes, finite_service=True)
        with patch.object(shadow_cluster, '_fork', side_effect=AssertionError('idle fork')):
            actual = shadow_cluster.forecasts(
                sim, req, sim.nodes, finite_service=True, lazy_fork=True)
        self.assertEqual(expected, actual)
        self.assertEqual(actual[1]['total_hit_blocks'], 10)
        self.assertEqual(before, (sim.nodes, sim.events, sim.network.__dict__))

    def test_mixed_readiness_preserves_forecast_and_does_not_drain_live_events(self):
        sim = Simulator([NodeSpec(i, 16) for i in range(3)],
            Config(policy='shadow_cluster_lazy', gpu_ms_per_new_token=1.))
        req = Request.from_tokens('next', 0., list(range(160)))
        sim.seed_cache(1, req.hashes)
        sim.nodes[0].queue.append(req)
        sim._start(0)
        before = deepcopy((sim.nodes, sim.events, sim.service_events, sim.network.__dict__))
        expected = shadow_cluster.forecasts(sim, req, sim.nodes, finite_service=True)
        actual = shadow_cluster.forecasts(
            sim, req, sim.nodes, finite_service=True, lazy_fork=True)
        self.assertEqual(expected, actual)
        self.assertGreater(actual[0]['queue_ms'], 0.)
        self.assertEqual(before, (sim.nodes, sim.events, sim.service_events, sim.network.__dict__))
