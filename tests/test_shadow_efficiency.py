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

    def test_active_flow_speeds_up_after_short_competitor_finishes(self):
        sim = Simulator([NodeSpec(i, 32, 1.) for i in range(3)],
                        Config(policy='shadow_network', fabric_gib_s=1.,
                               gpu_ms_per_new_token=1., remote_setup_ms=0.))
        long = Request.from_tokens('long', 0, list(range(160)))
        short = Request.from_tokens('short', 0, list(range(16)))
        sim.seed_cache(2, long.hashes)
        for node, req in enumerate((long, short)):
            job = sim.nodes[node]
            job.queue.append(req)
            sim._start(node)
            size = job.active.remote_blocks*sim.cfg.block_size*sim.cfg.kv_bytes_per_token
            job.active.flow_id = sim.network.start(0., 2, node, size)
        # A future heap event is not an input to the predictor.
        sim._schedule(.001, 'arrival', long)
        network_state = [(f.remaining_bytes, f.rate_bytes_per_ms)
                         for f in sim.network.flows.values()]
        predicted = active_remaining(sim)
        job = sim.nodes[0].active
        long_bytes = sim.network.flows[job.flow_id].remaining_bytes
        short_bytes = sim.network.flows[sim.nodes[1].active.flow_id].remaining_bytes
        expected = (long_bytes + short_bytes)/sim.network.fabric
        expected += sim._h2d_time(job.hit) + sim._compute_time(job.req, job.hit)
        self.assertAlmostEqual(predicted[0], expected)
        self.assertLess(predicted[0], sim._remaining_active_ms(sim.nodes[0]))
        self.assertEqual(network_state, [(f.remaining_bytes, f.rate_bytes_per_ms)
                                         for f in sim.network.flows.values()])

    def test_ingress_delay_counts_toward_original_arrival_ttft(self):
        from scripts.research_shadow import trial
        scenario = dict(name='small', nodes=2, shared=4, capacity=16)
        sim, _ = trial(scenario, 31, 'always', 'round_robin_cost', 8,
                       ingress_ms=1.)
        first = min(sim.rows, key=lambda row: row['arrival_ms'])
        self.assertEqual(first['arrival_ms'], 0.)
        self.assertAlmostEqual(first['queue_ms'], 1.)
        self.assertAlmostEqual(first['ttft_ms'], first['finished_ms'])
