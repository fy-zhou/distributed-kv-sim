"""Analytical FIFO cases, no-future-input and mutation invariants."""
import copy
import unittest
from kv_sim import Config, NodeSpec, Request, Simulator
from kv_sim.scheduler.shadow import forecast
from kv_sim.scheduler.reuse import minimum_service


class Observe(Simulator):
    def _choose(self, req):
        if req.request_id == 'third':
            self.forecast = forecast(self, req, 0)
            self.frozen = forecast(self, req, 0, horizon=0)
        return super()._choose(req)


class ShadowTests(unittest.TestCase):
    def test_single_node_rollout_predicts_replica_creation_exactly(self):
        reqs=[Request.from_tokens(name,float(i),list(range(160)))
              for i,name in enumerate(('first','second','third'))]
        sim=Observe([NodeSpec(0,32,1.)],Config(policy='shadow_cache',gpu_ms_per_new_token=1.))
        rows=sim.run(reqs)
        third=next(r for r in rows if r['request_id']=='third')
        self.assertAlmostEqual(sim.forecast['estimated_ms'],third['ttft_ms'])
        self.assertGreater(sim.frozen['estimated_ms'],sim.forecast['estimated_ms'])
        self.assertEqual(sim.forecast['cache_updates'],2)

    def test_forecast_does_not_mutate_cache_pins_jobs_network_or_events(self):
        sim=Simulator([NodeSpec(i,32,.1) for i in range(2)],Config(policy='shadow_cache'))
        req=Request.from_tokens('r',0,list(range(160)))
        sim.seed_cache(0,req.hashes)
        sim.nodes[0].cache.pin(req.hashes)
        sim.nodes[0].queue.append(req)
        sim.network.start(0,0,1,10000)
        def state():
            return copy.deepcopy((sim.nodes,sim.network.__dict__,sim.events,sim.now,sim.epoch))
        before=state()
        forecast(sim,req,0)
        self.assertEqual(before,state())

    def test_no_future_arrivals_and_horizon_accounts_for_entire_queue(self):
        sim=Simulator([NodeSpec(0,32,1.)],Config(policy='shadow_bounded',gpu_ms_per_new_token=1.))
        req=Request.from_tokens('r',0,list(range(160)))
        sim.nodes[0].queue.extend([req]*12)
        expected=forecast(sim,req,0,horizon=1)
        sim._schedule(0,'arrival',Request.from_tokens('future',0,list(range(1000,1160))))
        self.assertEqual(expected,forecast(sim,req,0,horizon=1))
        self.assertEqual(expected['rollout_jobs'],12)
        self.assertEqual(expected['cache_updates'],1)
        self.assertGreater(expected['queue_ms'],sim._compute_time(req,0))

    def test_oversized_active_admission_forecasts_prefix_loss(self):
        sim=Simulator([NodeSpec(0,2,1.)],Config(policy='shadow_cache',gpu_ms_per_new_token=1.))
        req=Request.from_tokens('first',0,list(range(48)))
        sim.seed_cache(0,req.hashes[:2]);sim.nodes[0].queue.append(req);sim._start(0)
        next_req=Request.from_tokens('next',1,list(range(48)))
        predicted=forecast(sim,next_req,0)
        self.assertEqual(predicted['total_hit_blocks'],0)
        self.assertEqual(sim.nodes[0].cache.prefix_len(req.hashes),2)

    def test_all_shadow_policies_use_identical_execution_actions(self):
        req=Request.from_tokens('r',0,list(range(160)))
        for policy in ('cost_aware','round_robin_cost','shadow_frozen','shadow_cache','shadow_bounded'):
            sim=Simulator([NodeSpec(i,32,1.)for i in range(2)],Config(policy=policy,gpu_ms_per_new_token=1.))
            sim.seed_cache(1,req.hashes)
            self.assertEqual(sim._reuse_choice(req,0),minimum_service(sim,req,0))

    def test_no_remote_admission_is_respected_in_rollout(self):
        req=Request.from_tokens('first',0,list(range(160)))
        sim=Simulator([NodeSpec(i,32,100.)for i in range(2)],
                      Config(policy='shadow_cache',admission='no_remote',gpu_ms_per_new_token=1.))
        sim.seed_cache(1,req.hashes);sim.nodes[0].queue.append(req);sim._start(0)
        predicted=forecast(sim,req,0)
        self.assertEqual(predicted['local_blocks'],0)
        self.assertEqual(predicted['remote_blocks'],10)
