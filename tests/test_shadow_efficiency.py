import unittest

from kv_sim import Config, NodeSpec, Request, Simulator
from kv_sim.scheduler.shadow import forecast
from kv_sim.scheduler.shadow_network import active_remaining


class ForecastEfficiencyTests(unittest.TestCase):
    def test_fast_forecast_matches_reference_with_active_and_queued_jobs(self):
        sim = Simulator([NodeSpec(0, 520)], Config(policy='shadow_fast'))
        first = Request.from_tokens('first', 0, list(range(4160)))
        second = Request.from_tokens('second', 0, list(range(4050)))
        node = sim.nodes[0]
        node.queue.extend((first, second))
        sim._start(0)
        self.assertEqual(forecast(sim, first, 0),
                         forecast(sim, first, 0, cold_memo={}))

    def test_fast_forecast_falls_back_if_queued_reuse_can_win(self):
        sim = Simulator([NodeSpec(0, 520)], Config(policy='shadow_fast'))
        long = Request.from_tokens('long', 0, list(range(8200)))
        short = Request.from_tokens('short', 0, list(range(160)))
        sim.seed_cache(0, long.hashes)
        sim.nodes[0].queue.append(long)
        memo = {}
        self.assertEqual(forecast(sim, short, 0),
                         forecast(sim, short, 0, cold_memo=memo))
        self.assertIsNone(memo[(long.length_tokens, len(long.hashes))])

    def test_known_setups_share_fabric_and_replay_does_not_modify_live_state(self):
        sim = Simulator([NodeSpec(i, 32, 1.) for i in range(3)],
                        Config(policy='shadow_network', fabric_gib_s=1.,
                               gpu_ms_per_new_token=1., remote_setup_ms=2.))
        req = Request.from_tokens('r', 0, list(range(160)))
        sim.seed_cache(2, req.hashes)
        for node in (0, 1):
            sim.nodes[node].queue.append(req)
            sim._start(node)
        before = list(sim.events)
        predicted = active_remaining(sim)
        for node in (0, 1):
            job = sim.nodes[node].active
            payload = job.remote_blocks * sim.cfg.block_size * sim.cfg.kv_bytes_per_token
            expected = 2. + 2 * payload / sim.network.fabric
            expected += sim._h2d_time(job.hit) + sim._compute_time(req, job.hit)
            self.assertAlmostEqual(predicted[node], expected)
        self.assertEqual(sim.events, before)
        self.assertFalse(sim.network.flows)
