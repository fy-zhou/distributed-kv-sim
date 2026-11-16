"""Regression tests for paired robustness experiment methodology."""
import unittest
from kv_sim.core import NodeSpec
from kv_sim.workload.hotspot import (
    make_hotspot_workload, make_hotspot_initial_caches)
from scripts.hotspot_robustness import (
    paired_trial, aggregate_runs, percentile)


class RobustnessTests(unittest.TestCase):
    def test_lambda_zero_reproduces_greedy(self):
        specs = [NodeSpec(i, 24, 0.1) for i in range(3)]
        requests, hot = make_hotspot_workload(
            count=20, shared_blocks=8, suffix_blocks=2,
            burst_width=4, seed=5)
        seeds = make_hotspot_initial_caches(specs, hot)
        rows = paired_trial(
            specs, requests, seeds, fabric_gib_s=0.1,
            admission="always", lambdas=[0.0, 1.0])
        zero = rows[0]
        self.assertAlmostEqual(zero["p95_improvement_pct"], 0.0)
        self.assertAlmostEqual(zero["network_reduction_pct"], 0.0)
        self.assertEqual(zero["placement_changes"], 0)
        self.assertEqual(len(rows), 2)

    def test_paired_seed_aggregate(self):
        base = dict(fabric_gib_s=2., burst_width=8,
                    cache_blocks=256, lambda_=1.)
        records = [
            dict(fabric_gib_s=2., burst_width=8, cache_blocks=256,
                 **{"lambda": 1.},
                 p95_improvement_pct=10., network_reduction_pct=5.,
                 placement_change_pct=20., greedy_p95_ms=100.,
                 aware_p95_ms=90.),
            dict(fabric_gib_s=2., burst_width=8, cache_blocks=256,
                 **{"lambda": 1.},
                 p95_improvement_pct=-10., network_reduction_pct=-5.,
                 placement_change_pct=40., greedy_p95_ms=100.,
                 aware_p95_ms=110.),
        ]
        groups = aggregate_runs(records)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["seed_count"], 2)
        self.assertAlmostEqual(groups[0]["p95_improvement_mean_pct"], 0.)
        self.assertEqual(groups[0]["p95_positive_seeds"], 1)
        self.assertAlmostEqual(groups[0]["placement_change_mean_pct"], 30.)

    def test_percentiles(self):
        self.assertAlmostEqual(percentile([10, 20, 30], .5), 20)
        with self.assertRaises(ValueError):
            percentile([], .5)


if __name__ == "__main__":
    unittest.main()
