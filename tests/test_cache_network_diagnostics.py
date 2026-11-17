"""Check read-only hot-prefix residency trace and paired NIC/fabric diagnostics."""
import unittest

from kv_sim.core import Config, NodeSpec, Simulator
from kv_sim.workload.hotspot import make_hotspot_workload, make_hotspot_initial_caches
from scripts.cache_network_diagnostics import experiment


class CacheNetworkDiagnosticsTests(unittest.TestCase):
    def test_opt_in_tracing_does_not_change_results(self):
        specs = [NodeSpec(i, 32, .1) for i in range(3)]
        reqs, hot = make_hotspot_workload(
            count=16, shared_blocks=12, suffix_blocks=1,
            burst_width=4, seed=2)
        cache = make_hotspot_initial_caches(specs, hot)
        a=Simulator(specs, Config(policy="greedy",fabric_gib_s=.1))
        b=Simulator(specs, Config(policy="greedy",fabric_gib_s=.1))
        for node,keys in cache.items():
            a.seed_cache(node,keys)
            b.seed_cache(node,keys)
        b.track_prefix(hot)
        a.run(reqs)
        b.run(reqs)
        self.assertEqual(a.rows,b.rows)
        self.assertEqual(a.summary(),b.summary())
        self.assertEqual(len(a.cache_trace),0)
        self.assertEqual(len(b.cache_trace),len(reqs)*len(specs))
        self.assertTrue(all(0<=r["hot_prefix_resident_blocks"]<=len(hot)
                            for r in b.cache_trace))

    def test_diagnostics_have_source_residency(self):
        specs=[NodeSpec(i, 24, .1) for i in range(3)]
        reqs,hot=make_hotspot_workload(
            count=12,shared_blocks=8,burst_width=3)
        caches=make_hotspot_initial_caches(specs,hot)
        _,summary=experiment(specs,reqs,caches,hot,.1,1,
                             "bandwidth_aware")
        self.assertIn("source_hot_final_blocks",summary)
        self.assertIn("source_evictions",summary)
        self.assertGreaterEqual(summary["source_hot_mean_blocks"],0)


if __name__=="__main__":
    unittest.main()
