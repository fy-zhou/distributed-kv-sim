"""Counterexamples and action/execution consistency for cost-aware reuse."""
import unittest
from kv_sim import Config, NodeSpec, Request, Simulator
from kv_sim.scheduler.reuse import minimum_service
from kv_sim.workload.hotspot import make_hotspot_workload


class CostAwareTests(unittest.TestCase):
    def test_recompute_wins_over_expensive_remote_and_local_h2d(self):
        req = Request.from_tokens('r',0,list(range(4096)))
        sim = Simulator([NodeSpec(i,512,.1) for i in range(2)],
                        Config(policy='cost_aware',fabric_gib_s=.1))
        sim.seed_cache(1,req.hashes)
        self.assertEqual(minimum_service(sim,req,0),(0,None,0))
        # Full local H2D is more expensive than illustrative 4096-token compute.
        self.assertEqual(minimum_service(sim,req,1),(0,None,0))
        row = sim.run([req])[0]
        self.assertEqual(row['total_hit_tokens'],0)
        self.assertEqual(sim.network.total_bytes,0)

    def test_shorter_fast_source_beats_longest_slow_source(self):
        req = Request.from_tokens('r',0,list(range(1600)))
        sim = Simulator([NodeSpec(0,128,100.),NodeSpec(1,128,.001),NodeSpec(2,128,100.)],
                        Config(policy='cost_aware',gpu_ms_per_new_token=1.,
                               h2d_fixed_ms=0.,h2d_ms_per_gib=0.,fabric_gib_s=100.))
        sim.seed_cache(1,req.hashes)
        sim.seed_cache(2,req.hashes[:80])
        self.assertEqual(sim._best_source(req,0),(0,1,100))
        self.assertEqual(minimum_service(sim,req,0),(0,2,80))

    def test_plan_used_by_execution_and_estimate_includes_all_costs(self):
        req = Request.from_tokens('r',0,list(range(160)))
        sim = Simulator([NodeSpec(i,32,1.) for i in range(2)],
                        Config(policy='round_robin_cost',gpu_ms_per_new_token=1.,
                               fabric_gib_s=1.))
        sim.seed_cache(1,req.hashes)
        predicted=sim._estimate_breakdown(req,0)
        row=sim.run([req])[0]
        self.assertEqual(row['source'],1)
        self.assertEqual(row['total_hit_tokens'],160)
        self.assertAlmostEqual(predicted['estimated_ms'],row['ttft_ms'])

    def test_execution_replans_when_decision_cache_is_gone(self):
        req=Request.from_tokens('r',0,list(range(160)))
        sim=Simulator([NodeSpec(i,32,1.)for i in range(2)],
                      Config(policy='round_robin_cost',gpu_ms_per_new_token=1.))
        sim.seed_cache(1,req.hashes)
        self.assertEqual(sim._reuse_choice(req,0)[1],1)
        sim.nodes[1].cache.blocks.clear()
        sim.nodes[0].queue.append(req)
        sim._start(0)
        self.assertIsNone(sim.nodes[0].active.source)
        self.assertEqual(sim.nodes[0].active.hit,0)

    def test_local_extension_transfers_only_missing_bytes(self):
        req=Request.from_tokens('r',0,list(range(160)))
        sim=Simulator([NodeSpec(i,32,1.)for i in range(2)],
                      Config(policy='round_robin_cost',gpu_ms_per_new_token=1.))
        sim.seed_cache(0,req.hashes[:4]);sim.seed_cache(1,req.hashes)
        row=sim.run([req])[0]
        self.assertEqual(row['local_hit_tokens'],64)
        self.assertEqual(row['remote_hit_tokens'],96)
        self.assertEqual(sim.network.total_bytes,96*sim.cfg.kv_bytes_per_token)

    def test_popularity_option_keeps_default_trace(self):
        a,_=make_hotspot_workload(count=12,shared_blocks=8,seed=9)
        b,_=make_hotspot_workload(count=12,shared_blocks=8,seed=9,hot_probability=.85)
        self.assertEqual(a,b)
        with self.assertRaises(ValueError):
            make_hotspot_workload(hot_probability=2.)
