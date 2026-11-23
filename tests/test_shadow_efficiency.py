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

    def test_incremental_private_lru_matches_batch_order_for_oversized_request(self):
        from kv_sim.cache import BlockCache
        from kv_sim.scheduler.shadow import complete_cache, complete_unpinned_lru
        from itertools import product
        for keys in product('abcd', repeat=5):
            a, b = BlockCache(3), BlockCache(3)
            for cache in (a, b):
                cache.admit('bza')
            req = Request('r', 0., keys, 80)
            complete_cache(a, req, 0, 1, 'always')
            complete_unpinned_lru(b, req, 0, 1, 'always')
            self.assertEqual(list(a.blocks), list(b.blocks))

    def test_cluster_rollout_predicts_known_queue_and_does_not_dispatch_arrivals(self):
        from copy import deepcopy
        from kv_sim.scheduler.shadow_cluster import forecasts

        sim = Simulator([NodeSpec(i, 32, 1.) for i in range(3)],
                        Config(policy='shadow_cluster', fabric_gib_s=1.,
                               gpu_ms_per_new_token=1., remote_setup_ms=2.))
        active = Request.from_tokens('active', 0, list(range(160)))
        queued = Request.from_tokens('queued', 0, list(range(160)))
        incoming = Request.from_tokens('incoming', 0, list(range(160)))
        sim.seed_cache(2, active.hashes)
        for node in (0, 1):
            sim.nodes[node].queue.append(active)
            sim._start(node)
        sim.nodes[0].queue.append(queued)
        before = forecasts(sim, incoming, sim.nodes)
        sim._schedule(.01, 'arrival', incoming)
        state = deepcopy((sim.events, sim.nodes, sim.network.__dict__))
        self.assertEqual(before, forecasts(sim, incoming, sim.nodes))
        self.assertEqual(state, (sim.events, sim.nodes, sim.network.__dict__))
        self.assertEqual(before[0]['local_blocks'], 10)
        service = sim._h2d_time(10) + sim._compute_time(active, 10)
        payload = 160 * sim.cfg.kv_bytes_per_token
        self.assertAlmostEqual(before[0]['queue_ms'],
                               2. + 2*payload/sim.network.fabric + 2*service)

    def test_cluster_pruning_skips_late_node_but_preserves_winner(self):
        from kv_sim.scheduler.shadow_cluster import forecasts
        sim = Simulator([NodeSpec(i, 32) for i in range(3)],
                        Config(policy='shadow_cluster_pruned', gpu_ms_per_new_token=1.))
        long = Request.from_tokens('long', 0., list(range(160)))
        short = Request.from_tokens('short', 0., list(range(16)))
        sim.nodes[0].queue.append(long)
        sim._start(0)
        full = forecasts(sim, short, sim.nodes)
        pruned = forecasts(sim, short, sim.nodes, prune=True)
        self.assertEqual(set(pruned), {1, 2})
        winner = lambda plans: min(plans, key=lambda i: (plans[i]['estimated_ms'], i))
        self.assertEqual(winner(full), winner(pruned))
        self.assertEqual(full[1], pruned[1])

    def test_cluster_cold_shortcut_matches_absolute_event_timestamps(self):
        from kv_sim.scheduler.shadow_cluster import cold_forecasts, forecasts
        sim = Simulator([NodeSpec(i, 32) for i in range(2)], Config(policy='shadow_cluster_fast'))
        req = Request.from_tokens('r', .14, list(range(4160)))
        sim.now = .14
        for node in sim.nodes.values():
            node.queue.extend([req]*3)
            sim._start(node.spec.node_id)
        self.assertEqual(cold_forecasts(sim, req, sim.nodes),
                         forecasts(sim, req, sim.nodes))

    def test_efficiency_runner_keeps_prepared_workload_out_of_results(self):
        import csv
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from scripts import research_rollout_efficiency as runner

        scenario = dict(name='small', nodes=2, shared=4, capacity=16)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/'fresh'
            with patch.object(runner, 'SCENARIOS', [scenario]), \
                 patch.object(runner, 'SEEDS', {'development': (31,)}), \
                 patch('sys.argv', ['runner', '--split', 'development', '--out', str(out)]):
                runner.main()
            with (out/'runs.csv').open(newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 10)
            self.assertNotIn('prepared', rows[0])
            self.assertLess((out/'runs.csv').stat().st_size, 16000)

    def test_cluster_lru_matches_live_cache_through_pins_and_evictions(self):
        from kv_sim.cache import BlockCache
        from kv_sim.scheduler.shadow_cluster import _ForecastCache

        a, b = BlockCache(3), _ForecastCache(3)
        for cache in (a, b):
            cache.admit('abc')
            cache.pin(('b',))
            cache.admit('def')
            cache.unpin(('b',))
            cache.admit('dggca')
        self.assertEqual(a.__dict__, b.__dict__)

    def test_indexed_private_cache_matches_pinned_admission(self):
        from itertools import product
        from kv_sim.cache import BlockCache
        from kv_sim.scheduler.shadow_cluster import _ForecastIndexedCache, _ForecastGatedCache
        for pinned in ('a', 'ab', 'abc'):
            for keys in product('abcx', repeat=4):
                caches = (BlockCache(3), _ForecastIndexedCache(3), _ForecastGatedCache(3))
                for cache in caches:
                    cache.admit('abc')
                    cache.pin(pinned)
                counts = [cache.admit(keys) for cache in caches]
                self.assertEqual(counts, [counts[0]]*3)
                for cache in caches[1:]:
                    self.assertEqual(caches[0].__dict__, cache.__dict__, pinned)

    def test_cheap_queue_forecast_counts_each_pending_request_without_cache_updates(self):
        from kv_sim.scheduler.shadow import cold_queue_forecasts
        sim = Simulator([NodeSpec(0, 32)], Config(policy='shadow_cold_queue'))
        short = Request.from_tokens('short', 0., list(range(160)))
        long = Request.from_tokens('long', 0., list(range(320)))
        sim.nodes[0].queue.extend((short, long, short))
        score = cold_queue_forecasts(sim, short, sim.nodes)[0]
        self.assertAlmostEqual(score['queue_ms'],
                               2*sim._compute_time(short, 0)+sim._compute_time(long, 0))
        self.assertEqual(score['cache_updates'], 0)

    def test_proposed_transfer_recovers_bandwidth_after_known_incumbent_completes(self):
        from kv_sim.scheduler.shadow_network import proposed_transfer_ms
        sim = Simulator([NodeSpec(i, 16, 1.) for i in range(3)],
                        Config(policy='shadow_cluster_finite', fabric_gib_s=1.,
                               gpu_ms_per_new_token=1., remote_setup_ms=2.))
        req = Request.from_tokens('r', 0., list(range(16)))
        sim.seed_cache(2, req.hashes)
        sim.nodes[1].queue.append(req)
        sim._start(1)
        short = sim.cfg.block_size * sim.cfg.kv_bytes_per_token
        sim.nodes[1].active.flow_id = sim.network.start(0., 2, 1, short)
        payload = 8 * short
        # The incumbent is gone before the candidate's two-ms setup ends.
        self.assertLess(short/sim.network.fabric, 2.)
        self.assertAlmostEqual(proposed_transfer_ms(sim, 2, 0, payload),
                               2. + payload/sim.network.fabric)
        self.assertEqual(sim.network.flows[1].remaining_bytes, short)

    def test_service_event_index_preserves_finite_forecasts_and_drains(self):
        from scripts.research_shadow import trial
        scenario = dict(name='small', nodes=3, shared=32, capacity=36,
                        compute_scale=16., burst=4, fabric=1.)
        original, _ = trial(scenario, 2101, 'always', 'shadow_cluster_finite', 24)
        candidate, _ = trial(scenario, 2101, 'always', 'shadow_cluster_events', 24)
        self.assertEqual(original.rows, candidate.rows)
        self.assertEqual(original.residuals, candidate.residuals)
        self.assertEqual(candidate.service_events, {})

    def test_certified_execution_reuse_shortcut_preserves_cold_and_warm_actions(self):
        from scripts.research_shadow import trial
        for scale in (1., 16.):
            scenario = dict(name='small', shared=64, capacity=136, compute_scale=scale)
            base, _ = trial(scenario, 2201, 'always', 'round_robin_cost', 20)
            fast, _ = trial(scenario, 2201, 'always', 'round_robin_cost', 20,
                            certified_reuse_shortcut=True)
            self.assertEqual(base.rows, fast.rows)
            self.assertEqual(base.residuals, fast.residuals)
